"""Source-lifecycle audit-trail tests (Phase 1 of the source-lifecycle plan)."""

from __future__ import annotations

from pathlib import Path

from app.learning.source_lifecycle_audit import (
    LifecycleEvent,
    append_lifecycle_event,
    read_lifecycle_events,
)


def _event(source: str = "decrypt", to_status: str = "archived") -> LifecycleEvent:
    return LifecycleEvent(
        source=source,
        from_status="active",
        to_status=to_status,
        reason="rotation",
        recorded_at_utc="2026-06-23T20:00:00+00:00",
        evidence={"wilson_lower": 0.2, "n": 25},
    )


def test_append_and_read_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "lc.jsonl"
    append_lifecycle_event(_event(), p)
    append_lifecycle_event(_event(source="btc-echo", to_status="silent"), p)
    events = read_lifecycle_events(p)
    assert len(events) == 2
    assert events[0].source == "decrypt"
    assert events[0].to_status == "archived"
    assert events[0].evidence == {"wilson_lower": 0.2, "n": 25}
    assert events[1].to_status == "silent"


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_lifecycle_events(tmp_path / "nope.jsonl") == []


def test_corrupt_line_is_skipped(tmp_path: Path) -> None:
    p = tmp_path / "lc.jsonl"
    append_lifecycle_event(_event(), p)
    with p.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
        fh.write("\n")
    assert len(read_lifecycle_events(p)) == 1
