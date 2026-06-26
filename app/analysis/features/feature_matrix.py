"""Per-candle feature matrix — compose existing + new indicators.

``build_feature_matrix`` takes an oldest-first OHLCV series for a SINGLE symbol
and timeframe and returns one :class:`FeatureRow` per candle. Every indicator
used is CAUSAL: the row at index ``i`` depends only on ``candles[0..i]``. This
no-look-ahead property is the integrity foundation for any forward-return
backtest built on top of these features — it is asserted directly in
``tests/unit/test_feature_matrix.py``.

All feature fields are ``float | None`` — None during an indicator's warm-up,
so callers never have to align indices manually.

This module only COMPOSES; the indicator math (and its correctness tests) lives
in ``app.analysis.indicators`` and is reused verbatim (no re-implementation).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.analysis.features.funding_align import FundingPoint, align_funding_to_bars
from app.analysis.features.whale_flow_align import FlowPoint, align_flow_to_bars
from app.analysis.indicators.adx import ADX_DEFAULT_PERIOD, compute_adx_di
from app.analysis.indicators.bollinger import (
    BOLLINGER_DEFAULT_WINDOW,
    compute_bollinger_z,
)
from app.analysis.indicators.ema import compute_ema
from app.analysis.indicators.realized_volatility import (
    RV_DEFAULT_WINDOW,
    compute_log_returns,
    compute_realized_volatility,
)
from app.analysis.indicators.rsi import RSI_DEFAULT_PERIOD, compute_rsi
from app.market_data.models import OHLCV

# MACD-style fast/slow EMA periods (standard 12/26).
EMA_FAST_PERIOD = 12
EMA_SLOW_PERIOD = 26
# Time-series-momentum lookback (bars) for the trailing-return feature.
TRAIL_RETURN_WINDOW = 20


def compute_trailing_returns(closes: list[float], window: int) -> list[float | None]:
    """Causal trailing simple return over ``window`` bars: close[i]/close[i-window]-1.

    None for the first ``window`` bars (no prior anchor) and where the anchor close
    is non-positive. Pure; one value per input close.
    """
    out: list[float | None] = [None] * len(closes)
    for i in range(window, len(closes)):
        anchor = closes[i - window]
        if anchor > 0:
            out[i] = closes[i] / anchor - 1.0
    return out


@dataclass(frozen=True)
class FeatureRow:
    """One candle's causal feature vector. Feature fields are None during warm-up."""

    timestamp_utc: str
    close: float
    log_return: float | None
    rsi_14: float | None
    adx_14: float | None
    plus_di_14: float | None
    minus_di_14: float | None
    realized_vol_24: float | None
    ema_12: float | None
    ema_26: float | None
    macd: float | None
    bollinger_z_20: float | None
    # Time-series momentum: trailing simple return over TRAIL_RETURN_WINDOW bars
    # (the canonical TS-momentum signal, Liu-Tsyvinski-Wu). None during warm-up.
    # Defaulted so existing keyword/test constructors stay valid.
    trail_return_20: float | None = None
    # Perpetual funding, aligned causally onto the bar (see funding_align). Only
    # populated when build_feature_matrix is given a funding series; otherwise
    # None (OHLCV-only callers and existing tests are unaffected).
    funding_rate: float | None = None  # as-of settled funding rate (fraction/8h)
    funding_rate_z: float | None = None  # rolling z-score of funding (regime-relative)
    # Whale exchange-flow, aligned causally onto the bar (see whale_flow_align).
    # ``coin_*`` = this asset's own coin flow to/from exchanges (inflow bearish);
    # ``stable_*`` = market-wide stablecoin flow to/from exchanges (inflow = dry
    # powder, bullish). Only populated when build_feature_matrix is given the
    # respective flow series; otherwise None (OHLCV-only callers unaffected).
    coin_netflow_usd: float | None = None  # trailing 24h net coin flow (USD, +inflow)
    coin_netflow_z: float | None = None  # rolling z-score of coin netflow
    stable_netflow_usd: float | None = None  # trailing 24h net stablecoin flow (USD)
    stable_netflow_z: float | None = None  # rolling z-score of stablecoin netflow


def build_feature_matrix(
    candles: list[OHLCV],
    funding: Sequence[FundingPoint] | None = None,
    *,
    coin_flows: Sequence[FlowPoint] | None = None,
    stable_flows: Sequence[FlowPoint] | None = None,
) -> list[FeatureRow]:
    """Compose a causal feature matrix from an OHLCV series.

    Args:
        candles: oldest-first OHLCV candles for a single symbol/timeframe.
        funding: optional settled funding events for the same symbol. When given,
            each bar gets the most recent funding rate settled at or before its
            OPEN timestamp (causal as-of join) plus a rolling funding z-score.
            When omitted, the funding fields stay None (OHLCV-only behaviour).
        coin_flows: optional whale exchange-flow events for THIS asset's own coin
            (inflow to exchanges signed +). Aligned to a trailing-24h net + z.
        stable_flows: optional market-wide stablecoin exchange-flow events (same
            for every symbol). Aligned to a trailing-24h net + z. Both flow series
            default None → their fields stay None (existing callers unaffected).

    Returns:
        One FeatureRow per candle (len == len(candles)); empty for empty input.
    """
    n = len(candles)
    if n == 0:
        return []

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    bar_ts = [c.timestamp_utc for c in candles]

    log_ret = compute_log_returns(closes)
    rsi = compute_rsi(closes, period=RSI_DEFAULT_PERIOD)
    adx = compute_adx_di(highs, lows, closes, period=ADX_DEFAULT_PERIOD)
    rv = compute_realized_volatility(closes, window=RV_DEFAULT_WINDOW)
    ema_fast = compute_ema(closes, period=EMA_FAST_PERIOD)
    ema_slow = compute_ema(closes, period=EMA_SLOW_PERIOD)
    boll = compute_bollinger_z(closes, window=BOLLINGER_DEFAULT_WINDOW)
    trail_ret = compute_trailing_returns(closes, TRAIL_RETURN_WINDOW)
    if funding:
        funding_rate, funding_rate_z = align_funding_to_bars(bar_ts, funding)
    else:
        funding_rate = [None] * n
        funding_rate_z = [None] * n
    if coin_flows:
        coin_netflow, coin_netflow_z = align_flow_to_bars(bar_ts, coin_flows)
    else:
        coin_netflow = [None] * n
        coin_netflow_z = [None] * n
    if stable_flows:
        stable_netflow, stable_netflow_z = align_flow_to_bars(bar_ts, stable_flows)
    else:
        stable_netflow = [None] * n
        stable_netflow_z = [None] * n

    rows: list[FeatureRow] = []
    for i in range(n):
        fast = ema_fast[i]
        slow = ema_slow[i]
        macd = fast - slow if (fast is not None and slow is not None) else None
        rows.append(
            FeatureRow(
                timestamp_utc=candles[i].timestamp_utc,
                close=closes[i],
                log_return=log_ret[i],
                rsi_14=rsi[i],
                adx_14=adx.adx[i],
                plus_di_14=adx.plus_di[i],
                minus_di_14=adx.minus_di[i],
                realized_vol_24=rv[i],
                ema_12=fast,
                ema_26=slow,
                macd=macd,
                bollinger_z_20=boll[i],
                trail_return_20=trail_ret[i],
                funding_rate=funding_rate[i],
                funding_rate_z=funding_rate_z[i],
                coin_netflow_usd=coin_netflow[i],
                coin_netflow_z=coin_netflow_z[i],
                stable_netflow_usd=stable_netflow[i],
                stable_netflow_z=stable_netflow_z[i],
            )
        )
    return rows
