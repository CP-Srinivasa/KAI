"""Portfolio Optimizer — institutional-grade strategic allocation.

Pure-Python implementation. No numpy / scipy dependency.

Methods:
- equal_weight                — naive baseline / crisis fallback
- min_variance                — closed-form Σ⁻¹·1 / 1'Σ⁻¹·1, projected
- max_sharpe                  — closed-form Σ⁻¹·μ_e / 1'Σ⁻¹·μ_e, projected
- max_sortino                 — same shape, downside semi-cov
- risk_parity                 — Spinu cyclical-coordinate descent
- hierarchical_risk_parity    — Lopez de Prado HRP

Crypto adjustments:
- Funding-cost subtracted from expected returns
- Liquidity-score caps weight per asset
- Per-exchange concentration cap (FTX-style risk)
- Stablecoin floor in crisis regime
- Volatility targeting (scale to σ_target, capped at max_leverage)
- Max-drawdown constraint via historical replay (scale exposure if breached)

Dynamic dispatch:
- Method picked by regime via `regime_method_map` (config) when caller
  passes regime. Operator can override per call.

Output: PortfolioAllocation with target weights, drift vs. current, action
per asset, expected portfolio metrics, and rebalance decision.
"""

from __future__ import annotations

import hashlib
import logging
import math
import statistics
from datetime import UTC, datetime

from app.risk.portfolio_optimizer_models import (
    ACTION_BUY,
    ACTION_HOLD,
    ACTION_SELL,
    ALL_METHODS,
    METHOD_EQUAL_WEIGHT,
    METHOD_HRP,
    METHOD_MAX_SHARPE,
    METHOD_MAX_SORTINO,
    METHOD_MIN_VARIANCE,
    METHOD_RISK_PARITY,
    Asset,
    AssetAllocation,
    OptimizationConfig,
    PortfolioAllocation,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Linear algebra helpers (pure Python)
# ============================================================================


def _invert_matrix(matrix: list[list[float]]) -> list[list[float]] | None:
    """Gauss-Jordan inverse with partial pivoting. Returns None if singular."""
    n = len(matrix)
    if n == 0:
        return []
    aug = [list(matrix[i]) + [1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for col in range(n):
        # Partial pivot
        max_row = col
        for r in range(col + 1, n):
            if abs(aug[r][col]) > abs(aug[max_row][col]):
                max_row = r
        if abs(aug[max_row][col]) < 1e-15:
            return None
        aug[col], aug[max_row] = aug[max_row], aug[col]
        pivot = aug[col][col]
        for j in range(2 * n):
            aug[col][j] /= pivot
        for r in range(n):
            if r != col and aug[r][col] != 0.0:
                factor = aug[r][col]
                for j in range(2 * n):
                    aug[r][j] -= factor * aug[col][j]
    return [row[n:] for row in aug]


def _matvec(matrix: list[list[float]], vec: list[float]) -> list[float]:
    n = len(matrix)
    return [sum(matrix[i][j] * vec[j] for j in range(n)) for i in range(n)]


def _quadratic_form(weights: list[float], cov: list[list[float]]) -> float:
    n = len(weights)
    total = 0.0
    for i in range(n):
        for j in range(n):
            total += weights[i] * cov[i][j] * weights[j]
    return total


def _project_simplex_capped(v: list[float], total: float, lo: float, hi: float) -> list[float]:
    """Project v onto {w : Σw = total, lo ≤ w_i ≤ hi}.

    Uses bisection on the dual variable. O(N log(1/ε)).
    Robust to infeasible inputs — clamps to nearest feasible if total cannot
    be met within bounds. For long-only sum-to-1: lo=0, hi=1.
    """
    n = len(v)
    if n == 0:
        return []
    if total <= 0.0:
        return [lo for _ in v]
    # Feasibility check
    if lo * n > total or hi * n < total:
        # Can't satisfy sum exactly — clip to bounds, then renormalize.
        clipped = [max(lo, min(hi, x)) for x in v]
        s = sum(clipped)
        if s <= 0.0:
            return [total / n for _ in v]
        return [x * total / s for x in clipped]

    def s_of(theta: float) -> float:
        return sum(max(lo, min(hi, x - theta)) for x in v)

    lo_t, hi_t = -max(abs(x) for x in v) - 10.0, max(abs(x) for x in v) + 10.0
    for _ in range(80):
        mid = 0.5 * (lo_t + hi_t)
        if s_of(mid) > total:
            lo_t = mid
        else:
            hi_t = mid
    theta = 0.5 * (lo_t + hi_t)
    return [max(lo, min(hi, x - theta)) for x in v]


def _hierarchical_clusters(
    distance_matrix: list[list[float]], symbols: list[str]
) -> list[list[str]]:
    """Single-linkage agglomerative clustering. Returns the merge order as
    a list of grouped clusters of symbols, root-to-leaves order.

    For HRP we need the *quasi-diagonal* ordering of leaves. We return the
    final tree's leaf order so HRP can split it via recursive bisection.
    """
    n = len(symbols)
    if n <= 1:
        return [list(symbols)]

    # Initialize each symbol as its own cluster
    clusters: list[list[int]] = [[i] for i in range(n)]
    distances = [row[:] for row in distance_matrix]

    while len(clusters) > 1:
        # Find closest pair
        best_d = math.inf
        best_a, best_b = 0, 1
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                d = min(distances[i][j] for i in clusters[a] for j in clusters[b])
                if d < best_d:
                    best_d = d
                    best_a, best_b = a, b
        merged = clusters[best_a] + clusters[best_b]
        # Drop b first (higher index) to keep a stable
        clusters = [c for k, c in enumerate(clusters) if k not in (best_a, best_b)]
        clusters.append(merged)

    leaf_order = clusters[0]
    return [[symbols[i] for i in leaf_order]]


# ============================================================================
# Statistics helpers
# ============================================================================


def _mean(xs: list[float]) -> float:
    return statistics.fmean(xs) if xs else 0.0


def _covariance_matrix(returns: list[list[float]]) -> list[list[float]]:
    n = len(returns)
    if n == 0:
        return []
    n_obs = min(len(r) for r in returns)
    if n_obs < 2:
        return [[0.0] * n for _ in range(n)]
    aligned = [r[-n_obs:] for r in returns]
    means = [_mean(r) for r in aligned]
    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            cov[i][j] = (
                sum((aligned[i][t] - means[i]) * (aligned[j][t] - means[j]) for t in range(n_obs))
                / n_obs
            )
    return cov


def _semicov_matrix(returns: list[list[float]], mar: float) -> list[list[float]]:
    """Downside semi-covariance: cov(min(r-MAR, 0), min(r-MAR, 0))."""
    n = len(returns)
    if n == 0:
        return []
    n_obs = min(len(r) for r in returns)
    if n_obs < 2:
        return [[0.0] * n for _ in range(n)]
    clipped = [[min(r[t] - mar, 0.0) for t in range(len(r) - n_obs, len(r))] for r in returns]
    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            cov[i][j] = sum(clipped[i][t] * clipped[j][t] for t in range(n_obs)) / n_obs
    return cov


def _correlation_matrix(cov: list[list[float]]) -> list[list[float]]:
    n = len(cov)
    if n == 0:
        return []
    sigmas = [math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0 for i in range(n)]
    corr = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if sigmas[i] <= 0.0 or sigmas[j] <= 0.0:
                corr[i][j] = 1.0 if i == j else 0.0
            else:
                corr[i][j] = max(-1.0, min(1.0, cov[i][j] / (sigmas[i] * sigmas[j])))
    return corr


def _regularize(cov: list[list[float]], eps: float) -> list[list[float]]:
    n = len(cov)
    if n == 0:
        return cov
    trace = sum(cov[i][i] for i in range(n))
    ridge = eps * max(trace / max(n, 1), 1.0)
    return [[cov[i][j] + (ridge if i == j else 0.0) for j in range(n)] for i in range(n)]


# ============================================================================
# Core optimizers
# ============================================================================


def _equal_weight(n: int) -> list[float]:
    if n <= 0:
        return []
    return [1.0 / n] * n


def _min_variance(cov: list[list[float]], lo: float, hi: float) -> list[float] | None:
    n = len(cov)
    if n == 0:
        return None
    inv = _invert_matrix(cov)
    if inv is None:
        return None
    ones = [1.0] * n
    raw = _matvec(inv, ones)
    s = sum(raw)
    if abs(s) < 1e-12:
        return None
    w = [x / s for x in raw]
    return _project_simplex_capped(w, total=1.0, lo=lo, hi=hi)


def _max_sharpe(
    expected_returns: list[float],
    cov: list[list[float]],
    risk_free: float,
    lo: float,
    hi: float,
) -> list[float] | None:
    n = len(cov)
    if n == 0:
        return None
    inv = _invert_matrix(cov)
    if inv is None:
        return None
    excess = [r - risk_free for r in expected_returns]
    raw = _matvec(inv, excess)
    s = sum(raw)
    if abs(s) < 1e-12:
        # All excess returns ≈ 0 → fall back to equal weight
        return _equal_weight(n)
    w = [x / s for x in raw]
    return _project_simplex_capped(w, total=1.0, lo=lo, hi=hi)


def _max_sortino(
    expected_returns: list[float],
    semi_cov: list[list[float]],
    mar: float,
    lo: float,
    hi: float,
) -> list[float] | None:
    """Same shape as max_sharpe but with downside semi-covariance."""
    n = len(semi_cov)
    if n == 0:
        return None
    inv = _invert_matrix(semi_cov)
    if inv is None:
        return None
    excess = [r - mar for r in expected_returns]
    raw = _matvec(inv, excess)
    s = sum(raw)
    if abs(s) < 1e-12:
        return _equal_weight(n)
    w = [x / s for x in raw]
    return _project_simplex_capped(w, total=1.0, lo=lo, hi=hi)


def _risk_parity(
    cov: list[list[float]],
    max_iter: int = 200,
    tol: float = 1e-6,
) -> list[float] | None:
    """Spinu (2013) cyclical-coordinate-descent risk-parity.

    Solves Σ_i (Σw)_i / w_i = const → equal risk contribution.
    Iteratively updates w_i = b_i / (Σw)_i where b_i = 1/n.
    """
    n = len(cov)
    if n == 0:
        return None
    if n == 1:
        return [1.0]
    target = [1.0 / n] * n
    w = [1.0 / n] * n
    for _ in range(max_iter):
        prev = list(w)
        sigma_p_sq = _quadratic_form(w, cov)
        for i in range(n):
            sigma_p_w_i = sum(cov[i][j] * w[j] for j in range(n))
            if sigma_p_w_i <= 0.0:
                continue
            # Spinu (2013) fixed-point: w_i ← b_i · σ²_p / (Σw)_i
            w[i] = target[i] * sigma_p_sq / sigma_p_w_i
            w[i] = max(w[i], 1e-12)
            # Refresh σ²_p so the next coordinate sees the latest w
            sigma_p_sq = _quadratic_form(w, cov)
        # Renormalize (long-only sum-to-1)
        s = sum(w)
        if s <= 0.0:
            return None
        w = [x / s for x in w]
        if max(abs(w[i] - prev[i]) for i in range(n)) < tol:
            break
    return w


def _hrp(cov: list[list[float]], symbols: list[str]) -> list[float] | None:
    """Hierarchical Risk Parity (Lopez de Prado, 2016)."""
    n = len(cov)
    if n == 0:
        return None
    if n == 1:
        return [1.0]

    corr = _correlation_matrix(cov)
    distance = [[math.sqrt(max(0.5 * (1.0 - corr[i][j]), 0.0)) for j in range(n)] for i in range(n)]
    ordered = _hierarchical_clusters(distance, symbols)[0]
    sym_to_idx = {s: i for i, s in enumerate(symbols)}
    order = [sym_to_idx[s] for s in ordered]

    # Inverse-variance baseline weights
    weights_in_order = [1.0] * n

    def _ivp(indices: list[int]) -> list[float]:
        ivar = [1.0 / max(cov[i][i], 1e-30) for i in indices]
        s = sum(ivar)
        return [v / s for v in ivar] if s > 0 else [1.0 / len(indices)] * len(indices)

    def _cluster_var(indices: list[int]) -> float:
        sub_w = _ivp(indices)
        sub_cov = [[cov[i][j] for j in indices] for i in indices]
        return _quadratic_form(sub_w, sub_cov)

    def _bisect(indices: list[int]) -> None:
        if len(indices) <= 1:
            return
        mid = len(indices) // 2
        left, right = indices[:mid], indices[mid:]
        var_l = _cluster_var(left)
        var_r = _cluster_var(right)
        denom = var_l + var_r
        if denom <= 0.0:
            alpha = 0.5
        else:
            alpha = 1.0 - var_l / denom  # weight on left ↑ when its var is low
        for i in left:
            weights_in_order[order.index(i)] *= alpha
        for i in right:
            weights_in_order[order.index(i)] *= 1.0 - alpha
        _bisect(left)
        _bisect(right)

    _bisect(order)

    # Map back from quasi-diagonal order to original symbol order
    weights = [0.0] * n
    for pos, idx in enumerate(order):
        weights[idx] = weights_in_order[pos]
    s = sum(weights)
    if s <= 0.0:
        return _equal_weight(n)
    return [w / s for w in weights]


# ============================================================================
# Engine
# ============================================================================


class PortfolioOptimizer:
    """Strategic-allocation engine — dynamic, regime-aware."""

    def __init__(self, config: OptimizationConfig | None = None) -> None:
        self._config = config or OptimizationConfig()

    # -------------------------------------------------- helpers / setup

    def _hash_inputs(
        self,
        assets: list[Asset],
        method: str,
        regime: str | None,
        portfolio_value_usd: float,
    ) -> str:
        payload = "|".join(
            [
                method,
                regime or "none",
                f"{portfolio_value_usd:.2f}",
                *[f"{a.symbol}:{a.exchange}:{a.signal_quality:.3f}" for a in assets],
            ]
        )
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _expected_returns(
        self,
        assets: list[Asset],
        returns_history: dict[str, list[float]],
    ) -> list[float]:
        cfg = self._config
        out: list[float] = []
        for a in assets:
            if a.expected_return_override is not None:
                base = a.expected_return_override
            else:
                series = returns_history.get(a.symbol)
                base = _mean(series) if series else 0.0
            # Signal-quality tilt: centered at 0.5 → no tilt; range ±strength·|μ|
            tilt = (a.signal_quality - 0.5) * 2.0 * cfg.signal_quality_tilt_strength
            adjusted = base * (1.0 + tilt) if base != 0.0 else tilt * 0.0001
            if cfg.apply_funding_cost:
                adjusted -= a.funding_cost_pct_daily
            out.append(adjusted)
        return out

    # ---------------------------------------- optimizer dispatch

    def _solve(
        self,
        method: str,
        assets: list[Asset],
        returns: list[list[float]],
        cov_reg: list[list[float]],
        expected_returns: list[float],
    ) -> tuple[list[float], str, list[str]]:
        """Returns (weights, method_used, warnings)."""
        cfg = self._config
        n = len(assets)
        warnings: list[str] = []

        if method not in ALL_METHODS:
            warnings.append(f"unknown_method:{method}|fallback:equal_weight")
            return _equal_weight(n), METHOD_EQUAL_WEIGHT, warnings

        if method == METHOD_EQUAL_WEIGHT:
            return _equal_weight(n), method, warnings

        lo = cfg.min_weight_per_asset
        hi = cfg.max_weight_per_asset

        if method == METHOD_MIN_VARIANCE:
            w = _min_variance(cov_reg, lo, hi)
        elif method == METHOD_MAX_SHARPE:
            w = _max_sharpe(expected_returns, cov_reg, cfg.risk_free_rate_daily, lo, hi)
        elif method == METHOD_MAX_SORTINO:
            semi = _semicov_matrix(returns, cfg.mar_for_sortino_daily)
            semi_reg = _regularize(semi, cfg.cov_regularization_eps)
            w = _max_sortino(expected_returns, semi_reg, cfg.mar_for_sortino_daily, lo, hi)
        elif method == METHOD_RISK_PARITY:
            w = _risk_parity(cov_reg, cfg.risk_parity_max_iter, cfg.risk_parity_tol)
            if w is not None:
                w = _project_simplex_capped(w, total=1.0, lo=lo, hi=hi)
        elif method == METHOD_HRP:
            w = _hrp(cov_reg, [a.symbol for a in assets])
            if w is not None:
                w = _project_simplex_capped(w, total=1.0, lo=lo, hi=hi)
        else:
            w = None

        if w is None:
            warnings.append(f"{method}_failed|fallback:equal_weight")
            return _equal_weight(n), METHOD_EQUAL_WEIGHT, warnings
        return w, method, warnings

    # ---------------------------------------- crypto post-processing

    def _apply_liquidity_caps(
        self,
        weights: list[float],
        assets: list[Asset],
    ) -> tuple[list[float], list[bool]]:
        cfg = self._config
        capped: list[bool] = [False] * len(weights)
        new_weights = list(weights)
        for i, a in enumerate(assets):
            asset_cap = cfg.max_weight_per_asset * max(a.liquidity_score, 0.05)
            if new_weights[i] > asset_cap:
                new_weights[i] = asset_cap
                capped[i] = True
        # Renormalize to sum 1 (will be re-scaled later by vol-target)
        s = sum(new_weights)
        if s > 0:
            new_weights = [w / s for w in new_weights]
        return new_weights, capped

    def _apply_exchange_concentration(
        self,
        weights: list[float],
        assets: list[Asset],
    ) -> tuple[list[float], bool]:
        cfg = self._config
        per_ex: dict[str, list[int]] = {}
        for i, a in enumerate(assets):
            per_ex.setdefault(a.exchange.lower(), []).append(i)

        new_weights = list(weights)
        active = False
        for _ex, idxs in per_ex.items():
            ex_total = sum(new_weights[i] for i in idxs)
            if ex_total > cfg.max_exchange_concentration:
                scale = cfg.max_exchange_concentration / ex_total
                for i in idxs:
                    new_weights[i] *= scale
                active = True
        s = sum(new_weights)
        if s > 0:
            new_weights = [w / s for w in new_weights]
        return new_weights, active

    def _apply_stablecoin_floor(
        self,
        weights: list[float],
        assets: list[Asset],
        regime: str | None,
    ) -> tuple[list[float], bool]:
        cfg = self._config
        if regime != "crisis":
            return weights, False
        stable_idx = [
            i
            for i, a in enumerate(assets)
            if a.is_stablecoin or a.symbol.lower() in cfg.stablecoin_set
        ]
        if not stable_idx:
            return weights, False

        target_floor = cfg.stablecoin_floor_in_crisis
        cur_floor = sum(weights[i] for i in stable_idx)
        if cur_floor >= target_floor:
            return weights, False

        new_weights = list(weights)
        deficit = target_floor - cur_floor
        # Scale non-stable assets down by (1 - deficit / non_stable_total)
        non_stable_total = 1.0 - cur_floor
        if non_stable_total <= 0.0:
            return weights, False
        scale_non_stable = (1.0 - target_floor) / non_stable_total
        for i, _a in enumerate(assets):
            if i in stable_idx:
                # distribute deficit equally over stables, preserving proportions
                share = weights[i] / cur_floor if cur_floor > 0 else 1.0 / len(stable_idx)
                new_weights[i] = weights[i] + deficit * share
            else:
                new_weights[i] = weights[i] * scale_non_stable
        s = sum(new_weights)
        if s > 0:
            new_weights = [w / s for w in new_weights]
        return new_weights, True

    def _apply_volatility_target(
        self,
        weights: list[float],
        cov: list[list[float]],
    ) -> tuple[list[float], float, bool]:
        """Returns (weights_scaled, gross_leverage, target_active)."""
        cfg = self._config
        if not cfg.enforce_vol_target:
            return weights, 1.0, False
        port_var = _quadratic_form(weights, cov)
        if port_var <= 0.0:
            return weights, 1.0, False
        sigma_daily = math.sqrt(port_var)
        sigma_annual = sigma_daily * math.sqrt(cfg.annualization_factor)
        if sigma_annual <= 0.0:
            return weights, 1.0, False
        scale = cfg.target_volatility_annual / sigma_annual
        scale = min(scale, cfg.max_leverage)
        scale = max(scale, 0.0)
        return [w * scale for w in weights], scale, True

    def _apply_max_drawdown_constraint(
        self,
        weights: list[float],
        returns: list[list[float]],
        leverage: float,
    ) -> tuple[float, bool, float]:
        """Replay portfolio returns at the proposed weights × leverage and
        compute max drawdown. If breached, scale leverage down so the
        constraint is met. Returns (new_leverage, active, expected_dd_pct)."""
        cfg = self._config
        if not cfg.enforce_max_drawdown or not returns:
            return leverage, False, 0.0
        n_obs = min(len(r) for r in returns)
        if n_obs < 30:
            return leverage, False, 0.0
        aligned = [r[-n_obs:] for r in returns]
        port_returns = [
            sum(weights[i] * aligned[i][t] for i in range(len(weights))) for t in range(n_obs)
        ]

        def _max_dd(rets: list[float]) -> float:
            equity = 1.0
            peak = 1.0
            mx = 0.0
            for r in rets:
                equity *= 1.0 + r
                if equity > peak:
                    peak = equity
                if peak > 0:
                    dd = (peak - equity) / peak * 100.0
                    if dd > mx:
                        mx = dd
            return mx

        # Try the proposed leverage first
        scaled = [r * leverage for r in port_returns]
        dd = _max_dd(scaled)
        if dd <= cfg.max_drawdown_constraint_pct:
            return leverage, False, dd

        # Solve for leverage that meets the constraint via bisection
        lo, hi = 0.0, leverage
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            scaled = [r * mid for r in port_returns]
            if _max_dd(scaled) <= cfg.max_drawdown_constraint_pct:
                lo = mid
            else:
                hi = mid
        new_lev = 0.5 * (lo + hi)
        scaled = [r * new_lev for r in port_returns]
        return new_lev, True, _max_dd(scaled)

    # ---------------------------------------- rebalancing

    def _build_allocations(
        self,
        assets: list[Asset],
        target_weights: list[float],
        leverage: float,
        current_weights: dict[str, float],
        portfolio_value_usd: float,
        cov: list[list[float]],
        expected_returns_daily: list[float],
        capped_flags: list[bool],
        annualization_factor: int,
    ) -> tuple[list[AssetAllocation], float, float, float]:
        """Returns (allocations, max_drift_pct, turnover_pct, est_turnover_usd)."""
        cfg = self._config
        n = len(assets)

        # Risk contributions (in daily variance units)
        port_var = _quadratic_form([w * leverage for w in target_weights], cov)
        port_sigma = math.sqrt(max(port_var, 0.0))

        max_drift = 0.0
        total_drift_abs = 0.0
        total_turnover_usd = 0.0

        allocations: list[AssetAllocation] = []
        for i, a in enumerate(assets):
            tgt_w = target_weights[i] * leverage
            tgt_pct = tgt_w * 100.0
            cur_pct = current_weights.get(a.symbol, 0.0) * 100.0
            drift = tgt_pct - cur_pct
            if abs(drift) > max_drift:
                max_drift = abs(drift)
            total_drift_abs += abs(drift)

            # Risk contribution
            if port_sigma > 0.0:
                marginal = sum(cov[i][j] * (target_weights[j] * leverage) for j in range(n))
                rc = (target_weights[i] * leverage) * marginal / (port_sigma * port_sigma)
                rc_pct = max(0.0, min(100.0, rc * 100.0))
            else:
                rc_pct = 0.0

            mu_daily = expected_returns_daily[i]
            sigma_daily = math.sqrt(max(cov[i][i], 0.0))
            er_annual = mu_daily * annualization_factor if mu_daily else None
            sigma_annual = sigma_daily * math.sqrt(annualization_factor) if sigma_daily else None
            funding_adj = (
                er_annual - a.funding_cost_pct_daily * annualization_factor
                if er_annual is not None and cfg.apply_funding_cost
                else er_annual
            )

            trade_pct = drift / 100.0  # fraction of portfolio
            trade_usd = trade_pct * portfolio_value_usd
            total_turnover_usd += abs(trade_usd)

            min_trade = cfg.min_trade_size_pct
            if abs(trade_pct) < min_trade:
                action = ACTION_HOLD
            elif trade_pct > 0:
                action = ACTION_BUY
            else:
                action = ACTION_SELL

            allocations.append(
                AssetAllocation(
                    symbol=a.symbol,
                    target_weight_pct=tgt_pct,
                    current_weight_pct=cur_pct,
                    drift_pct=drift,
                    risk_contribution_pct=rc_pct,
                    expected_return_annual=er_annual,
                    expected_volatility_annual=sigma_annual,
                    funding_adjusted_return_annual=funding_adj,
                    action=action,
                    trade_size_usd=trade_usd,
                    trade_size_pct=trade_pct * 100.0,
                    liquidity_capped=capped_flags[i],
                )
            )

        turnover_pct = (
            total_turnover_usd / portfolio_value_usd * 100.0
            if portfolio_value_usd > 0
            else total_drift_abs
        )
        return allocations, max_drift, turnover_pct, total_turnover_usd

    # ---------------------------------------- public entry

    def optimize(  # noqa: C901 — orchestrator, deliberately linear
        self,
        *,
        assets: list[Asset],
        returns_history: dict[str, list[float]],
        method: str | None = None,
        regime: str | None = None,
        current_weights: dict[str, float] | None = None,
        portfolio_value_usd: float = 0.0,
    ) -> PortfolioAllocation:
        """Compute the dynamic target portfolio.

        - `method` overrides the regime-driven dispatch.
        - `regime` (e.g. from VolatilityEngine) selects method via
          `regime_method_map` if `method` is None.
        - `current_weights` are fractions in [0, 1]. Missing symbols default
          to 0 % current weight.
        - `returns_history` keys must be aligned to `assets[i].symbol`.
          Missing series → asset is dropped from analytical surface.
        """
        cfg = self._config
        ts_now = datetime.now(UTC).isoformat()
        warnings: list[str] = []
        notes: dict[str, object] = {}
        constraints: list[str] = []
        current_weights = current_weights or {}

        # Drop assets without return history (analytical methods need it)
        kept: list[Asset] = []
        kept_returns: list[list[float]] = []
        for a in assets:
            series = returns_history.get(a.symbol)
            if series and len(series) >= 30:
                kept.append(a)
                kept_returns.append(series)
            else:
                warnings.append(f"missing_or_short_returns:{a.symbol}")

        if not kept:
            return self._empty_allocation(
                ts_now, regime, portfolio_value_usd, warnings + ["no_usable_assets"]
            )

        # Pick method
        chosen = method
        if chosen is None and regime is not None:
            chosen = cfg.regime_method_map.get(regime, cfg.default_method)
        if chosen is None:
            chosen = cfg.default_method
        if chosen not in ALL_METHODS:
            warnings.append(f"unknown_method:{chosen}|fallback:equal_weight")
            chosen = METHOD_EQUAL_WEIGHT

        # Cov matrix + expected returns
        cov = _covariance_matrix(kept_returns)
        cov_reg = _regularize(cov, cfg.cov_regularization_eps)
        expected = self._expected_returns(kept, returns_history)

        # Solve
        weights, method_used, solve_warnings = self._solve(
            chosen, kept, kept_returns, cov_reg, expected
        )
        warnings.extend(solve_warnings)

        # Crypto post-processing pipeline
        weights, capped_flags = self._apply_liquidity_caps(weights, kept)
        if any(capped_flags):
            constraints.append("liquidity_cap")

        weights, ex_active = self._apply_exchange_concentration(weights, kept)
        if ex_active:
            constraints.append("exchange_concentration")

        weights, sf_active = self._apply_stablecoin_floor(weights, kept, regime)
        if sf_active:
            constraints.append("stablecoin_floor")

        # Vol targeting (sets the gross leverage)
        weights_scaled, leverage, vt_active = self._apply_volatility_target(weights, cov_reg)
        if vt_active:
            constraints.append("vol_target")

        # Max-DD constraint (further scales leverage down if needed)
        leverage, dd_active, expected_dd = self._apply_max_drawdown_constraint(
            weights, kept_returns, leverage
        )
        if dd_active:
            constraints.append("max_drawdown")

        # Re-derive scaled weights with the (possibly reduced) leverage
        weights_scaled = [w * leverage for w in weights]

        # Expected portfolio metrics (forward-looking)
        port_mu_daily = sum(weights_scaled[i] * expected[i] for i in range(len(kept)))
        port_var_daily = _quadratic_form(weights_scaled, cov_reg)
        port_sigma_daily = math.sqrt(max(port_var_daily, 0.0))
        ann = cfg.annualization_factor
        expected_return_annual = port_mu_daily * ann
        expected_volatility_annual = port_sigma_daily * math.sqrt(ann)
        expected_sharpe = (
            (port_mu_daily - cfg.risk_free_rate_daily) / port_sigma_daily * math.sqrt(ann)
            if port_sigma_daily > 0
            else None
        )
        # Sortino: realized downside dev of the portfolio's historical replay
        n_obs = min(len(r) for r in kept_returns)
        replay = [
            sum(weights[i] * kept_returns[i][-n_obs:][t] for i in range(len(kept)))
            for t in range(n_obs)
        ]
        downside = [min(r - cfg.mar_for_sortino_daily, 0.0) for r in replay]
        downside_dev = math.sqrt(sum(x * x for x in downside) / max(len(downside), 1))
        expected_sortino = (
            (port_mu_daily - cfg.mar_for_sortino_daily) / downside_dev * math.sqrt(ann)
            if downside_dev > 0
            else None
        )

        # Build per-asset allocations
        allocations, max_drift, turnover_pct, est_turnover_usd = self._build_allocations(
            kept,
            weights,
            leverage,
            current_weights,
            portfolio_value_usd,
            cov_reg,
            expected,
            capped_flags,
            ann,
        )

        # Rebalance decision
        rebalance = (
            max_drift / 100.0 >= cfg.rebalance_drift_threshold
            or turnover_pct / 100.0 >= cfg.rebalance_total_drift_threshold
        )

        # Exposure metrics
        gross = sum(abs(w) for w in weights_scaled)
        net = sum(weights_scaled)
        cash_pct = max(0.0, 1.0 - sum(weights_scaled)) * 100.0
        stable_idx = [
            i
            for i, a in enumerate(kept)
            if a.is_stablecoin or a.symbol.lower() in cfg.stablecoin_set
        ]
        stablecoin_exposure = sum(weights_scaled[i] for i in stable_idx) * 100.0
        n_active = sum(1 for w in weights_scaled if w > cfg.min_trade_size_pct)

        return PortfolioAllocation(
            timestamp_utc=ts_now,
            method_used=method_used,
            regime=regime,
            target_volatility_annual=cfg.target_volatility_annual,
            portfolio_value_usd=portfolio_value_usd,
            expected_return_annual=expected_return_annual,
            expected_volatility_annual=expected_volatility_annual,
            expected_sharpe=expected_sharpe,
            expected_sortino=expected_sortino,
            expected_max_drawdown_pct=expected_dd,
            gross_leverage=gross,
            net_exposure_pct=net * 100.0,
            cash_pct=cash_pct,
            stablecoin_exposure_pct=stablecoin_exposure,
            n_active_positions=n_active,
            allocations=allocations,
            rebalance_required=rebalance,
            max_drift_pct=max_drift,
            turnover_pct=turnover_pct,
            estimated_turnover_usd=est_turnover_usd,
            constraints_active=constraints,
            warnings=warnings,
            notes=notes,
            inputs_hash=self._hash_inputs(kept, method_used, regime, portfolio_value_usd),
        )

    def _empty_allocation(
        self,
        ts: str,
        regime: str | None,
        portfolio_value_usd: float,
        warnings: list[str],
    ) -> PortfolioAllocation:
        return PortfolioAllocation(
            timestamp_utc=ts,
            method_used=METHOD_EQUAL_WEIGHT,
            regime=regime,
            target_volatility_annual=self._config.target_volatility_annual,
            portfolio_value_usd=portfolio_value_usd,
            expected_return_annual=None,
            expected_volatility_annual=None,
            expected_sharpe=None,
            expected_sortino=None,
            expected_max_drawdown_pct=None,
            gross_leverage=0.0,
            net_exposure_pct=0.0,
            cash_pct=100.0,
            stablecoin_exposure_pct=0.0,
            n_active_positions=0,
            allocations=[],
            rebalance_required=False,
            max_drift_pct=0.0,
            turnover_pct=0.0,
            estimated_turnover_usd=0.0,
            constraints_active=[],
            warnings=warnings,
            notes={},
            inputs_hash="sha256:empty",
        )
