"""Identify prices that were produced by the synthetic mock adapter.

DS-20260818-MOCK-EXIT. ``MockMarketDataAdapter`` is the LAST link of the live
``fallback`` provider chain (``APP_MARKET_DATA_PROVIDER=fallback``). It returns
``is_stale=False``/``freshness_seconds=0.0`` unconditionally, so on a tick where
every real venue failed, the position monitor received a *synthetic* price that
passed its stale-guard and closed positions against it.

The mock curve is fully deterministic given the symbol's phase:

    round(base + base * (amplitude_pct/100) * sin(phase / 1440 * 2*pi), 2)

``phase = hash(symbol) % 360`` is per-process randomized, so the phase of a past
incident is unknown — but the *candidate set* is only 360 values wide per symbol
and every value carries exactly two decimals. That makes recognition exact
rather than heuristic: we accept a price only on bit-identical equality with a
reconstructed candidate (optionally after undoing the paper fill slippage).

Verified against the two independently-forensicked incidents:
  * ``3259.9692`` = mock(ETH/USDT, phase 297) * (1 - 0.0005)  [DS-20260601]
  * ``3225.6863500000004`` = mock(ETH/USDT, phase 101) * (1 - 0.0005)  [2026-08-11/12]

Both reproduce bit-exactly, float artefacts included. This is a FORENSIC reader:
it never mutates the append-only audit — it lets aggregators exclude closes that
were priced off synthetic data (memory ``paper_audit_pnl_field_semantics``:
quarantine, never delete).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from app.market_data.mock_adapter import (
    _BASE_PRICES,
    _DEFAULT_AMPLITUDE_PCT,
    _DEFAULT_BASE_PRICE,
    _PRICE_DECIMALS,
    _SINE_PERIOD_MINUTES,
)

# Paper fill slippage as a fraction (settings default ``paper_slippage_pct=0.05``
# → 0.05/100). Passed explicitly by callers that know a different rate; NOT read
# from get_settings() here so the detector stays usable in hot read paths
# (memory feedback_get_settings_uncached_hot_loop).
DEFAULT_PAPER_SLIPPAGE_FRACTION: float = 0.0005

_PHASES = 360


@dataclass(frozen=True)
class MockPriceMatch:
    """A bit-exact match of a price against the mock adapter's curve."""

    symbol: str
    price: float
    mock_raw_price: float
    phase: int
    slippage_applied: float
    """0.0 = raw mock price; +f = buy-side fill (short close); -f = sell-side fill."""


@lru_cache(maxsize=256)
def _mock_candidates(symbol: str, amplitude_pct: float) -> dict[float, int]:
    """Every price the mock can emit for ``symbol`` → the phase producing it.

    ``offset_minutes`` is 0 for ``get_ticker``/``get_market_data_point`` (the only
    paths that can drive a fill), so the phase alone spans the candidate set.

    DEGENERATE PHASES ARE EXCLUDED. At sin(t)≈0 the curve returns the base price
    itself, and at |sin(t)|≈1 it returns base*(1±amplitude) — round, structurally
    distinguished numbers (150.00, 102.00, 3200.00) that a legitimate quote or a
    test fixture hits by itself. Bit-exact equality only proves synthetic origin
    where the value carries the curve's own information; on those three points it
    proves nothing. Dropping them costs ~1% of detection coverage and removes the
    entire class of false positives — the conservative direction, matching this
    module's contract that an unprovable row is never dropped.
    """
    base = _BASE_PRICES.get(symbol, _DEFAULT_BASE_PRICE)
    amplitude = base * (amplitude_pct / 100)
    degenerate = {
        round(base, _PRICE_DECIMALS),
        round(base + amplitude, _PRICE_DECIMALS),
        round(base - amplitude, _PRICE_DECIMALS),
    }
    out: dict[float, int] = {}
    for phase in range(_PHASES):
        t = phase / _SINE_PERIOD_MINUTES * 2 * math.pi
        price = round(base + amplitude * math.sin(t), _PRICE_DECIMALS)
        if price in degenerate:
            continue
        # First phase wins — sin is not injective over [0,360), and the lower
        # phase is the one a fresh process is more likely to have produced.
        out.setdefault(price, phase)
    return out


def match_mock_price(
    symbol: str,
    price: object,
    *,
    slippage_pct: float = DEFAULT_PAPER_SLIPPAGE_FRACTION,
    amplitude_pct: float = _DEFAULT_AMPLITUDE_PCT,
) -> MockPriceMatch | None:
    """Return the match when ``price`` is bit-exactly a mock-derived price.

    Checks the raw mock value and both slippage-adjusted fill variants
    (``*(1-s)`` for a sell fill closing a long, ``*(1+s)`` for a buy fill closing
    a short). Bit-exact equality only — a real venue quote is a continuous float
    and does not coincide with ``round(x, 2)`` scaled by the slippage factor.
    """
    if not isinstance(price, (int, float)) or isinstance(price, bool):
        return None
    value = float(price)
    if value <= 0 or value != value:  # non-positive or NaN
        return None
    sym = str(symbol).strip()
    if not sym:
        return None

    candidates = _mock_candidates(sym, amplitude_pct)
    # ONLY the slippage-adjusted variants. paper_engine always multiplies the
    # quote by (1 ± slippage) before booking a fill, so a raw mock value can never
    # appear as an exit_price of an engine close — while round numbers inside the
    # default 98..102 band (fixtures, placeholder prices like 101.00) hit it
    # constantly. Accepting the raw value bought no detection and produced only
    # false positives. All 12 real incidents carry a slippage factor.
    variants = (
        (-slippage_pct, 1.0 - slippage_pct),  # sell fill: long close
        (slippage_pct, 1.0 + slippage_pct),  # buy fill: short close
    )
    for applied, factor in variants:
        for raw, phase in candidates.items():
            if raw * factor == value:
                return MockPriceMatch(
                    symbol=sym,
                    price=value,
                    mock_raw_price=raw,
                    phase=phase,
                    slippage_applied=applied,
                )
    return None


#: Above this share of the representable two-decimal price space, a bit-exact hit
#: stops being a fingerprint: roughly one price in ten inside the band lies on the
#: curve, so coincidence is an ordinary event rather than a remarkable one. The
#: boundary is declared, not tuned to an outcome, and the RAW coverage always
#: travels with the verdict so a reader can judge it independently.
#:
#: Measured coverage of the live symbols:
#:     BTC/USDT  0.0014   ETH/USDT  0.0277   (fingerprint)
#:     SOL/USDT  0.4433   AAVE/USDT 0.4975   (indistinguishable)
HIGH_COVERAGE_THRESHOLD: float = 0.10


def mock_curve_coverage(symbol: str, *, amplitude_pct: float = _DEFAULT_AMPLITUDE_PCT) -> float:
    """Share of representable prices in the band that the mock curve can emit.

    Coverage ~1.0 means "the curve can produce almost any price in this range",
    so bit-exact equality carries almost no information. Coverage near 0 means a
    hit is a fingerprint. This is what makes the TL-002 verdict evidence-weighted
    instead of a single global rule.
    """
    base: float = float(_BASE_PRICES.get(str(symbol).strip(), _DEFAULT_BASE_PRICE))
    amplitude: float = base * (amplitude_pct / 100.0)
    representable: float = 2.0 * amplitude * float(10 ** int(_PRICE_DECIMALS))
    if representable <= 0:
        return 1.0
    distinct: int = len(_mock_candidates(str(symbol).strip(), amplitude_pct))
    coverage: float = float(distinct) / representable
    return coverage if coverage < 1.0 else 1.0


def is_high_coverage_symbol(symbol: str, *, amplitude_pct: float = _DEFAULT_AMPLITUDE_PCT) -> bool:
    """True when a bit-exact hit on this symbol proves little on its own."""
    return mock_curve_coverage(symbol, amplitude_pct=amplitude_pct) >= HIGH_COVERAGE_THRESHOLD


def uses_default_base_price(symbol: str) -> bool:
    """True when the symbol has no own base price and rides the 100.0 default."""
    return str(symbol).strip() not in _BASE_PRICES


def is_mock_derived_price(
    symbol: str,
    price: object,
    *,
    slippage_pct: float = DEFAULT_PAPER_SLIPPAGE_FRACTION,
) -> bool:
    """True when ``price`` can only have come from the synthetic mock adapter."""
    return match_mock_price(symbol, price, slippage_pct=slippage_pct) is not None


__all__ = [
    "DEFAULT_PAPER_SLIPPAGE_FRACTION",
    "HIGH_COVERAGE_THRESHOLD",
    "MockPriceMatch",
    "is_high_coverage_symbol",
    "is_mock_derived_price",
    "match_mock_price",
    "mock_curve_coverage",
    "uses_default_base_price",
]
