"""WP-D (2026-06-15): asset-agnostic technical signal screener.

The narrative/news engine is structurally BTC-biased (BTC gets the most
coverage), so the autonomous funnel keeps collapsing to BTC. This screener is
the asset-agnostic counterweight: it judges *price action* over a liquid
universe, not news. Its core is **relative strength vs BTC** — an altcoin that
is outperforming BTC scores positively even at modest absolute momentum, while
BTC itself has zero relative strength and therefore ranks *below* any
outperforming alt. That structurally forces non-BTC selection.

This module is pure / I/O-free by design (WP-D part 1): it consumes already-
fetched OHLCV and emits ranked ``TechnicalSignal`` candidates with an
asset-agnostic ``strength`` in ``[0, 1]``. The strength feeds the technical
eligibility path (WP-B ``signal_path="technical"``, gated by
``ALERT_MIN_TECHNICAL_STRENGTH``). The live wiring — sourcing the liquid
universe, fetching OHLCV via the provider-open market-data service, writing
shadow candidates, the scheduler + default-off flag — is WP-D part 2 and never
runs automatically from here.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.market_data.models import OHLCV

# Default momentum lookback in candles. With a 1h timeframe this is ~1 day.
DEFAULT_LOOKBACK = 24

# Normalisation anchors: the fractional move that maps a component to its full
# ±1 contribution. 0.10 = a 10% move (absolute, or relative-to-BTC) is "full
# strength". Deliberately conservative defaults — WP-D part 2 calibrates them
# against real technical-path outcomes, exactly like ALERT_MIN_TECHNICAL_STRENGTH.
MOMENTUM_FULL_SCALE = 0.10
RELATIVE_FULL_SCALE = 0.10

# Blend weights (sum to 1.0): absolute momentum, relative strength vs BTC, and a
# Donchian breakout flag. Relative strength is weighted equally with momentum so
# an alt outperforming a flat/up BTC still surfaces.
WEIGHT_MOMENTUM = 0.4
WEIGHT_RELATIVE = 0.4
WEIGHT_BREAKOUT = 0.2

# Scores whose absolute value is below this are treated as "no signal" (flat).
MIN_ABS_SCORE = 1e-6

_BULLISH = "bullish"
_BEARISH = "bearish"


@dataclass(frozen=True)
class TechnicalSignal:
    """An asset-agnostic price/flow signal for the technical eligibility path."""

    symbol: str
    direction: str  # "bullish" | "bearish"
    strength: float  # [0, 1] — feeds ALERT_MIN_TECHNICAL_STRENGTH
    momentum_pct: float  # fractional move over the lookback (0.05 = +5%)
    relative_strength: float  # momentum_pct minus BTC's momentum_pct
    breakout: int  # +1 Donchian high break, -1 low break, 0 none


def _sorted_closes(candles: list[OHLCV]) -> list[OHLCV]:
    return sorted(candles, key=lambda c: c.timestamp_utc)


def _momentum(candles: list[OHLCV], lookback: int) -> float | None:
    """Fractional close-to-close change over ``lookback`` candles."""
    if len(candles) < lookback + 1:
        return None
    ordered = _sorted_closes(candles)
    past = ordered[-(lookback + 1)].close
    last = ordered[-1].close
    if past <= 0:
        return None
    return (last - past) / past


def _breakout(candles: list[OHLCV], lookback: int) -> int:
    """Donchian breakout over the prior ``lookback`` window (excl. last close).

    +1 if the last close prints at/above the window high, -1 if at/below the
    window low, else 0.
    """
    if len(candles) < lookback + 1:
        return 0
    ordered = _sorted_closes(candles)
    window = ordered[-(lookback + 1) : -1]
    last = ordered[-1].close
    window_high = max(c.high for c in window)
    window_low = min(c.low for c in window)
    # Strict break: a flat market (last == high == low) must NOT register a
    # breakout, so require the last close to clear the prior window extreme.
    if last > window_high:
        return 1
    if last < window_low:
        return -1
    return 0


def _clamp_unit(value: float) -> float:
    return max(-1.0, min(1.0, value))


def compute_technical_signal(
    symbol: str,
    candles: list[OHLCV],
    btc_candles: list[OHLCV],
    *,
    lookback: int = DEFAULT_LOOKBACK,
) -> TechnicalSignal | None:
    """Compute an asset-agnostic technical signal, or None if flat/insufficient.

    ``strength`` is a deterministic blend in ``[0, 1]`` of normalised absolute
    momentum, relative strength vs BTC, and a Donchian breakout flag. Direction
    is the sign of the blended score. BTC vs itself has zero relative strength,
    so an outperforming alt outranks it — the anti-monoculture mechanism.
    """
    momentum = _momentum(candles, lookback)
    if momentum is None:
        return None
    btc_momentum = _momentum(btc_candles, lookback) or 0.0
    relative = momentum - btc_momentum
    breakout = _breakout(candles, lookback)

    momentum_n = _clamp_unit(momentum / MOMENTUM_FULL_SCALE)
    relative_n = _clamp_unit(relative / RELATIVE_FULL_SCALE)
    score = (
        WEIGHT_MOMENTUM * momentum_n
        + WEIGHT_RELATIVE * relative_n
        + WEIGHT_BREAKOUT * float(breakout)
    )
    if abs(score) < MIN_ABS_SCORE:
        return None

    return TechnicalSignal(
        symbol=symbol,
        direction=_BULLISH if score > 0 else _BEARISH,
        strength=min(1.0, abs(score)),
        momentum_pct=momentum,
        relative_strength=relative,
        breakout=breakout,
    )


def screen_universe(
    candles_by_symbol: dict[str, list[OHLCV]],
    btc_candles: list[OHLCV],
    *,
    lookback: int = DEFAULT_LOOKBACK,
    min_strength: float = 0.0,
    top_n: int | None = None,
) -> list[TechnicalSignal]:
    """Rank a pre-fetched universe by technical strength (pure, no I/O).

    Returns signals with ``strength >= min_strength`` sorted strongest-first;
    ``top_n`` caps the result. Symbols with insufficient/flat data are dropped.
    Because relative strength is measured against BTC, outperforming alts sort
    above BTC for the same absolute move — the structural non-BTC bias.
    """
    signals: list[TechnicalSignal] = []
    for symbol, candles in candles_by_symbol.items():
        signal = compute_technical_signal(symbol, candles, btc_candles, lookback=lookback)
        if signal is None or signal.strength < min_strength:
            continue
        signals.append(signal)
    signals.sort(key=lambda s: s.strength, reverse=True)
    if top_n is not None:
        return signals[:top_n]
    return signals
