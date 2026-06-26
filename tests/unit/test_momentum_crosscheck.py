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


class _FundingSource(_FakeSource):
    """OHLCV fake that also exposes get_funding_rate (like the exchange adapters)."""

    def __init__(self, ohlcv: dict[str, list[OHLCV]], funding: dict[str, float]) -> None:
        super().__init__(ohlcv)
        self._funding = funding

    async def get_funding_rate(self, symbol: str):  # type: ignore[no-untyped-def]
        rate = self._funding.get(symbol)
        if rate is None:
            return None
        return type("FR", (), {"rate": rate})()


class TestFundingEnrichment:
    def test_funding_bps_and_crowded_signal(self) -> None:
        from app.market_data.ta_rating import TaRating

        universe = [{"symbol": "BTC/USDT", "rank": 1, "momentum_score": 0.9}]
        ratings = {"BTC/USDT": TaRating("buy", 0.3, 55.0, 1.0, 1.0, "up")}
        # 0.0008 fraction = 8 bps per 8h → long_crowded (>= 5 bps).
        rows = build_crosscheck_rows(universe, ratings, funding={"BTC/USDT": 0.0008})
        assert rows[0]["funding_bps"] == 8.0
        assert rows[0]["funding_signal"] == "long_crowded"

    def test_short_crowded_and_neutral(self) -> None:
        universe = [
            {"symbol": "A/USDT", "rank": 1, "momentum_score": 0.5},
            {"symbol": "B/USDT", "rank": 2, "momentum_score": 0.5},
        ]
        rows = build_crosscheck_rows(universe, {}, funding={"A/USDT": -0.0009, "B/USDT": 0.0001})
        by = {r["symbol"]: r for r in rows}
        assert by["A/USDT"]["funding_signal"] == "short_crowded"  # -9 bps
        assert by["B/USDT"]["funding_signal"] == "neutral"  # +1 bp

    def test_missing_funding_is_unavailable(self) -> None:
        rows = build_crosscheck_rows([{"symbol": "X/USDT", "rank": 1, "momentum_score": 0.5}], {})
        assert rows[0]["funding_bps"] is None
        assert rows[0]["funding_signal"] == "unavailable"

    async def test_builder_fetches_funding(self, tmp_path: Path) -> None:
        from app.observability.momentum_universe import RankedSymbol
        from app.observability.momentum_universe_ledger import append_snapshot

        ledger = tmp_path / "u.jsonl"
        append_snapshot(
            ledger,
            [RankedSymbol("BTC/USDT", 0.9, 0.9, 0.9, 1, {})],
            now=datetime(2026, 6, 26, tzinfo=UTC),
        )
        src = _FundingSource(
            {"BTC/USDT": _candles([float(100 + i) for i in range(40)])},
            {"BTC/USDT": 0.0007},  # +7 bps → long_crowded
        )
        rows = await build_crosscheck(src, ledger_path=ledger, top_n=10)
        assert rows[0]["funding_bps"] == 7.0
        assert rows[0]["funding_signal"] == "long_crowded"


def _range_candles(closes: list[float], spread_pct: float) -> list[OHLCV]:
    """Candles with a high-low spread = spread_pct of close (for ATR%/vol-regime)."""
    return [
        OHLCV(
            symbol="X",
            timestamp_utc=f"2026-06-{i + 1:02d}T00:00:00Z",
            timeframe="1d",
            open=c,
            high=c * (1.0 + spread_pct / 200.0),
            low=c * (1.0 - spread_pct / 200.0),
            close=c,
            volume=1.0,
        )
        for i, c in enumerate(closes)
    ]


class TestVolRegime:
    def test_regime_thresholds_via_rows(self) -> None:
        universe = [
            {"symbol": "HI/USDT", "rank": 1, "momentum_score": 0.5},
            {"symbol": "LO/USDT", "rank": 2, "momentum_score": 0.5},
            {"symbol": "MID/USDT", "rank": 3, "momentum_score": 0.5},
            {"symbol": "NA/USDT", "rank": 4, "momentum_score": 0.5},
        ]
        rows = build_crosscheck_rows(
            universe, {}, vol={"HI/USDT": 12.0, "LO/USDT": 2.0, "MID/USDT": 5.0}
        )
        by = {r["symbol"]: r for r in rows}
        assert by["HI/USDT"]["vol_regime"] == "high_vol"
        assert by["LO/USDT"]["vol_regime"] == "low_vol"
        assert by["MID/USDT"]["vol_regime"] == "normal"
        assert by["NA/USDT"]["vol_regime"] == "unavailable"
        assert by["NA/USDT"]["atr_pct"] is None

    async def test_builder_computes_high_vol(self, tmp_path: Path) -> None:
        from app.observability.momentum_universe import RankedSymbol
        from app.observability.momentum_universe_ledger import append_snapshot

        ledger = tmp_path / "u.jsonl"
        append_snapshot(
            ledger,
            [RankedSymbol("BTC/USDT", 0.9, 0.9, 0.9, 1, {})],
            now=datetime(2026, 6, 26, tzinfo=UTC),
        )
        # ~16% daily high-low spread → ATR% well above the 8% high-vol threshold.
        src = _FakeSource({"BTC/USDT": _range_candles([float(100 + i) for i in range(40)], 16.0)})
        rows = await build_crosscheck(src, ledger_path=ledger, top_n=10)
        assert rows[0]["vol_regime"] == "high_vol"
        assert rows[0]["atr_pct"] is not None and rows[0]["atr_pct"] > 8.0

    async def test_builder_flat_is_low_vol(self, tmp_path: Path) -> None:
        from app.observability.momentum_universe import RankedSymbol
        from app.observability.momentum_universe_ledger import append_snapshot

        ledger = tmp_path / "u.jsonl"
        append_snapshot(
            ledger,
            [RankedSymbol("BTC/USDT", 0.9, 0.9, 0.9, 1, {})],
            now=datetime(2026, 6, 26, tzinfo=UTC),
        )
        src = _FakeSource({"BTC/USDT": _candles([float(100 + i) for i in range(40)])})  # zero range
        rows = await build_crosscheck(src, ledger_path=ledger, top_n=10)
        assert rows[0]["vol_regime"] == "low_vol"
