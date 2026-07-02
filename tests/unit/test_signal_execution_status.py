from __future__ import annotations

import json
from pathlib import Path

from app.execution.signal_execution_status import build_signal_execution_status


def _write(path: Path, row: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def test_signal_execution_status_joins_bridge_watcher_and_paper(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.jsonl"
    paper = tmp_path / "paper.jsonl"
    watcher = tmp_path / "watcher.jsonl"
    _write(
        bridge,
        {
            "timestamp_utc": "2026-05-10T12:00:00+00:00",
            "envelope_id": "env-1",
            "correlation_id": "corr-1",
            "stage": "pending",
            "lifecycle_state": "WAITING_FOR_ENTRY",
            "audit_reason": "entry_not_reached",
        },
    )
    _write(
        watcher,
        {
            "timestamp_utc": "2026-05-10T12:00:01+00:00",
            "envelope_id": "env-1",
            "correlation_id": "corr-1",
            "symbol": "BTC/USDT",
            "decision": "TRIGGER_ENTRY",
            "reason": "range_hit",
            "lifecycle_state": "ENTRY_TRIGGERED",
            "price": 65250.0,
        },
    )
    _write(
        paper,
        {
            "timestamp_utc": "2026-05-10T12:00:02+00:00",
            "event_type": "lifecycle_transition",
            "correlation_id": "corr-1",
            "to_state": "POSITION_OPEN",
            "reason": "paper_position_opened",
        },
    )

    payload = build_signal_execution_status(
        bridge_log_path=bridge,
        paper_audit_log_path=paper,
        entry_watcher_log_path=watcher,
    )

    assert payload["total_correlations"] == 1
    assert payload["positions_open"] == 1
    assert payload["bridge_stage_counts"] == {"pending": 1}
    assert payload["recent"][0]["watcher_decision"] == "TRIGGER_ENTRY"
    assert payload["recent"][0]["lifecycle_state"] == "POSITION_OPEN"
