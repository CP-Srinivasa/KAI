"""Paper-entry accounting — shared opening-fill truth (Sprint S7, D-234).

Extracted from trading_loop; these tests pin the module contract directly so
the daily cap (loop) and route limiter (entry_policy) consume ONE verified
definition. The loop-level behaviour stays covered by
test_trading_loop_paper_learning (via re-export).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.execution.paper_entry_accounting import (
    count_paper_entries_today,
    effective_daily_paper_cap,
    is_opening_fill,
)


def test_opening_fill_matrix() -> None:
    base = {"event_type": "order_filled"}
    assert is_opening_fill({**base, "side": "buy", "position_side": "long"}) is True
    assert is_opening_fill({**base, "side": "sell", "position_side": "short"}) is True
    # exits are never entries
    assert is_opening_fill({**base, "side": "sell", "position_side": "long"}) is False
    assert is_opening_fill({**base, "side": "buy", "position_side": "short"}) is False
    # legacy rows without position_side default long
    assert is_opening_fill({**base, "side": "buy"}) is True
    assert is_opening_fill({**base, "side": "sell"}) is False
    # non-fill events never count
    assert is_opening_fill({"event_type": "position_closed", "side": "buy"}) is False


def test_count_today_only_and_failopen(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    now = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    rows = [
        {
            "event_type": "order_filled",
            "side": "buy",
            "position_side": "long",
            "timestamp_utc": "2026-06-11T01:00:00+00:00",
        },
        {
            "event_type": "order_filled",
            "side": "sell",
            "position_side": "short",
            "timestamp_utc": "2026-06-11T02:00:00+00:00",
        },
        {
            "event_type": "order_filled",
            "side": "buy",
            "position_side": "long",
            "timestamp_utc": "2026-06-10T23:59:00+00:00",
        },  # gestern
        {
            "event_type": "order_filled",
            "side": "sell",
            "position_side": "long",
            "timestamp_utc": "2026-06-11T03:00:00+00:00",
        },  # exit
    ]
    audit.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert count_paper_entries_today(audit, now=now) == 2
    assert count_paper_entries_today(tmp_path / "missing.jsonl", now=now) == 0


def test_effective_cap_strictest_positive_wins() -> None:
    assert effective_daily_paper_cap(0, 0) == 0  # beide unlimited
    assert effective_daily_paper_cap(5, 0) == 5
    assert effective_daily_paper_cap(0, 3) == 3
    assert effective_daily_paper_cap(5, 3) == 3
    assert effective_daily_paper_cap(2, 7) == 2


def test_loop_reexport_stays_importable() -> None:
    """Bestehende Importe (Tests/Call-Sites) gehen weiter über den Loop."""
    from app.orchestrator.trading_loop import count_paper_entries_today as via_loop

    assert via_loop is count_paper_entries_today
