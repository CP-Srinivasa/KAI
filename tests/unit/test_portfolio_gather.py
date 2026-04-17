"""Tests for _gather_market_snapshots: parallelism, semaphore, timeout,
CancelledError labelling, and empty-input edge case.

Covers the P1-#4 (CancelledError-Label) and the P0 parallel-fan-out hardening
of build_portfolio_snapshot.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.execution.portfolio_read import _gather_market_snapshots
from app.market_data.base import MarketDataSnapshot


def _ok_snapshot(symbol: str, *, price: float = 100.0) -> MarketDataSnapshot:
    return MarketDataSnapshot(
        symbol=symbol,
        provider="coingecko",
        retrieved_at_utc="2026-04-17T00:00:00+00:00",
        source_timestamp_utc="2026-04-17T00:00:00+00:00",
        price=price,
        is_stale=False,
        freshness_seconds=5.0,
        available=True,
        error=None,
    )


@pytest.mark.asyncio
async def test_empty_symbols_returns_empty_dict() -> None:
    result = await _gather_market_snapshots(
        symbols=[],
        provider="coingecko",
        freshness_threshold_seconds=120.0,
        timeout_seconds=10,
    )
    assert result == {}


@pytest.mark.asyncio
async def test_parallel_fetch_returns_all_symbols() -> None:
    async def fake(symbol: str, **_: object) -> MarketDataSnapshot:
        await asyncio.sleep(0)  # yield to event loop
        return _ok_snapshot(symbol)

    with patch(
        "app.execution.portfolio_read.get_market_data_snapshot", side_effect=fake
    ):
        result = await _gather_market_snapshots(
            symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            provider="coingecko",
            freshness_threshold_seconds=120.0,
            timeout_seconds=10,
        )

    assert set(result.keys()) == {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
    for snap in result.values():
        assert snap.available is True
        assert snap.price == 100.0


@pytest.mark.asyncio
async def test_semaphore_caps_concurrency_to_two() -> None:
    """Only 2 fetches may be in-flight at any moment, regardless of symbol count."""
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def fake(symbol: str, **_: object) -> MarketDataSnapshot:
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.05)
        async with lock:
            active -= 1
        return _ok_snapshot(symbol)

    with patch(
        "app.execution.portfolio_read.get_market_data_snapshot", side_effect=fake
    ):
        await _gather_market_snapshots(
            symbols=["A", "B", "C", "D", "E"],
            provider="coingecko",
            freshness_threshold_seconds=120.0,
            timeout_seconds=10,
        )

    assert peak == 2, f"Semaphore should cap at 2 concurrent calls, saw {peak}"


@pytest.mark.asyncio
async def test_overall_timeout_labels_unresolved_as_timeout() -> None:
    """When the global timeout fires, every unresolved symbol gets
    error='snapshot_gather_timeout', NOT 'snapshot_gather_failed:...'."""

    async def fake_slow(symbol: str, **_: object) -> MarketDataSnapshot:
        await asyncio.sleep(10)  # exceeds overall timeout
        return _ok_snapshot(symbol)

    with patch(
        "app.execution.portfolio_read.get_market_data_snapshot", side_effect=fake_slow
    ), patch(
        "app.execution.portfolio_read._PORTFOLIO_MARK_TO_MARKET_OVERALL_TIMEOUT_SECONDS",
        0.1,
    ):
        result = await _gather_market_snapshots(
            symbols=["BTC/USDT", "ETH/USDT"],
            provider="coingecko",
            freshness_threshold_seconds=120.0,
            timeout_seconds=10,
        )

    for sym in ("BTC/USDT", "ETH/USDT"):
        assert result[sym].available is False
        assert result[sym].error == "snapshot_gather_timeout"
        assert result[sym].is_stale is True


@pytest.mark.asyncio
async def test_per_symbol_exception_labeled_with_class_name() -> None:
    """If a single fetch raises outside the timeout path, the error tag
    includes the exception class so the operator can diagnose."""

    async def fake(symbol: str, **_: object) -> MarketDataSnapshot:
        if symbol == "BROKEN":
            raise ValueError("bad symbol")
        return _ok_snapshot(symbol)

    with patch(
        "app.execution.portfolio_read.get_market_data_snapshot", side_effect=fake
    ):
        result = await _gather_market_snapshots(
            symbols=["BTC/USDT", "BROKEN"],
            provider="coingecko",
            freshness_threshold_seconds=120.0,
            timeout_seconds=10,
        )

    assert result["BTC/USDT"].available is True
    assert result["BROKEN"].available is False
    assert result["BROKEN"].error == "snapshot_gather_failed:ValueError"
