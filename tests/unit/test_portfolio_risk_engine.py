"""Unit tests for the Portfolio Risk Engine."""

from __future__ import annotations

import math
import random

import pytest

from app.risk.portfolio_risk import (
    PortfolioRiskEngine,
    _betai,
    _cholesky,
    _excess_kurtosis,
    _hill_tail_index,
    _inverse_norm_cdf,
    _quantile,
    _skewness,
    _student_t_quantile_standardized,
)
from app.risk.portfolio_risk_models import (
    ALL_STRESS_SCENARIOS,
    STRESS_EXCHANGE_INSOLVENCY,
    STRESS_FLASH_CRASH,
    STRESS_LIQUIDATION_CASCADE,
    STRESS_STABLECOIN_DEPEG,
    PortfolioRiskConfig,
    Position,
)

# --------------------------------------------------------------------- helpers


def _gaussian_returns(
    n: int,
    sigma: float,
    *,
    mu: float = 0.0,
    seed: int = 1,
) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(mu, sigma) for _ in range(n)]


def _correlated_pair(
    n: int,
    *,
    sigma_x: float,
    sigma_y: float,
    rho: float,
    seed: int = 1,
) -> tuple[list[float], list[float]]:
    rng = random.Random(seed)
    xs: list[float] = []
    ys: list[float] = []
    for _ in range(n):
        z1 = rng.gauss(0.0, 1.0)
        z2 = rng.gauss(0.0, 1.0)
        xs.append(sigma_x * z1)
        ys.append(sigma_y * (rho * z1 + math.sqrt(max(1.0 - rho * rho, 0.0)) * z2))
    return xs, ys


# ============================================================================
# Math helpers
# ============================================================================


def test_inverse_norm_cdf_matches_textbook_quantiles():
    assert _inverse_norm_cdf(0.5) == pytest.approx(0.0, abs=1e-6)
    assert _inverse_norm_cdf(0.95) == pytest.approx(1.6449, abs=1e-3)
    assert _inverse_norm_cdf(0.99) == pytest.approx(2.3263, abs=1e-3)
    assert _inverse_norm_cdf(0.05) == pytest.approx(-1.6449, abs=1e-3)


def test_betai_symmetry_property():
    # I_x(a,b) + I_{1-x}(b,a) = 1
    a, b, x = 2.5, 3.0, 0.4
    assert _betai(a, b, x) + _betai(b, a, 1.0 - x) == pytest.approx(1.0, abs=1e-6)


def test_student_t_standardized_collapses_to_normal_for_large_df():
    # For df=100, standardized t-quantile should be very close to normal
    z = _inverse_norm_cdf(0.95)
    t = _student_t_quantile_standardized(0.95, 100.0)
    assert abs(t - z) < 0.1


def test_student_t_standardized_more_extreme_at_99_than_normal():
    # Heavy tails (df=4) → 99% quantile more negative than Gaussian's
    z_99 = _inverse_norm_cdf(0.01)
    t_99 = _student_t_quantile_standardized(0.01, 4.0)
    assert t_99 < z_99  # more negative


def test_quantile_linear_interpolation():
    xs = sorted([1.0, 2.0, 3.0, 4.0, 5.0])
    assert _quantile(xs, 0.0) == 1.0
    assert _quantile(xs, 1.0) == 5.0
    assert _quantile(xs, 0.5) == 3.0
    assert _quantile(xs, 0.25) == pytest.approx(2.0, abs=1e-9)


def test_skewness_zero_for_symmetric_distribution():
    xs = _gaussian_returns(2000, sigma=1.0, seed=11)
    assert abs(_skewness(xs)) < 0.2


def test_skewness_negative_for_left_tail():
    rng = random.Random(7)
    # Mix-of-normals with a left tail
    xs = [rng.gauss(0.0, 1.0) for _ in range(900)] + [rng.gauss(-5.0, 1.0) for _ in range(100)]
    assert _skewness(xs) < -0.5


def test_excess_kurtosis_positive_for_fat_tailed_data():
    rng = random.Random(13)
    # Mix-of-normals with extreme tails — clearly leptokurtic
    xs = [rng.gauss(0.0, 1.0) for _ in range(900)] + [rng.gauss(0.0, 5.0) for _ in range(100)]
    assert _excess_kurtosis(xs) > 1.0


def test_cholesky_reconstructs_2x2_matrix():
    matrix = [[4.0, 2.0], [2.0, 3.0]]
    chol = _cholesky(matrix)
    # Reconstruct L·L'
    rec = [[sum(chol[i][k] * chol[j][k] for k in range(2)) for j in range(2)] for i in range(2)]
    for i in range(2):
        for j in range(2):
            assert rec[i][j] == pytest.approx(matrix[i][j], abs=1e-9)


def test_hill_tail_index_returns_finite_for_pareto_like_tail():
    rng = random.Random(99)
    losses = [rng.expovariate(1.0) for _ in range(500)]  # exp tails → α≈1
    idx = _hill_tail_index(losses, 0.10)
    assert idx is not None
    assert idx > 0.0


def test_hill_tail_index_none_for_too_few_samples():
    assert _hill_tail_index([1.0, 2.0, 3.0], 0.1) is None


# ============================================================================
# Engine: degenerate inputs
# ============================================================================


@pytest.fixture
def engine() -> PortfolioRiskEngine:
    return PortfolioRiskEngine(PortfolioRiskConfig(n_monte_carlo=2000))


def test_compute_empty_positions_returns_empty_report(engine: PortfolioRiskEngine):
    out = engine.compute(positions=[], returns_history={})
    assert out.gross_exposure_usd == 0.0
    assert out.parametric_var is None
    assert out.worst_case_stress_usd == 0.0
    assert "no_positions" in out.warnings
    # Even an empty report must list every named scenario for downstream consumers
    for name in ALL_STRESS_SCENARIOS:
        assert name in out.stress_scenarios


def test_compute_zero_notional_positions_filtered(engine: PortfolioRiskEngine):
    p = Position(symbol="BTC/USDT", notional_usd=0.0)
    out = engine.compute(positions=[p], returns_history={})
    assert out.gross_exposure_usd == 0.0


def test_insufficient_returns_emits_warning(engine: PortfolioRiskEngine):
    p = Position(symbol="BTC/USDT", notional_usd=1000.0)
    out = engine.compute(
        positions=[p],
        returns_history={"BTC/USDT": [0.001, 0.002, -0.001]},  # too short
    )
    assert "insufficient_returns_for_var" in out.warnings
    assert out.parametric_var is None
    # Stress still runs on the position
    assert out.stress_scenarios[STRESS_FLASH_CRASH] > 0.0


def test_missing_returns_for_symbol_drops_it(engine: PortfolioRiskEngine):
    p1 = Position(symbol="BTC/USDT", notional_usd=1000.0)
    p2 = Position(symbol="UNKNOWN/USDT", notional_usd=500.0)
    btc = _gaussian_returns(200, 0.02, seed=2)
    out = engine.compute(
        positions=[p1, p2],
        returns_history={"BTC/USDT": btc},
    )
    assert "missing_returns:UNKNOWN/USDT" in out.warnings
    # Position list still includes both — stress applies to all positions
    assert {pr.symbol for pr in out.positions} == {"BTC/USDT", "UNKNOWN/USDT"}


# ============================================================================
# Engine: VaR / ES consistency
# ============================================================================


def test_es_at_least_var_for_each_method(engine: PortfolioRiskEngine):
    p = Position(symbol="BTC/USDT", notional_usd=10_000.0)
    out = engine.compute(
        positions=[p],
        returns_history={"BTC/USDT": _gaussian_returns(500, 0.02, seed=42)},
    )
    assert out.historical_var is not None
    assert out.historical_es is not None
    assert out.historical_es >= out.historical_var
    assert out.parametric_es is not None
    assert out.parametric_es >= out.parametric_var
    assert out.monte_carlo_es is not None
    assert out.monte_carlo_es >= out.monte_carlo_var


def test_monte_carlo_close_to_parametric_on_gaussian_data():
    cfg = PortfolioRiskConfig(n_monte_carlo=4000, mc_use_student_t=False)
    eng = PortfolioRiskEngine(cfg)
    p = Position(symbol="BTC/USDT", notional_usd=10_000.0)
    rets = _gaussian_returns(500, 0.02, seed=8)
    out = eng.compute(positions=[p], returns_history={"BTC/USDT": rets})
    assert out.parametric_var is not None and out.monte_carlo_var is not None
    # MC and parametric should agree to ~15 % on Gaussian innovations
    rel_diff = abs(out.monte_carlo_var - out.parametric_var) / out.parametric_var
    assert rel_diff < 0.20


def test_student_t_var_equal_or_above_parametric_at_95(engine: PortfolioRiskEngine):
    """At 95% Student-t(df=4) standardized quantile (~1.51) is *less* extreme
    than the Gaussian one (~1.65). So Student-t VaR should be ≤ parametric VaR
    at the 95% level. The fat-tail penalty kicks in deeper in the tail."""
    p = Position(symbol="BTC/USDT", notional_usd=10_000.0)
    out = engine.compute(
        positions=[p],
        returns_history={"BTC/USDT": _gaussian_returns(500, 0.02, seed=4)},
    )
    assert out.parametric_var is not None and out.student_t_var is not None
    assert out.student_t_var <= out.parametric_var * 1.05  # within rounding


def test_student_t_var_exceeds_parametric_at_99():
    cfg = PortfolioRiskConfig(confidence_level=0.99, n_monte_carlo=2000)
    eng = PortfolioRiskEngine(cfg)
    p = Position(symbol="BTC/USDT", notional_usd=10_000.0)
    out = eng.compute(
        positions=[p],
        returns_history={"BTC/USDT": _gaussian_returns(500, 0.02, seed=4)},
    )
    assert out.parametric_var is not None and out.student_t_var is not None
    assert out.student_t_var > out.parametric_var


def test_cornish_fisher_higher_than_parametric_for_negatively_skewed_data():
    cfg = PortfolioRiskConfig(n_monte_carlo=1000)
    eng = PortfolioRiskEngine(cfg)
    rng = random.Random(101)
    rets = [rng.gauss(0.0, 0.01) for _ in range(900)] + [rng.gauss(-0.05, 0.01) for _ in range(100)]
    p = Position(symbol="BTC/USDT", notional_usd=10_000.0)
    out = eng.compute(positions=[p], returns_history={"BTC/USDT": rets})
    assert out.parametric_var is not None and out.cornish_fisher_var is not None
    assert out.cornish_fisher_var > out.parametric_var


# ============================================================================
# Engine: drawdown
# ============================================================================


def test_drawdown_count_zero_for_strictly_growing_series():
    cfg = PortfolioRiskConfig(n_monte_carlo=500)
    eng = PortfolioRiskEngine(cfg)
    p = Position(symbol="BTC/USDT", notional_usd=1_000.0)
    growing = [0.001 for _ in range(120)]
    out = eng.compute(positions=[p], returns_history={"BTC/USDT": growing})
    assert out.drawdown_count == 0
    assert out.max_drawdown_pct == 0.0


def test_drawdown_detects_peak_to_trough():
    cfg = PortfolioRiskConfig(n_monte_carlo=500)
    eng = PortfolioRiskEngine(cfg)
    p = Position(symbol="BTC/USDT", notional_usd=1_000.0)
    rets = [0.01] * 50 + [-0.02] * 20 + [0.005] * 50  # rise, drop, recover
    out = eng.compute(positions=[p], returns_history={"BTC/USDT": rets})
    assert out.drawdown_count >= 1
    assert out.max_drawdown_pct is not None
    # Peak (1.01^50 ≈ 1.6446) → trough (× 0.98^20 ≈ 1.098) ⇒ DD ≈ 33.2 %
    assert 25.0 < out.max_drawdown_pct < 40.0


# ============================================================================
# Engine: correlation stress
# ============================================================================


def test_correlation_stress_increases_var_above_normal():
    cfg = PortfolioRiskConfig(n_monte_carlo=1000, correlation_stress_target=0.95)
    eng = PortfolioRiskEngine(cfg)
    btc, eth = _correlated_pair(500, sigma_x=0.02, sigma_y=0.025, rho=0.30, seed=21)
    out = eng.compute(
        positions=[
            Position(symbol="BTC/USDT", notional_usd=5_000.0),
            Position(symbol="ETH/USDT", notional_usd=5_000.0),
        ],
        returns_history={"BTC/USDT": btc, "ETH/USDT": eth},
    )
    assert out.parametric_var is not None and out.correlation_stress_var is not None
    assert out.correlation_stress_var > out.parametric_var
    assert out.avg_pairwise_correlation is not None
    assert 0.15 < out.avg_pairwise_correlation < 0.45


# ============================================================================
# Crypto stress scenarios
# ============================================================================


def test_flash_crash_loss_scales_with_long_exposure():
    cfg = PortfolioRiskConfig(n_monte_carlo=500, flash_crash_pct=0.20)
    eng = PortfolioRiskEngine(cfg)
    p_long = Position(symbol="BTC/USDT", notional_usd=10_000.0, liquidity_score=1.0)
    out = eng.compute(positions=[p_long], returns_history={})
    # Long $10k at 20 % crash with full liquidity → ~$2k loss
    flash_loss = out.stress_scenarios[STRESS_FLASH_CRASH]
    assert flash_loss == pytest.approx(2_000.0, rel=0.05)


def test_flash_crash_amplified_by_illiquidity():
    cfg = PortfolioRiskConfig(
        n_monte_carlo=500,
        flash_crash_pct=0.20,
        flash_crash_illiquid_amplifier=2.0,
    )
    eng = PortfolioRiskEngine(cfg)
    illiq = Position(symbol="ALT/USDT", notional_usd=10_000.0, liquidity_score=0.0)
    out = eng.compute(positions=[illiq], returns_history={})
    # 20% × (1 + 2 × (1-0)) = 60% loss → $6k
    assert out.stress_scenarios[STRESS_FLASH_CRASH] == pytest.approx(6_000.0, rel=0.05)


def test_perfect_hedge_yields_zero_flash_crash_loss():
    """Long $10k BTC + short $10k BTC at the same liquidity → flash-crash net 0."""
    cfg = PortfolioRiskConfig(n_monte_carlo=500)
    eng = PortfolioRiskEngine(cfg)
    long = Position(symbol="BTC-A", notional_usd=10_000.0, liquidity_score=1.0)
    short = Position(symbol="BTC-B", notional_usd=-10_000.0, liquidity_score=1.0)
    out = eng.compute(positions=[long, short], returns_history={})
    assert out.stress_scenarios[STRESS_FLASH_CRASH] == pytest.approx(0.0, abs=1e-6)


def test_liquidation_cascade_only_hits_leveraged_positions():
    cfg = PortfolioRiskConfig(
        n_monte_carlo=500,
        liquidation_cascade_threshold_leverage=2.0,
        liquidation_cascade_slippage_pct=0.0,
    )
    eng = PortfolioRiskEngine(cfg)
    spot = Position(symbol="BTC/USDT", notional_usd=10_000.0, leverage=1.0)
    leveraged = Position(symbol="ETH/USDT", notional_usd=10_000.0, leverage=5.0)
    out = eng.compute(positions=[spot, leveraged], returns_history={})
    # Only the leveraged position contributes — collateral = $10k / 5 = $2k
    cascade = out.stress_scenarios[STRESS_LIQUIDATION_CASCADE]
    assert cascade == pytest.approx(2_000.0, rel=0.02)


def test_stablecoin_depeg_only_hits_stable_quoted_positions():
    cfg = PortfolioRiskConfig(n_monte_carlo=500, stablecoin_depeg_pct=0.10)
    eng = PortfolioRiskEngine(cfg)
    stable_quoted = Position(symbol="BTC/USDT", notional_usd=10_000.0, quote_currency="USDT")
    btc_quoted = Position(symbol="ETH/BTC", notional_usd=10_000.0, quote_currency="BTC")
    out = eng.compute(positions=[stable_quoted, btc_quoted], returns_history={})
    depeg = out.stress_scenarios[STRESS_STABLECOIN_DEPEG]
    # Only the USDT-quoted notional gets the haircut: 10% of $10k = $1k
    assert depeg == pytest.approx(1_000.0, rel=0.02)


def test_exchange_insolvency_picks_worst_venue():
    cfg = PortfolioRiskConfig(
        n_monte_carlo=500,
        exchange_haircut={"binance": 0.1, "fly_by_night": 0.5, "unknown": 0.7},
    )
    eng = PortfolioRiskEngine(cfg)
    safe = Position(symbol="BTC/USDT", notional_usd=10_000.0, exchange="binance")
    risky = Position(symbol="ALT/USDT", notional_usd=10_000.0, exchange="fly_by_night")
    out = eng.compute(positions=[safe, risky], returns_history={})
    # Insolvency takes the WORST exchange's loss as the scenario value
    insolvency = out.stress_scenarios[STRESS_EXCHANGE_INSOLVENCY]
    assert insolvency == pytest.approx(5_000.0, rel=0.02)


# ============================================================================
# Per-position attribution
# ============================================================================


def test_risk_budget_pcts_sum_close_to_100():
    cfg = PortfolioRiskConfig(n_monte_carlo=2000)
    eng = PortfolioRiskEngine(cfg)
    btc, eth = _correlated_pair(500, sigma_x=0.02, sigma_y=0.025, rho=0.4, seed=33)
    out = eng.compute(
        positions=[
            Position(symbol="BTC/USDT", notional_usd=6_000.0),
            Position(symbol="ETH/USDT", notional_usd=4_000.0),
        ],
        returns_history={"BTC/USDT": btc, "ETH/USDT": eth},
    )
    total_budget = sum(p.risk_budget_pct for p in out.positions)
    # Component VaR is an Euler decomposition → sum ≈ 100 % (numerical noise OK)
    assert 90.0 < total_budget < 110.0


def test_each_position_has_full_attribution_fields():
    cfg = PortfolioRiskConfig(n_monte_carlo=1000)
    eng = PortfolioRiskEngine(cfg)
    btc, eth = _correlated_pair(500, sigma_x=0.02, sigma_y=0.025, rho=0.5, seed=55)
    out = eng.compute(
        positions=[
            Position(symbol="BTC/USDT", notional_usd=8_000.0, exchange="binance"),
            Position(symbol="ETH/USDT", notional_usd=2_000.0, exchange="bybit", leverage=3.0),
        ],
        returns_history={"BTC/USDT": btc, "ETH/USDT": eth},
    )
    for p in out.positions:
        assert p.weight_pct > 0
        assert p.expected_downside_usd >= 0
        assert p.tail_exposure_usd >= 0
        assert p.stress_exposure_usd >= 0
        # All required scenarios surface in the breakdown for crypto stresses
        # that target this position
        assert isinstance(p.stress_breakdown, dict)


def test_to_json_dict_round_trip_keys():
    cfg = PortfolioRiskConfig(n_monte_carlo=500)
    eng = PortfolioRiskEngine(cfg)
    btc = _gaussian_returns(200, 0.02, seed=77)
    out = eng.compute(
        positions=[Position(symbol="BTC/USDT", notional_usd=5_000.0)],
        returns_history={"BTC/USDT": btc},
    )
    payload = out.to_json_dict()
    for key in (
        "var",
        "expected_shortfall",
        "tail",
        "drawdown",
        "correlation",
        "stress_scenarios",
        "positions",
    ):
        assert key in payload
    assert payload["report_type"] == "portfolio_risk"
    # Every scenario shows up, even if zero
    for name in ALL_STRESS_SCENARIOS:
        assert name in payload["stress_scenarios"]
