"""Portfolio Optimizer — typed inputs, outputs, configuration.

The optimizer is the *strategic allocation* layer. It sits above:
- `app/risk/volatility.py::VolatilityEngine`     (per-asset vol regime)
- `app/risk/portfolio_risk.py::PortfolioRiskEngine` (portfolio-level risk)

…and below:
- `app/risk/engine.py::RiskEngine`               (per-order pre-trade gate)

Workflow: regime + vol + signal-quality → optimizer → target weights →
rebalance plan → per-trade orders (gated by RiskEngine).

All models are frozen / immutable, mirroring the rest of `app/risk/`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

# --- Optimization methods (string constants for stable JSON output) ---

METHOD_EQUAL_WEIGHT: Final[str] = "equal_weight"
METHOD_MIN_VARIANCE: Final[str] = "min_variance"
METHOD_MAX_SHARPE: Final[str] = "max_sharpe"
METHOD_MAX_SORTINO: Final[str] = "max_sortino"
METHOD_RISK_PARITY: Final[str] = "risk_parity"
METHOD_HRP: Final[str] = "hierarchical_risk_parity"

ALL_METHODS: Final[tuple[str, ...]] = (
    METHOD_EQUAL_WEIGHT,
    METHOD_MIN_VARIANCE,
    METHOD_MAX_SHARPE,
    METHOD_MAX_SORTINO,
    METHOD_RISK_PARITY,
    METHOD_HRP,
)

# --- Action constants for AssetAllocation ---
ACTION_BUY: Final[str] = "buy"
ACTION_SELL: Final[str] = "sell"
ACTION_HOLD: Final[str] = "hold"

# --- Common stablecoin tickers (lowercased) ---
_DEFAULT_STABLECOINS: Final[tuple[str, ...]] = (
    "usdt",
    "usdc",
    "busd",
    "tusd",
    "dai",
    "fdusd",
)


@dataclass(frozen=True)
class Asset:
    """Universe asset + meta used by the optimizer.

    `signal_quality` is an operator/strategy-supplied 0..1 score that tilts
    expected returns: higher score → higher μ. `expected_return_override`
    bypasses both historical-mean and signal-quality tilt when set.
    """

    symbol: str
    liquidity_score: float = 1.0
    funding_cost_pct_daily: float = 0.0  # decimal; e.g. 0.0001 = 1 bp / day
    exchange: str = "binance"
    quote_currency: str = "USDT"
    signal_quality: float = 0.5
    is_stablecoin: bool = False
    expected_return_override: float | None = None  # daily decimal


@dataclass(frozen=True)
class OptimizationConfig:
    """All thresholds explicit and audit-friendly.

    Keep numbers in whatever units the engine expects:
    - Returns are daily decimals (0.01 = 1 % daily) by default.
    - `target_volatility_annual` is annualized fraction (0.30 = 30 % annual).
    - Drawdown / weight / drift are *fractions* in [0, 1] except where
      explicitly named with `_pct` (then percent in [0, 100]).
    """

    # Risk-free + return tilt
    risk_free_rate_daily: float = 0.0
    signal_quality_tilt_strength: float = 0.5  # 0 = ignore signal_quality

    # Vol / DD targets
    target_volatility_annual: float = 0.30
    enforce_vol_target: bool = True
    max_drawdown_constraint_pct: float = 25.0
    enforce_max_drawdown: bool = True

    # Per-asset weight bounds
    max_weight_per_asset: float = 0.40
    min_weight_per_asset: float = 0.0
    long_only: bool = True

    # Leverage
    max_leverage: float = 2.0

    # Concentration caps
    max_exchange_concentration: float = 0.50
    stablecoin_floor_in_crisis: float = 0.50

    # Rebalance
    rebalance_drift_threshold: float = 0.05  # max-asset-drift trigger
    rebalance_total_drift_threshold: float = 0.15  # Σ|drift| trigger
    min_trade_size_pct: float = 0.005  # ignore < 0.5 % trades

    # Method selection
    default_method: str = METHOD_RISK_PARITY
    regime_method_map: dict[str, str] = field(
        default_factory=lambda: {
            "low_vol": METHOD_MAX_SHARPE,
            "normal": METHOD_MAX_SHARPE,
            "elevated": METHOD_RISK_PARITY,
            "high_vol": METHOD_MIN_VARIANCE,
            "crisis": METHOD_EQUAL_WEIGHT,
            "unknown": METHOD_RISK_PARITY,
        }
    )

    # Annualization (crypto default: 24/7 trading)
    annualization_factor: int = 365
    mar_for_sortino_daily: float = 0.0

    # Numerical
    cov_regularization_eps: float = 1e-6
    risk_parity_max_iter: int = 200
    risk_parity_tol: float = 1e-6
    optimizer_max_iter: int = 200

    # Crypto adjustments
    apply_funding_cost: bool = True
    funding_cost_horizon_bars: int = 30

    # Stablecoin universe
    stablecoin_set: tuple[str, ...] = _DEFAULT_STABLECOINS

    def __post_init__(self) -> None:
        if not (0.0 <= self.max_weight_per_asset <= 1.0):
            raise ValueError("max_weight_per_asset must be in [0, 1]")
        if not (0.0 <= self.min_weight_per_asset <= self.max_weight_per_asset):
            raise ValueError("min_weight_per_asset must be in [0, max_weight_per_asset]")
        if self.max_leverage < 1.0:
            raise ValueError("max_leverage must be >= 1.0 (1.0 = unleveraged)")
        if self.target_volatility_annual <= 0.0:
            raise ValueError("target_volatility_annual must be > 0")
        if self.default_method not in ALL_METHODS:
            raise ValueError(f"default_method must be one of {ALL_METHODS}")


@dataclass(frozen=True)
class AssetAllocation:
    """Per-asset target allocation + diff vs. current portfolio."""

    symbol: str
    target_weight_pct: float  # 0..100, signed if long-short enabled
    current_weight_pct: float
    drift_pct: float  # target − current, in pct points
    risk_contribution_pct: float  # 0..100 — share of portfolio variance
    expected_return_annual: float | None
    expected_volatility_annual: float | None
    funding_adjusted_return_annual: float | None
    action: str  # ACTION_BUY / SELL / HOLD
    trade_size_usd: float  # signed
    trade_size_pct: float  # signed, % of portfolio
    liquidity_capped: bool = False
    notes: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PortfolioAllocation:
    """Output of the optimizer — the dynamic target portfolio."""

    timestamp_utc: str
    method_used: str
    regime: str | None
    target_volatility_annual: float
    portfolio_value_usd: float

    # Expected portfolio metrics (forward-looking)
    expected_return_annual: float | None
    expected_volatility_annual: float | None
    expected_sharpe: float | None
    expected_sortino: float | None
    expected_max_drawdown_pct: float | None  # historical-replay estimate

    # Exposure
    gross_leverage: float
    net_exposure_pct: float
    cash_pct: float
    stablecoin_exposure_pct: float
    n_active_positions: int

    # Per-asset
    allocations: list[AssetAllocation] = field(default_factory=list)

    # Rebalancing
    rebalance_required: bool = False
    max_drift_pct: float = 0.0
    turnover_pct: float = 0.0
    estimated_turnover_usd: float = 0.0

    # Diagnostics
    constraints_active: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: dict[str, object] = field(default_factory=dict)
    inputs_hash: str = ""

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "portfolio_allocation",
            "timestamp_utc": self.timestamp_utc,
            "method_used": self.method_used,
            "regime": self.regime,
            "target_volatility_annual": self.target_volatility_annual,
            "portfolio_value_usd": self.portfolio_value_usd,
            "expected": {
                "return_annual": self.expected_return_annual,
                "volatility_annual": self.expected_volatility_annual,
                "sharpe": self.expected_sharpe,
                "sortino": self.expected_sortino,
                "max_drawdown_pct": self.expected_max_drawdown_pct,
            },
            "exposure": {
                "gross_leverage": self.gross_leverage,
                "net_exposure_pct": self.net_exposure_pct,
                "cash_pct": self.cash_pct,
                "stablecoin_exposure_pct": self.stablecoin_exposure_pct,
                "n_active_positions": self.n_active_positions,
            },
            "rebalance": {
                "required": self.rebalance_required,
                "max_drift_pct": self.max_drift_pct,
                "turnover_pct": self.turnover_pct,
                "estimated_turnover_usd": self.estimated_turnover_usd,
            },
            "allocations": [
                {
                    "symbol": a.symbol,
                    "target_weight_pct": a.target_weight_pct,
                    "current_weight_pct": a.current_weight_pct,
                    "drift_pct": a.drift_pct,
                    "risk_contribution_pct": a.risk_contribution_pct,
                    "expected_return_annual": a.expected_return_annual,
                    "expected_volatility_annual": a.expected_volatility_annual,
                    "funding_adjusted_return_annual": a.funding_adjusted_return_annual,
                    "action": a.action,
                    "trade_size_usd": a.trade_size_usd,
                    "trade_size_pct": a.trade_size_pct,
                    "liquidity_capped": a.liquidity_capped,
                    "notes": dict(a.notes),
                }
                for a in self.allocations
            ],
            "constraints_active": list(self.constraints_active),
            "warnings": list(self.warnings),
            "notes": dict(self.notes),
            "inputs_hash": self.inputs_hash,
        }
