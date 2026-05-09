"""Regime layer typed models.

R1 covers six discriminative classes derivable from price-history alone:
    trend_up / trend_down       — sustained directional move (ADX >= 30)
    breakout_up / breakout_down — emerging move with volatility anomaly
                                  (ADX 25-30 plus ATR z-score >= 1)
    chop_quiet / chop_volatile  — low-trend (ADX < 25) split by 30d
                                  realized-vol percentile
    unknown                     — missing indicator data

panic / euphoria / low_liquidity / high_manipulation / macro_risk_off
require funding rates, stablecoin flows, orderbook imbalance — separate
data-source acquisition sprints in R5+.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.analysis.indicators.realized_volatility import VolClass


class RegimeClass(StrEnum):
    """Six discriminative regime classes for R1, plus ``unknown`` sentinel."""

    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    BREAKOUT_UP = "breakout_up"
    BREAKOUT_DOWN = "breakout_down"
    CHOP_QUIET = "chop_quiet"
    CHOP_VOLATILE = "chop_volatile"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RegimeSnapshot:
    """A single regime classification at one timestamp for one asset.

    Stored append-only as JSONL line, one snapshot per asset per hour.
    Indicator values are persisted alongside the regime label so a future
    audit can reproduce the decision without re-running the pipeline.

    Hysteresis fields (``pending_regime``, ``pending_consecutive``) carry
    forward the candidate-class state between bars: when the raw classifier
    output differs from the committed regime, the candidate is parked here
    until it persists for ``HYSTERESIS_BARS`` consecutive bars.
    """

    asset: str
    timestamp: str  # ISO-8601 UTC, hour-truncated
    regime: RegimeClass
    vol_class: VolClass
    confidence: float  # 0-1; set to 1.0 in R1 (deterministic threshold model)

    adx: float | None = None
    plus_di: float | None = None
    minus_di: float | None = None
    rv_24h: float | None = None
    atr_zscore: float | None = None

    pending_regime: RegimeClass | None = None
    pending_consecutive: int = 0

    def to_json_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "asset": self.asset,
            "timestamp": self.timestamp,
            "regime": str(self.regime),
            "vol_class": self.vol_class,
            "confidence": self.confidence,
        }
        for key, value in (
            ("adx", self.adx),
            ("plus_di", self.plus_di),
            ("minus_di", self.minus_di),
            ("rv_24h", self.rv_24h),
            ("atr_zscore", self.atr_zscore),
        ):
            if value is not None:
                d[key] = value
        if self.pending_regime is not None:
            d["pending_regime"] = str(self.pending_regime)
            d["pending_consecutive"] = self.pending_consecutive
        return d
