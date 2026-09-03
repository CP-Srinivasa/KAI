"""Der Reconcile-Timer als PROZESS (ADR 0018 §5/§8, Health-Pfad §10).

Zwei Fragen, die kein Modultest beantwortet:

1. **Sendet der Timer?** ADR §5 sagt: ein sendender Prozess (``kai-server``),
   der Timer haengt nur Outcomes an. Das ist keine Konvention, sondern die
   Grundlage der Doppelzahlungs-Freiheit — zwei sendende Prozesse haetten zwei
   Meinungen ueber denselben Intent. Der Test faehrt den Skript-Rumpf und
   prueft, dass ``rail.pay`` nicht einmal beruehrt wurde.
2. **Merkt es jemand, wenn er ``attention`` meldet?** Ein Waisen-Settlement,
   das nur in einer JSON-Datei steht, ist ein Befund ohne Empfaenger.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.alerts.alert_classes import COMPONENT_CLASSES, AlertClass
from app.alerts.health_check import _check_payment_reconciliation
from app.core.payment_settings import PaymentSettings
from app.payments.journal import PaymentJournal
from app.payments.reconcile_types import STATE_FILENAME, ReconcileState, save_state

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Der Timer-Prozess
# --------------------------------------------------------------------------- #


async def test_der_timer_prozess_sendet_nie(tmp_path: Path, monkeypatch: Any) -> None:
    """``scripts/ln_reconcile.py`` haengt Outcomes an — es sendet nicht."""
    import scripts.ln_reconcile as timer

    from app.payments.rails.simulation import SimulationRail

    class PaySpy(SimulationRail):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.pay_calls = 0

        async def pay(self, intent: Any, attempt: Any) -> Any:  # pragma: no cover - darf nie
            self.pay_calls += 1
            raise AssertionError("the reconcile timer must never send")

    journal_path = tmp_path / "payments" / "payment_journal.jsonl"
    journal = PaymentJournal(journal_path)
    journal.open()
    journal.append("pi_1", "intent_created", {"status": "REQUESTED"}, ts=NOW)

    rail = PaySpy(now=NOW)
    settings = PaymentSettings(mode="simulation", journal_path=str(journal_path))
    report = await timer.reconcile_payments(
        journal=journal,
        rail=rail,
        settings=settings,
        state_path=tmp_path / STATE_FILENAME,
    )

    assert rail.pay_calls == 0
    assert report["status"] == "ok"
    assert report["rail"] == rail.name


async def test_der_timer_meldet_seinen_befund_weiter(tmp_path: Path) -> None:
    """Ein ``attention``-Lauf muss auf der Platte stehen — der Health-Check
    laeuft in einem ANDEREN Prozess und hat sonst nichts zu lesen."""
    import scripts.ln_reconcile as timer

    from app.payments.rails.simulation import SimulationRail

    journal_path = tmp_path / "payments" / "payment_journal.jsonl"
    journal = PaymentJournal(journal_path)
    journal.open()
    rail = SimulationRail(now=NOW)
    rail.inject_payment("a" * 64)
    state_path = tmp_path / STATE_FILENAME

    report = await timer.reconcile_payments(
        journal=journal,
        rail=rail,
        settings=PaymentSettings(mode="simulation", journal_path=str(journal_path)),
        state_path=state_path,
    )

    assert report["status"] == "attention"
    assert json.loads(state_path.read_text(encoding="utf-8"))["last_status"] == "attention"


def test_das_skript_behaelt_seinen_einstiegspunkt() -> None:
    """Name, Pfad und Unit bleiben — der Timer laeuft schon."""
    import scripts.ln_reconcile as timer

    assert hasattr(timer, "_main")
    assert hasattr(timer, "reconcile_payments")
    unit = Path("deploy/systemd/kai-ln-reconcile.service")
    if unit.is_file():
        assert "ln_reconcile.py" in unit.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Der Alarmpfad
# --------------------------------------------------------------------------- #


def test_kein_zustand_ist_kein_befund(tmp_path: Path) -> None:
    """Vor dem ersten Lauf gibt es nichts zu melden."""
    assert _check_payment_reconciliation(tmp_path) == []


def test_ok_ist_kein_befund(tmp_path: Path) -> None:
    save_state(
        tmp_path / "payments" / STATE_FILENAME,
        ReconcileState(last_run_utc=NOW.isoformat(), last_status="ok"),
    )
    assert _check_payment_reconciliation(tmp_path) == []


def test_attention_ist_ein_befund(tmp_path: Path) -> None:
    save_state(
        tmp_path / "payments" / STATE_FILENAME,
        ReconcileState(last_run_utc=NOW.isoformat(), last_status="attention", last_orphans=2),
    )
    issues = _check_payment_reconciliation(tmp_path)
    assert len(issues) == 1
    assert issues[0].severity == "critical"
    assert issues[0].component == "payment_reconciliation"
    assert "orphan" in issues[0].message


def test_ein_unlesbarer_zustand_ist_ein_befund(tmp_path: Path) -> None:
    """Fail-closed: eine kaputte Zustandsdatei ist nicht "alles in Ordnung"."""
    path = tmp_path / "payments" / STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    issues = _check_payment_reconciliation(tmp_path)
    assert len(issues) == 1
    assert issues[0].component == "payment_reconciliation"


@pytest.mark.parametrize("component", ["payment_journal", "payment_reconciliation"])
def test_jede_geld_komponente_hat_eine_alarmklasse(component: str) -> None:
    """Ein Befund ohne Klasse faellt in die Sammelmeldung — Geld nicht."""
    assert COMPONENT_CLASSES[component] is AlertClass.P0
