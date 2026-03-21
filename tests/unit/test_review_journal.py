from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.research.operational_readiness import (
    JOURNAL_STATUS_OPEN,
    JOURNAL_STATUS_RESOLVED,
    REVIEW_ACTION_DEFER,
    REVIEW_ACTION_NOTE,
    REVIEW_ACTION_RESOLVE,
    append_review_journal_entry_jsonl,
    build_operator_decision_pack,
    build_review_journal_summary,
    build_review_resolution_summary,
    create_review_journal_entry,
    load_review_journal_entries,
)


def test_create_review_journal_entry_maps_actions_to_status() -> None:
    note = create_review_journal_entry(
        source_ref="act_blocking_1",
        operator_id="ops-1",
        review_action=REVIEW_ACTION_NOTE,
        review_note="Initial review logged.",
    )
    defer = create_review_journal_entry(
        source_ref="act_blocking_2",
        operator_id="ops-1",
        review_action=REVIEW_ACTION_DEFER,
        review_note="Deferred until more evidence is collected.",
    )
    resolved = create_review_journal_entry(
        source_ref="act_blocking_3",
        operator_id="ops-1",
        review_action=REVIEW_ACTION_RESOLVE,
        review_note="Resolved after operator validation.",
    )

    assert note.journal_status == JOURNAL_STATUS_OPEN
    assert defer.journal_status == JOURNAL_STATUS_OPEN
    assert resolved.journal_status == JOURNAL_STATUS_RESOLVED


def test_create_review_journal_entry_rejects_invalid_action() -> None:
    with pytest.raises(ValueError, match="review_action must be one of"):
        create_review_journal_entry(
            source_ref="act_blocking_1",
            operator_id="ops-1",
            review_action="promote",
            review_note="This must fail closed.",
        )


def test_append_review_journal_entry_jsonl_is_append_only(tmp_path: Path) -> None:
    journal_path = tmp_path / "operator_review_journal.jsonl"
    first = create_review_journal_entry(
        source_ref="act_blocking_1",
        operator_id="ops-1",
        review_action=REVIEW_ACTION_NOTE,
        review_note="First append.",
    )
    second = create_review_journal_entry(
        source_ref="act_blocking_2",
        operator_id="ops-2",
        review_action=REVIEW_ACTION_RESOLVE,
        review_note="Second append.",
    )

    append_review_journal_entry_jsonl(first, journal_path)
    append_review_journal_entry_jsonl(second, journal_path)

    lines = journal_path.read_text(encoding="utf-8").splitlines()
    loaded = load_review_journal_entries(journal_path)

    assert len(lines) == 2
    assert len(loaded) == 2
    assert loaded[0].review_id == first.review_id
    assert loaded[1].review_id == second.review_id


def test_load_review_journal_entries_skips_malformed_rows(tmp_path: Path) -> None:
    journal_path = tmp_path / "operator_review_journal.jsonl"
    valid = create_review_journal_entry(
        source_ref="act_blocking_1",
        operator_id="ops-1",
        review_action=REVIEW_ACTION_NOTE,
        review_note="Valid entry.",
    )
    journal_path.write_text(
        json.dumps(valid.to_json_dict()) + "\n" + "{not-json}\n",
        encoding="utf-8",
    )

    loaded = load_review_journal_entries(journal_path)

    assert len(loaded) == 1
    assert loaded[0].review_id == valid.review_id


def test_build_review_journal_summary_tracks_latest_status_per_source_ref() -> None:
    entries = [
        create_review_journal_entry(
            source_ref="act_blocking_1",
            operator_id="ops-1",
            review_action=REVIEW_ACTION_NOTE,
            review_note="Initial note.",
            created_at="2026-03-21T10:00:00+00:00",
        ),
        create_review_journal_entry(
            source_ref="act_blocking_1",
            operator_id="ops-1",
            review_action=REVIEW_ACTION_RESOLVE,
            review_note="Resolved later.",
            created_at="2026-03-21T11:00:00+00:00",
        ),
        create_review_journal_entry(
            source_ref="artifacts/decision_pack.json",
            operator_id="ops-2",
            review_action=REVIEW_ACTION_DEFER,
            review_note="Still open.",
            created_at="2026-03-21T12:00:00+00:00",
        ),
    ]

    summary = build_review_journal_summary(
        entries,
        journal_path="artifacts/operator_review_journal.jsonl",
    )

    assert summary.total_count == 3
    assert summary.source_ref_count == 2
    assert summary.open_count == 1
    assert summary.resolved_count == 1
    assert summary.journal_status == JOURNAL_STATUS_OPEN
    assert summary.latest_created_at == "2026-03-21T12:00:00+00:00"
    assert len(summary.latest_entries) == 2
    assert summary.execution_enabled is False
    assert summary.write_back_allowed is False


def test_build_review_resolution_summary_lists_latest_open_and_resolved_sources() -> None:
    entries = [
        create_review_journal_entry(
            source_ref="act_blocking_1",
            operator_id="ops-1",
            review_action=REVIEW_ACTION_RESOLVE,
            review_note="Closed.",
            created_at="2026-03-21T10:00:00+00:00",
        ),
        create_review_journal_entry(
            source_ref="act_blocking_2",
            operator_id="ops-2",
            review_action=REVIEW_ACTION_DEFER,
            review_note="Still open.",
            created_at="2026-03-21T11:00:00+00:00",
        ),
    ]
    summary = build_review_journal_summary(entries, journal_path="artifacts/journal.jsonl")

    resolution = build_review_resolution_summary(summary)

    assert resolution.open_count == 1
    assert resolution.resolved_count == 1
    assert resolution.open_source_refs == ["act_blocking_2"]
    assert resolution.resolved_source_refs == ["act_blocking_1"]
    assert resolution.execution_enabled is False
    assert resolution.write_back_allowed is False


def test_review_journal_entries_do_not_mutate_operator_decision_pack(tmp_path: Path) -> None:
    pack = build_operator_decision_pack()
    before = json.dumps(pack.to_json_dict(), sort_keys=True)

    entry = create_review_journal_entry(
        source_ref="rbk_123",
        operator_id="ops-1",
        review_action=REVIEW_ACTION_NOTE,
        review_note="Journal only.",
    )
    append_review_journal_entry_jsonl(entry, tmp_path / "operator_review_journal.jsonl")

    after = json.dumps(pack.to_json_dict(), sort_keys=True)

    assert before == after


def test_review_journal_summary_has_no_trading_semantics() -> None:
    entry = create_review_journal_entry(
        source_ref="rbk_123",
        operator_id="ops-1",
        review_action=REVIEW_ACTION_NOTE,
        review_note="Audit only.",
    )
    summary = build_review_journal_summary([entry], journal_path="artifacts/journal.jsonl")
    resolution = build_review_resolution_summary(summary)

    serialized = json.dumps(
        {
            "summary": summary.to_json_dict(),
            "resolution": resolution.to_json_dict(),
        }
    ).lower()

    assert "trade" not in serialized
    assert "order" not in serialized
