"""Tests für den fail-soften Eligibility-Fetcher + Orchestrator."""

from __future__ import annotations

import pytest

from app.market_data.models import OHLCV, Ticker
from app.trading.symbol_eligibility_fetch import build_eligibility, fetch_metrics


def _ticker(symbol: str, last: float, volume: float) -> Ticker:
    return Ticker(
        symbol=symbol,
        timestamp_utc="2026-06-29T00:00:00Z",
        bid=last,
        ask=last,
        last=last,
        volume_24h=volume,
        change_pct_24h=0.0,
    )


def _candle(close: float) -> OHLCV:
    return OHLCV(
        symbol="X",
        timestamp_utc="2026-06-01T00:00:00Z",
        timeframe="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
    )


class FakeSource:
    """Protocol-compatible fake. ``missing`` symbols mimic off-Binance (no data)."""

    def __init__(self, *, missing: set[str] | None = None, history: int = 365) -> None:
        self._missing = missing or set()
        self._history = history

    async def get_ticker(self, symbol: str) -> Ticker | None:
        if symbol in self._missing:
            return None
        return _ticker(symbol, last=100.0, volume=1_000_000.0)  # 100 * 1e6 = 1e8 turnover

    async def get_ohlcv(self, symbol, timeframe="1h", limit=100):  # type: ignore[no-untyped-def]
        if symbol in self._missing:
            return []
        return [_candle(100.0) for _ in range(self._history)]


@pytest.mark.asyncio
async def test_fetch_metrics_computes_turnover_and_history() -> None:
    m = await fetch_metrics(FakeSource(), "BTC/USDT", min_history_days=30)
    assert m.turnover_24h_usd == pytest.approx(1e8)  # 100 * 1_000_000
    assert m.history_days == 365
    assert m.base == "BTC"
    assert m.quote == "USDT"


@pytest.mark.asyncio
async def test_fetch_metrics_offvenue_yields_none() -> None:
    m = await fetch_metrics(FakeSource(missing={"SLX/USDT"}), "SLX/USDT", min_history_days=30)
    assert m.turnover_24h_usd is None
    assert m.history_days is None


@pytest.mark.asyncio
async def test_build_eligibility_flags_offvenue_and_keeps_good() -> None:
    src = FakeSource(missing={"SLX/USDT"})
    verdicts = await build_eligibility(src, ["BTC/USDT", "SLX/USDT"])
    by = {v.symbol: v for v in verdicts}
    assert by["BTC/USDT"].eligible is True
    assert by["SLX/USDT"].eligible is False
    assert by["SLX/USDT"].reasons == ["no_canonical_venue_data"]


@pytest.mark.asyncio
async def test_build_eligibility_flags_duplicate() -> None:
    verdicts = await build_eligibility(FakeSource(), ["BTC/USDT", "BTC/USDC"])
    by = {v.symbol: v for v in verdicts}
    assert by["BTC/USDT"].eligible is True
    assert by["BTC/USDC"].eligible is False
    assert "duplicate_of:BTC/USDT" in by["BTC/USDC"].reasons


@pytest.mark.asyncio
async def test_build_eligibility_short_history_ineligible() -> None:
    verdicts = await build_eligibility(FakeSource(history=10), ["ETH/USDT"], min_history_days=30)
    assert verdicts[0].eligible is False
    assert "below_min_history" in verdicts[0].reasons


class FakeRaisingSource:
    async def get_ticker(self, symbol):
        raise RuntimeError("venue down")

    async def get_ohlcv(self, symbol, timeframe="1d", limit=30):
        raise RuntimeError("venue down")


@pytest.mark.asyncio
async def test_fetch_metrics_exception_yields_none() -> None:
    m = await fetch_metrics(FakeRaisingSource(), "BTC/USDT", min_history_days=30)
    assert m.turnover_24h_usd is None
    assert m.history_days is None
