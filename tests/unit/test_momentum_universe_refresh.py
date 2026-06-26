"""Tests for scripts/momentum_universe_refresh — the oneshot the timer fires.

The orchestration (build_universe, append_snapshot) is covered elsewhere; here
we lock the refresher's own contract: it writes a snapshot on success, and on a
dead source it KEEPS the last good snapshot (never overwrites with an empty one).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from scripts.momentum_universe_refresh import _run

from app.market_data.models import OHLCV
from app.observability.momentum_universe import RankedSymbol
from app.observability.momentum_universe_ledger import append_snapshot, read_latest


class _FakeSource:
    def __init__(self, symbols: list[str]) -> None:
        self._symbols = symbols

    async def top_symbols_by_volume(self, limit: int = 50) -> list[str]:
        return list(self._symbols[:limit])

    async def get_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 100) -> list[OHLCV]:
        return [
            OHLCV(
                symbol=symbol,
                timestamp_utc=f"2026-06-{i + 1:02d}T00:00:00Z",
                timeframe="1d",
                open=100.0 + i,
                high=100.0 + i,
                low=100.0 + i,
                close=100.0 + i,
                volume=1000.0,
            )
            for i in range(8)
        ]


class _DeadSource:
    async def top_symbols_by_volume(self, limit: int = 50) -> list[str]:
        raise RuntimeError("exchange down")

    async def get_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 100) -> list[OHLCV]:
        return []


def test_refresh_writes_snapshot(tmp_path: Path) -> None:
    ledger = tmp_path / "u.jsonl"
    rc = asyncio.run(_run(_FakeSource(["BTC/USDT", "ETH/USDT"]), ledger))
    assert rc == 0
    latest = read_latest(ledger)
    assert latest is not None
    assert latest["count"] == 2


def test_refresh_keeps_last_snapshot_when_source_dead(tmp_path: Path) -> None:
    ledger = tmp_path / "u.jsonl"
    append_snapshot(
        ledger,
        [RankedSymbol("BTC/USDT", 0.9, 0.9, 0.9, 1, {})],
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    rc = asyncio.run(_run(_DeadSource(), ledger))
    assert rc == 0
    latest = read_latest(ledger)
    # The dead-source build returned [] and MUST NOT overwrite the good snapshot.
    assert latest is not None
    assert latest["count"] == 1
