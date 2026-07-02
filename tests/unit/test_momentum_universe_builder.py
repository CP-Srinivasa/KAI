"""Tests for momentum_universe_builder — OHLCV → candidate + fail-soft orchestration."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.market_data.models import OHLCV
from app.observability.momentum_universe import RankedSymbol
from app.observability.momentum_universe_builder import (
    build_universe,
    candidate_from_ohlcv,
)


def _candles(closes: Sequence[float], volume: float = 1000.0) -> list[OHLCV]:
    return [
        OHLCV(
            symbol="X",
            timestamp_utc=f"2026-06-{i + 1:02d}T00:00:00Z",
            timeframe="1d",
            open=c,
            high=c,
            low=c,
            close=c,
            volume=volume,
        )
        for i, c in enumerate(closes)
    ]


class TestCandidateFromOhlcv:
    def test_too_few_candles_returns_none(self) -> None:
        assert candidate_from_ohlcv("A/USDT", _candles([100.0])) is None
        assert candidate_from_ohlcv("A/USDT", []) is None

    def test_invalid_last_close_returns_none(self) -> None:
        assert candidate_from_ohlcv("A/USDT", _candles([100.0, 0.0])) is None
        assert candidate_from_ohlcv("A/USDT", _candles([100.0, float("nan")])) is None

    def test_two_candles_only_24h(self) -> None:
        c = candidate_from_ohlcv("A/USDT", _candles([100.0, 110.0]))
        assert c is not None
        assert set(c.window_returns_pct) == {"24h"}
        assert c.window_returns_pct["24h"] == pytest.approx(10.0)

    def test_eight_candles_24h_and_7d(self) -> None:
        closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 120.0]
        c = candidate_from_ohlcv("A/USDT", _candles(closes))
        assert c is not None
        assert set(c.window_returns_pct) == {"24h", "7d"}
        assert c.window_returns_pct["7d"] == pytest.approx(20.0)  # 120/100 - 1

    def test_31_candles_all_windows(self) -> None:
        closes = [float(100 + i) for i in range(31)]  # 100 .. 130
        c = candidate_from_ohlcv("A/USDT", _candles(closes))
        assert c is not None
        assert set(c.window_returns_pct) == {"24h", "7d", "30d"}
        assert c.window_returns_pct["30d"] == pytest.approx(30.0)  # 130/100 - 1

    def test_turnover_proxy_is_volume_times_close(self) -> None:
        c = candidate_from_ohlcv("A/USDT", _candles([100.0, 200.0], volume=5.0))
        assert c is not None
        assert c.turnover_24h == pytest.approx(200.0 * 5.0)

    def test_window_with_bad_base_is_skipped(self) -> None:
        # 24h base = closes[-2] = 0 → 24h skipped; only 2 candles → no windows → None.
        assert candidate_from_ohlcv("A/USDT", _candles([0.0, 100.0])) is None


class FakeSource:
    def __init__(
        self,
        symbols: list[str],
        ohlcv: dict[str, list[OHLCV]],
        *,
        raise_top: bool = False,
        raise_for: set[str] | None = None,
    ) -> None:
        self._symbols = symbols
        self._ohlcv = ohlcv
        self._raise_top = raise_top
        self._raise_for = raise_for or set()

    async def top_symbols_by_volume(self, limit: int = 50) -> list[str]:
        if self._raise_top:
            raise RuntimeError("boom")
        return list(self._symbols[:limit])

    async def get_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 100) -> list[OHLCV]:
        if symbol in self._raise_for:
            raise RuntimeError("boom")
        return self._ohlcv.get(symbol, [])


class TestBuildUniverse:
    async def test_happy_path_ranks(self) -> None:
        src = FakeSource(
            ["BEST/USDT", "WORST/USDT"],
            {
                "BEST/USDT": _candles([100, 101, 102, 103, 104, 105, 106, 140], volume=10.0),
                "WORST/USDT": _candles([100, 99, 98, 97, 96, 95, 94, 80], volume=1.0),
            },
        )
        ranked = await build_universe(src, top_n=2)
        assert [r.symbol for r in ranked] == ["BEST/USDT", "WORST/USDT"]
        assert all(isinstance(r, RankedSymbol) for r in ranked)

    async def test_top_symbols_failure_yields_empty(self) -> None:
        assert await build_universe(FakeSource([], {}, raise_top=True), top_n=5) == []

    async def test_symbol_ohlcv_failure_is_skipped(self) -> None:
        src = FakeSource(
            ["A/USDT", "B/USDT"],
            {"B/USDT": _candles([100, 110])},
            raise_for={"A/USDT"},
        )
        ranked = await build_universe(src, top_n=5)
        assert [r.symbol for r in ranked] == ["B/USDT"]

    async def test_insufficient_candles_skipped(self) -> None:
        src = FakeSource(
            ["A/USDT", "B/USDT"],
            {"A/USDT": _candles([100.0]), "B/USDT": _candles([100, 110])},
        )
        ranked = await build_universe(src, top_n=5)
        assert [r.symbol for r in ranked] == ["B/USDT"]

    async def test_empty_symbols_yields_empty(self) -> None:
        assert await build_universe(FakeSource([], {}), top_n=5) == []
