"""ta_rating — a ToS-compliant TradingView-rating substitute (G4).

Computes a directional technical rating from our OWN OHLCV (no scraping, no key,
no new dependency): a moving-average trend (SMA short vs long) blended with a
Wilder-RSI bias into a signed score ``[-1, +1]`` and a label
(strong_sell … strong_buy). Pure + deterministic. Used ONLY as an informational
cross-check signal next to the own-data momentum rank — zero sizing impact.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.market_data.models import OHLCV


@dataclass(frozen=True)
class TaRating:
    label: str  # strong_sell | sell | neutral | buy | strong_buy
    score: float  # [-1, +1], + bullish
    rsi: float | None
    sma_short: float | None
    sma_long: float | None
    trend: str  # up | down | flat


def compute_sma(closes: Sequence[float], period: int) -> float | None:
    if period <= 0 or len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def compute_rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Wilder's RSI over the last ``period`` deltas. ``None`` if too short."""
    if period <= 0 or len(closes) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _label(score: float) -> str:
    if score > 0.5:
        return "strong_buy"
    if score > 0.15:
        return "buy"
    if score < -0.5:
        return "strong_sell"
    if score < -0.15:
        return "sell"
    return "neutral"


def compute_ta_rating(
    candles: Sequence[OHLCV],
    *,
    rsi_period: int = 14,
    sma_short_period: int = 10,
    sma_long_period: int = 30,
) -> TaRating | None:
    """Blend an SMA-cross trend with an RSI bias into a signed rating.

    Returns ``None`` when there is not enough history for the longest input
    (the long SMA / the RSI period).
    """
    if len(candles) < max(sma_long_period, rsi_period + 1):
        return None
    closes = [c.close for c in sorted(candles, key=lambda c: c.timestamp_utc)]

    sma_short = compute_sma(closes, sma_short_period)
    sma_long = compute_sma(closes, sma_long_period)
    rsi = compute_rsi(closes, rsi_period)
    if sma_short is None or sma_long is None or rsi is None:
        return None

    # Trend arm: +1 short above long (uptrend), -1 below, ~flat near equality.
    spread = (sma_short - sma_long) / sma_long if sma_long > 0 else 0.0
    if spread > 0.001:
        trend = "up"
    elif spread < -0.001:
        trend = "down"
    else:
        trend = "flat"
    ma_signal = _clamp(spread * 50.0, -1.0, 1.0)  # ~2% spread saturates the arm

    # RSI arm: 50 neutral; ±20 around it saturates (70→+1, 30→-1).
    rsi_signal = _clamp((rsi - 50.0) / 20.0, -1.0, 1.0)

    score = _clamp(0.6 * ma_signal + 0.4 * rsi_signal, -1.0, 1.0)
    return TaRating(
        label=_label(score),
        score=score,
        rsi=rsi,
        sma_short=sma_short,
        sma_long=sma_long,
        trend=trend,
    )


__all__ = ["TaRating", "compute_rsi", "compute_sma", "compute_ta_rating"]
