"""Portfolio Risk Engine — typed inputs, outputs, configuration.

All models are frozen / immutable, mirroring the rest of `app/risk/`.

The PortfolioRiskEngine is a *portfolio-level analytics* engine. It complements
- `app/risk/engine.py::RiskEngine`        (per-order pre-trade gate, hard)
- `app/risk/volatility.py::VolatilityEngine` (per-instrument vol regime)

This engine produces VaR, ES, tail-risk, drawdown distribution, correlation
stress and crypto-specific stress impact at the portfolio level, plus
per-position attribution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

# Stress-scenario name constants (stable JSON keys)
STRESS_FLASH_CRASH: Final[str] = "flash_crash"
STRESS_LIQUIDATION_CASCADE: Final[str] = "liquidation_cascade"
STRESS_STABLECOIN_DEPEG: Final[str] = "stablecoin_depeg"
STRESS_EXCHANGE_INSOLVENCY: Final[str] = "exchange_insolvency"
STRESS_EXTREME_VOLATILITY: Final[str] = "extreme_volatility"
STRESS_CORRELATION_BREAKDOWN: Final[str] = "correlation_breakdown"

ALL_STRESS_SCENARIOS: Final[tuple[str, ...]] = (
    STRESS_FLASH_CRASH,
    STRESS_LIQUIDATION_CASCADE,
    STRESS_STABLECOIN_DEPEG,
    STRESS_EXCHANGE_INSOLVENCY,
    STRESS_EXTREME_VOLATILITY,
    STRESS_CORRELATION_BREAKDOWN,
)

# Common stablecoins (lowercased for matching)
_DEFAULT_STABLECOINS: Final[tuple[str, ...]] = (
    "usdt", "usdc", "busd", "tusd", "dai", "fdusd",
)

# Default per-exchange insolvency haircut (loss fraction in failure event).
# Calibrated qualitatively from public cases (FTX, Mt.Gox, Celsius, etc.).
# Operators should override these with their own desk-policy values.
_DEFAULT_EXCHANGE_HAIRCUT: Final[dict[str, float]] = {
    "binance": 0.20,
    "coinbase": 0.15,
    "kraken": 0.15,
    "okx": 0.30,
    "bybit": 0.30,
    "bitmex": 0.40,
    "kucoin": 0.40,
    "gate": 0.45,
    "huobi": 0.45,
    "mexc": 0.50,
    "unknown": 0.70,
}


@dataclass(frozen=True)
class Position:
    """A single portfolio position.

    notional_usd: signed dollar notional.
        +1000 = long $1000 of the asset
        -1000 = short $1000 of the asset
    leverage: 1.0 = spot/unleveraged, 5.0 = 5× leverage
    quote_currency: counter-asset (e.g. "USDT" for BTC/USDT)
    exchange: where the position sits (used for insolvency stress)
    liquidity_score: 0..1 — higher = more liquid (used for crash amplification)
    """

    symbol: str
    notional_usd: float
    leverage: float = 1.0
    quote_currency: str = "USDT"
    exchange: str = "binance"
    liquidity_score: float = 1.0


@dataclass(frozen=True)
class PortfolioRiskConfig:
    """Engine configuration. All numbers are explicit and audit-friendly."""

    # VaR / ES
    confidence_level: float = 0.95
    horizon_bars: int = 1
    annualization_factor: int = 365  # bars per year for daily-bar default

    # Heavy-tailed parametric VaR (crypto default ν=4 — fat tails)
    student_t_df: float = 4.0

    # Monte Carlo
    n_monte_carlo: int = 10_000
    mc_seed: int = 42
    mc_use_student_t: bool = True  # crypto default — heavy-tailed innovations

    # Tail risk
    hill_estimator_k_pct: float = 0.10  # use top 10 % of tail
    tail_prob_threshold_sigma: float = 3.0  # P(loss > 3σ)

    # Drawdown
    drawdown_quantile: float = 0.95  # report the p95 drawdown

    # Crypto stress
    flash_crash_pct: float = 0.25
    flash_crash_illiquid_amplifier: float = 2.0
    liquidation_cascade_threshold_leverage: float = 2.0
    liquidation_cascade_slippage_pct: float = 0.05  # additional skid loss
    stablecoin_depeg_pct: float = 0.15
    extreme_volatility_multiplier: float = 5.0
    correlation_stress_target: float = 0.95
    exchange_haircut: dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_EXCHANGE_HAIRCUT)
    )
    stablecoin_set: tuple[str, ...] = _DEFAULT_STABLECOINS

    # Numerical
    cholesky_jitter: float = 1e-10
    min_returns_for_var: int = 30
    min_returns_for_baseline_corr: int = 60

    def __post_init__(self) -> None:
        if not (0.5 <= self.confidence_level < 1.0):
            raise ValueError("confidence_level must be in [0.5, 1)")
        if self.horizon_bars < 1:
            raise ValueError("horizon_bars must be >= 1")
        if self.n_monte_carlo < 100:
            raise ValueError("n_monte_carlo must be >= 100")
        if self.student_t_df <= 2.0:
            raise ValueError("student_t_df must be > 2 (variance undefined)")


@dataclass(frozen=True)
class PositionRisk:
    """Per-position risk attribution."""

    symbol: str
    notional_usd: float
    weight_pct: float                  # |notional| / gross_exposure × 100
    risk_budget_pct: float             # component VaR / portfolio VaR × 100
    expected_downside_usd: float       # standalone position VaR
    tail_exposure_usd: float           # contribution to ES (MC-attributed)
    stress_exposure_usd: float         # max loss across stress scenarios
    stress_breakdown: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PortfolioRiskReport:
    """Complete portfolio risk assessment."""

    # Identity
    timestamp_utc: str
    portfolio_value_usd: float          # gross exposure
    gross_exposure_usd: float
    net_exposure_usd: float
    confidence_level: float
    horizon_bars: int

    # VaR (all in USD, positive numbers = potential loss)
    historical_var: float | None
    parametric_var: float | None
    cornish_fisher_var: float | None
    student_t_var: float | None
    monte_carlo_var: float | None

    # Expected Shortfall (USD, positive = loss)
    historical_es: float | None
    parametric_es: float | None
    monte_carlo_es: float | None

    # Tail risk
    portfolio_skew: float | None
    portfolio_excess_kurtosis: float | None
    tail_index: float | None             # Hill-estimator α; smaller = fatter
    tail_prob_threshold_sigma: float
    tail_prob_exceedance: float | None   # empirical P(loss > k·σ)

    # Drawdown distribution
    max_drawdown_pct: float | None
    drawdown_p95_pct: float | None
    avg_drawdown_pct: float | None
    avg_recovery_bars: float | None
    drawdown_count: int

    # Correlation stress
    avg_pairwise_correlation: float | None
    correlation_stress_var: float | None

    # Crypto stress (USD loss per scenario, positive = loss)
    stress_scenarios: dict[str, float] = field(default_factory=dict)
    worst_case_stress_usd: float = 0.0
    worst_case_stress_name: str = ""

    # Per-position attribution
    positions: list[PositionRisk] = field(default_factory=list)

    # Diagnostics & audit
    sample_size: int = 0
    inputs_hash: str = ""
    warnings: list[str] = field(default_factory=list)
    notes: dict[str, object] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "portfolio_risk",
            "timestamp_utc": self.timestamp_utc,
            "portfolio_value_usd": self.portfolio_value_usd,
            "gross_exposure_usd": self.gross_exposure_usd,
            "net_exposure_usd": self.net_exposure_usd,
            "confidence_level": self.confidence_level,
            "horizon_bars": self.horizon_bars,
            "var": {
                "historical": self.historical_var,
                "parametric": self.parametric_var,
                "cornish_fisher": self.cornish_fisher_var,
                "student_t": self.student_t_var,
                "monte_carlo": self.monte_carlo_var,
            },
            "expected_shortfall": {
                "historical": self.historical_es,
                "parametric": self.parametric_es,
                "monte_carlo": self.monte_carlo_es,
            },
            "tail": {
                "skew": self.portfolio_skew,
                "excess_kurtosis": self.portfolio_excess_kurtosis,
                "tail_index": self.tail_index,
                "prob_exceedance": self.tail_prob_exceedance,
                "sigma_threshold": self.tail_prob_threshold_sigma,
            },
            "drawdown": {
                "max_pct": self.max_drawdown_pct,
                "p95_pct": self.drawdown_p95_pct,
                "avg_pct": self.avg_drawdown_pct,
                "avg_recovery_bars": self.avg_recovery_bars,
                "count": self.drawdown_count,
            },
            "correlation": {
                "avg": self.avg_pairwise_correlation,
                "stress_var": self.correlation_stress_var,
            },
            "stress_scenarios": dict(self.stress_scenarios),
            "worst_case_stress_usd": self.worst_case_stress_usd,
            "worst_case_stress_name": self.worst_case_stress_name,
            "positions": [
                {
                    "symbol": p.symbol,
                    "notional_usd": p.notional_usd,
                    "weight_pct": p.weight_pct,
                    "risk_budget_pct": p.risk_budget_pct,
                    "expected_downside_usd": p.expected_downside_usd,
                    "tail_exposure_usd": p.tail_exposure_usd,
                    "stress_exposure_usd": p.stress_exposure_usd,
                    "stress_breakdown": dict(p.stress_breakdown),
                }
                for p in self.positions
            ],
            "sample_size": self.sample_size,
            "inputs_hash": self.inputs_hash,
            "warnings": list(self.warnings),
            "notes": dict(self.notes),
        }
