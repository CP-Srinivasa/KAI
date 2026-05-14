"""Volatility Engine — typed outputs and config.

All models are frozen / immutable, mirroring the Risk Engine style so outputs
are auditable and safe to ship across module boundaries.

The engine is a *recommender*: it produces volatility measurements and
trading parameter suggestions. The hard gate remains the existing
`app/risk/engine.py::RiskEngine`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

# --- Regime constants (string enum-style for stable JSON output) ---

REGIME_LOW: Final[str] = "low_vol"
REGIME_NORMAL: Final[str] = "normal"
REGIME_ELEVATED: Final[str] = "elevated"
REGIME_HIGH: Final[str] = "high_vol"
REGIME_CRISIS: Final[str] = "crisis"
REGIME_UNKNOWN: Final[str] = "unknown"

ALL_REGIMES: Final[tuple[str, ...]] = (
    REGIME_LOW,
    REGIME_NORMAL,
    REGIME_ELEVATED,
    REGIME_HIGH,
    REGIME_CRISIS,
    REGIME_UNKNOWN,
)

CLUSTER_NONE: Final[str] = "no_cluster"
CLUSTER_WEAK: Final[str] = "weak_cluster"
CLUSTER_MODERATE: Final[str] = "clustered"
CLUSTER_STRONG: Final[str] = "strong_cluster"


@dataclass(frozen=True)
class VolatilityConfig:
    """Engine configuration. All thresholds are explicit and audit-friendly."""

    # Rolling windows
    hv_window: int = 30  # bars used for historical volatility
    rv_window: int = 30  # bars used for realized volatility
    intraday_window: int = 30  # bars for Garman-Klass / Parkinson
    baseline_window: int = 90  # long-term baseline used for regime ratio
    cluster_window: int = 30  # bars for ρ₁(r²) clustering

    # ATR
    atr_period: int = 14

    # EWMA (RiskMetrics-style)
    ewma_lambda: float = 0.94  # daily decay
    ewma_lambda_intraday: float = 0.97  # intraday decay

    # Regime thresholds (current σ / baseline σ)
    regime_threshold_low: float = 0.6
    regime_threshold_elevated: float = 1.2
    regime_threshold_high: float = 2.0
    regime_threshold_crisis: float = 3.5

    # Clustering thresholds for ρ₁(r²)
    cluster_threshold_weak: float = 0.05
    cluster_threshold_moderate: float = 0.20
    cluster_threshold_strong: float = 0.40

    # Regime → leverage caps
    leverage_low: float = 5.0
    leverage_normal: float = 3.0
    leverage_elevated: float = 2.0
    leverage_high: float = 1.0
    leverage_crisis: float = 0.0

    # Regime → max position size cap (% of equity)
    max_position_low_pct: float = 25.0
    max_position_normal_pct: float = 15.0
    max_position_elevated_pct: float = 10.0
    max_position_high_pct: float = 5.0
    max_position_crisis_pct: float = 0.0

    # Stop distance: ATR multiplier per regime
    stop_atr_mult_low: float = 1.5
    stop_atr_mult_normal: float = 2.0
    stop_atr_mult_elevated: float = 2.5
    stop_atr_mult_high: float = 3.0
    stop_atr_mult_crisis: float = 4.0

    # Liquidity
    liquidity_volume_floor_usd: float = 1_000_000.0  # below = penalty
    liquidity_spread_ceiling_pct: float = 0.5  # above = penalty
    liquidity_penalty_alpha: float = 0.5  # LAV = HV * (1 + α * illiquidity)

    # Annualization
    bars_per_year_default: int = 365  # crypto trading is 24/7
    risk_per_trade_pct_default: float = 0.5  # for max-position calc fallback

    def __post_init__(self) -> None:
        if self.hv_window < 5:
            raise ValueError("hv_window must be >= 5")
        if not (0.0 < self.ewma_lambda < 1.0):
            raise ValueError("ewma_lambda must be in (0, 1)")
        if not (
            self.regime_threshold_low
            < self.regime_threshold_elevated
            < self.regime_threshold_high
            < self.regime_threshold_crisis
        ):
            raise ValueError("regime thresholds must be strictly increasing")


@dataclass(frozen=True)
class VolatilityRegimeOutput:
    """Single, complete volatility assessment for an instrument."""

    # Identity
    symbol: str
    timestamp_utc: str
    timeframe: str

    # Raw measurements (annualized fractions, e.g. 0.65 = 65% annual vol)
    atr: float | None
    historical_volatility: float | None
    realized_volatility: float | None
    intraday_volatility: float | None  # Garman-Klass
    parkinson_volatility: float | None
    ewma_volatility: float | None

    # Clustering
    clustering_score: float  # ρ₁(r²) ∈ [-1, 1]
    clustering_label: str  # CLUSTER_*

    # Regime classification
    volatility_regime: str  # REGIME_*
    regime_ratio: float | None  # current_vol / baseline_vol
    regime_confidence: float  # 0..1

    # Liquidity adjustment
    liquidity_score: float | None  # 0..1 — higher = more liquid
    liquidity_adjusted_volatility: float | None

    # Trading recommendations
    expected_move_pct_1bar: float | None  # ±% on next bar (1σ)
    expected_move_pct_1d: float | None  # ±% over next 24h (1σ)
    stop_distance_pct: float | None  # recommended SL distance %
    leverage_recommendation: float  # 0..max_leverage_cap
    max_position_size_pct: float  # % of equity
    liquidation_risk_score: float  # 0..1 — higher = riskier

    # Diagnostics & audit
    sample_size: int
    inputs_hash: str
    warnings: list[str] = field(default_factory=list)
    notes: dict[str, object] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "volatility_regime",
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "timeframe": self.timeframe,
            "atr": self.atr,
            "historical_volatility": self.historical_volatility,
            "realized_volatility": self.realized_volatility,
            "intraday_volatility": self.intraday_volatility,
            "parkinson_volatility": self.parkinson_volatility,
            "ewma_volatility": self.ewma_volatility,
            "clustering_score": self.clustering_score,
            "clustering_label": self.clustering_label,
            "volatility_regime": self.volatility_regime,
            "regime_ratio": self.regime_ratio,
            "regime_confidence": self.regime_confidence,
            "liquidity_score": self.liquidity_score,
            "liquidity_adjusted_volatility": self.liquidity_adjusted_volatility,
            "expected_move_pct_1bar": self.expected_move_pct_1bar,
            "expected_move_pct_1d": self.expected_move_pct_1d,
            "stop_distance_pct": self.stop_distance_pct,
            "leverage_recommendation": self.leverage_recommendation,
            "max_position_size_pct": self.max_position_size_pct,
            "liquidation_risk_score": self.liquidation_risk_score,
            "sample_size": self.sample_size,
            "inputs_hash": self.inputs_hash,
            "warnings": list(self.warnings),
            "notes": dict(self.notes),
        }
