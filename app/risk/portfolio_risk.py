"""Portfolio Risk Engine — institutional-grade VaR / ES / tail / stress.

Pure-Python implementation. No numpy / scipy dependency.

Computes:
- Historical VaR (empirical α-quantile of portfolio P&L)
- Parametric VaR (Gaussian, μ + σ·z)
- Cornish-Fisher VaR (skew/kurtosis adjusted — fat-tail aware)
- Student-t VaR (heavy-tailed parametric, ν=4 default for crypto)
- Monte Carlo VaR (correlated Cholesky draws, optional Student-t innovations)
- Expected Shortfall (historical, parametric, MC) — mean loss past VaR
- Tail risk (skew, excess kurt, Hill estimator, P(loss > k·σ))
- Drawdown distribution (max / p95 / avg / recovery bars / count)
- Correlation stress (lift off-diagonal to target — typical crypto-shock pattern)

Crypto-specific stress scenarios:
- flash_crash           — N% snap drop, illiquidity-amplified
- liquidation_cascade   — leveraged positions wiped + slippage
- stablecoin_depeg      — USDT/USDC-quoted notional haircut
- exchange_insolvency   — per-exchange recovery haircut, worst venue
- extreme_volatility    — 5× σ regime, parametric VaR re-evaluated
- correlation_breakdown — all pairs correlated to ~1, parametric VaR re-evaluated

Per-position attribution:
- risk_budget_pct       — Component VaR (Euler decomposition) / total VaR
- expected_downside_usd — standalone position VaR
- tail_exposure_usd     — MC-derived contribution to ES
- stress_exposure_usd   — max position loss across stress scenarios

Design:
- The engine is *analytics*, not an enforcer. Hard pre-trade gating remains
  `app.risk.engine.RiskEngine`.
- Robust: never raises on degenerate data; emits warnings instead.
- All outputs are immutable, JSON-serializable dataclasses.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
import statistics
from collections.abc import Callable
from datetime import UTC, datetime

from app.risk.portfolio_risk_models import (
    ALL_STRESS_SCENARIOS,
    STRESS_CORRELATION_BREAKDOWN,
    STRESS_EXCHANGE_INSOLVENCY,
    STRESS_EXTREME_VOLATILITY,
    STRESS_FLASH_CRASH,
    STRESS_LIQUIDATION_CASCADE,
    STRESS_STABLECOIN_DEPEG,
    PortfolioRiskConfig,
    PortfolioRiskReport,
    Position,
    PositionRisk,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Pure-stat helpers
# ============================================================================


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _inverse_norm_cdf(p: float) -> float:
    """Φ⁻¹(p) via bisection on _norm_cdf. Robust, ~1e-9 precision in 60 iter."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    lo, hi = -10.0, 10.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _quantile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation quantile of an *already-sorted* sequence."""
    n = len(sorted_values)
    if n == 0:
        raise ValueError("empty sequence")
    if n == 1:
        return sorted_values[0]
    if q <= 0.0:
        return sorted_values[0]
    if q >= 1.0:
        return sorted_values[-1]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_values[lo]
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def _skewness(xs: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mean = statistics.fmean(xs)
    var = statistics.pvariance(xs)
    if var <= 0.0:
        return 0.0
    sd = math.sqrt(var)
    m3 = sum((x - mean) ** 3 for x in xs) / n
    return m3 / (sd ** 3)


def _excess_kurtosis(xs: list[float]) -> float:
    n = len(xs)
    if n < 4:
        return 0.0
    mean = statistics.fmean(xs)
    var = statistics.pvariance(xs)
    if var <= 0.0:
        return 0.0
    m4 = sum((x - mean) ** 4 for x in xs) / n
    return m4 / (var ** 2) - 3.0


def _correlation(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    xs2 = xs[-n:]
    ys2 = ys[-n:]
    mx = statistics.fmean(xs2)
    my = statistics.fmean(ys2)
    sxx = sum((x - mx) ** 2 for x in xs2)
    syy = sum((y - my) ** 2 for y in ys2)
    sxy = sum((xs2[i] - mx) * (ys2[i] - my) for i in range(n))
    if sxx <= 0.0 or syy <= 0.0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def _covariance(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    xs2 = xs[-n:]
    ys2 = ys[-n:]
    mx = statistics.fmean(xs2)
    my = statistics.fmean(ys2)
    return sum((xs2[i] - mx) * (ys2[i] - my) for i in range(n)) / n


def _hill_tail_index(losses: list[float], k_pct: float) -> float | None:
    """Hill estimator of the tail index α (smaller α = fatter tails).

    `losses` are positive loss magnitudes. Uses the top k = max(5, k_pct·n)
    largest losses. Returns None if the sample is too small.
    """
    n = len(losses)
    if n < 30:
        return None
    sorted_losses = sorted(losses, reverse=True)
    k = max(5, int(round(k_pct * n)))
    k = min(k, n - 1)
    threshold = sorted_losses[k]
    if threshold <= 0.0:
        return None
    log_excesses = []
    for x in sorted_losses[:k]:
        if x > 0.0:
            log_excesses.append(math.log(x) - math.log(threshold))
    if not log_excesses:
        return None
    xi = sum(log_excesses) / len(log_excesses)
    if xi <= 0.0:
        return None
    return 1.0 / xi


# --- Cholesky decomposition (LL') ------------------------------------------


def _cholesky(matrix: list[list[float]], jitter: float = 1e-10) -> list[list[float]]:
    """Cholesky factor L of a symmetric positive-definite matrix.

    Adds `jitter` to the diagonal as a regularizer for near-singular cov.
    Returns L such that L·L' = A. On numerical failure clamps to small ε
    rather than raising — the engine's caller treats degenerate cov as a
    warning, not a crash.
    """
    n = len(matrix)
    chol = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(chol[i][k] * chol[j][k] for k in range(j))
            if i == j:
                val = matrix[i][i] + jitter - s
                if val <= 0.0:
                    val = jitter  # numerical floor
                chol[i][j] = math.sqrt(val)
            elif chol[j][j] <= 0.0:
                chol[i][j] = 0.0
            else:
                chol[i][j] = (matrix[i][j] - s) / chol[j][j]
    return chol


# --- Regularized incomplete beta + Student-t quantile ----------------------


def _betacf(a: float, b: float, x: float) -> float:
    """Continued-fraction representation of the incomplete beta function.
    Numerical Recipes-style; ~30 terms is enough for double precision."""
    eps = 3e-12
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_bt = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    bt = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _student_t_cdf_raw(t: float, df: float) -> float:
    """CDF of *unstandardized* Student-t (variance df/(df-2))."""
    x = df / (df + t * t)
    half = 0.5 * _betai(df / 2.0, 0.5, x)
    return 1.0 - half if t > 0 else half


def _student_t_quantile_raw(p: float, df: float) -> float:
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    lo, hi = -50.0, 50.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if _student_t_cdf_raw(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _student_t_quantile_standardized(p: float, df: float) -> float:
    """Quantile of standardized Student-t (unit variance)."""
    if df <= 2.0:
        return _inverse_norm_cdf(p)
    raw = _student_t_quantile_raw(p, df)
    return raw * math.sqrt((df - 2.0) / df)


def _student_t_sample(df: float, rng: random.Random) -> float:
    """Draw from standardized Student-t (unit variance). Uses Z / √(χ²/ν) and
    rescales by √((df-2)/df) so output has unit variance."""
    if df <= 2.0:
        return rng.gauss(0.0, 1.0)
    z = rng.gauss(0.0, 1.0)
    chi_sq = sum(rng.gauss(0.0, 1.0) ** 2 for _ in range(int(df)))
    if chi_sq <= 0.0:
        return 0.0
    raw = z / math.sqrt(chi_sq / df)
    return raw * math.sqrt((df - 2.0) / df)


# ============================================================================
# Engine
# ============================================================================


class PortfolioRiskEngine:
    """Institutional portfolio-risk analytics engine."""

    def __init__(self, config: PortfolioRiskConfig | None = None) -> None:
        self._config = config or PortfolioRiskConfig()

    # ----------------------------------------------------------- helpers

    def _hash_inputs(
        self, positions: list[Position], aligned_n: int
    ) -> str:
        payload_parts = [str(aligned_n)]
        for p in positions:
            payload_parts.append(
                f"{p.symbol}|{p.notional_usd:.6f}|{p.leverage}|"
                f"{p.exchange}|{p.quote_currency}"
            )
        payload = "||".join(payload_parts)
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _align_returns(
        self, positions: list[Position], returns_history: dict[str, list[float]]
    ) -> tuple[list[str], list[list[float]], int, list[str]]:
        """Return (symbols, aligned_returns, common_n, warnings).

        Aligns histories by truncating to the shortest length (end-aligned).
        Symbols missing from `returns_history` are dropped with a warning.
        """
        warnings: list[str] = []
        present: list[str] = []
        for p in positions:
            if p.symbol in returns_history and returns_history[p.symbol]:
                present.append(p.symbol)
            else:
                warnings.append(f"missing_returns:{p.symbol}")
        if not present:
            return [], [], 0, warnings
        common_n = min(len(returns_history[s]) for s in present)
        aligned = [returns_history[s][-common_n:] for s in present]
        return present, aligned, common_n, warnings

    def _portfolio_returns(
        self,
        weights: dict[str, float],
        symbols: list[str],
        aligned_returns: list[list[float]],
    ) -> list[float]:
        if not aligned_returns:
            return []
        n = len(aligned_returns[0])
        out: list[float] = []
        for t in range(n):
            r_t = 0.0
            for i, sym in enumerate(symbols):
                r_t += weights.get(sym, 0.0) * aligned_returns[i][t]
            out.append(r_t)
        return out

    # ------------------------------------------------------------ VaR/ES

    def _historical_var_es(
        self, portfolio_returns: list[float], conf: float, value: float, horizon: int
    ) -> tuple[float | None, float | None]:
        if len(portfolio_returns) < self._config.min_returns_for_var:
            return None, None
        scaled = [r * math.sqrt(horizon) for r in portfolio_returns]
        sorted_returns = sorted(scaled)
        q = 1.0 - conf
        var_ret = _quantile(sorted_returns, q)
        var_usd = max(0.0, -var_ret * value)
        # ES: mean of returns at or below VaR threshold
        threshold = var_ret
        tail = [r for r in sorted_returns if r <= threshold]
        if not tail:
            return var_usd, var_usd
        es_ret = statistics.fmean(tail)
        es_usd = max(0.0, -es_ret * value)
        return var_usd, es_usd

    def _parametric_var_es(
        self, mu: float, sigma: float, conf: float, value: float, horizon: int
    ) -> tuple[float | None, float | None]:
        if sigma <= 0.0:
            return None, None
        sqrt_h = math.sqrt(horizon)
        z = _inverse_norm_cdf(1.0 - conf)
        var_ret = mu * horizon + sigma * sqrt_h * z
        var_usd = max(0.0, -var_ret * value)
        # ES under Gaussian: μ - σ·φ(z)/(1-conf)
        phi_z = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
        es_ret = mu * horizon - sigma * sqrt_h * phi_z / (1.0 - conf)
        es_usd = max(0.0, -es_ret * value)
        return var_usd, es_usd

    def _cornish_fisher_var(
        self,
        mu: float,
        sigma: float,
        skew: float,
        excess_kurt: float,
        conf: float,
        value: float,
        horizon: int,
    ) -> float | None:
        if sigma <= 0.0:
            return None
        z = _inverse_norm_cdf(1.0 - conf)
        z2, z3 = z * z, z * z * z
        z_cf = (
            z
            + (z2 - 1.0) * skew / 6.0
            + (z3 - 3.0 * z) * excess_kurt / 24.0
            - (2.0 * z3 - 5.0 * z) * (skew * skew) / 36.0
        )
        var_ret = mu * horizon + sigma * math.sqrt(horizon) * z_cf
        return max(0.0, -var_ret * value)

    def _student_t_var(
        self,
        mu: float,
        sigma: float,
        df: float,
        conf: float,
        value: float,
        horizon: int,
    ) -> float | None:
        if sigma <= 0.0:
            return None
        t_q = _student_t_quantile_standardized(1.0 - conf, df)
        var_ret = mu * horizon + sigma * math.sqrt(horizon) * t_q
        return max(0.0, -var_ret * value)

    def _monte_carlo_var_es(
        self,
        symbols: list[str],
        aligned_returns: list[list[float]],
        weights: dict[str, float],
        n_paths: int,
        conf: float,
        seed: int,
        value: float,
        horizon: int,
        use_student_t: bool,
        df: float,
    ) -> tuple[float | None, float | None, list[tuple[float, list[float]]] | None]:
        """Monte-Carlo simulation. Returns (VaR, ES, [(portfolio_pnl, per_sym_pnl)]).

        The per-path per-symbol PnL list is used downstream for tail attribution.
        """
        n_assets = len(symbols)
        if n_assets == 0 or len(aligned_returns[0]) < 2:
            return None, None, None

        # Mean and covariance of returns
        means = [statistics.fmean(r) for r in aligned_returns]
        cov = [[0.0] * n_assets for _ in range(n_assets)]
        for i in range(n_assets):
            for j in range(n_assets):
                cov[i][j] = _covariance(aligned_returns[i], aligned_returns[j])

        chol = _cholesky(cov, jitter=self._config.cholesky_jitter)

        rng = random.Random(seed)
        weight_vec = [weights[s] for s in symbols]
        pnl_paths: list[float] = []
        per_path_returns: list[list[float]] = []

        sample_fn: Callable[[], float] = (
            (lambda: _student_t_sample(df, rng))
            if use_student_t
            else (lambda: rng.gauss(0.0, 1.0))
        )

        for _ in range(n_paths):
            # Aggregate over horizon — sum of `horizon` independent draws
            asset_returns = [0.0] * n_assets
            for _h in range(horizon):
                z = [sample_fn() for _ in range(n_assets)]
                shock = [
                    sum(chol[i][k] * z[k] for k in range(n_assets))
                    for i in range(n_assets)
                ]
                for i in range(n_assets):
                    asset_returns[i] += means[i] + shock[i]

            port_ret = sum(weight_vec[i] * asset_returns[i] for i in range(n_assets))
            pnl_paths.append(port_ret)
            per_path_returns.append(asset_returns)

        sorted_pnl = sorted(pnl_paths)
        var_ret = _quantile(sorted_pnl, 1.0 - conf)
        var_usd = max(0.0, -var_ret * value)

        tail = [r for r in sorted_pnl if r <= var_ret]
        es_usd = max(0.0, -statistics.fmean(tail) * value) if tail else var_usd

        # Pair (port_pnl, per_asset_returns) for downstream attribution
        paired = list(zip(pnl_paths, per_path_returns, strict=False))
        return var_usd, es_usd, paired

    # ----------------------------------------------------------- drawdown

    def _drawdown_distribution(
        self, returns: list[float]
    ) -> tuple[float | None, float | None, float | None, float | None, int]:
        """Return (max_dd_pct, p95_dd_pct, avg_dd_pct, avg_recovery_bars, count).

        Drawdowns are positive percentages (0..100). Equity is the cumulative
        product of (1 + r). A drawdown segment starts when equity falls from a
        peak and ends when equity reclaims that peak (recovery), or at end of
        series (open drawdown — counted with its current depth).
        """
        if len(returns) < 5:
            return None, None, None, None, 0

        equity = [1.0]
        for r in returns:
            equity.append(equity[-1] * (1.0 + r))

        peak = equity[0]
        peak_idx = 0
        in_dd = False
        dd_depth = 0.0
        dd_segments: list[tuple[float, int]] = []  # (depth_pct, recovery_bars)

        for i in range(1, len(equity)):
            v = equity[i]
            if v > peak:
                if in_dd:
                    dd_segments.append((dd_depth, i - peak_idx))
                    in_dd = False
                    dd_depth = 0.0
                peak = v
                peak_idx = i
            else:
                cur_dd = 0.0 if peak <= 0 else (peak - v) / peak * 100.0
                if cur_dd > dd_depth:
                    dd_depth = cur_dd
                in_dd = cur_dd > 0.0

        if in_dd and dd_depth > 0.0:
            dd_segments.append((dd_depth, len(equity) - 1 - peak_idx))

        if not dd_segments:
            return 0.0, 0.0, 0.0, 0.0, 0

        depths = [d for d, _ in dd_segments]
        recoveries = [r for _, r in dd_segments]
        max_dd = max(depths)
        p95 = _quantile(sorted(depths), self._config.drawdown_quantile)
        avg = statistics.fmean(depths)
        avg_recovery = statistics.fmean(recoveries) if recoveries else 0.0
        return max_dd, p95, avg, float(avg_recovery), len(dd_segments)

    # -------------------------------------------------------- correlation

    def _correlation_stress_var(
        self,
        symbols: list[str],
        aligned_returns: list[list[float]],
        weights: dict[str, float],
        target_corr: float,
        conf: float,
        value: float,
        horizon: int,
    ) -> float | None:
        n = len(symbols)
        if n < 1 or not aligned_returns:
            return None
        sigmas = [math.sqrt(_covariance(r, r)) for r in aligned_returns]
        means = [statistics.fmean(r) for r in aligned_returns]
        # Stressed cov: σ_i σ_j × target_corr off-diagonal, σ_i² on-diagonal
        stressed_cov = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    stressed_cov[i][j] = sigmas[i] * sigmas[i]
                else:
                    stressed_cov[i][j] = target_corr * sigmas[i] * sigmas[j]
        weight_vec = [weights[s] for s in symbols]
        port_var = sum(
            weight_vec[i] * weight_vec[j] * stressed_cov[i][j]
            for i in range(n) for j in range(n)
        )
        port_sigma = math.sqrt(max(port_var, 0.0))
        port_mean = sum(weight_vec[i] * means[i] for i in range(n))
        z = _inverse_norm_cdf(1.0 - conf)
        var_ret = port_mean * horizon + port_sigma * math.sqrt(horizon) * z
        return max(0.0, -var_ret * value)

    # -------------------------------------------------- crypto stress

    def _stress_flash_crash(
        self, positions: list[Position]
    ) -> tuple[float, dict[str, float]]:
        cfg = self._config
        per_sym: dict[str, float] = {}
        net_pnl = 0.0
        for p in positions:
            illiq = max(0.0, 1.0 - p.liquidity_score)
            shock = cfg.flash_crash_pct * (1.0 + cfg.flash_crash_illiquid_amplifier * illiq)
            # Long takes -shock·notional, short takes +shock·notional
            pnl_pos = -math.copysign(1.0, p.notional_usd) * abs(p.notional_usd) * shock
            if p.notional_usd == 0.0:
                pnl_pos = 0.0
            net_pnl += pnl_pos
            per_sym[p.symbol] = per_sym.get(p.symbol, 0.0) + max(0.0, -pnl_pos)
        loss = max(0.0, -net_pnl)
        return loss, per_sym

    def _stress_liquidation_cascade(
        self, positions: list[Position]
    ) -> tuple[float, dict[str, float]]:
        cfg = self._config
        per_sym: dict[str, float] = {}
        total_loss = 0.0
        leveraged = [
            p for p in positions if p.leverage >= cfg.liquidation_cascade_threshold_leverage
        ]
        n_liquidated = len(leveraged)
        cascade_factor = 1.0 + 0.10 * max(0, n_liquidated - 1)  # 10 % per added liq
        for p in leveraged:
            collateral_loss = abs(p.notional_usd) / max(p.leverage, 1.0)
            slippage_loss = abs(p.notional_usd) * cfg.liquidation_cascade_slippage_pct
            loss = (collateral_loss + slippage_loss) * cascade_factor
            total_loss += loss
            per_sym[p.symbol] = per_sym.get(p.symbol, 0.0) + loss
        return total_loss, per_sym

    def _stress_stablecoin_depeg(
        self, positions: list[Position]
    ) -> tuple[float, dict[str, float]]:
        cfg = self._config
        per_sym: dict[str, float] = {}
        total_loss = 0.0
        for p in positions:
            quote = p.quote_currency.lower()
            if quote in cfg.stablecoin_set:
                # Quote-asset depeg: notional value in USD drops by depeg_pct
                loss = abs(p.notional_usd) * cfg.stablecoin_depeg_pct
                total_loss += loss
                per_sym[p.symbol] = per_sym.get(p.symbol, 0.0) + loss
        return total_loss, per_sym

    def _stress_exchange_insolvency(
        self, positions: list[Position]
    ) -> tuple[float, dict[str, float]]:
        cfg = self._config
        per_exchange: dict[str, list[Position]] = {}
        for p in positions:
            per_exchange.setdefault(p.exchange.lower(), []).append(p)

        worst_loss = 0.0
        worst_per_sym: dict[str, float] = {}
        for ex, ex_positions in per_exchange.items():
            haircut = cfg.exchange_haircut.get(ex, cfg.exchange_haircut.get("unknown", 0.7))
            ex_total = 0.0
            ex_per_sym: dict[str, float] = {}
            for p in ex_positions:
                loss = abs(p.notional_usd) * haircut
                ex_total += loss
                ex_per_sym[p.symbol] = ex_per_sym.get(p.symbol, 0.0) + loss
            if ex_total > worst_loss:
                worst_loss = ex_total
                worst_per_sym = ex_per_sym
        return worst_loss, worst_per_sym

    def _stress_extreme_volatility(
        self,
        symbols: list[str],
        aligned_returns: list[list[float]],
        weights: dict[str, float],
        value: float,
        horizon: int,
        conf: float,
    ) -> tuple[float, dict[str, float]]:
        cfg = self._config
        if not aligned_returns:
            return 0.0, {}
        sigmas = [math.sqrt(_covariance(r, r)) for r in aligned_returns]
        means = [statistics.fmean(r) for r in aligned_returns]
        weight_vec = [weights[s] for s in symbols]
        # Stressed sigma: σ × multiplier
        mult = cfg.extreme_volatility_multiplier
        n = len(symbols)
        # Approximate cov with current correlation but stressed sigmas
        port_var = 0.0
        for i in range(n):
            for j in range(n):
                rho = _correlation(aligned_returns[i], aligned_returns[j]) if i != j else 1.0
                stressed_cov_ij = (mult * sigmas[i]) * (mult * sigmas[j]) * rho
                port_var += weight_vec[i] * weight_vec[j] * stressed_cov_ij
        port_sigma = math.sqrt(max(port_var, 0.0))
        port_mean = sum(weight_vec[i] * means[i] for i in range(n))
        z = _inverse_norm_cdf(1.0 - conf)
        var_ret = port_mean * horizon + port_sigma * math.sqrt(horizon) * z
        var_usd = max(0.0, -var_ret * value)

        # Per-symbol attribution: standalone stressed VaR
        per_sym: dict[str, float] = {}
        for i, sym in enumerate(symbols):
            sym_stressed_sigma = mult * sigmas[i]
            sym_var_ret = means[i] * horizon + sym_stressed_sigma * math.sqrt(horizon) * z
            per_sym[sym] = max(0.0, -sym_var_ret * abs(weight_vec[i]) * value)
        return var_usd, per_sym

    # ------------------------------------------------- per-position risk

    def _component_var_attribution(
        self,
        symbols: list[str],
        aligned_returns: list[list[float]],
        weights: dict[str, float],
        portfolio_var_usd: float | None,
        value: float,
        horizon: int,
        conf: float,
    ) -> dict[str, float]:
        """Component VaR by Euler decomposition — includes the mean term so
        Σ_i component_i = parametric VaR.

        Parametric VaR = -(μ_p · h + σ_p · √h · z) · V,
        with μ_p = Σ w_i μ_i and σ_p² = Σ_ij w_i w_j σ_ij.

        Component_i = -(μ_i · h + z · σ_ip · √h / σ_p) · w_i · V
        where σ_ip = Σ_j w_j σ_ij.
        """
        n = len(symbols)
        if n == 0 or portfolio_var_usd is None or portfolio_var_usd <= 0.0:
            return dict.fromkeys(symbols, 0.0)
        cov = [
            [_covariance(aligned_returns[i], aligned_returns[j]) for j in range(n)]
            for i in range(n)
        ]
        weight_vec = [weights[s] for s in symbols]
        means = [statistics.fmean(r) for r in aligned_returns]
        port_var_ret = sum(
            weight_vec[i] * weight_vec[j] * cov[i][j]
            for i in range(n)
            for j in range(n)
        )
        port_sigma = math.sqrt(max(port_var_ret, 0.0))
        if port_sigma <= 0.0:
            return dict.fromkeys(symbols, 0.0)
        z = _inverse_norm_cdf(1.0 - conf)
        sqrt_h = math.sqrt(horizon)
        components: dict[str, float] = {}
        for i, s in enumerate(symbols):
            sigma_ip = sum(weight_vec[j] * cov[i][j] for j in range(n))
            comp = -(
                means[i] * horizon + z * sigma_ip * sqrt_h / port_sigma
            ) * weight_vec[i] * value
            components[s] = comp  # already in loss-positive convention
        return components

    def _tail_attribution_from_mc(
        self,
        symbols: list[str],
        weights: dict[str, float],
        mc_paths: list[tuple[float, list[float]]] | None,
        conf: float,
        value: float,
    ) -> dict[str, float]:
        """Per-symbol contribution to ES from MC paths."""
        if not mc_paths:
            return dict.fromkeys(symbols, 0.0)
        sorted_paths = sorted(mc_paths, key=lambda p: p[0])
        cutoff = int((1.0 - conf) * len(sorted_paths))
        if cutoff < 1:
            cutoff = max(1, len(sorted_paths) // 100)
        tail_paths = sorted_paths[:cutoff]
        if not tail_paths:
            return dict.fromkeys(symbols, 0.0)
        per_sym: dict[str, float] = dict.fromkeys(symbols, 0.0)
        for _port, asset_returns in tail_paths:
            for i, s in enumerate(symbols):
                # Position contribution to portfolio loss along this tail path
                contribution = -(weights[s] * asset_returns[i]) * value
                per_sym[s] += max(0.0, contribution)
        for s in per_sym:
            per_sym[s] /= len(tail_paths)
        return per_sym

    # ------------------------------------------------------------ compute

    def compute(  # noqa: C901 — single orchestrator, deliberately linear
        self,
        *,
        positions: list[Position],
        returns_history: dict[str, list[float]],
        confidence_level: float | None = None,
        horizon_bars: int | None = None,
        n_monte_carlo: int | None = None,
    ) -> PortfolioRiskReport:
        """Compute the portfolio risk report.

        `returns_history` maps symbol → list of log-returns (oldest first).
        Series may have different lengths; the engine end-aligns them to the
        shortest common window. Symbols missing from `returns_history` are
        dropped from the analytical surface but kept on stress-only positions.
        """
        cfg = self._config
        conf = confidence_level if confidence_level is not None else cfg.confidence_level
        horizon = horizon_bars if horizon_bars is not None else cfg.horizon_bars
        n_mc = n_monte_carlo if n_monte_carlo is not None else cfg.n_monte_carlo

        ts_now = datetime.now(UTC).isoformat()
        warnings: list[str] = []
        notes: dict[str, object] = {}

        # Drop zero-notional
        positions = [p for p in positions if p.notional_usd != 0.0]

        if not positions:
            return self._empty_report(ts_now, conf, horizon, warnings + ["no_positions"])

        gross = sum(abs(p.notional_usd) for p in positions)
        net = sum(p.notional_usd for p in positions)
        if gross <= 0.0:
            return self._empty_report(ts_now, conf, horizon, warnings + ["zero_gross_exposure"])

        # Align return histories
        symbols, aligned_returns, common_n, align_warnings = self._align_returns(
            positions, returns_history
        )
        warnings.extend(align_warnings)

        sufficient = (
            len(symbols) >= 1
            and common_n >= cfg.min_returns_for_var
        )

        # Weights (signed) by gross exposure
        weights: dict[str, float] = {
            p.symbol: p.notional_usd / gross for p in positions
        }

        # Defaults for "no analytical data" branch — stress-only report
        historical_var = parametric_var = cf_var = t_var = mc_var = None
        historical_es = parametric_es = mc_es = None
        skew = excess_kurt = None
        tail_index = tail_prob_exceed = None
        max_dd = p95_dd = avg_dd = avg_recov = None
        dd_count = 0
        avg_corr = corr_stress_var = None
        mc_paths: list[tuple[float, list[float]]] | None = None
        portfolio_returns: list[float] = []

        if sufficient:
            portfolio_returns = self._portfolio_returns(weights, symbols, aligned_returns)
            mu = statistics.fmean(portfolio_returns) if portfolio_returns else 0.0
            sigma = statistics.pstdev(portfolio_returns) if len(portfolio_returns) > 1 else 0.0

            # VaR / ES suite
            historical_var, historical_es = self._historical_var_es(
                portfolio_returns, conf, gross, horizon
            )
            parametric_var, parametric_es = self._parametric_var_es(
                mu, sigma, conf, gross, horizon
            )

            skew = _skewness(portfolio_returns)
            excess_kurt = _excess_kurtosis(portfolio_returns)
            cf_var = self._cornish_fisher_var(
                mu, sigma, skew, excess_kurt, conf, gross, horizon
            )
            t_var = self._student_t_var(
                mu, sigma, cfg.student_t_df, conf, gross, horizon
            )

            mc_var, mc_es, mc_paths = self._monte_carlo_var_es(
                symbols, aligned_returns, weights,
                n_mc, conf, cfg.mc_seed, gross, horizon,
                cfg.mc_use_student_t, cfg.student_t_df,
            )

            # Tail risk
            losses = [-r for r in portfolio_returns if r < 0.0]
            tail_index = _hill_tail_index(losses, cfg.hill_estimator_k_pct)
            if sigma > 0.0:
                k_sigma = cfg.tail_prob_threshold_sigma * sigma
                exceed = sum(1 for r in portfolio_returns if -r > k_sigma)
                tail_prob_exceed = exceed / len(portfolio_returns)

            # Drawdown
            max_dd, p95_dd, avg_dd, avg_recov, dd_count = self._drawdown_distribution(
                portfolio_returns
            )

            # Correlation
            n = len(symbols)
            if n >= 2 and common_n >= cfg.min_returns_for_baseline_corr:
                pair_corrs: list[float] = []
                for i in range(n):
                    for j in range(i + 1, n):
                        pair_corrs.append(
                            _correlation(aligned_returns[i], aligned_returns[j])
                        )
                if pair_corrs:
                    avg_corr = statistics.fmean(pair_corrs)
            corr_stress_var = self._correlation_stress_var(
                symbols, aligned_returns, weights,
                cfg.correlation_stress_target, conf, gross, horizon,
            )
        else:
            warnings.append("insufficient_returns_for_var")

        # ------------------------------------------------ stress scenarios
        stress_results: dict[str, float] = {}
        stress_per_position: dict[str, dict[str, float]] = {
            p.symbol: {} for p in positions
        }

        scenarios: list[
            tuple[str, tuple[float, dict[str, float]]]
        ] = [
            (STRESS_FLASH_CRASH, self._stress_flash_crash(positions)),
            (STRESS_LIQUIDATION_CASCADE, self._stress_liquidation_cascade(positions)),
            (STRESS_STABLECOIN_DEPEG, self._stress_stablecoin_depeg(positions)),
            (STRESS_EXCHANGE_INSOLVENCY, self._stress_exchange_insolvency(positions)),
        ]
        if sufficient:
            scenarios.append(
                (
                    STRESS_EXTREME_VOLATILITY,
                    self._stress_extreme_volatility(
                        symbols, aligned_returns, weights, gross, horizon, conf
                    ),
                )
            )
            scenarios.append(
                (
                    STRESS_CORRELATION_BREAKDOWN,
                    (
                        corr_stress_var or 0.0,
                        # Per-symbol attribution: weight × component-VaR-style proxy
                        {
                            s: abs(weights[s]) * (corr_stress_var or 0.0)
                            for s in symbols
                        },
                    ),
                )
            )

        worst_name = ""
        worst_loss = 0.0
        for name, (total_loss, per_sym) in scenarios:
            stress_results[name] = total_loss
            for sym, loss in per_sym.items():
                if sym in stress_per_position:
                    stress_per_position[sym][name] = max(
                        stress_per_position[sym].get(name, 0.0), loss
                    )
            if total_loss > worst_loss:
                worst_loss = total_loss
                worst_name = name

        # Sanity: ensure all named scenarios appear in the dict (zero if absent)
        for name in ALL_STRESS_SCENARIOS:
            stress_results.setdefault(name, 0.0)

        # ------------------------------------------------ per-position

        component_vars = self._component_var_attribution(
            symbols, aligned_returns, weights, parametric_var, gross, horizon, conf
        ) if sufficient else dict.fromkeys(symbols, 0.0)
        tail_attribution = self._tail_attribution_from_mc(
            symbols, weights, mc_paths, conf, gross
        ) if sufficient else dict.fromkeys(symbols, 0.0)

        z_param = _inverse_norm_cdf(1.0 - conf) if sufficient else 0.0
        sym_sigmas: dict[str, float] = {}
        if sufficient:
            for i, s in enumerate(symbols):
                sym_sigmas[s] = math.sqrt(_covariance(aligned_returns[i], aligned_returns[i]))

        position_risks: list[PositionRisk] = []
        param_var = parametric_var or 0.0
        for p in positions:
            weight_pct = abs(p.notional_usd) / gross * 100.0
            comp = component_vars.get(p.symbol, 0.0)
            risk_budget_pct = (comp / param_var * 100.0) if param_var > 0.0 else 0.0
            sigma_i = sym_sigmas.get(p.symbol, 0.0)
            standalone_var = max(
                0.0,
                -(sigma_i * math.sqrt(horizon) * z_param) * abs(p.notional_usd),
            ) if sufficient and sigma_i > 0.0 else 0.0
            stress_breakdown = stress_per_position.get(p.symbol, {})
            stress_exposure = max(stress_breakdown.values()) if stress_breakdown else 0.0
            position_risks.append(
                PositionRisk(
                    symbol=p.symbol,
                    notional_usd=p.notional_usd,
                    weight_pct=weight_pct,
                    risk_budget_pct=risk_budget_pct,
                    expected_downside_usd=standalone_var,
                    tail_exposure_usd=tail_attribution.get(p.symbol, 0.0),
                    stress_exposure_usd=stress_exposure,
                    stress_breakdown=stress_breakdown,
                )
            )

        return PortfolioRiskReport(
            timestamp_utc=ts_now,
            portfolio_value_usd=gross,
            gross_exposure_usd=gross,
            net_exposure_usd=net,
            confidence_level=conf,
            horizon_bars=horizon,
            historical_var=historical_var,
            parametric_var=parametric_var,
            cornish_fisher_var=cf_var,
            student_t_var=t_var,
            monte_carlo_var=mc_var,
            historical_es=historical_es,
            parametric_es=parametric_es,
            monte_carlo_es=mc_es,
            portfolio_skew=skew,
            portfolio_excess_kurtosis=excess_kurt,
            tail_index=tail_index,
            tail_prob_threshold_sigma=cfg.tail_prob_threshold_sigma,
            tail_prob_exceedance=tail_prob_exceed,
            max_drawdown_pct=max_dd,
            drawdown_p95_pct=p95_dd,
            avg_drawdown_pct=avg_dd,
            avg_recovery_bars=avg_recov,
            drawdown_count=dd_count,
            avg_pairwise_correlation=avg_corr,
            correlation_stress_var=corr_stress_var,
            stress_scenarios=stress_results,
            worst_case_stress_usd=worst_loss,
            worst_case_stress_name=worst_name,
            positions=position_risks,
            sample_size=common_n,
            inputs_hash=self._hash_inputs(positions, common_n),
            warnings=warnings,
            notes=notes,
        )

    def _empty_report(
        self,
        ts: str,
        conf: float,
        horizon: int,
        warnings: list[str],
    ) -> PortfolioRiskReport:
        return PortfolioRiskReport(
            timestamp_utc=ts,
            portfolio_value_usd=0.0,
            gross_exposure_usd=0.0,
            net_exposure_usd=0.0,
            confidence_level=conf,
            horizon_bars=horizon,
            historical_var=None,
            parametric_var=None,
            cornish_fisher_var=None,
            student_t_var=None,
            monte_carlo_var=None,
            historical_es=None,
            parametric_es=None,
            monte_carlo_es=None,
            portfolio_skew=None,
            portfolio_excess_kurtosis=None,
            tail_index=None,
            tail_prob_threshold_sigma=self._config.tail_prob_threshold_sigma,
            tail_prob_exceedance=None,
            max_drawdown_pct=None,
            drawdown_p95_pct=None,
            avg_drawdown_pct=None,
            avg_recovery_bars=None,
            drawdown_count=0,
            avg_pairwise_correlation=None,
            correlation_stress_var=None,
            stress_scenarios=dict.fromkeys(ALL_STRESS_SCENARIOS, 0.0),
            worst_case_stress_usd=0.0,
            worst_case_stress_name="",
            positions=[],
            sample_size=0,
            inputs_hash="sha256:empty",
            warnings=warnings,
        )
