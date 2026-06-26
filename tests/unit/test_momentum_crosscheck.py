"""Tests for momentum_crosscheck (G4) — own momentum rank vs own-TA rating.

Pure ``build_crosscheck_rows`` flags agreement/divergence between the momentum
percentile (best-performer) and the TA rating (the ToS-compliant TradingView-
rating substitute). The async builder fetches OHLCV per universe symbol via an
injected source. Informational only — zero sizing impact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.market_data.models import OHLCV
from app.market_data.ta_rating import TaRating
from app.observability.momentum_crosscheck import (
    append_crosscheck,
    build_crosscheck,
    build_crosscheck_rows,
    read_latest_crosscheck,
)


def _rating(score: float, label: str, trend: str) -> TaRating:
    return TaRating(label=label, score=score, rsi=55.0, sma_short=1.0, sma_long=1.0, trend=trend)


class TestBuildRows:
    def test_agreement_and_divergence(self) -> None:
        universe = [
            {"symbol": "BTC/USDT", "rank": 1, "momentum_score": 0.9},  # bullish momentum
            {"symbol": "DOGE/USDT", "rank": 2, "momentum_score": 0.8},  # bullish momentum
            {"symbol": "XRP/USDT", "rank": 3, "momentum_score": 0.2},  # weak momentum
        ]
        ratings = {
            "BTC/USDT": _rating(0.7, "strong_buy", "up"),  # TA bullish → agree
            "DOGE/USDT": _rating(-0.6, "strong_sell", "down"),  # TA bearish → divergence
            # XRP: no rating (insufficient history)
        }
        rows = build_crosscheck_rows(universe, ratings)
        by = {r["symbol"]: r for r in rows}
        assert by["BTC/USDT"]["agreement"] == "agree_bullish"
        assert by["DOGE/USDT"]["agreement"] == "divergence"
        assert by["XRP/USDT"]["ta_label"] == "unavailable"
        assert by["BTC/USDT"]["ta_score"] == 0.7

    def test_row_shape(self) -> None:
        rows = build_crosscheck_rows(
            [{"symbol": "BTC/USDT", "rank": 1, "momentum_score": 0.9}],
            {"BTC/USDT": _rating(0.3, "buy", "up")},
        )
        r = rows[0]
        assert set(r) >= {
            "symbol",
            "rank",
            "momentum_score",
            "ta_label",
            "ta_score",
            "ta_trend",
            "agreement",
        }


class _FakeSource:
    def __init__(self, ohlcv: dict[str, list[OHLCV]]) -> None:
        self._ohlcv = ohlcv

    async def get_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 100) -> list[OHLCV]:
        return self._ohlcv.get(symbol, [])


def _candles(closes: list[float]) -> list[OHLCV]:
    return [
        OHLCV(
            symbol="X",
            timestamp_utc=f"2026-06-{i + 1:02d}T00:00:00Z",
            timeframe="1d",
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1.0,
        )
        for i, c in enumerate(closes)
    ]


class TestBuilderAndLedger:
    async def test_build_crosscheck_from_universe(self, tmp_path: Path) -> None:
        from app.observability.momentum_universe import RankedSymbol
        from app.observability.momentum_universe_ledger import append_snapshot

        ledger = tmp_path / "u.jsonl"
        append_snapshot(
            ledger,
            [RankedSymbol("BTC/USDT", 0.9, 0.9, 0.9, 1, {})],
            now=datetime(2026, 6, 26, tzinfo=UTC),
        )
        src = _FakeSource({"BTC/USDT": _candles([float(100 + i) for i in range(40)])})  # uptrend
        rows = await build_crosscheck(src, ledger_path=ledger, top_n=10)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BTC/USDT"
        assert rows[0]["ta_trend"] == "up"

    async def test_build_empty_universe(self, tmp_path: Path) -> None:
        rows = await build_crosscheck(
            _FakeSource({}), ledger_path=tmp_path / "none.jsonl", top_n=10
        )
        assert rows == []

    def test_ledger_round_trip(self, tmp_path: Path) -> None:
        p = tmp_path / "cc.jsonl"
        rows = [{"symbol": "BTC/USDT", "rank": 1, "agreement": "agree_bullish"}]
        append_crosscheck(p, rows, now=datetime(2026, 6, 26, tzinfo=UTC))
        latest = read_latest_crosscheck(p)
        assert latest is not None
        assert latest["count"] == 1
        assert latest["rows"][0]["symbol"] == "BTC/USDT"

    def test_read_latest_missing(self, tmp_path: Path) -> None:
        assert read_latest_crosscheck(tmp_path / "nope.jsonl") is None
