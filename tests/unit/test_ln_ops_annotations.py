"""G2 — the annotation ledger beside the money journal, and the writer guard.

Every guard here carries its positive control. A detector that only proves it
rejects the bad case has proven half of nothing: the expensive failure is the one
where it also rejects the real thing and nobody notices until a spend is blocked.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.lightning.ops_annotations import (
    LightningOpsAnnotationError,
    annotate_for_display,
    annotation_overlay,
    append_annotation,
    read_annotations,
    verify_annotations,
)
from app.lightning.ops_ledger import (
    LightningOpsLedgerError,
    prepare_ln_intent,
    verify_ln_ops_ledger,
)
from app.lightning.plan_guards import plan_soft_flags, plan_structural_defects

REAL_PUBKEY = "024a7f9c" + "0" * 58  # 66 hex chars, valid prefix
REAL_TXID = "a" * 64
REAL_ADDR = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"

# The four values that actually reached the production money journal on 2026-08-05.
FIXTURE_PUBKEY = "02ab"
FIXTURE_TXID = "deadbeef"
FIXTURE_ADDR = "bc1q"


# --------------------------------------------------------------------------- #
# The structural guard
# --------------------------------------------------------------------------- #


def test_the_four_values_that_reached_production_are_rejected() -> None:
    assert plan_structural_defects("open_channel", {"node_pubkey_hex": FIXTURE_PUBKEY})
    assert plan_structural_defects("open_channel", {"funding_txid_str": FIXTURE_TXID})
    assert plan_structural_defects("send_coins", {"addr": FIXTURE_ADDR})
    assert plan_structural_defects("keysend", {"dest_pubkey_hex": FIXTURE_PUBKEY})


def test_positive_control_the_real_plans_pass() -> None:
    """The three actions the G2 forensics proved real must not be blocked."""
    assert plan_structural_defects("open_channel", {"node_pubkey_hex": REAL_PUBKEY}) == []
    assert plan_structural_defects("open_channel", {"funding_txid_str": REAL_TXID}) == []
    assert plan_structural_defects("send_coins", {"addr": REAL_ADDR}) == []
    assert plan_structural_defects("pay_invoice", {"amount_sat": 2100, "fee_limit_sat": 50}) == []


def test_guard_is_silent_about_fields_it_cannot_decide() -> None:
    """Absence, amounts and peers are policy — not this guard's business."""
    assert plan_structural_defects("open_channel", {}) == []
    assert plan_structural_defects("open_channel", {"local_funding_sat": 0}) == []
    assert plan_structural_defects("open_channel", {"node_pubkey_hex": ""}) == []


def test_a_wrong_prefix_is_a_defect_but_a_valid_one_is_not() -> None:
    assert plan_structural_defects("open_channel", {"node_pubkey_hex": "04" + "a" * 64})
    assert plan_structural_defects("open_channel", {"node_pubkey_hex": "03" + "b" * 64}) == []


def test_zero_fee_is_flagged_never_rejected() -> None:
    """sat_per_vbyte=0 is unverified, not impossible — it must not block a call."""
    plan = {"node_pubkey_hex": REAL_PUBKEY, "sat_per_vbyte": 0}
    assert plan_structural_defects("open_channel", plan) == []
    assert plan_soft_flags("open_channel", plan) == ["implausible_fee: sat_per_vbyte=0"]
    assert plan_soft_flags("open_channel", {"sat_per_vbyte": 2}) == []


def test_the_writer_refuses_a_fixture_plan_and_journals_nothing(tmp_path: Path) -> None:
    journal = tmp_path / "ln_ops_ledger_v2.jsonl"
    with pytest.raises(LightningOpsLedgerError):
        prepare_ln_intent(
            "open_channel",
            plan={"node_pubkey_hex": FIXTURE_PUBKEY, "local_funding_sat": 50_000},
            path=journal,
        )
    assert not journal.exists(), "a rejected plan must leave no trace in the money journal"


def test_the_writer_accepts_a_real_plan(tmp_path: Path) -> None:
    journal = tmp_path / "ln_ops_ledger_v2.jsonl"
    record = prepare_ln_intent(
        "open_channel",
        plan={"node_pubkey_hex": REAL_PUBKEY, "local_funding_sat": 50_000},
        path=journal,
    )
    assert record["state"] == "intent"
    assert verify_ln_ops_ledger(journal)["ok"] is True


# --------------------------------------------------------------------------- #
# The annotation ledger
# --------------------------------------------------------------------------- #


def _ops(seq: int, state: str, record_hash: str) -> dict[str, object]:
    return {"seq": seq, "state": state, "action": "open_channel", "record_hash": record_hash}


def test_chain_verifies_and_is_ordered(tmp_path: Path) -> None:
    path = tmp_path / "ann.jsonl"
    first = append_annotation(
        kind="TEST_FIXTURE",
        target_seq=[21, 22],
        target_record_hash=["h21", "h22"],
        assertion="node_pubkey and funding_txid are fixture values",
        evidence=["A12-075"],
        author="operator",
        path=path,
    )
    second = append_annotation(
        kind="RESOLVED_EXECUTED",
        target_seq=[13, 14],
        target_record_hash=["h13", "h14"],
        assertion="wallet fell by 400308 sat; the channel went active",
        evidence=["A12-071", "A12-072"],
        author="operator",
        path=path,
    )
    assert first["seq"] == 1 and second["seq"] == 2
    assert second["prev_hash"] == first["record_hash"]
    assert verify_annotations(path) == {"ok": True, "records": 2, "errors": []}


def test_a_tampered_annotation_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "ann.jsonl"
    append_annotation(
        kind="TEST_FIXTURE",
        target_seq=[21],
        target_record_hash=["h21"],
        assertion="fixture",
        evidence=["A12-075"],
        author="operator",
        path=path,
    )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["assertion"] = "something else entirely"
    path.write_text(json.dumps(rows[0], sort_keys=True) + "\n", encoding="utf-8")
    report = verify_annotations(path)
    assert report["ok"] is False
    assert any(e["reason"] == "record_hash mismatch" for e in report["errors"])


def test_an_annotation_must_carry_kind_target_assertion_and_evidence(tmp_path: Path) -> None:
    path = tmp_path / "ann.jsonl"
    common = {
        "target_seq": [1],
        "target_record_hash": ["h1"],
        "assertion": "x",
        "evidence": ["e"],
        "author": "operator",
        "path": path,
    }
    with pytest.raises(LightningOpsAnnotationError):
        append_annotation(**{**common, "kind": "SOMETHING_NEW"})  # type: ignore[arg-type]
    with pytest.raises(LightningOpsAnnotationError):
        append_annotation(**{**common, "kind": "TEST_FIXTURE", "target_seq": []})  # type: ignore[arg-type]
    with pytest.raises(LightningOpsAnnotationError):
        append_annotation(**{**common, "kind": "TEST_FIXTURE", "assertion": "  "})  # type: ignore[arg-type]
    with pytest.raises(LightningOpsAnnotationError):
        append_annotation(**{**common, "kind": "TEST_FIXTURE", "evidence": []})  # type: ignore[arg-type]
    assert not path.exists()


def test_overlay_applies_only_to_the_row_it_was_written_against(tmp_path: Path) -> None:
    path = tmp_path / "ann.jsonl"
    append_annotation(
        kind="TEST_FIXTURE",
        target_seq=[21],
        target_record_hash=["hash-at-annotation-time"],
        assertion="fixture",
        evidence=["A12-075"],
        author="operator",
        path=path,
    )
    annotations = read_annotations(path)

    matching = annotation_overlay([_ops(21, "executed", "hash-at-annotation-time")], annotations)
    assert matching[21]["kind"] == "TEST_FIXTURE"

    replaced = annotation_overlay([_ops(21, "executed", "a-different-hash")], annotations)
    assert replaced == {}, "a changed row must lose its annotation, not inherit the claim"


def test_a_retraction_removes_the_claim(tmp_path: Path) -> None:
    path = tmp_path / "ann.jsonl"
    first = append_annotation(
        kind="RESOLVED_EXECUTED",
        target_seq=[13],
        target_record_hash=["h13"],
        assertion="executed",
        evidence=["A12-071"],
        author="operator",
        path=path,
    )
    append_annotation(
        kind="RETRACTION",
        target_seq=[first["seq"]],
        target_record_hash=[first["record_hash"]],
        assertion="withdrawn: evidence was misread",
        evidence=["G2"],
        author="operator",
        path=path,
    )
    overlay = annotation_overlay([_ops(13, "error", "h13")], read_annotations(path))
    assert overlay == {}
    assert verify_annotations(path)["ok"] is True


def test_display_never_changes_state_and_never_mutates_the_input(tmp_path: Path) -> None:
    """The whole point of way B: the cap and the dedup must see the raw journal."""
    path = tmp_path / "ann.jsonl"
    append_annotation(
        kind="RESOLVED_EXECUTED",
        target_seq=[13],
        target_record_hash=["h13"],
        assertion="wallet fell by 400308 sat",
        evidence=["A12-071"],
        author="operator",
        path=path,
    )
    records = [_ops(13, "error", "h13")]
    shown = annotate_for_display(records, read_annotations(path))
    assert shown[0]["annotation"]["kind"] == "RESOLVED_EXECUTED"
    assert shown[0]["state"] == "error", "the journal's own state is never overwritten"
    assert "annotation" not in records[0], "the caller's records must not be mutated"


def test_a_missing_ledger_is_a_valid_empty_state(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist.jsonl"
    assert verify_annotations(path) == {"ok": True, "records": 0, "errors": []}
    assert read_annotations(path) == []


def test_annotations_do_not_touch_the_money_journal(tmp_path: Path) -> None:
    journal = tmp_path / "ln_ops_ledger_v2.jsonl"
    prepare_ln_intent(
        "open_channel",
        plan={"node_pubkey_hex": REAL_PUBKEY, "local_funding_sat": 50_000},
        path=journal,
        now=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
    )
    before = journal.read_bytes()
    append_annotation(
        kind="TEST_FIXTURE",
        target_seq=[1],
        target_record_hash=["irrelevant"],
        assertion="x",
        evidence=["e"],
        author="operator",
        path=tmp_path / "ann.jsonl",
    )
    assert journal.read_bytes() == before
