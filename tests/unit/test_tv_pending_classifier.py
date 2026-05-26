"""Unit tests for app.observability.tv_pending_classifier."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.observability.tv_pending_classifier import build_tv_pending_breakdown


def _write_pending(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_age_buckets_split_by_received_at(tmp_path: Path) -> None:
    """Mirror the 2026-05-26 75-event tail freshness check — without
    age buckets the operator cannot tell whether the backlog is one
    day old or two weeks."""
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    path = tmp_path / "tv_pending.jsonl"
    rows = [
        {
            "event_id": "e0",
            "received_at": (now - timedelta(hours=4)).isoformat(),
            "ticker": "BTCUSDT",
        },
        {
            "event_id": "e1",
            "received_at": (now - timedelta(days=3)).isoformat(),
            "ticker": "BTCUSDT",
        },
        {
            "event_id": "e2",
            "received_at": (now - timedelta(days=10)).isoformat(),
            "ticker": "BTC",
        },
        {
            "event_id": "e3",
            "received_at": (now - timedelta(days=20)).isoformat(),
            "ticker": "ETH",
        },
    ]
    _write_pending(path, rows)

    bd = build_tv_pending_breakdown(audit_path=path, now_utc=now)
    assert bd.total == 4
    assert bd.by_age_bucket["<1d"] == 1
    assert bd.by_age_bucket["1-7d"] == 1
    assert bd.by_age_bucket["7-14d"] == 1
    assert bd.by_age_bucket[">14d"] == 1


def test_records_without_timestamp_count_as_unknown(tmp_path: Path) -> None:
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    path = tmp_path / "tv_pending.jsonl"
    rows = [
        {"event_id": "x", "ticker": "BTC"},
        {"event_id": "y", "received_at": "garbage", "ticker": "BTC"},
    ]
    _write_pending(path, rows)
    bd = build_tv_pending_breakdown(audit_path=path, now_utc=now)
    assert bd.total == 2
    assert bd.by_age_bucket.get("unknown") == 2


def test_top_tickers_and_externals(tmp_path: Path) -> None:
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
    path = tmp_path / "tv_pending.jsonl"
    rows = [
        {"event_id": f"e{i}", "ticker": "BTCUSDT", "external_event_id": "tvalert-12345"}
        for i in range(3)
    ] + [
        {"event_id": f"e{i + 3}", "ticker": "ETH", "external_event_id": "tvalert-99"}
        for i in range(2)
    ]
    _write_pending(path, rows)
    bd = build_tv_pending_breakdown(audit_path=path, now_utc=now)
    assert bd.by_ticker[0] == ("BTCUSDT", 3)
    assert bd.by_external_event_id[0] == ("tvalert-12345", 3)


def test_missing_file_returns_zero_breakdown(tmp_path: Path) -> None:
    bd = build_tv_pending_breakdown(audit_path=tmp_path / "missing.jsonl")
    assert bd.total == 0
    assert bd.by_ticker == []
