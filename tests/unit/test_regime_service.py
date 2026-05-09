"""Tests for RegimeService — orchestration of fetch → classify → persist."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.market_data.models import OHLCV
from app.regime.models import RegimeClass
from app.regime.service import RegimeService
from app.regime.storage import load_regime_snapshots


class _FakeProvider:
    """Mock market_data with a pre-canned OHLCV list."""

    def __init__(self, candles: list[OHLCV]) -> None:
        self.candles = candles
        self.calls: list[tuple[str, str, int]] = []

    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]:
        self.calls.append((symbol, timeframe, limit))
        return self.candles


class _RaisingProvider:
    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[OHLCV]:
        raise RuntimeError("boom")


def _candle(i: int, base: float = 100.0, range_: float = 1.0) -> OHLCV:
    return OHLCV(
        symbol="BTC",
        timestamp_utc=f"2026-05-{(i // 24) + 1:02d}T{i % 24:02d}:00:00Z",
        timeframe="1h",
        open=base + i,
        high=base + i + range_,
        low=base + i,
        close=base + i + range_ / 2.0,
        volume=10.0,
    )


def _uptrend_candles(n: int = 60) -> list[OHLCV]:
    return [_candle(i, base=100.0, range_=1.0) for i in range(n)]


def _flat_candles(n: int = 60) -> list[OHLCV]:
    return [
        OHLCV(
            symbol="BTC",
            timestamp_utc=f"2026-05-{(i // 24) + 1:02d}T{i % 24:02d}:00:00Z",
            timeframe="1h",
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.0 + (0.1 if i % 2 == 0 else -0.1),
            volume=10.0,
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_service_classifies_uptrend_to_trend_up_and_persists(tmp_path: Path) -> None:
    provider = _FakeProvider(_uptrend_candles(60))
    svc = RegimeService(market_data=provider, storage_dir=tmp_path)
    snap = await svc.classify_once("BTC")
    assert snap.regime == RegimeClass.TREND_UP
    persisted = load_regime_snapshots("BTC", tmp_path)
    assert len(persisted) == 1
    assert persisted[0].regime == RegimeClass.TREND_UP
    assert persisted[0].adx is not None
    assert persisted[0].plus_di is not None and persisted[0].plus_di > 0


@pytest.mark.asyncio
async def test_service_classifies_flat_market_to_chop(tmp_path: Path) -> None:
    provider = _FakeProvider(_flat_candles(60))
    svc = RegimeService(market_data=provider, storage_dir=tmp_path)
    snap = await svc.classify_once("BTC")
    assert snap.regime in (RegimeClass.CHOP_QUIET, RegimeClass.CHOP_VOLATILE)


@pytest.mark.asyncio
async def test_service_uses_correct_ohlcv_request(tmp_path: Path) -> None:
    provider = _FakeProvider(_uptrend_candles(60))
    svc = RegimeService(
        market_data=provider, storage_dir=tmp_path, ohlcv_limit=150, timeframe="4h"
    )
    await svc.classify_once("ETH")
    assert provider.calls == [("ETH", "4h", 150)]


@pytest.mark.asyncio
async def test_service_returns_unknown_on_provider_exception(tmp_path: Path) -> None:
    svc = RegimeService(market_data=_RaisingProvider(), storage_dir=tmp_path)
    snap = await svc.classify_once("BTC")
    assert snap.regime == RegimeClass.UNKNOWN
    assert snap.confidence == 0.0
    persisted = load_regime_snapshots("BTC", tmp_path)
    assert len(persisted) == 1
    assert persisted[0].regime == RegimeClass.UNKNOWN


@pytest.mark.asyncio
async def test_service_returns_unknown_on_insufficient_data(tmp_path: Path) -> None:
    # Need at least 2*14 = 28 bars; provide only 10.
    provider = _FakeProvider(_uptrend_candles(10))
    svc = RegimeService(market_data=provider, storage_dir=tmp_path)
    snap = await svc.classify_once("BTC")
    assert snap.regime == RegimeClass.UNKNOWN
    persisted = load_regime_snapshots("BTC", tmp_path)
    assert len(persisted) == 1


@pytest.mark.asyncio
async def test_service_chains_hysteresis_across_calls(tmp_path: Path) -> None:
    # Call 1 with uptrend data → commit TREND_UP.
    up_provider = _FakeProvider(_uptrend_candles(60))
    svc = RegimeService(market_data=up_provider, storage_dir=tmp_path)
    snap1 = await svc.classify_once("BTC")
    assert snap1.regime == RegimeClass.TREND_UP

    # Call 2 with flat data → hysteresis must mark pending, not commit yet.
    flat_provider = _FakeProvider(_flat_candles(60))
    svc2 = RegimeService(market_data=flat_provider, storage_dir=tmp_path)
    snap2 = await svc2.classify_once("BTC")
    assert snap2.regime == RegimeClass.TREND_UP, "hysteresis must hold previous"
    assert snap2.pending_regime in (RegimeClass.CHOP_QUIET, RegimeClass.CHOP_VOLATILE)
    assert snap2.pending_consecutive == 1


@pytest.mark.asyncio
async def test_service_separate_assets_persist_separately(tmp_path: Path) -> None:
    up_provider = _FakeProvider(_uptrend_candles(60))
    flat_provider = _FakeProvider(_flat_candles(60))
    btc_svc = RegimeService(market_data=up_provider, storage_dir=tmp_path)
    eth_svc = RegimeService(market_data=flat_provider, storage_dir=tmp_path)
    await btc_svc.classify_once("BTC")
    await eth_svc.classify_once("ETH")
    btc = load_regime_snapshots("BTC", tmp_path)
    eth = load_regime_snapshots("ETH", tmp_path)
    assert len(btc) == 1 and btc[0].regime == RegimeClass.TREND_UP
    assert len(eth) == 1
