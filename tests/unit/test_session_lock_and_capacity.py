"""Telegram session lock + read-only capacity report."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.ingestion import telegram_session_lock as lock
from app.observability.capacity_report import build_capacity_report

# --------------------------------------------------------------------------- #
# Session lock
# --------------------------------------------------------------------------- #


def test_acquire_then_same_owner_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "listener.lock"
    lock.acquire(p, host="pi", pid=100)
    # same host+pid re-acquire must not raise
    info = lock.acquire(p, host="pi", pid=100)
    assert info.host == "pi"
    assert info.pid == 100


def test_foreign_fresh_lock_blocks_startup(tmp_path: Path) -> None:
    p = tmp_path / "listener.lock"
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    lock.acquire(p, host="pi", pid=100, now=now)
    # a different host trying to start 1 minute later -> blocked
    with pytest.raises(lock.SessionLockError):
        lock.acquire(p, host="windows-dev", pid=200, now=now + timedelta(minutes=1))


def test_stale_foreign_lock_is_taken_over(tmp_path: Path) -> None:
    p = tmp_path / "listener.lock"
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    lock.acquire(p, host="pi", pid=100, now=now)
    # 31 min later (default stale window 30 min) a new host may take over
    info = lock.acquire(p, host="windows-dev", pid=200, now=now + timedelta(minutes=31))
    assert info.host == "windows-dev"


def test_heartbeat_refreshes_only_for_owner(tmp_path: Path) -> None:
    p = tmp_path / "listener.lock"
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    lock.acquire(p, host="pi", pid=100, now=now)
    assert lock.heartbeat(p, host="pi", pid=100, now=now + timedelta(minutes=5)) is True
    assert lock.heartbeat(p, host="other", pid=999) is False


def test_release_only_by_owner(tmp_path: Path) -> None:
    p = tmp_path / "listener.lock"
    lock.acquire(p, host="pi", pid=100)
    assert lock.release(p, host="other", pid=1) is False
    assert lock.release(p, host="pi", pid=100) is True
    assert not p.exists()


def test_corrupt_lock_is_treated_as_stale(tmp_path: Path) -> None:
    p = tmp_path / "listener.lock"
    p.write_text("{not valid json", encoding="utf-8")
    # must not raise — corrupt lock is taken over
    info = lock.acquire(p, host="pi", pid=100)
    assert info.host == "pi"


# --------------------------------------------------------------------------- #
# Capacity report
# --------------------------------------------------------------------------- #


def _write_audit_open_positions(path: Path, symbols: list[str]) -> None:
    """Minimal paper audit that opens `symbols` (long) with no closes."""
    lines = []
    for i, sym in enumerate(symbols):
        lines.append(
            json.dumps(
                {
                    "event_type": "order_filled",
                    "timestamp_utc": "2026-06-01T10:00:00+00:00",
                    "fill_id": f"fill_{i}",
                    "order_id": f"ord_{i}",
                    "symbol": sym,
                    "side": "buy",
                    "quantity": 10.0,
                    "fill_price": 100.0,
                    "fee_usd": 0.1,
                    "filled_at": "2026-06-01T10:00:00+00:00",
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_book_full_detected(tmp_path: Path) -> None:
    audit = tmp_path / "paper.jsonl"
    pending = tmp_path / "pending.jsonl"
    _write_audit_open_positions(audit, ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    rep = build_capacity_report(max_open_positions=3, audit_path=audit, pending_path=pending)
    assert rep.open_count == 3
    assert rep.book_full is True
    assert rep.slots_free == 0
    assert any("book full" in n for n in rep.notes)


def test_slots_free_when_under_cap(tmp_path: Path) -> None:
    audit = tmp_path / "paper.jsonl"
    pending = tmp_path / "pending.jsonl"
    _write_audit_open_positions(audit, ["BTC/USDT"])
    rep = build_capacity_report(max_open_positions=6, audit_path=audit, pending_path=pending)
    assert rep.open_count == 1
    assert rep.slots_free == 5
    assert rep.book_full is False


def test_stale_pending_flagged_not_deleted(tmp_path: Path) -> None:
    audit = tmp_path / "paper.jsonl"
    pending = tmp_path / "pending.jsonl"
    _write_audit_open_positions(audit, [])
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    # one fresh pending, one 30h-old pending
    rows = [
        {
            "event": "operator_signal_bridge",
            "envelope_id": "ENV-FRESH",
            "stage": "pending",
            "symbol": "AAA/USDT",
            "origin_envelope_timestamp": (now - timedelta(hours=1)).isoformat(),
            "timestamp_utc": now.isoformat(),
        },
        {
            "event": "operator_signal_bridge",
            "envelope_id": "ENV-STALE",
            "stage": "pending",
            "symbol": "BBB/USDT",
            "origin_envelope_timestamp": (now - timedelta(hours=30)).isoformat(),
            "timestamp_utc": now.isoformat(),
        },
    ]
    pending.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    rep = build_capacity_report(
        max_open_positions=6, audit_path=audit, pending_path=pending, ttl_hours=24, now=now
    )
    assert rep.pending_count == 2
    assert len(rep.stale_pending) == 1
    assert rep.stale_pending[0].envelope_id == "ENV-STALE"
    assert rep.stale_pending[0].age_hours == pytest.approx(30.0, abs=0.1)


def test_terminal_stage_not_counted_as_pending(tmp_path: Path) -> None:
    audit = tmp_path / "paper.jsonl"
    pending = tmp_path / "pending.jsonl"
    _write_audit_open_positions(audit, [])
    rows = [
        {"envelope_id": "ENV-1", "stage": "pending", "timestamp_utc": "2026-06-01T10:00:00+00:00"},
        {"envelope_id": "ENV-1", "stage": "filled", "timestamp_utc": "2026-06-01T11:00:00+00:00"},
    ]
    pending.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    rep = build_capacity_report(max_open_positions=6, audit_path=audit, pending_path=pending)
    # ENV-1's last stage is 'filled' (terminal) -> not pending
    assert rep.pending_count == 0
