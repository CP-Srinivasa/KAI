"""WP-D (2026-06-15): asset-agnostic technical screener core.

Pins the relative-strength-vs-BTC mechanism (the anti-BTC-monoculture lever),
direction/strength derivation, the strict Donchian breakout, and the universe
ranking — all pure, no I/O.
"""

from __future__ import annotations

from app.market_data.models import OHLCV
from app.signals.technical_screener import (
    DEFAULT_LOOKBACK,
    TechnicalSignal,
    _breakout,
    compute_technical_signal,
    screen_universe,
)

_BASE = 100.0


def _candles(closes: list[float], *, symbol: str = "X/USDT") -> list[OHLCV]:
    """Build a chronological candle series from closes (no wicks: high=low=close)."""
    out: list[OHLCV] = []
    for i, close in enumerate(closes):
        out.append(
            OHLCV(
                symbol=symbol,
                timestamp_utc=f"{i:04d}",  # sorted lexically by the module
                timeframe="1h",
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1.0,
            )
        )
    return out


def _flat(value: float = _BASE) -> list[OHLCV]:
    return _candles([value] * (DEFAULT_LOOKBACK + 1))


def _move(total_pct: float) -> list[OHLCV]:
    """Series with EXACTLY ``total_pct`` momentum over the lookback.

    Flat for the window, then a single final step — so momentum = total_pct and
    the Donchian breakout sign matches, with no compounding saturation.
    """
    closes = [_BASE] * DEFAULT_LOOKBACK + [_BASE * (1 + total_pct)]
    return _candles(closes)


def test_insufficient_candles_returns_none() -> None:
    short = _candles([100.0, 101.0, 102.0])  # < lookback + 1
    assert compute_technical_signal("X/USDT", short, _flat()) is None


def test_flat_market_returns_none() -> None:
    assert compute_technical_signal("X/USDT", _flat(), _flat()) is None


def test_bullish_momentum_yields_bullish_signal() -> None:
    sig = compute_technical_signal("X/USDT", _move(0.05), _flat())
    assert sig is not None
    assert sig.direction == "bullish"
    assert 0.0 < sig.strength <= 1.0
    assert sig.momentum_pct > 0


def test_bearish_momentum_yields_bearish_signal() -> None:
    sig = compute_technical_signal("X/USDT", _move(-0.05), _flat())
    assert sig is not None
    assert sig.direction == "bearish"
    assert sig.momentum_pct < 0


def test_relative_strength_vs_btc_is_the_discriminator() -> None:
    """Identical absolute momentum, different BTC backdrop → outperformer wins."""
    alt = _move(0.04)
    out_vs_flat = compute_technical_signal("ALT/USDT", alt, _flat())
    out_vs_strong = compute_technical_signal("ALT/USDT", alt, _move(0.04))  # BTC matches
    assert out_vs_flat is not None and out_vs_strong is not None
    # Same absolute momentum, but outperforming a flat BTC beats merely matching
    # a strong BTC (relative strength ≈ 0).
    assert out_vs_flat.strength > out_vs_strong.strength
    assert out_vs_flat.relative_strength > out_vs_strong.relative_strength


def test_btc_itself_has_zero_relative_strength() -> None:
    btc = _move(0.04)
    sig = compute_technical_signal("BTC/USDT", btc, btc)  # vs itself
    assert sig is not None
    assert abs(sig.relative_strength) < 1e-9


def test_outperforming_alt_outranks_btc() -> None:
    """The structural non-BTC bias: an alt outperforming BTC ranks above it."""
    btc = _move(0.02)
    alt = _move(0.06)  # outperforms BTC
    ranked = screen_universe({"BTC/USDT": btc, "ALT/USDT": alt}, btc, min_strength=0.0)
    assert ranked[0].symbol == "ALT/USDT"
    btc_sig = next(s for s in ranked if s.symbol == "BTC/USDT")
    alt_sig = next(s for s in ranked if s.symbol == "ALT/USDT")
    assert alt_sig.strength > btc_sig.strength
    assert abs(btc_sig.relative_strength) < 1e-9  # BTC vs itself


def test_breakout_strict_no_false_positive_on_flat() -> None:
    assert _breakout(_flat(), DEFAULT_LOOKBACK) == 0
    assert _breakout(_move(0.03), DEFAULT_LOOKBACK) == 1
    assert _breakout(_move(-0.03), DEFAULT_LOOKBACK) == -1


def test_screen_universe_filters_and_caps() -> None:
    universe = {
        "STRONG/USDT": _move(0.08),
        "WEAK/USDT": _move(0.002),
        "FLAT/USDT": _flat(),  # dropped (no signal)
        "SHORT/USDT": _candles([1.0, 2.0]),  # dropped (insufficient)
    }
    ranked = screen_universe(universe, _flat(), min_strength=0.3, top_n=1)
    assert len(ranked) == 1
    assert ranked[0].symbol == "STRONG/USDT"
    assert all(isinstance(s, TechnicalSignal) for s in ranked)


def test_screen_universe_sorted_strongest_first() -> None:
    universe = {
        "A/USDT": _move(0.02),
        "B/USDT": _move(0.08),
        "C/USDT": _move(0.05),
    }
    ranked = screen_universe(universe, _flat())
    strengths = [s.strength for s in ranked]
    assert strengths == sorted(strengths, reverse=True)
    assert ranked[0].symbol == "B/USDT"
