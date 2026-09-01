"""STAB-2026-09-01 — INVALIDATED_PENDING_SUCCESSOR: abgebrochen, Nachfolger offen.

``INVALIDATED_BEFORE_MEASUREMENT`` verlangt ``replaced_by``. Beim Abbruch des
zweiten G8-Akts (``ebbf451f432cbc80``, 2026-09-01T20:38:51Z) existierte die
Nachfolge-ID noch gar nicht: sie faellt deterministisch aus dem DEPLOYTEN Code
(Mainline-SHA + evaluator_sha256 + health_notify_sha256 + Config-SHA), und der
Deploy stand aus.

Eine vorausberechnete Platzhalter-ID waere exakt der Fehler gewesen, den #843
teuer nachgewiesen hat: die erste Vorausberechnung ergab ``b7f9a8e204e40e23`` und
traf nicht, weil der gepinnte Evaluator danach noch einmal bearbeitet wurde.

Der Entscheid ist damit NICHT aufgeschoben — er ist getroffen, datiert und
gehasht. Nur die Verkettung zum Nachfolger fehlt.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.research.prereg_maturity import (
    INVALIDATED_STATES,
    INVALIDATION_SUCCESSOR_TRANSITION,
    SUPERVISING_DECISION_STATES,
    validate_invalidated_entry,
)

REGISTER = Path(__file__).resolve().parents[2] / "config" / "prereg_supervision.json"
ACT2 = "ebbf451f432cbc80"


def _register() -> dict:
    return json.loads(REGISTER.read_text(encoding="utf-8"))


def _entry(prereg_id: str) -> dict:
    for e in _register()["entries"]:
        if e.get("prereg_id") == prereg_id:
            return e
    raise AssertionError(f"{prereg_id} not in register")


def _valid_pending(**over) -> dict:
    base = {
        "decision_state": "INVALIDATED_PENDING_SUCCESSOR",
        "substantive_verdict": "NONE",
        "invalidation_reason": "MEASUREMENT_INSTRUMENT_DEFECT_DISCOVERED_POST_T0",
        "invalidated_at_utc": "2026-09-01T20:38:51Z",
        "replacement_pending": True,
        "replaced_by": None,
        "MATURITY_SPEC": "none",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------
def test_the_state_is_supervised_so_it_is_not_reported_as_a_gap() -> None:
    """An invalidated act is DECIDED, not overlooked.

    If the watchdog treated it as an oversight gap it would emit a finding about
    its own register every run — and that self-finding is precisely what poisoned
    the FIRST G8 act.
    """
    assert "INVALIDATED_PENDING_SUCCESSOR" in SUPERVISING_DECISION_STATES
    assert "INVALIDATED_BEFORE_MEASUREMENT" in SUPERVISING_DECISION_STATES


def test_a_wellformed_pending_entry_validates() -> None:
    assert validate_invalidated_entry(_valid_pending()) == []


def test_the_only_allowed_transition_is_to_before_measurement() -> None:
    assert INVALIDATION_SUCCESSOR_TRANSITION == (
        "INVALIDATED_PENDING_SUCCESSOR",
        "INVALIDATED_BEFORE_MEASUREMENT",
    )
    assert set(INVALIDATED_STATES) == set(INVALIDATION_SUCCESSOR_TRANSITION)


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS — exactly the ones the order demands
# --------------------------------------------------------------------------
def test_a_substantive_verdict_is_refused() -> None:
    """An aborted measurement must never carry a result. It measured nothing."""
    for verdict in ("MET", "NOT_MET", "INVALID", "INCONCLUSIVE"):
        errors = validate_invalidated_entry(_valid_pending(substantive_verdict=verdict))
        assert any("substantive_verdict" in e for e in errors), verdict


def test_a_watcher_is_refused() -> None:
    """A dead measurement must not keep being watched."""
    errors = validate_invalidated_entry(_valid_pending(watcher_id="kai-prereg-maturity"))
    assert any("watcher_id" in e for e in errors)


def test_a_review_date_is_refused() -> None:
    errors = validate_invalidated_entry(_valid_pending(next_review_utc="2026-09-15T14:00:00Z"))
    assert any("next_review_utc" in e for e in errors)


def test_a_cadence_is_refused() -> None:
    errors = validate_invalidated_entry(_valid_pending(cadence="one deadline"))
    assert any("cadence" in e for e in errors)


def test_a_maturity_spec_is_refused() -> None:
    errors = validate_invalidated_entry(_valid_pending(MATURITY_SPEC="back_edge_v2"))
    assert any("MATURITY_SPEC" in e for e in errors)


def test_a_placeholder_successor_id_is_refused() -> None:
    """THE point of this state. No invented replaced_by."""
    errors = validate_invalidated_entry(_valid_pending(replaced_by="deadbeefdeadbeef"))
    assert any("replaced_by" in e for e in errors)


def test_pending_without_the_pending_flag_is_refused() -> None:
    errors = validate_invalidated_entry(_valid_pending(replacement_pending=False))
    assert any("replacement_pending" in e for e in errors)


@pytest.mark.parametrize("missing", ["invalidation_reason", "invalidated_at_utc"])
def test_reason_and_timestamp_are_mandatory(missing: str) -> None:
    entry = _valid_pending()
    entry[missing] = None
    errors = validate_invalidated_entry(entry)
    assert any(missing in e for e in errors)


def test_the_successor_state_still_requires_a_real_id() -> None:
    """NEGATIVE CONTROL for the other side: the transition must not be free."""
    entry = _valid_pending(decision_state="INVALIDATED_BEFORE_MEASUREMENT")
    errors = validate_invalidated_entry(entry)
    assert any("replaced_by" in e for e in errors)

    entry["replaced_by"] = "abc123abc123abc1"
    entry.pop("replacement_pending", None)
    assert validate_invalidated_entry(entry) == []


def test_a_non_invalidated_state_is_not_policed_by_this_rule() -> None:
    assert validate_invalidated_entry({"decision_state": "WATCH", "watcher_id": "x"}) == []


# --------------------------------------------------------------------------
# The live register entry
# --------------------------------------------------------------------------
def test_g8_act2_is_recorded_as_aborted_without_a_result() -> None:
    e = _entry(ACT2)
    assert e["decision_state"] == "INVALIDATED_PENDING_SUCCESSOR"
    assert e["substantive_verdict"] == "NONE"
    assert e["invalidation_reason"] == "MEASUREMENT_INSTRUMENT_DEFECT_DISCOVERED_POST_T0"
    assert e["invalidated_at_utc"] == "2026-09-01T20:38:51Z"
    assert e["replacement_pending"] is True
    assert e["replaced_by"] is None
    assert validate_invalidated_entry(e) == []


def test_the_live_entry_carries_its_audit_artifact_hash() -> None:
    e = _entry(ACT2)
    assert e["audit_artifact"].endswith("G8_ACT2_INVALIDATION_20260901T203851Z.json")
    assert len(e["audit_artifact_sha256"]) == 64


def test_the_reason_is_the_instrument_not_the_branch_hash() -> None:
    """The distinction the operator insisted on, pinned in the record itself.

    A waiting branch changes nothing in production. The hard reason is that the
    RUNNING instrument was measured and found wrong.
    """
    rationale = _entry(ACT2)["rationale"]
    assert "ALTERSBLINDE" in rationale
    assert "NULL" in rationale
    assert "veraendert Produktion nicht" in rationale
    assert "KEIN emitted" in rationale or "KEIN Evaluator" in rationale


def test_no_count_was_read() -> None:
    """No evaluator run, no emitted/acted count, no interim result."""
    artifact = (
        Path(__file__).resolve().parents[2].parent
        / "KAI-mirror"
        / "reports"
        / "G8_ACT2_INVALIDATION_20260901T203851Z.json"
    )
    if not artifact.exists():  # the mirror is not part of the repo checkout in CI
        pytest.skip("audit artifact lives in KAI-mirror, outside the repo")
    doc = json.loads(artifact.read_text(encoding="utf-8"))
    nd = doc["not_done"]
    assert nd["evaluator_executed"] is False
    assert nd["emitted_count_inspected"] is False
    assert nd["acted_count_inspected"] is False
    assert nd["outcome_inspected"] is False
