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

from dataclasses import dataclass

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


def build_feature_matrix(candles: list[OHLCV]) -> list[FeatureRow]:
    """Compose a causal feature matrix from an OHLCV series.

    Args:
        candles: oldest-first OHLCV candles for a single symbol/timeframe.

    Returns:
        One FeatureRow per candle (len == len(candles)); empty for empty input.
    """
    n = len(candles)
    if n == 0:
        return []

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]

    log_ret = compute_log_returns(closes)
    rsi = compute_rsi(closes, period=RSI_DEFAULT_PERIOD)
    adx = compute_adx_di(highs, lows, closes, period=ADX_DEFAULT_PERIOD)
    rv = compute_realized_volatility(closes, window=RV_DEFAULT_WINDOW)
    ema_fast = compute_ema(closes, period=EMA_FAST_PERIOD)
    ema_slow = compute_ema(closes, period=EMA_SLOW_PERIOD)
    boll = compute_bollinger_z(closes, window=BOLLINGER_DEFAULT_WINDOW)
    trail_ret = compute_trailing_returns(closes, TRAIL_RETURN_WINDOW)

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
            )
        )
    return rows
