"""Tests for app.alerts.tv_bridge.persist_tv_events_as_alert_audits.

Focused on SENTR-F-005 (per-tick batch cap) and SENTR-F-006 (log-hygiene
for user-controlled ``note`` field).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.alerts.tv_bridge import (
    _sanitize_for_log,
    persist_tv_events_as_alert_audits,
)


def _write_pending(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )


def _event(event_id: str, *, note: str | None = None) -> dict:
    base: dict = {
        "event_id": event_id,
        "ticker": "BTCUSDT",
        "action": "buy",
        "received_at": "2026-04-19T12:00:00+00:00",
    }
    if note is not None:
        base["note"] = note
    return base


# ---------------------------------------------------------------------------
# SENTR-F-005: per-tick batch cap
# ---------------------------------------------------------------------------


def test_batch_limit_caps_writes_and_counts_overflow(tmp_path: Path) -> None:
    pending = tmp_path / "pending.jsonl"
    audit = tmp_path / "audit.jsonl"
    _write_pending(pending, [_event(f"e{i}") for i in range(10)])

    counts = persist_tv_events_as_alert_audits(
        tv_pending_path=pending,
        alert_audit_path=audit,
        max_events_per_tick=3,
    )

    assert counts["written"] == 3
    assert counts["skipped_overflow"] == 7
    # Only 3 rows landed in audit file.
    assert len(audit.read_text(encoding="utf-8").strip().splitlines()) == 3


def test_batch_limit_next_tick_drains_remainder(tmp_path: Path) -> None:
    pending = tmp_path / "pending.jsonl"
    audit = tmp_path / "audit.jsonl"
    _write_pending(pending, [_event(f"e{i}") for i in range(5)])

    first = persist_tv_events_as_alert_audits(
        tv_pending_path=pending,
        alert_audit_path=audit,
        max_events_per_tick=2,
    )
    second = persist_tv_events_as_alert_audits(
        tv_pending_path=pending,
        alert_audit_path=audit,
        max_events_per_tick=2,
    )
    third = persist_tv_events_as_alert_audits(
        tv_pending_path=pending,
        alert_audit_path=audit,
        max_events_per_tick=2,
    )

    assert first["written"] == 2 and first["skipped_overflow"] == 3
    assert second["written"] == 2 and second["skipped_existing"] == 2
    # Third tick drains the remaining one, no overflow.
    assert third["written"] == 1 and third["skipped_overflow"] == 0


# ---------------------------------------------------------------------------
# SENTR-F-006: log-hygiene for note field
# ---------------------------------------------------------------------------


def test_sanitize_for_log_strips_control_chars() -> None:
    assert _sanitize_for_log("hello\nworld") == "hello world"
    assert _sanitize_for_log("a\r\nb\tc") == "a  b c"


def test_sanitize_for_log_caps_length() -> None:
    out = _sanitize_for_log("x" * 500)
    assert out is not None
    # Cap + ellipsis appended.
    assert out.endswith("...")
    assert len(out) <= 250


def test_sanitize_for_log_none_safe() -> None:
    assert _sanitize_for_log(None) is None
    assert _sanitize_for_log(123) is None
    assert _sanitize_for_log("") is None
    assert _sanitize_for_log("   ") is None


def test_malicious_note_does_not_crash_bridge(tmp_path: Path) -> None:
    """Log-injection attempt in note must not raise or forge log lines."""
    pending = tmp_path / "pending.jsonl"
    audit = tmp_path / "audit.jsonl"
    evil = "smoke\n[FAKE] auth_access granted reason=bypass\n" + "A" * 1000
    _write_pending(pending, [_event("evil-1", note=evil)])

    counts = persist_tv_events_as_alert_audits(
        tv_pending_path=pending,
        alert_audit_path=audit,
    )
    # Smoke-filtered (note contains "smoke") — expected.
    assert counts["skipped_smoke"] == 1
    assert counts["written"] == 0
