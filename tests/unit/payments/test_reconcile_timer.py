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
from datetime import UTC, datetime, timedelta
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
        clock=lambda: NOW,
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
        clock=lambda: NOW,
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
    assert _check_payment_reconciliation(tmp_path, now=NOW) == []


def test_ok_ist_kein_befund(tmp_path: Path) -> None:
    save_state(
        tmp_path / "payments" / STATE_FILENAME,
        ReconcileState(last_run_utc=NOW.isoformat(), last_status="ok"),
    )
    assert _check_payment_reconciliation(tmp_path, now=NOW) == []


def test_attention_ist_ein_befund(tmp_path: Path) -> None:
    save_state(
        tmp_path / "payments" / STATE_FILENAME,
        ReconcileState(last_run_utc=NOW.isoformat(), last_status="attention", last_orphans=2),
    )
    issues = _check_payment_reconciliation(tmp_path, now=NOW)
    assert len(issues) == 1
    assert issues[0].severity == "critical"
    assert issues[0].component == "payment_reconciliation"
    assert "orphan" in issues[0].message


def test_ein_unlesbarer_zustand_ist_ein_befund(tmp_path: Path) -> None:
    """Fail-closed: eine kaputte Zustandsdatei ist nicht "alles in Ordnung"."""
    path = tmp_path / "payments" / STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    issues = _check_payment_reconciliation(tmp_path, now=NOW)
    assert len(issues) == 1
    assert issues[0].component == "payment_reconciliation"


# --------------------------------------------------------------------------- #
# Die Lebendigkeit des Laufs (ADR 0018 §12, PR 2)
# --------------------------------------------------------------------------- #
#
# Bis PR 2 hing die Lebend-Wache des Geldpfads an ``ln_reconciliation.jsonl``:
# derselbe Timer schrieb beide Haelften, also verriet der Alt-Report auch den
# Tod des neuen Laufs. Mit dem Wegfall der v2-Haelfte hat diese Datei keinen
# Schreiber mehr — eine Schwelle darauf waere ab sofort entweder ein
# Daueralarm oder, nach dem Loeschen des Eintrags, gar keine Wache mehr.
#
# Das waere exakt das Muster vom 2026-08-08 (TV-Eingang, sechs Tage tot bei
# gruener Unit). Der Waechter zieht darum auf ``reconcile_state.json`` um, und
# zwar auf ``last_run_utc`` statt auf die mtime: die mtime setzt jedes ``cp``
# und jeder Restore neu, das Feld schreibt nur der Reconciler selbst.


def test_ein_still_gestorbener_reconcile_timer_ist_ein_befund(tmp_path: Path) -> None:
    """``last_status=ok`` von vor einer Stunde ist kein gruener Geldpfad.

    Der Zustand bleibt nach dem Tod des Timers fuer immer auf dem letzten
    Ergebnis stehen. Ohne Altersgrenze meldet die Wache genau so lange
    "in Ordnung", wie niemand mehr nachsieht.
    """
    save_state(
        tmp_path / "payments" / STATE_FILENAME,
        ReconcileState(last_run_utc=(NOW - timedelta(minutes=60)).isoformat(), last_status="ok"),
    )
    issues = _check_payment_reconciliation(tmp_path, now=NOW)
    assert len(issues) == 1
    assert issues[0].severity == "critical"
    assert issues[0].component == "payment_reconciliation"
    assert "60min" in issues[0].message


def test_ein_einzelner_verpasster_lauf_ist_kein_befund(tmp_path: Path) -> None:
    """15-min-Takt plus RandomizedDelaySec=2min — 20 min sind noch normal."""
    save_state(
        tmp_path / "payments" / STATE_FILENAME,
        ReconcileState(last_run_utc=(NOW - timedelta(minutes=20)).isoformat(), last_status="ok"),
    )
    assert _check_payment_reconciliation(tmp_path, now=NOW) == []


def test_ein_alter_zustand_meldet_das_alter_und_nicht_nur_den_status(tmp_path: Path) -> None:
    """Ein alter ``attention``-Zustand darf nicht als frischer Befund gelten."""
    save_state(
        tmp_path / "payments" / STATE_FILENAME,
        ReconcileState(
            last_run_utc=(NOW - timedelta(minutes=90)).isoformat(),
            last_status="attention",
            last_orphans=1,
        ),
    )
    issues = _check_payment_reconciliation(tmp_path, now=NOW)
    assert [i.component for i in issues] == ["payment_reconciliation"]
    assert "90min" in issues[0].message


def test_eine_unlesbare_laufzeit_ist_fail_closed(tmp_path: Path) -> None:
    """Ein ``last_run_utc``, das keine Zeit ist, ist kein junger Lauf."""
    save_state(
        tmp_path / "payments" / STATE_FILENAME,
        ReconcileState(last_run_utc="gestern", last_status="ok"),
    )
    issues = _check_payment_reconciliation(tmp_path, now=NOW)
    assert len(issues) == 1
    assert issues[0].component == "payment_reconciliation"


def test_der_alte_report_bewacht_den_geldpfad_nicht_laenger(tmp_path: Path) -> None:
    """``ln_reconciliation.jsonl`` hat seit PR 2 keinen Schreiber mehr.

    Eine Freshness-Schwelle auf eine Datei, die niemand mehr schreibt, ist
    entweder ein Daueralarm oder eine Zusage ohne Deckung. Beides ist
    schlimmer als keine Zeile — die Lebend-Wache steht jetzt woanders.
    """
    import os

    from app.alerts.health_check import _FRESHNESS_PER_FILE_MIN, _check_data_freshness

    assert "ln_reconciliation.jsonl" not in _FRESHNESS_PER_FILE_MIN

    report = tmp_path / "lightning" / "ln_reconciliation.jsonl"
    report.parent.mkdir(parents=True)
    report.write_text("{}\n", encoding="utf-8")
    aged = (NOW - timedelta(minutes=600)).timestamp()
    os.utime(report, (aged, aged))
    issues, _ = _check_data_freshness(tmp_path, NOW)
    assert not any(i.component.startswith("ln_reconcile") for i in issues)


@pytest.mark.parametrize("component", ["payment_journal", "payment_reconciliation"])
def test_jede_geld_komponente_hat_eine_alarmklasse(component: str) -> None:
    """Ein Befund ohne Klasse faellt in die Sammelmeldung — Geld nicht."""
    assert COMPONENT_CLASSES[component] is AlertClass.P0
