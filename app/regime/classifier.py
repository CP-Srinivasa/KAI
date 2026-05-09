"""Regime classifier — deterministic threshold model with hysteresis.

R1 design rationale: the model is intentionally simple and explainable.
Every committed regime is reproducible from the indicator values stored in
the snapshot (ADX, +DI, -DI, ATR z-score, vol class). Operator can audit by
inspecting JSONL — no opaque ML state. HMM / Bayesian change-point come in
R3+ once 14 days of operator-validated classifications give us ground
truth to train against.

Crypto-adjusted thresholds (operator decision 2026-05-09):
    ADX >= 30 → trend     (FX-default 25 too noisy on 24/7 crypto markets)
    ADX 25-30 → breakout candidate (requires ATR z-score >= 1)
    ADX < 25  → chop

Hysteresis (2 bars) prevents flickering at threshold edges (ADX = 27 → 30 → 27).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.indicators.realized_volatility import VolClass
from app.regime.models import RegimeClass, RegimeSnapshot


ADX_TREND_THRESHOLD = 30.0
ADX_BREAKOUT_THRESHOLD = 25.0
ATR_Z_BREAKOUT_THRESHOLD = 1.0
HYSTERESIS_BARS = 2


@dataclass(frozen=True)
class ClassifierInputs:
    """Indicator snapshot at the bar being classified."""

    adx: float | None
    plus_di: float | None
    minus_di: float | None
    rv_24h: float | None
    atr_zscore: float | None
    vol_class: VolClass


def classify_raw(inputs: ClassifierInputs) -> RegimeClass:
    """Classify a single bar via threshold logic (no hysteresis).

    Order matters — most specific first. Missing core indicators (ADX, DIs)
    short-circuit to ``unknown``; missing ATR-z degrades the bar from
    breakout-candidate to chop, but does not block trend or chop classification.
    """
    if inputs.adx is None or inputs.plus_di is None or inputs.minus_di is None:
        return RegimeClass.UNKNOWN

    plus_dominant = inputs.plus_di > inputs.minus_di

    if inputs.adx >= ADX_TREND_THRESHOLD:
        return RegimeClass.TREND_UP if plus_dominant else RegimeClass.TREND_DOWN

    if inputs.adx >= ADX_BREAKOUT_THRESHOLD:
        atr_z_high = (
            inputs.atr_zscore is not None
            and inputs.atr_zscore >= ATR_Z_BREAKOUT_THRESHOLD
        )
        if atr_z_high:
            return RegimeClass.BREAKOUT_UP if plus_dominant else RegimeClass.BREAKOUT_DOWN
        # Transition zone without volatility anomaly → still chop, classified
        # by vol_class below.

    if inputs.vol_class == "vol_low":
        return RegimeClass.CHOP_QUIET
    return RegimeClass.CHOP_VOLATILE


def apply_hysteresis(
    raw: RegimeClass,
    previous: RegimeSnapshot | None,
    consecutive_required: int = HYSTERESIS_BARS,
) -> tuple[RegimeClass, RegimeClass | None, int]:
    """Apply N-bar hysteresis to a raw classification.

    Returns:
        (committed_regime, pending_regime, pending_consecutive_count)

    Rules:
        - No previous snapshot → commit raw immediately, no pending.
        - Raw equals previous committed regime → no change, pending cleared.
        - Raw differs and matches previous pending → increment counter;
          commit when ``pending_consecutive + 1 >= consecutive_required``.
        - Raw differs and is a new candidate → restart pending (counter = 1).
    """
    if previous is None:
        return raw, None, 0

    if raw == previous.regime:
        return raw, None, 0

    if previous.pending_regime == raw:
        new_consecutive = previous.pending_consecutive + 1
        if new_consecutive >= consecutive_required:
            return raw, None, 0
        return previous.regime, raw, new_consecutive

    # New candidate distinct from previous pending → restart counter at 1.
    return previous.regime, raw, 1


def classify_with_hysteresis(
    asset: str,
    timestamp: str,
    inputs: ClassifierInputs,
    previous: RegimeSnapshot | None,
) -> RegimeSnapshot:
    """End-to-end classification: raw threshold → hysteresis → snapshot."""
    raw = classify_raw(inputs)
    committed, pending, pending_n = apply_hysteresis(raw, previous)
    return RegimeSnapshot(
        asset=asset,
        timestamp=timestamp,
        regime=committed,
        vol_class=inputs.vol_class,
        confidence=1.0,
        adx=inputs.adx,
        plus_di=inputs.plus_di,
        minus_di=inputs.minus_di,
        rv_24h=inputs.rv_24h,
        atr_zscore=inputs.atr_zscore,
        pending_regime=pending,
        pending_consecutive=pending_n,
    )
