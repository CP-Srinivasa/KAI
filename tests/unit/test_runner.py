"""Edge-discovery runner tests — pure core, injected fetch, no network."""

from __future__ import annotations

import math

from app.market_data.models import OHLCV
from app.research.evaluate import HypothesisResult, SearchReport, SearchVerdict
from app.research.runner import (
    SymbolSearchResult,
    default_hypotheses,
    run_symbol_search,
    summarize_universe,
)
from app.research.stats import NetSummary

_H = 3_600_000  # 1h in ms


def _iso(ms: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _series_fetch(start_ms: int, interval_ms: int, n_bars: int):
    last = start_ms + (n_bars - 1) * interval_ms

    async def fetch(symbol: str, timeframe: str, window_start: int, limit: int):
        out: list[OHLCV] = []
        for k in range(limit):
            t = window_start + k * interval_ms
            if t > last:
                break
            price = 100.0 + 10.0 * math.sin((t - start_ms) / interval_ms / 5.0)
            out.append(
                OHLCV(
                    symbol=symbol,
                    timestamp_utc=_iso(t),
                    timeframe=timeframe,
                    open=price,
                    high=price * 1.01,
                    low=price * 0.99,
                    close=price,
                    volume=1000.0,
                )
            )
        return out

    return fetch


async def test_run_symbol_search_runs_end_to_end() -> None:
    n = 400
    fetch = _series_fetch(0, _H, n)
    result = await run_symbol_search(
        "BTC/USDT", "1h", 0, (n - 1) * _H, fetch, default_hypotheses(), round_trip_cost_bps=20.0
    )
    assert result.symbol == "BTC/USDT"
    assert result.n_candles == n
    assert result.gap_bars == 0
    assert result.report.n_hypotheses == len(default_hypotheses())
    assert len(result.report.verdicts) == len(default_hypotheses())


def _verdict(name: str, survives: bool, mean: float, n: int) -> SearchVerdict:
    summary = NetSummary(n=n, mean_bps=mean, std_bps=1.0, hit_rate=0.5, t_stat=0.0, p_value=0.5)
    result = HypothesisResult(name=name, summary=summary, n_buckets=5, n_buckets_positive=3)
    return SearchVerdict(name=name, result=result, survives=survives)


def _sym_result(symbol: str, verdicts: list[SearchVerdict]) -> SymbolSearchResult:
    report = SearchReport(
        verdicts=verdicts,
        n_hypotheses=len(verdicts),
        n_survivors=sum(v.survives for v in verdicts),
        alpha=0.05,
    )
    return SymbolSearchResult(symbol=symbol, n_candles=300, gap_bars=0, report=report)


def test_summarize_universe_trade_weighted_aggregation() -> None:
    results = [
        _sym_result("BTC/USDT", [_verdict("A", True, 50.0, 100), _verdict("B", False, -20.0, 80)]),
        _sym_result("ETH/USDT", [_verdict("A", True, 30.0, 200), _verdict("B", False, 10.0, 50)]),
    ]
    agg = {a.name: a for a in summarize_universe(results)}

    a = agg["A"]
    assert a.n_symbols_evaluated == 2
    assert a.n_symbols_survived == 2
    assert a.total_trades == 300
    # trade-weighted mean: (50*100 + 30*200) / 300
    assert math.isclose(a.mean_net_bps, (50.0 * 100 + 30.0 * 200) / 300, abs_tol=1e-9)

    b = agg["B"]
    assert b.n_symbols_survived == 0
    assert b.total_trades == 130
    assert math.isclose(b.mean_net_bps, (-20.0 * 80 + 10.0 * 50) / 130, abs_tol=1e-9)


def test_summarize_universe_empty() -> None:
    assert summarize_universe([]) == []


def test_default_hypotheses_are_none_safe() -> None:
    # Every decider must tolerate an all-None (warm-up) row without raising.
    from app.analysis.features.feature_matrix import FeatureRow

    blank = FeatureRow(
        timestamp_utc="t",
        close=100.0,
        log_return=None,
        rsi_14=None,
        adx_14=None,
        plus_di_14=None,
        minus_di_14=None,
        realized_vol_24=None,
        ema_12=None,
        ema_26=None,
        macd=None,
        bollinger_z_20=None,
    )
    for _name, decide in default_hypotheses():
        assert decide(blank) == 0


def test_ts_momentum_hypotheses_present_and_directional() -> None:
    """The TS-momentum hypotheses are in the BH-FDR set and read trail_return_20:
    long-only goes long on positive trailing return, the long/short variant flips."""
    from app.analysis.features.feature_matrix import FeatureRow

    deciders = dict(default_hypotheses())
    assert "ts_momentum_long" in deciders and "ts_momentum" in deciders

    def _row(trail: float | None) -> FeatureRow:
        return FeatureRow(
            timestamp_utc="t",
            close=100.0,
            log_return=None,
            rsi_14=None,
            adx_14=None,
            plus_di_14=None,
            minus_di_14=None,
            realized_vol_24=None,
            ema_12=None,
            ema_26=None,
            macd=None,
            bollinger_z_20=None,
            trail_return_20=trail,
        )

    assert deciders["ts_momentum_long"](_row(0.05)) == 1
    assert deciders["ts_momentum_long"](_row(-0.05)) == 0  # long-only: no short
    assert deciders["ts_momentum"](_row(0.05)) == 1
    assert deciders["ts_momentum"](_row(-0.05)) == -1
    assert deciders["ts_momentum"](_row(None)) == 0


def test_risk_adjusted_and_trend_confirmed_momentum() -> None:
    """Vol-scaled TSMOM trades only when momentum exceeds ~1 horizon-sigma;
    trend-confirmed momentum trades only when ADX shows a trending regime."""
    from app.analysis.features.feature_matrix import FeatureRow

    deciders = dict(default_hypotheses())
    assert "tsmom_vol_scaled" in deciders and "tsmom_adx_confirmed" in deciders

    def _row(
        *, trail: float | None, vol: float | None = None, adx: float | None = None
    ) -> FeatureRow:
        return FeatureRow(
            timestamp_utc="t",
            close=100.0,
            log_return=None,
            rsi_14=None,
            adx_14=adx,
            plus_di_14=None,
            minus_di_14=None,
            realized_vol_24=vol,
            ema_12=None,
            ema_26=None,
            macd=None,
            bollinger_z_20=None,
            trail_return_20=trail,
        )

    vs = deciders["tsmom_vol_scaled"]
    # z = trail / (vol * sqrt(20)); 0.10/(0.01*4.47) ≈ 2.24 → high conviction.
    assert vs(_row(trail=0.10, vol=0.01)) == 1
    assert vs(_row(trail=-0.10, vol=0.01)) == -1
    # weak momentum vs vol → z≈0.22 < 1 → no trade.
    assert vs(_row(trail=0.01, vol=0.01)) == 0
    assert vs(_row(trail=0.10, vol=None)) == 0  # no vol → no trade
    assert vs(_row(trail=None, vol=0.01)) == 0

    ac = deciders["tsmom_adx_confirmed"]
    assert ac(_row(trail=0.05, adx=30.0)) == 1  # trending → take momentum
    assert ac(_row(trail=-0.05, adx=30.0)) == -1
    assert ac(_row(trail=0.05, adx=20.0)) == 0  # not trending → stand aside
    assert ac(_row(trail=0.05, adx=None)) == 0
