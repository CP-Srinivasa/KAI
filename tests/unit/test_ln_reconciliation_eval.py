"""Messvorschrift zur Prä-Reg ``0879a65c5fd01f65`` (ln_reconciliation_shadow_integrity_v1).

Die Prä-Reg trägt ``gate=null`` — ``prereg-check`` kann sie strukturell nicht
beurteilen. Ohne ausführbaren Evaluator wäre das Verdikt zum Fälligkeitstag
nicht reproduzierbar und ein FAIL nicht von „Skript kaputt" unterscheidbar
(Lehre aus C1, #630).

Die Konstruktion wird NICHT angetastet: das Skript liest den versiegelten
``success_criteria``-Text aus dem Prä-Reg-Ledger, prüft seine Schlüsselklauseln
wörtlich und bricht bei Divergenz ab, statt eine zweite Wahrheit zu erfinden.

``test_positivkontrolle_sauberer_lauf_ergibt_pass`` ist die Positivkontrolle:
ein synthetischer, sauberer Stream MUSS PASS liefern. Ohne sie wäre ein FAIL
auf echten Daten nicht von einem kaputten Evaluator zu unterscheiden.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from scripts.ln_reconciliation_eval import (
    CriteriaDivergenceError,
    evaluate,
    load_prereg,
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


def _prereg(criteria: str = SEALED_CRITERIA) -> dict[str, Any]:
    return {
        "prereg_id": "0879a65c5fd01f65",
        "name": "ln_reconciliation_shadow_integrity_v1",
        "created_at_utc": T0.isoformat(),
        "horizon": "7d",
        "sample_size_target": 96,
        "gate": None,
        "success_criteria": criteria,
    }


def _run(
    minutes: int,
    *,
    contained: bool = True,
    status: str = "ok",
    intents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "ln-reconciliation/v1",
        "ts": (T0 + timedelta(minutes=minutes)).isoformat(),
        "status": status,
        "tip_cross_check": {
            "contained": contained,
            "truth_seq": 75,
            "journal_seq": 26,
            "reason": "contained" if contained else "attested_tip_not_in_journal",
        },
        "journal": {"records_before": 26, "open_before": 0, "records_after": 26, "open_after": 0},
        "node": {"pages": 0, "payments": 0, "skipped": "no_open_intents"},
        "intents": intents or [],
        "errors": [],
    }


def _leerlauf(n: int) -> list[dict[str, Any]]:
    """n saubere Leerlauf-Runs im 15-min-Takt."""
    return [_run(15 * i) for i in range(n)]


# --------------------------------------------------------------------------
# Positivkontrolle + Reife
# --------------------------------------------------------------------------


def test_positivkontrolle_sauberer_lauf_ergibt_pass() -> None:
    result = evaluate(prereg=_prereg(), runs=_leerlauf(96))
    assert result["verdict"] == "PASS"
    assert result["passed"] is True
    assert result["safety_axis"]["passed"] is True
    assert result["runs_counted"] == 96


def test_zu_wenige_laeufe_sind_unreife_kein_verdikt() -> None:
    """n < n_target ist Unreife — NICHT FAIL (Lehre aus ND-v2)."""
    result = evaluate(prereg=_prereg(), runs=_leerlauf(40))
    assert result["verdict"] == "IMMATURE"
    assert result["passed"] is False
    assert result["runs_counted"] == 40


def test_nur_die_ersten_96_laeufe_zaehlen() -> None:
    result = evaluate(prereg=_prereg(), runs=_leerlauf(120))
    assert result["runs_counted"] == 96


def test_laeufe_ausserhalb_des_fensters_zaehlen_nicht() -> None:
    runs = _leerlauf(96)
    runs.append(_run(15 * 96 + 60 * 24 * 8))  # 8 Tage nach t0 => ausserhalb 7d
    runs.insert(0, _run(-30))  # vor der Versiegelung
    result = evaluate(prereg=_prereg(), runs=runs)
    assert result["runs_counted"] == 96


# --------------------------------------------------------------------------
# Sicherheits-/Tip-Achse
# --------------------------------------------------------------------------


def test_ein_einziger_containment_bruch_kippt_die_sicherheitsachse() -> None:
    runs = _leerlauf(96)
    runs[42] = _run(15 * 42, contained=False, status="error")
    result = evaluate(prereg=_prereg(), runs=runs)
    assert result["safety_axis"]["passed"] is False
    assert result["safety_axis"]["tip_containment_failures"] == 1
    assert result["verdict"] == "FAIL"


@pytest.mark.parametrize(
    "reason",
    [
        "unsupported_action",
        "payment_not_found",
        "ambiguous_payment_hash",
        "amount_mismatch",
        "node_payment_nonterminal",
        "invalid_intent_payment_hash",
    ],
)
def test_terminalisierung_eines_nicht_berechtigten_intents_ist_fail(reason: str) -> None:
    runs = _leerlauf(96)
    runs[7] = _run(
        15 * 7,
        intents=[
            {
                "intent_id": "i-1",
                "action": "pay_invoice",
                "payment_hash": "a" * 64,
                "node_status": "",
                "result": "journalled_executed",  # <- unzulaessig bei diesem reason
                "reason": reason,
            }
        ],
    )
    result = evaluate(prereg=_prereg(), runs=runs)
    assert result["safety_axis"]["passed"] is False
    assert result["safety_axis"]["illegal_terminalisations"] == 1
    assert result["verdict"] == "FAIL"


def test_offen_gelassener_nicht_berechtigter_intent_ist_kein_verstoss() -> None:
    """left_open ist das erwartete, sichere Verhalten — kein Verstoss."""
    runs = _leerlauf(96)
    runs[7] = _run(
        15 * 7,
        status="attention",
        intents=[
            {
                "intent_id": "i-1",
                "action": "pay_invoice",
                "payment_hash": "a" * 64,
                "node_status": "",
                "result": "left_open",
                "reason": "payment_not_found",
            }
        ],
    )
    result = evaluate(prereg=_prereg(), runs=runs)
    assert result["safety_axis"]["passed"] is True
    assert result["safety_axis"]["illegal_terminalisations"] == 0


# --------------------------------------------------------------------------
# Transitions-Achse
# --------------------------------------------------------------------------


def test_ohne_vorfaelle_bleibt_die_transitionsachse_insufficient_n() -> None:
    result = evaluate(prereg=_prereg(), runs=_leerlauf(96))
    assert result["transition_axis"]["status"] == "INSUFFICIENT_N"
    assert result["transition_axis"]["eligible_incidents"] == 0
    # Versiegelt: dann darf NUR die Sicherheitsachse bestehen.
    assert result["verdict"] == "PASS"
    assert result["scope_note"]


def test_sauber_terminalisierter_treffer_laesst_die_transitionsachse_bestehen() -> None:
    runs = _leerlauf(96)
    runs[10] = _run(
        15 * 10,
        intents=[
            {
                "intent_id": "i-42",
                "action": "pay_invoice",
                "payment_hash": "b" * 64,
                "node_status": "SUCCEEDED",
                "result": "journalled_executed",
                "reason": "node_terminal_match",
            }
        ],
    )
    result = evaluate(prereg=_prereg(), runs=runs)
    assert result["transition_axis"]["status"] == "PASS"
    assert result["transition_axis"]["eligible_incidents"] == 1
    assert result["verdict"] == "PASS"


def test_doppelte_terminalisierung_desselben_intents_ist_fail() -> None:
    """„exactly once" ist wörtlich versiegelt — zweimal ist ein Verstoss."""
    runs = _leerlauf(96)
    treffer = {
        "intent_id": "i-42",
        "action": "pay_invoice",
        "payment_hash": "b" * 64,
        "node_status": "SUCCEEDED",
        "result": "journalled_executed",
        "reason": "node_terminal_match",
    }
    runs[10] = _run(15 * 10, intents=[dict(treffer)])
    runs[11] = _run(15 * 11, intents=[dict(treffer)])
    result = evaluate(prereg=_prereg(), runs=runs)
    assert result["transition_axis"]["duplicate_terminalisations"] == 1
    assert result["transition_axis"]["status"] == "FAIL"
    assert result["verdict"] == "FAIL"


def test_treffer_ohne_anhang_ist_fail() -> None:
    runs = _leerlauf(96)
    runs[10] = _run(
        15 * 10,
        intents=[
            {
                "intent_id": "i-42",
                "action": "pay_invoice",
                "payment_hash": "b" * 64,
                "node_status": "SUCCEEDED",
                "result": "append_unproven",
                "reason": "journal_append_failed",
            }
        ],
    )
    result = evaluate(prereg=_prereg(), runs=runs)
    assert result["transition_axis"]["unappended_matches"] == 1
    assert result["transition_axis"]["status"] == "FAIL"
    assert result["verdict"] == "FAIL"


# --------------------------------------------------------------------------
# Bindung an den versiegelten Text
# --------------------------------------------------------------------------


def test_divergenter_kriterientext_bricht_ab_statt_zu_raten() -> None:
    kaputt = SEALED_CRITERIA.replace("96 enabled shadow runs", "12 enabled shadow runs")
    with pytest.raises(CriteriaDivergenceError):
        evaluate(prereg=_prereg(kaputt), runs=_leerlauf(96))


def test_kriterien_hash_steht_im_ergebnis() -> None:
    result = evaluate(prereg=_prereg(), runs=_leerlauf(96))
    assert len(result["success_criteria_sha256"]) == 64
    assert result["prereg_id"] == "0879a65c5fd01f65"


def test_load_prereg_findet_den_versiegelten_satz(tmp_path: Path) -> None:
    ledger = tmp_path / "prereg_ledger.jsonl"
    ledger.write_text(
        json.dumps({"prereg_id": "aaaa", "name": "fremd"}) + "\n" + json.dumps(_prereg()) + "\n",
        encoding="utf-8",
    )
    found = load_prereg(ledger, "0879a65c5fd01f65")
    assert found["name"] == "ln_reconciliation_shadow_integrity_v1"


def test_load_prereg_meldet_fehlenden_satz(tmp_path: Path) -> None:
    ledger = tmp_path / "prereg_ledger.jsonl"
    ledger.write_text(json.dumps({"prereg_id": "aaaa"}) + "\n", encoding="utf-8")
    with pytest.raises(LookupError):
        load_prereg(ledger, "0879a65c5fd01f65")
