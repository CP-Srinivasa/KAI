"""Pure-function tests for the first technical_paper-fill notifier.

Mirrors the script's --self-test so the detection contract is CI-covered, not
only Pi-local: only paper_trade_label rows with feed_source="technical_paper"
count, the EARLIEST timestamp is the "first" fill, corrupt rows are skipped.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import technical_paper_first_fill_notifier as noti  # noqa: E402


def _label(feed: str, ts: str, symbol: str, event: str = "paper_trade_label") -> str:
    return json.dumps(
        {
            "event_type": event,
            "feed_source": feed,
            "timestamp_utc": ts,
            "symbol": symbol,
            "direction": "long",
        }
    )


def test_no_fills_returns_zero_none() -> None:
    assert noti.find_technical_paper_fills([]) == (0, None)
    # autonomous_loop labels + a non-label event must NOT count
    rows = [
        _label("autonomous_loop", "2026-06-23T10:00:00Z", "X/USDT"),
        _label("technical_paper", "2026-06-23T10:00:00Z", "Y/USDT", event="order_filled"),
    ]
    assert noti.find_technical_paper_fills(rows) == (0, None)


def test_picks_earliest_technical_paper_label() -> None:
    rows = [
        _label("technical_paper", "2026-06-23T12:00:00Z", "SOL/USDT"),
        _label("autonomous_loop", "2026-06-23T09:00:00Z", "BTC/USDT"),
        _label("technical_paper", "2026-06-23T11:00:00Z", "ETH/USDT"),
        "{corrupt",
    ]
    count, first = noti.find_technical_paper_fills(rows)
    assert count == 2
    assert first is not None and first["symbol"] == "ETH/USDT"  # earliest ts


def test_compose_message_carries_first_fill_and_count() -> None:
    _, first = noti.find_technical_paper_fills(
        [_label("technical_paper", "2026-06-23T11:00:00Z", "ETH/USDT")]
    )
    assert first is not None
    msg = noti.compose_message(3, first)
    assert "ETH/USDT" in msg
    assert "technical_paper-Fills bisher: 3" in msg
    assert "2026-06-23T11:00:00Z" in msg
