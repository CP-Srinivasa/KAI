"""Unit tests for the Portfolio Optimizer."""

from __future__ import annotations

import math
import random

import pytest

from app.risk.portfolio_optimizer import (
    PortfolioOptimizer,
    _equal_weight,
    _hierarchical_clusters,
    _hrp,
    _invert_matrix,
    _max_sharpe,
    _min_variance,
    _project_simplex_capped,
    _risk_parity,
)
from app.risk.portfolio_optimizer_models import (
    ACTION_BUY,
    ACTION_HOLD,
    ACTION_SELL,
    ALL_METHODS,
    METHOD_EQUAL_WEIGHT,
    METHOD_MAX_SHARPE,
    METHOD_MIN_VARIANCE,
    METHOD_RISK_PARITY,
    Asset,
    OptimizationConfig,
)

# --------------------------------------------------------------------- helpers


def _gauss(n: int, sigma: float, *, mu: float = 0.0, seed: int = 1) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(mu, sigma) for _ in range(n)]


def _correlated_returns(
    n: int,
    sigmas: list[float],
    rho: float,
    seed: int = 1,
) -> list[list[float]]:
    """Generate K correlated return series with constant pairwise rho."""
    rng = random.Random(seed)
    k = len(sigmas)
    out: list[list[float]] = [[] for _ in range(k)]
    for _ in range(n):
        z_common = rng.gauss(0.0, 1.0)
        idiosyncratic = [rng.gauss(0.0, 1.0) for _ in range(k)]
        for i in range(k):
            r = sigmas[i] * (
                math.sqrt(rho) * z_common + math.sqrt(max(1.0 - rho, 0.0)) * idiosyncratic[i]
            )
            out[i].append(r)
    return out


# ============================================================================
# Linear-algebra helpers
# ============================================================================


def test_invert_matrix_2x2_known_inverse():
    matrix = [[4.0, 7.0], [2.0, 6.0]]
    inv = _invert_matrix(matrix)
    assert inv is not None
    expected = [[0.6, -0.7], [-0.2, 0.4]]
    for i in range(2):
        for j in range(2):
            assert inv[i][j] == pytest.approx(expected[i][j], abs=1e-6)


def test_invert_singular_returns_none():
    singular = [[1.0, 2.0], [2.0, 4.0]]  # rows linearly dependent
    assert _invert_matrix(singular) is None


def test_simplex_projection_sums_to_one_and_in_bounds():
    v = [1.5, 0.4, -0.2, 0.3]
    w = _project_simplex_capped(v, total=1.0, lo=0.0, hi=0.5)
    assert sum(w) == pytest.approx(1.0, abs=1e-6)
    for x in w:
        assert 0.0 <= x <= 0.5 + 1e-9


def test_simplex_projection_handles_infeasible_input():
    # Lower bound forces a sum > total
    v = [0.4, 0.4, 0.4]
    w = _project_simplex_capped(v, total=1.0, lo=0.5, hi=1.0)
    # Should still produce something — clipped + renormalized
    assert sum(w) == pytest.approx(1.0, abs=1e-6)


def test_hierarchical_cluster_groups_correlated_assets():
    # Three assets where 0,1 are very close, 2 is far
    distance = [
        [0.0, 0.05, 0.9],
        [0.05, 0.0, 0.9],
        [0.9, 0.9, 0.0],
    ]
    [order] = _hierarchical_clusters(distance, ["A", "B", "C"])
    # The two close assets should sit adjacent in the leaf order
    pos = {s: i for i, s in enumerate(order)}
    assert abs(pos["A"] - pos["B"]) == 1


# ============================================================================
# Optimizer methods (algorithmic correctness)
# ============================================================================


def test_equal_weight_sums_to_one():
    w = _equal_weight(5)
    assert sum(w) == pytest.approx(1.0)
    assert all(abs(x - 0.2) < 1e-12 for x in w)


def test_min_variance_picks_lower_volatility_asset():
    """Cov diagonal: σ² = [0.04, 0.01]. Lower-vol asset (idx 1) gets more weight."""
    cov = [[0.04, 0.0], [0.0, 0.01]]
    w = _min_variance(cov, lo=0.0, hi=1.0)
    assert w is not None
    assert w[1] > w[0]
    assert sum(w) == pytest.approx(1.0, abs=1e-6)


def test_max_sharpe_tilts_to_higher_return_per_risk():
    cov = [[0.04, 0.0], [0.0, 0.04]]  # equal vol
    expected = [0.001, 0.005]  # asset 1 has 5× the daily return
    w = _max_sharpe(expected, cov, risk_free=0.0, lo=0.0, hi=1.0)
    assert w is not None
    assert w[1] > w[0]


def test_risk_parity_equalizes_risk_contributions():
    # Two assets with very different vol; RP should give the lower-vol one
    # a much larger weight so risk contributions match.
    cov = [[0.04, 0.0], [0.0, 0.01]]
    w = _risk_parity(cov, max_iter=200, tol=1e-9)
    assert w is not None
    rc_0 = w[0] * (cov[0][0] * w[0])
    rc_1 = w[1] * (cov[1][1] * w[1])
    assert rc_0 == pytest.approx(rc_1, rel=0.05)
    # Lower-vol asset gets larger weight
    assert w[1] > w[0]


def test_hrp_returns_valid_weights():
    sigmas = [0.02, 0.025, 0.05, 0.04]
    returns = _correlated_returns(300, sigmas, rho=0.3, seed=11)
    n = len(sigmas)
    # Build cov from returns
    means = [sum(r) / len(r) for r in returns]
    n_obs = len(returns[0])

    def _cov(i: int, j: int) -> float:
        return (
            sum((returns[i][t] - means[i]) * (returns[j][t] - means[j]) for t in range(n_obs))
            / n_obs
        )

    cov = [[_cov(i, j) for j in range(n)] for i in range(n)]
    w = _hrp(cov, ["A", "B", "C", "D"])
    assert w is not None
    assert sum(w) == pytest.approx(1.0, abs=1e-6)
    assert all(x >= 0 for x in w)


# ============================================================================
# Engine: dispatch and constraints
# ============================================================================


@pytest.fixture
def basic_returns() -> dict[str, list[float]]:
    rng = random.Random(31)
    return {
        "BTC/USDT": [rng.gauss(0.0008, 0.025) for _ in range(300)],
        "ETH/USDT": [rng.gauss(0.0010, 0.030) for _ in range(300)],
        "SOL/USDT": [rng.gauss(0.0015, 0.045) for _ in range(300)],
    }


@pytest.fixture
def basic_assets() -> list[Asset]:
    return [
        Asset(symbol="BTC/USDT", liquidity_score=1.0, exchange="binance"),
        Asset(symbol="ETH/USDT", liquidity_score=0.9, exchange="binance"),
        Asset(symbol="SOL/USDT", liquidity_score=0.7, exchange="okx"),
    ]


def test_optimize_returns_valid_allocation(
    basic_assets: list[Asset], basic_returns: dict[str, list[float]]
):
    opt = PortfolioOptimizer()
    out = opt.optimize(
        assets=basic_assets,
        returns_history=basic_returns,
        method=METHOD_RISK_PARITY,
        portfolio_value_usd=10_000.0,
    )
    assert out.method_used == METHOD_RISK_PARITY
    assert len(out.allocations) == 3
    # Target weights sum (after vol target) ≤ max leverage
    total = sum(a.target_weight_pct for a in out.allocations) / 100.0
    assert 0.0 <= total <= opt._config.max_leverage + 1e-6
    assert out.inputs_hash.startswith("sha256:")


def test_method_falls_back_to_equal_weight_on_unknown():
    opt = PortfolioOptimizer()
    rng = random.Random(2)
    rets = {"BTC/USDT": [rng.gauss(0.0, 0.02) for _ in range(100)]}
    out = opt.optimize(
        assets=[Asset(symbol="BTC/USDT")],
        returns_history=rets,
        method="not_a_real_method",
    )
    assert out.method_used == METHOD_EQUAL_WEIGHT
    assert any("unknown_method" in w for w in out.warnings)


def test_regime_dispatch_picks_min_variance_in_high_vol(
    basic_assets: list[Asset], basic_returns: dict[str, list[float]]
):
    opt = PortfolioOptimizer()
    out = opt.optimize(
        assets=basic_assets,
        returns_history=basic_returns,
        regime="high_vol",
    )
    assert out.method_used == METHOD_MIN_VARIANCE


def test_regime_crisis_uses_equal_weight_and_floors_stables(basic_returns: dict[str, list[float]]):
    cfg = OptimizationConfig(stablecoin_floor_in_crisis=0.5)
    opt = PortfolioOptimizer(cfg)
    rng = random.Random(7)
    returns = dict(basic_returns)
    returns["USDT"] = [rng.gauss(0.0, 0.0001) for _ in range(300)]  # quiet
    assets = [
        Asset(symbol="BTC/USDT", liquidity_score=1.0),
        Asset(symbol="ETH/USDT", liquidity_score=0.9),
        Asset(symbol="USDT", is_stablecoin=True, liquidity_score=1.0),
    ]
    out = opt.optimize(
        assets=assets,
        returns_history=returns,
        regime="crisis",
    )
    assert out.method_used == METHOD_EQUAL_WEIGHT
    assert "stablecoin_floor" in out.constraints_active
    gross_pct = sum(a.target_weight_pct for a in out.allocations) / 100.0
    assert out.stablecoin_exposure_pct >= 50.0 * gross_pct - 1e-6


def test_liquidity_cap_active_for_thin_assets(basic_returns: dict[str, list[float]]):
    cfg = OptimizationConfig(max_weight_per_asset=0.6)
    opt = PortfolioOptimizer(cfg)
    assets = [
        Asset(symbol="BTC/USDT", liquidity_score=1.0),
        Asset(symbol="ETH/USDT", liquidity_score=0.9),
        Asset(symbol="SOL/USDT", liquidity_score=0.05),  # very illiquid
    ]
    out = opt.optimize(
        assets=assets,
        returns_history=basic_returns,
        method=METHOD_MAX_SHARPE,
    )
    sol = next(a for a in out.allocations if a.symbol == "SOL/USDT")
    # 0.05 liquidity × 0.6 max = 0.03 cap → 3 % weight
    assert sol.target_weight_pct <= 3.0 * opt._config.max_leverage + 1e-3
    assert "liquidity_cap" in out.constraints_active or sol.liquidity_capped


def test_exchange_concentration_cap_engages_when_one_venue_dominates(
    basic_returns: dict[str, list[float]],
):
    cfg = OptimizationConfig(max_exchange_concentration=0.6)
    opt = PortfolioOptimizer(cfg)
    assets = [
        Asset(symbol="BTC/USDT", exchange="binance"),
        Asset(symbol="ETH/USDT", exchange="binance"),
        Asset(symbol="SOL/USDT", exchange="okx"),
    ]
    out = opt.optimize(
        assets=assets,
        returns_history=basic_returns,
        method=METHOD_EQUAL_WEIGHT,
    )
    binance_total = sum(
        a.target_weight_pct for a in out.allocations if a.symbol in ("BTC/USDT", "ETH/USDT")
    )
    # binance positions ≤ 0.6 × leverage × 100
    assert binance_total <= 0.6 * opt._config.max_leverage * 100.0 + 1e-3
    assert "exchange_concentration" in out.constraints_active


def test_volatility_targeting_scales_to_hit_target(
    basic_assets: list[Asset], basic_returns: dict[str, list[float]]
):
    cfg = OptimizationConfig(
        target_volatility_annual=0.15,
        max_leverage=3.0,
        enforce_max_drawdown=False,
    )
    opt = PortfolioOptimizer(cfg)
    out = opt.optimize(
        assets=basic_assets,
        returns_history=basic_returns,
        method=METHOD_RISK_PARITY,
    )
    assert "vol_target" in out.constraints_active
    assert out.expected_volatility_annual is not None
    # Vol-target should land within ±20 % of the 15 % target after scaling
    assert abs(out.expected_volatility_annual - 0.15) < 0.05


def test_max_drawdown_constraint_de_levers(
    basic_assets: list[Asset], basic_returns: dict[str, list[float]]
):
    # Tight DD constraint with high target vol → optimizer must scale down
    cfg = OptimizationConfig(
        target_volatility_annual=0.80,
        max_leverage=5.0,
        max_drawdown_constraint_pct=5.0,
        enforce_max_drawdown=True,
    )
    opt = PortfolioOptimizer(cfg)
    out = opt.optimize(
        assets=basic_assets,
        returns_history=basic_returns,
        method=METHOD_RISK_PARITY,
    )
    assert "max_drawdown" in out.constraints_active
    assert out.expected_max_drawdown_pct is not None
    assert out.expected_max_drawdown_pct <= 5.0 + 1e-3


# ============================================================================
# Rebalance logic
# ============================================================================


def test_no_rebalance_when_already_at_target(
    basic_assets: list[Asset], basic_returns: dict[str, list[float]]
):
    opt = PortfolioOptimizer(
        OptimizationConfig(enforce_vol_target=False, enforce_max_drawdown=False)
    )
    # First pass to discover target
    first = opt.optimize(
        assets=basic_assets,
        returns_history=basic_returns,
        method=METHOD_EQUAL_WEIGHT,
    )
    current = {a.symbol: a.target_weight_pct / 100.0 for a in first.allocations}
    # Second pass with current = previous target
    second = opt.optimize(
        assets=basic_assets,
        returns_history=basic_returns,
        method=METHOD_EQUAL_WEIGHT,
        current_weights=current,
    )
    assert not second.rebalance_required
    for a in second.allocations:
        assert a.action == ACTION_HOLD
        assert abs(a.drift_pct) < 1e-6


def test_rebalance_required_when_drift_exceeds_threshold(
    basic_assets: list[Asset], basic_returns: dict[str, list[float]]
):
    cfg = OptimizationConfig(
        rebalance_drift_threshold=0.05,
        enforce_vol_target=False,
        enforce_max_drawdown=False,
    )
    opt = PortfolioOptimizer(cfg)
    out = opt.optimize(
        assets=basic_assets,
        returns_history=basic_returns,
        method=METHOD_EQUAL_WEIGHT,
        current_weights={"BTC/USDT": 0.7, "ETH/USDT": 0.2, "SOL/USDT": 0.1},
        portfolio_value_usd=100_000.0,
    )
    assert out.rebalance_required
    btc = next(a for a in out.allocations if a.symbol == "BTC/USDT")
    eth = next(a for a in out.allocations if a.symbol == "ETH/USDT")
    assert btc.action == ACTION_SELL  # over-weight → trim
    assert eth.action == ACTION_BUY  # under-weight → add
    assert btc.trade_size_usd < 0
    assert eth.trade_size_usd > 0


# ============================================================================
# Edge cases
# ============================================================================


def test_empty_universe_returns_empty_allocation():
    opt = PortfolioOptimizer()
    out = opt.optimize(assets=[], returns_history={})
    assert out.allocations == []
    assert "no_usable_assets" in out.warnings
    assert out.cash_pct == 100.0


def test_assets_without_history_are_dropped():
    opt = PortfolioOptimizer()
    rng = random.Random(5)
    rets = {"BTC/USDT": [rng.gauss(0.0, 0.02) for _ in range(100)]}
    assets = [
        Asset(symbol="BTC/USDT"),
        Asset(symbol="UNKNOWN/USDT"),  # no returns provided
    ]
    out = opt.optimize(assets=assets, returns_history=rets, method=METHOD_EQUAL_WEIGHT)
    # Only BTC survives
    assert {a.symbol for a in out.allocations} == {"BTC/USDT"}
    assert any("missing_or_short_returns:UNKNOWN/USDT" in w for w in out.warnings)


def test_funding_cost_reduces_expected_return(basic_returns: dict[str, list[float]]):
    """Asset with high funding cost should rank lower in max-Sharpe."""
    rets = dict(basic_returns)
    base = [Asset(symbol=s) for s in rets]
    high_funding = [
        Asset(symbol="BTC/USDT", funding_cost_pct_daily=0.0),
        Asset(symbol="ETH/USDT", funding_cost_pct_daily=0.0),
        Asset(symbol="SOL/USDT", funding_cost_pct_daily=0.005),  # huge funding
    ]
    opt = PortfolioOptimizer(
        OptimizationConfig(enforce_vol_target=False, enforce_max_drawdown=False)
    )
    out_base = opt.optimize(assets=base, returns_history=rets, method=METHOD_MAX_SHARPE)
    out_funded = opt.optimize(assets=high_funding, returns_history=rets, method=METHOD_MAX_SHARPE)
    sol_base = next(a for a in out_base.allocations if a.symbol == "SOL/USDT")
    sol_fund = next(a for a in out_funded.allocations if a.symbol == "SOL/USDT")
    assert sol_fund.target_weight_pct <= sol_base.target_weight_pct + 1e-6


def test_to_json_dict_contains_all_required_sections(
    basic_assets: list[Asset], basic_returns: dict[str, list[float]]
):
    opt = PortfolioOptimizer()
    out = opt.optimize(
        assets=basic_assets,
        returns_history=basic_returns,
        portfolio_value_usd=10_000.0,
    )
    payload = out.to_json_dict()
    for key in ("expected", "exposure", "rebalance", "allocations", "constraints_active"):
        assert key in payload
    assert payload["report_type"] == "portfolio_allocation"
    assert "method_used" in payload


def test_all_methods_produce_valid_weights(
    basic_assets: list[Asset], basic_returns: dict[str, list[float]]
):
    """Smoke test: every supported method produces a non-empty, normalized
    allocation without raising."""
    opt = PortfolioOptimizer(
        OptimizationConfig(enforce_vol_target=False, enforce_max_drawdown=False)
    )
    for m in ALL_METHODS:
        out = opt.optimize(assets=basic_assets, returns_history=basic_returns, method=m)
        assert out.method_used == m
        total = sum(a.target_weight_pct for a in out.allocations) / 100.0
        # Without vol-target the sum equals 1.0 (un-leveraged baseline)
        assert total == pytest.approx(1.0, abs=0.05), f"{m} produced sum={total:.4f}"
