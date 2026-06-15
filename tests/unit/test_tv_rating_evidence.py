"""WP-H (2026-06-15): TradingView Recommend.All evidence in the technical feed.

Recorded for measurement only — never mutates signal_confidence (keeps the WP-D
calibration pure). ``tv_contradiction`` flags signals TV strongly opposes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.market_data.models import OHLCV
from app.observability.technical_screener_feed import run_technical_screen
from app.signals.technical_screener import DEFAULT_LOOKBACK

_BASE = 100.0


def _series(total_pct: float) -> list[OHLCV]:
    closes = [_BASE] * DEFAULT_LOOKBACK + [_BASE * (1 + total_pct)]
    return [
        OHLCV(
            symbol="X",
            timestamp_utc=f"{i:04d}",
            timeframe="1h",
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1.0,
        )
        for i, c in enumerate(closes)
    ]


class _FakeAdapter:
    def __init__(self, series_by_symbol: dict[str, list[OHLCV]]) -> None:
        self._series = series_by_symbol

    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list[OHLCV]:
        return self._series.get(symbol, [])


def _rows(ledger: Path) -> list[dict]:
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_records_rating_and_contradiction_without_mutating_strength(tmp_path: Path) -> None:
    adapter = _FakeAdapter({"BTC/USDT": _series(0.01), "SOL/USDT": _series(0.06)})  # SOL bullish
    ledger = tmp_path / "s.jsonl"
    summary = await run_technical_screen(
        adapter,
        symbols=["BTC/USDT", "SOL/USDT"],
        min_strength=0.1,
        tv_ratings={"SOL/USDT": -0.6},  # TV strong-sell vs a bullish signal → contradiction
        ledger_path=ledger,
        now_utc="2026-06-15T00:00:00+00:00",
    )
    assert int(summary["tv_contradictions"]) == 1  # type: ignore[call-overload]
    sol = next(r for r in _rows(ledger) if r["symbol"] == "SOL/USDT")
    assert sol["tv_rating"] == -0.6
    assert sol["tv_contradiction"] is True
    # Strength is recorded from the screener, NOT dampened by the TV rating.
    assert sol["signal_confidence"] > 0


@pytest.mark.asyncio
async def test_agreeing_rating_is_not_a_contradiction(tmp_path: Path) -> None:
    adapter = _FakeAdapter({"BTC/USDT": _series(0.01), "SOL/USDT": _series(0.06)})
    ledger = tmp_path / "s.jsonl"
    summary = await run_technical_screen(
        adapter,
        symbols=["BTC/USDT", "SOL/USDT"],
        min_strength=0.1,
        tv_ratings={"SOL/USDT": 0.6},  # TV strong-buy agrees with bullish
        ledger_path=ledger,
        now_utc="2026-06-15T00:00:00+00:00",
    )
    assert int(summary["tv_contradictions"]) == 0  # type: ignore[call-overload]
    sol = next(r for r in _rows(ledger) if r["symbol"] == "SOL/USDT")
    assert sol["tv_rating"] == 0.6
    assert sol["tv_contradiction"] is False


@pytest.mark.asyncio
async def test_bearish_signal_contradicted_by_strong_buy(tmp_path: Path) -> None:
    adapter = _FakeAdapter({"BTC/USDT": _series(0.05), "ADA/USDT": _series(-0.05)})  # ADA bearish
    ledger = tmp_path / "s.jsonl"
    await run_technical_screen(
        adapter,
        symbols=["BTC/USDT", "ADA/USDT"],
        min_strength=0.1,
        tv_ratings={"ADA/USDT": 0.7},  # TV strong-buy vs a bearish signal → contradiction
        ledger_path=ledger,
        now_utc="2026-06-15T00:00:00+00:00",
    )
    ada = next(r for r in _rows(ledger) if r["symbol"] == "ADA/USDT")
    assert ada["side"] == "short"
    assert ada["tv_contradiction"] is True


@pytest.mark.asyncio
async def test_no_tv_ratings_leaves_fields_none(tmp_path: Path) -> None:
    adapter = _FakeAdapter({"BTC/USDT": _series(0.01), "SOL/USDT": _series(0.06)})
    ledger = tmp_path / "s.jsonl"
    summary = await run_technical_screen(
        adapter,
        symbols=["BTC/USDT", "SOL/USDT"],
        min_strength=0.1,
        tv_ratings=None,  # datafeed off / unavailable
        ledger_path=ledger,
        now_utc="2026-06-15T00:00:00+00:00",
    )
    assert int(summary["tv_contradictions"]) == 0  # type: ignore[call-overload]
    sol = next(r for r in _rows(ledger) if r["symbol"] == "SOL/USDT")
    assert sol["tv_rating"] is None
    assert sol["tv_contradiction"] is None
