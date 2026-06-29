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
from app.observability.symbol_eligibility_ledger import read_latest_eligibility
from app.trading.symbol_eligibility import EligibilityVerdict


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
    elig_ledger = tmp_path / "elig.jsonl"
    rc = asyncio.run(_run(_FakeSource(["BTC/USDT", "ETH/USDT"]), ledger, elig_ledger))
    assert rc == 0
    latest = read_latest(ledger)
    assert latest is not None
    assert latest["count"] == 2


def test_refresh_keeps_last_snapshot_when_source_dead(tmp_path: Path) -> None:
    ledger = tmp_path / "u.jsonl"
    elig_ledger = tmp_path / "elig.jsonl"
    append_snapshot(
        ledger,
        [RankedSymbol("BTC/USDT", 0.9, 0.9, 0.9, 1, {})],
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    rc = asyncio.run(_run(_DeadSource(), ledger, elig_ledger))
    assert rc == 0
    latest = read_latest(ledger)
    # The dead-source build returned [] and MUST NOT overwrite the good snapshot.
    assert latest is not None
    assert latest["count"] == 1


def test_refresh_writes_eligibility_without_filtering(tmp_path, monkeypatch) -> None:
    import scripts.momentum_universe_refresh as refresh

    ranked = [
        RankedSymbol("BTC/USDT", 0.9, 0.9, 0.9, 1),
        RankedSymbol("SLX/USDT", 0.8, 0.8, 0.8, 2),
    ]

    async def _fake_build_universe(*a, **k):
        return ranked

    async def _fake_build_eligibility(source, symbols, **k):
        return [
            EligibilityVerdict("BTC/USDT", True, []),
            EligibilityVerdict("SLX/USDT", False, ["no_canonical_venue_data"]),
        ]

    monkeypatch.setattr(refresh, "build_universe", _fake_build_universe)
    monkeypatch.setattr(refresh, "build_eligibility", _fake_build_eligibility)

    uni_ledger = tmp_path / "uni.jsonl"
    elig_ledger = tmp_path / "elig.jsonl"
    rc = asyncio.run(refresh._run(object(), uni_ledger, elig_ledger))
    assert rc == 0

    # Universe ledger keeps BOTH symbols (no filtering) but carries the flag.
    import json

    uni = json.loads(uni_ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert uni["count"] == 2
    rows = {r["symbol"]: r for r in uni["universe"]}
    assert rows["SLX/USDT"]["eligible"] is False

    elig = read_latest_eligibility(elig_ledger)
    assert elig is not None and elig["eligible_count"] == 1
