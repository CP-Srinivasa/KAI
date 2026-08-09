"""Fristablauf als eigener, alarmierender Zustand des Reconciliation-Evaluators.

Der Evaluator kannte bis hierher nur ``PASS``/``FAIL``/``IMMATURE``. ``IMMATURE``
ist bis zur Reife der Normalzustand und alarmiert bewusst nicht — genau deshalb
konnte eine Prä-Reg still über ihre Frist hinauslaufen: am Stichtag hätte
niemand etwas gehört, weil ``IMMATURE`` schweigt und die Unit gruen bleibt.

Das ist dieselbe Klasse wie der H2-Zombie (14/50, unbegrenzt „reifend"), nur
eine Ebene tiefer: dort fehlte die Frist in der Prä-Reg, hier fehlt ihr
*Vollzug* im Evaluator.

Festgehalten wird:
* Fenster abgelaufen UND unreif ⇒ ``INCONCLUSIVE_BY_TIMEOUT`` (kein Sachverdikt,
  ``passed=false``, aber ausdruecklich KEIN ``FAIL``).
* Vor Fristablauf bleibt ``IMMATURE`` unveraendert stumm — rein additiv.
* Reife schlaegt die Frist: wer sein n erreicht hat, wird normal beurteilt.
* Der Timeout alarmiert und faerbt die Unit rot; ein verpasster Stichtag darf
  nicht einmal blinken und dann verschwinden.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from scripts.ln_reconciliation_eval import (
    VERDICT_TIMEOUT,
    evaluate,
    exit_code,
    maybe_alert,
)

T0 = datetime(2026, 8, 8, 10, 50, 9, tzinfo=UTC)

SEALED_CRITERIA = (
    "In the first 96 enabled shadow runs within 7d: all runs pass Truth-tip containment "
    "and zero unsupported, unmatched, ambiguous, amount-mismatched or nonterminal intents "
    "are terminalised; every naturally observed uniquely matched terminal BOLT11 payment "
    "with equal hash and amount is appended exactly once by the next completed run. If zero "
    "eligible open-intent incidents occur, transition-effectiveness remains INSUFFICIENT_N "
    "and only the safety/tip axis may pass. This is no readiness, capital, alpha or revenue "
    "claim."
)


def _prereg() -> dict[str, Any]:
    return {
        "prereg_id": "0879a65c5fd01f65",
        "name": "ln_reconciliation_shadow_integrity_v1",
        "created_at_utc": T0.isoformat(),
        "horizon": "7d",
        "sample_size_target": 96,
        "success_criteria": SEALED_CRITERIA,
    }


def _clean_run(ts: datetime) -> dict[str, Any]:
    """Ein sauberer Shadow-Lauf: Tip enthalten, nichts terminalisiert."""
    return {
        "ts": ts.isoformat(),
        "enabled": True,
        "tip_cross_check": {"contained": True},
        "intents": [],
    }


def _runs(count: int) -> list[dict[str, Any]]:
    return [_clean_run(T0 + timedelta(minutes=15 * i)) for i in range(count)]


def test_frist_abgelaufen_und_unreif_ergibt_timeout_statt_immature() -> None:
    """Der Stichtag ist verstrichen, n ist nicht erreicht — das muss enden koennen."""
    result = evaluate(
        prereg=_prereg(),
        runs=_runs(4),  # weit unter 96
        now=T0 + timedelta(days=7, seconds=1),
    )

    assert result["verdict"] == VERDICT_TIMEOUT
    assert result["passed"] is False
    # Ein Timeout ist KEIN Sachverdikt: die Hypothese ist nicht widerlegt,
    # sie wurde nur nicht messbar. Diese Unterscheidung ist der ganze Punkt.
    assert result["verdict"] != "FAIL"
    assert result["mature"] is False


def test_vor_fristablauf_bleibt_immature_stumm() -> None:
    """Rein additiv: solange das Fenster laeuft, aendert sich nichts."""
    result = evaluate(
        prereg=_prereg(),
        runs=_runs(4),
        now=T0 + timedelta(days=3),
    )

    assert result["verdict"] == "IMMATURE"
    assert result["passed"] is False


def test_reife_schlaegt_die_frist() -> None:
    """Wer sein n erreicht hat, wird beurteilt — die Frist ist Zombie-Bremse, kein Deckel."""
    result = evaluate(
        prereg=_prereg(),
        runs=_runs(96),
        now=T0 + timedelta(days=30),
    )

    assert result["verdict"] == "PASS"
    assert result["passed"] is True


def test_timeout_alarmiert_beim_wechsel() -> None:
    sent: list[str] = []
    result = evaluate(
        prereg=_prereg(),
        runs=_runs(4),
        now=T0 + timedelta(days=7, seconds=1),
    )

    alerted = maybe_alert(result, recorded=True, sender=sent.append)

    assert alerted is True
    assert len(sent) == 1
    assert "0879a65c5fd01f65" in sent[0]
    # Der Text muss den Operator zur versiegelten Regel fuehren, nicht zu einem Fix.
    assert "frist" in sent[0].lower()
    assert "nicht widerlegt" in sent[0].lower()


def test_timeout_alarmiert_nicht_ohne_verdikt_wechsel() -> None:
    """Stuendlicher Timer: nach dem ersten Timeout-Alarm ist Ruhe bis zur Handlung."""
    sent: list[str] = []
    result = evaluate(
        prereg=_prereg(),
        runs=_runs(4),
        now=T0 + timedelta(days=7, seconds=1),
    )

    assert maybe_alert(result, recorded=False, sender=sent.append) is False
    assert sent == []


def test_timeout_faerbt_die_unit_rot() -> None:
    """Ein verpasster Stichtag muss sichtbar BLEIBEN, nicht einmal blinken."""
    result = evaluate(
        prereg=_prereg(),
        runs=_runs(4),
        now=T0 + timedelta(days=7, seconds=1),
    )

    assert exit_code(result, exit_nonzero_on_fail=True) == 1
    # Ohne das Flag bleibt das Verhalten unveraendert.
    assert exit_code(result, exit_nonzero_on_fail=False) == 0


def test_immature_faerbt_die_unit_weiterhin_nicht_rot() -> None:
    result = evaluate(prereg=_prereg(), runs=_runs(4), now=T0 + timedelta(days=3))

    assert exit_code(result, exit_nonzero_on_fail=True) == 0
