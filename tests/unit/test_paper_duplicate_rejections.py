"""Unit tests for app.observability.paper_duplicate_rejections."""

from __future__ import annotations

import json
from pathlib import Path

from app.observability.paper_duplicate_rejections import (
    build_paper_duplicate_rejection_summary,
)


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_groups_by_idempotency_key_with_first_last_seen(tmp_path: Path) -> None:
    """Q/USDT 30-replay regression: same idempotency_key across 30 events."""
    path = tmp_path / "audit.jsonl"
    rows = [
        {
            "event_type": "order_created_rejected_duplicate",
            "symbol": "Q/USDT",
            "side": "buy",
            "idempotency_key": "opbridge:ENV-X",
            "rejected_at": f"2026-05-{14 + i:02d}T10:46:52+00:00",
        }
        for i in range(3)
    ] + [
        {"event_type": "order_created", "symbol": "Q/USDT"},  # ignored
        {"event_type": "order_filled", "symbol": "BTC/USDT"},  # ignored
    ]
    _write(path, rows)
    summary = build_paper_duplicate_rejection_summary(audit_path=path)
    assert summary.total == 3
    assert len(summary.by_idempotency_key) == 1
    entry = summary.by_idempotency_key[0]
    assert entry["idempotency_key"] == "opbridge:ENV-X"
    assert entry["count"] == 3
    assert entry["first_seen"] == "2026-05-14T10:46:52+00:00"
    assert entry["last_seen"] == "2026-05-16T10:46:52+00:00"
    assert summary.first_rejected_at == "2026-05-14T10:46:52+00:00"
    assert summary.last_rejected_at == "2026-05-16T10:46:52+00:00"


def test_sorts_keys_by_count_descending(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    rows = [
        {
            "event_type": "order_created_rejected_duplicate",
            "symbol": "Q/USDT",
            "idempotency_key": "k1",
            "rejected_at": "2026-05-14T10:00:00+00:00",
        }
    ] * 2 + [
        {
            "event_type": "order_created_rejected_duplicate",
            "symbol": "Q/USDT",
            "idempotency_key": "k2",
            "rejected_at": "2026-05-14T10:00:00+00:00",
        }
    ] * 5
    _write(path, rows)
    summary = build_paper_duplicate_rejection_summary(audit_path=path)
    assert summary.by_idempotency_key[0]["idempotency_key"] == "k2"
    assert summary.by_idempotency_key[0]["count"] == 5
    assert summary.by_idempotency_key[1]["idempotency_key"] == "k1"
    assert summary.by_idempotency_key[1]["count"] == 2


def test_missing_file_is_empty_summary(tmp_path: Path) -> None:
    summary = build_paper_duplicate_rejection_summary(audit_path=tmp_path / "missing.jsonl")
    assert summary.total == 0
    assert summary.by_idempotency_key == []
