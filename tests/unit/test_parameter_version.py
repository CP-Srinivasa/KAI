"""Unit tests for the hash-chained ParameterVersion-Journal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.learning.parameter_version import (
    GENESIS_PREV_HASH,
    SCHEMA_VERSION,
    ParameterChange,
    ParameterVersionStore,
    _hash_record,
)


@pytest.fixture
def store(tmp_path: Path) -> ParameterVersionStore:
    return ParameterVersionStore(tmp_path / "journal.jsonl")


# ============================================================================
# Genesis + basic write
# ============================================================================


def test_propose_genesis_uses_genesis_prev_hash(store: ParameterVersionStore):
    rec = store.propose_version(
        parameter_path="bayes.calibrator.global",
        parameter_set={"intercept": 0.05, "slope": 0.92},
        evidence={"n_pairs": 80, "brier_before": 0.21, "brier_after": 0.18},
    )
    assert rec.prev_chain_hash == GENESIS_PREV_HASH
    assert rec.record_type == "version_proposed"
    assert rec.schema_version == SCHEMA_VERSION
    assert rec.version_id.startswith("pv_")
    assert rec.parent_version_id is None  # nothing was active before


def test_journal_file_is_appended_one_line_per_record(store: ParameterVersionStore):
    store.propose_version(parameter_path="x", parameter_set={"a": 1})
    store.propose_version(parameter_path="x", parameter_set={"a": 2})
    store.propose_version(parameter_path="y", parameter_set={"b": 3})
    lines = store.path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3
    # Each line is valid JSON
    for line in lines:
        json.loads(line)


# ============================================================================
# Hash-chain integrity
# ============================================================================


def test_chain_remains_consistent_across_multiple_records(store: ParameterVersionStore):
    rec1 = store.propose_version(parameter_path="p", parameter_set={"v": 1})
    rec2 = store.propose_version(parameter_path="p", parameter_set={"v": 2})
    rec3 = store.propose_version(parameter_path="q", parameter_set={"v": 3})
    assert rec1.prev_chain_hash == GENESIS_PREV_HASH
    assert rec2.prev_chain_hash == _hash_record(rec1)
    assert rec3.prev_chain_hash == _hash_record(rec2)
    ok, err = store.verify_chain()
    assert ok, err


def test_chain_documents_last_line_limitation(store: ParameterVersionStore):
    """Standard hash-chain property: tampering with the *last* record alone
    cannot be detected — the chain only seals records that have a successor.

    Once any further legitimate write happens via propose_version(), the
    tampering is sealed in by the chain hash of the new line, and the
    intermediate-record test catches it from then on.
    """
    store.propose_version(parameter_path="p", parameter_set={"v": 1})
    store.propose_version(parameter_path="p", parameter_set={"v": 2})
    # Tamper the last line in place
    lines = store.path.read_text(encoding="utf-8").strip().split("\n")
    payload = json.loads(lines[1])
    payload["parameter_set"] = {"v": 999}
    lines[1] = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    store.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # As documented: still verifies clean — the last line is not yet sealed.
    ok, _ = store.verify_chain()
    assert ok


def test_verify_detects_in_place_change_of_intermediate_record(
    store: ParameterVersionStore,
):
    store.propose_version(parameter_path="p", parameter_set={"v": 1})
    store.propose_version(parameter_path="p", parameter_set={"v": 2})
    store.propose_version(parameter_path="p", parameter_set={"v": 3})
    # Tamper with the middle record's notes
    lines = store.path.read_text(encoding="utf-8").strip().split("\n")
    payload = json.loads(lines[1])
    payload["notes"] = "secretly altered"
    lines[1] = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    store.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, err = store.verify_chain()
    assert not ok
    assert "#3" in err  # mismatch is detected at line 3 (refers to tampered #2)


def test_empty_journal_verifies_clean(store: ParameterVersionStore):
    ok, err = store.verify_chain()
    assert ok
    assert err is None


# ============================================================================
# Activation, rollback, reject
# ============================================================================


def test_activation_links_to_existing_proposal(store: ParameterVersionStore):
    proposal = store.propose_version(
        parameter_path="bayes.calibrator.global",
        parameter_set={"intercept": 0.05, "slope": 0.92},
    )
    activation = store.activate_version(
        parameter_path="bayes.calibrator.global",
        version_id=proposal.version_id,
        notes="approved by operator",
    )
    assert activation.record_type == "version_activated"
    assert activation.version_id == proposal.version_id
    assert activation.parameter_set == {}  # activation events carry no payload
    ok, _ = store.verify_chain()
    assert ok


def test_activation_unknown_version_raises(store: ParameterVersionStore):
    with pytest.raises(ValueError, match="unknown_version"):
        store.activate_version(
            parameter_path="bayes.calibrator.global",
            version_id="pv_does_not_exist",
        )


def test_latest_active_returns_most_recently_activated_version(
    store: ParameterVersionStore,
):
    p1 = store.propose_version(parameter_path="p", parameter_set={"v": 1})
    p2 = store.propose_version(parameter_path="p", parameter_set={"v": 2})
    store.activate_version(parameter_path="p", version_id=p1.version_id)
    store.activate_version(parameter_path="p", version_id=p2.version_id)
    active = store.latest_active("p")
    assert active is not None
    assert active.version_id == p2.version_id
    assert active.parameter_set == {"v": 2}


def test_latest_active_is_none_for_proposed_only(store: ParameterVersionStore):
    store.propose_version(parameter_path="p", parameter_set={"v": 1})
    # Never activated
    assert store.latest_active("p") is None


def test_rollback_to_earlier_version_replaces_active_pointer(
    store: ParameterVersionStore,
):
    p1 = store.propose_version(parameter_path="p", parameter_set={"v": 1})
    p2 = store.propose_version(parameter_path="p", parameter_set={"v": 2})
    store.activate_version(parameter_path="p", version_id=p1.version_id)
    store.activate_version(parameter_path="p", version_id=p2.version_id)
    rollback = store.rollback_to(
        parameter_path="p", version_id=p1.version_id, notes="regression detected"
    )
    assert rollback.record_type == "version_rolled_back"
    assert store.latest_active("p").version_id == p1.version_id
    ok, _ = store.verify_chain()
    assert ok


def test_reject_marks_proposal_without_changing_active(store: ParameterVersionStore):
    p1 = store.propose_version(parameter_path="p", parameter_set={"v": 1})
    store.activate_version(parameter_path="p", version_id=p1.version_id)
    p2 = store.propose_version(parameter_path="p", parameter_set={"v": 2})
    store.reject_version(parameter_path="p", version_id=p2.version_id, reason="OoS-Brier worse")
    # Active is still p1 — reject doesn't switch
    assert store.latest_active("p").version_id == p1.version_id


def test_parent_version_is_auto_filled_to_latest_active(
    store: ParameterVersionStore,
):
    p1 = store.propose_version(parameter_path="p", parameter_set={"v": 1})
    store.activate_version(parameter_path="p", version_id=p1.version_id)
    p2 = store.propose_version(parameter_path="p", parameter_set={"v": 2})
    assert p2.parent_version_id == p1.version_id


# ============================================================================
# Round-trip + history
# ============================================================================


def test_records_round_trip_through_disk(store: ParameterVersionStore):
    store.propose_version(parameter_path="p", parameter_set={"v": 1})
    store.propose_version(parameter_path="p", parameter_set={"v": 2})
    # Re-open the store from path
    fresh = ParameterVersionStore(store.path)
    records = list(fresh.iter_records())
    assert len(records) == 2
    assert all(isinstance(r, ParameterChange) for r in records)
    assert [r.parameter_set["v"] for r in records] == [1, 2]


def test_history_filters_by_path(store: ParameterVersionStore):
    store.propose_version(parameter_path="a", parameter_set={"v": 1})
    store.propose_version(parameter_path="b", parameter_set={"v": 2})
    store.propose_version(parameter_path="a", parameter_set={"v": 3})
    a_history = store.history("a")
    assert len(a_history) == 2
    assert all(r.parameter_path == "a" for r in a_history)


def test_iter_records_skips_malformed_lines_with_warning(
    store: ParameterVersionStore,
):
    store.propose_version(parameter_path="p", parameter_set={"v": 1})
    # Inject garbage line
    with store.path.open("a", encoding="utf-8") as fh:
        fh.write("not-json-at-all\n")
    store.propose_version(parameter_path="p", parameter_set={"v": 2})
    records = list(store.iter_records())
    # Garbage skipped, two valid records remain
    assert len(records) == 2


def test_evidence_is_persisted_round_trip(store: ParameterVersionStore):
    rec = store.propose_version(
        parameter_path="bayes.calibrator.global",
        parameter_set={"intercept": 0.05, "slope": 0.92},
        evidence={
            "n_pairs": 80,
            "brier_before": 0.21,
            "brier_after": 0.18,
            "ece_before": 0.07,
            "ece_after": 0.04,
        },
        notes="auto-fit nightly run",
        created_by="learning-cron",
    )
    fresh = ParameterVersionStore(store.path).iter_records()
    persisted = next(fresh)
    assert persisted.evidence == rec.evidence
    assert persisted.notes == "auto-fit nightly run"
    assert persisted.created_by == "learning-cron"
