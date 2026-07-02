"""Volatility Engine — institutional-grade volatility assessment.

Pure-Python implementation. No numpy/scipy dependency.

Computes:
- ATR (Wilder), reused from app.market_data.indicators
- Historical volatility (rolling stdev of log-returns, annualized)
- Realized volatility (sqrt of summed squared returns, annualized)
- Intraday volatility (Garman-Klass + Parkinson — OHLC-aware estimators)
- EWMA volatility (RiskMetrics-style λ-decay)
- Volatility clustering via ρ₁(r²) lag-1 autocorrelation of squared returns
- Regime classification (low / normal / elevated / high / crisis)
- Liquidity-adjusted volatility (volume + spread penalty)

Outputs:
- Volatility regime (string label)
- Expected move (1 bar + 1 day)
- Stop distance (regime-aware ATR multiplier)
- Leverage recommendation
- Max position size %
- Liquidation risk score

Design:
- The engine is a *recommender*, not an enforcer. The hard pre-order gate
  remains `app.risk.engine.RiskEngine`.
- All methods are pure functions over OHLCV lists; the compute() entry point
  is robust: it never raises on bad data, returns warnings instead.
- Outputs are immutable dataclasses → safe to log, persist, and JSON-serialize.
"""

from __future__ import annotations

import hashlib
import logging
import math
import statistics
from typing import Final

from app.market_data.indicators import compute_atr
from app.market_data.models import OHLCV, Ticker
from app.risk.volatility_models import (
    ALL_REGIMES,
    CLUSTER_MODERATE,
    CLUSTER_NONE,
    CLUSTER_STRONG,
    CLUSTER_WEAK,
    REGIME_CRISIS,
    REGIME_ELEVATED,
    REGIME_HIGH,
    REGIME_LOW,
    REGIME_NORMAL,
    REGIME_UNKNOWN,
    VolatilityConfig,
    VolatilityRegimeOutput,
)

logger = logging.getLogger(__name__)


# Annualization factor per timeframe (bars per year, crypto 24/7)
_ANNUAL_FACTORS: Final[dict[str, int]] = {
    "1m": 365 * 1440,
    "5m": 365 * 288,
    "15m": 365 * 96,
    "30m": 365 * 48,
    "1h": 365 * 24,
    "2h": 365 * 12,
    "4h": 365 * 6,
    "6h": 365 * 4,
    "12h": 365 * 2,
    "1d": 365,
    "1w": 52,
    "1M": 12,
}

# ============================================================================
# Pure stat helpers
# ============================================================================


def _norm_cdf(x: float) -> float:
    """Standard normal CDF Φ(x) via stdlib erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _safe_log(numerator: float, denominator: float) -> float | None:
    """Return ln(num/den) or None if either side is non-positive."""
    if numerator <= 0.0 or denominator <= 0.0:
        return None
    return math.log(numerator / denominator)


def _log_returns(closes: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        cur = closes[i]
        if prev > 0.0 and cur > 0.0:
            out.append(math.log(cur / prev))
    return out


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _autocorr_lag1(series: list[float]) -> float:
    """Pearson autocorrelation at lag 1. Returns 0.0 for degenerate inputs."""
    n = len(series)
    if n < 3:
        return 0.0
    mean = statistics.fmean(series)
    var = sum((x - mean) ** 2 for x in series)
    if var <= 0.0:
        return 0.0
    cov = sum((series[i] - mean) * (series[i - 1] - mean) for i in range(1, n))
    return cov / var


# ============================================================================
# Volatility estimators (annualized as fractions, e.g. 0.65 = 65% annual)
# ============================================================================


def historical_volatility(
    returns: list[float],
    window: int,
    bars_per_year: int,
) -> float | None:
    """Annualized stdev of log-returns over the last `window` bars."""
    if len(returns) < max(window, 2):
        return None
    sample = returns[-window:]
    if len(sample) < 2:
        return None
    sigma_per_bar = statistics.pstdev(sample)
    return sigma_per_bar * math.sqrt(bars_per_year)


def realized_volatility(
    returns: list[float],
    window: int,
    bars_per_year: int,
) -> float | None:
    """Annualized realized vol = sqrt((bars_per_year / window) · Σ r_i²)."""
    if len(returns) < window or window < 1:
        return None
    sample = returns[-window:]
    sum_sq = sum(r * r for r in sample)
    if sum_sq <= 0.0:
        return 0.0
    return math.sqrt(sum_sq * (bars_per_year / window))


def parkinson_volatility(
    candles: list[OHLCV],
    window: int,
    bars_per_year: int,
) -> float | None:
    """Parkinson estimator: σ² = 1/(4·ln 2) · mean(ln(H/L)²)."""
    if len(candles) < window:
        return None
    sample = candles[-window:]
    contributions: list[float] = []
    for c in sample:
        ln_hl = _safe_log(c.high, c.low)
        if ln_hl is None:
            continue
        contributions.append(ln_hl * ln_hl)
    if not contributions:
        return None
    mean_sq = statistics.fmean(contributions)
    var_per_bar = mean_sq / (4.0 * math.log(2.0))
    return math.sqrt(var_per_bar * bars_per_year)


def garman_klass_volatility(
    candles: list[OHLCV],
    window: int,
    bars_per_year: int,
) -> float | None:
    """Garman-Klass: σ² = mean(0.5·ln(H/L)² − (2·ln 2 − 1)·ln(C/O)²)."""
    if len(candles) < window:
        return None
    sample = candles[-window:]
    k = 2.0 * math.log(2.0) - 1.0
    contributions: list[float] = []
    for c in sample:
        ln_hl = _safe_log(c.high, c.low)
        ln_co = _safe_log(c.close, c.open)
        if ln_hl is None or ln_co is None:
            continue
        contributions.append(0.5 * ln_hl * ln_hl - k * ln_co * ln_co)
    if not contributions:
        return None
    mean_var = statistics.fmean(contributions)
    if mean_var <= 0.0:
        # GK can be negative on degenerate bars; clip to floor
        return 0.0
    return math.sqrt(mean_var * bars_per_year)


def ewma_volatility(
    returns: list[float],
    lam: float,
    bars_per_year: int,
) -> float | None:
    """RiskMetrics EWMA: σ²_t = λ·σ²_{t-1} + (1-λ)·r²_{t-1}."""
    if len(returns) < 5 or not (0.0 < lam < 1.0):
        return None
    seed_window = returns[: min(20, len(returns))]
    if len(seed_window) < 2:
        return None
    var = statistics.pvariance(seed_window)
    for r in returns[len(seed_window) :]:
        var = lam * var + (1.0 - lam) * r * r
    return math.sqrt(var * bars_per_year)


# ============================================================================
# Engine
# ============================================================================


class VolatilityEngine:
    """Institutional volatility engine.

    Usage:
        engine = VolatilityEngine(VolatilityConfig())
        report = engine.compute(candles=hourly, ticker=ticker)
    """

    def __init__(self, config: VolatilityConfig | None = None) -> None:
        self._config = config or VolatilityConfig()

    # ------------------------------------------------------------------ utils

    def _annual_factor(self, timeframe: str, override: int | None = None) -> int:
        if override is not None and override > 0:
            return override
        return _ANNUAL_FACTORS.get(timeframe, self._config.bars_per_year_default)

    def _hash_inputs(self, candles: list[OHLCV]) -> str:
        if not candles:
            return "sha256:empty"
        first = candles[0]
        last = candles[-1]
        payload = (
            f"{last.symbol}|{last.timeframe}|{first.timestamp_utc}|"
            f"{last.timestamp_utc}|{len(candles)}|{last.close:.10f}"
        )
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # ----------------------------------------------------------- liquidity

    def _liquidity_score(self, ticker: Ticker | None) -> float | None:
        """Map (volume_24h, spread_pct) → liquidity score in [0, 1]."""
        if ticker is None:
            return None
        cfg = self._config

        if ticker.volume_24h <= 0.0 or ticker.last <= 0.0:
            return 0.0

        # Volume score: log-scaled around the floor
        vol_floor = max(cfg.liquidity_volume_floor_usd, 1.0)
        # ticker.volume_24h may be in base-units depending on adapter; we use
        # last price to convert if value looks small. Conservative: assume USD
        # if volume_24h > floor, else multiply by last.
        vol_usd = ticker.volume_24h
        if vol_usd < vol_floor and ticker.last > 0:
            vol_usd = ticker.volume_24h * ticker.last
        vol_score = _clamp(math.log10(max(vol_usd, 1.0) / vol_floor) / 3.0, 0.0, 1.0)

        # Spread score
        spread_pct = 0.0
        if ticker.bid > 0.0 and ticker.ask > 0.0:
            mid = 0.5 * (ticker.bid + ticker.ask)
            if mid > 0:
                spread_pct = (ticker.ask - ticker.bid) / mid * 100.0
        spread_ceiling = max(cfg.liquidity_spread_ceiling_pct, 0.01)
        spread_score = 1.0 - _clamp(spread_pct / spread_ceiling, 0.0, 1.0)

        return _clamp(0.6 * vol_score + 0.4 * spread_score, 0.0, 1.0)

    def _liquidity_adjusted_vol(self, hv: float | None, liq_score: float | None) -> float | None:
        if hv is None:
            return None
        if liq_score is None:
            # Conservative penalty when liquidity unknown
            return hv * (1.0 + 0.5 * self._config.liquidity_penalty_alpha)
        illiquidity = 1.0 - liq_score
        return hv * (1.0 + self._config.liquidity_penalty_alpha * illiquidity)

    # -------------------------------------------------------------- regime

    def _classify_regime(
        self,
        current_vol: float | None,
        baseline_vol: float | None,
    ) -> tuple[str, float | None, float]:
        """Return (regime_label, ratio, confidence)."""
        if current_vol is None or baseline_vol is None or baseline_vol <= 0.0:
            return REGIME_UNKNOWN, None, 0.0

        cfg = self._config
        ratio = current_vol / baseline_vol

        if ratio < cfg.regime_threshold_low:
            label = REGIME_LOW
        elif ratio < cfg.regime_threshold_elevated:
            label = REGIME_NORMAL
        elif ratio < cfg.regime_threshold_high:
            label = REGIME_ELEVATED
        elif ratio < cfg.regime_threshold_crisis:
            label = REGIME_HIGH
        else:
            label = REGIME_CRISIS

        # Confidence: distance from nearest threshold, normalized
        thresholds = [
            cfg.regime_threshold_low,
            cfg.regime_threshold_elevated,
            cfg.regime_threshold_high,
            cfg.regime_threshold_crisis,
        ]
        nearest = min(abs(ratio - t) for t in thresholds)
        # Confidence rises with distance from nearest boundary
        confidence = _clamp(nearest / 0.5, 0.0, 1.0)

        return label, ratio, confidence

    def _classify_clustering(self, score: float) -> str:
        cfg = self._config
        if score < cfg.cluster_threshold_weak:
            return CLUSTER_NONE
        if score < cfg.cluster_threshold_moderate:
            return CLUSTER_WEAK
        if score < cfg.cluster_threshold_strong:
            return CLUSTER_MODERATE
        return CLUSTER_STRONG

    # ---------------------------------------------------- recommendations

    def _regime_leverage(self, regime: str) -> float:
        cfg = self._config
        return {
            REGIME_LOW: cfg.leverage_low,
            REGIME_NORMAL: cfg.leverage_normal,
            REGIME_ELEVATED: cfg.leverage_elevated,
            REGIME_HIGH: cfg.leverage_high,
            REGIME_CRISIS: cfg.leverage_crisis,
            REGIME_UNKNOWN: 0.0,
        }.get(regime, 0.0)

    def _regime_max_position_pct(self, regime: str) -> float:
        cfg = self._config
        return {
            REGIME_LOW: cfg.max_position_low_pct,
            REGIME_NORMAL: cfg.max_position_normal_pct,
            REGIME_ELEVATED: cfg.max_position_elevated_pct,
            REGIME_HIGH: cfg.max_position_high_pct,
            REGIME_CRISIS: cfg.max_position_crisis_pct,
            REGIME_UNKNOWN: 0.0,
        }.get(regime, 0.0)

    def _regime_atr_mult(self, regime: str) -> float:
        cfg = self._config
        return {
            REGIME_LOW: cfg.stop_atr_mult_low,
            REGIME_NORMAL: cfg.stop_atr_mult_normal,
            REGIME_ELEVATED: cfg.stop_atr_mult_elevated,
            REGIME_HIGH: cfg.stop_atr_mult_high,
            REGIME_CRISIS: cfg.stop_atr_mult_crisis,
            REGIME_UNKNOWN: cfg.stop_atr_mult_high,
        }.get(regime, cfg.stop_atr_mult_high)

    def _liquidation_risk_score(
        self,
        leverage_rec: float,
        hv: float | None,
    ) -> float:
        """Probability proxy: chance of a daily move past the liquidation
        buffer (≈ 1/L), given annualized HV. Score ∈ [0, 1]. 1x leverage → 0."""
        if leverage_rec <= 1.0 or hv is None or hv <= 0.0:
            return 0.0
        sigma_1d = hv / math.sqrt(365)
        if sigma_1d <= 0.0:
            return 0.0
        liquidation_buffer = 1.0 / leverage_rec
        z = liquidation_buffer / sigma_1d
        return _clamp(_norm_cdf(-z), 0.0, 1.0)

    # ------------------------------------------------------------ compute

    def compute(  # noqa: C901 — single orchestrator, deliberately linear
        self,
        *,
        candles: list[OHLCV],
        intraday_candles: list[OHLCV] | None = None,
        ticker: Ticker | None = None,
        bars_per_year: int | None = None,
        max_leverage_cap: float = 10.0,
        risk_per_trade_pct: float | None = None,
        current_price: float | None = None,
    ) -> VolatilityRegimeOutput:
        """Compute the full volatility assessment.

        Robust by contract: never raises on data issues. Insufficient data
        produces an output with regime=UNKNOWN, leverage=0, position=0,
        and a populated `warnings` list.
        """
        cfg = self._config
        warnings: list[str] = []
        notes: dict[str, object] = {}

        if not candles:
            return self._empty_output(
                symbol="?",
                timeframe="?",
                timestamp_utc="",
                inputs_hash="sha256:empty",
                warnings=["no_candles_provided"],
            )

        # Sort chronologically (defensive — adapters may return arbitrary order)
        candles = sorted(candles, key=lambda c: c.timestamp_utc)
        last = candles[-1]
        timeframe = last.timeframe
        symbol = last.symbol

        if current_price is None:
            current_price = last.close
        if current_price is None or current_price <= 0.0:
            warnings.append("invalid_current_price")
            current_price = last.close if last.close > 0 else 0.0

        annual_factor = self._annual_factor(timeframe, bars_per_year)

        closes = [c.close for c in candles]
        returns = _log_returns(closes)
        sample_size = len(returns)

        # --- core measurements ---
        atr_value = compute_atr(candles, period=cfg.atr_period)

        hv = historical_volatility(returns, cfg.hv_window, annual_factor)
        rv = realized_volatility(returns, cfg.rv_window, annual_factor)
        ewma = ewma_volatility(returns, cfg.ewma_lambda, annual_factor)

        # Intraday: prefer dedicated intraday candles if supplied
        intraday_source = intraday_candles if intraday_candles else candles
        gk = garman_klass_volatility(
            sorted(intraday_source, key=lambda c: c.timestamp_utc),
            cfg.intraday_window,
            self._annual_factor(intraday_source[-1].timeframe, None)
            if intraday_candles
            else annual_factor,
        )
        park = parkinson_volatility(
            sorted(intraday_source, key=lambda c: c.timestamp_utc),
            cfg.intraday_window,
            self._annual_factor(intraday_source[-1].timeframe, None)
            if intraday_candles
            else annual_factor,
        )

        # --- clustering ---
        squared_returns = [r * r for r in returns[-cfg.cluster_window :]]
        clustering_score = _autocorr_lag1(squared_returns) if len(squared_returns) >= 3 else 0.0
        clustering_label = self._classify_clustering(clustering_score)

        # --- regime (current vs baseline) ---
        # Current vol: most-responsive available estimator
        current_vol = ewma if ewma is not None else hv
        baseline_vol = historical_volatility(returns, cfg.baseline_window, annual_factor)
        if baseline_vol is None and hv is not None:
            # Fallback: use HV as baseline if not enough bars for the long window
            baseline_vol = hv
            warnings.append("baseline_window_short_using_hv_fallback")
        regime, ratio, regime_confidence = self._classify_regime(current_vol, baseline_vol)

        # Strong clustering inflates regime severity by one notch (early-warning)
        if clustering_label in (CLUSTER_MODERATE, CLUSTER_STRONG) and regime in (
            REGIME_LOW,
            REGIME_NORMAL,
        ):
            notes["regime_bumped_by_clustering"] = regime
            regime = REGIME_ELEVATED if regime == REGIME_NORMAL else REGIME_NORMAL

        # --- liquidity ---
        liq_score = self._liquidity_score(ticker)
        if liq_score is None:
            warnings.append("liquidity_data_missing")
        lav = self._liquidity_adjusted_vol(hv, liq_score)

        # --- expected moves (1σ, percent of price) ---
        expected_move_1bar_pct: float | None = None
        expected_move_1d_pct: float | None = None
        if hv is not None and hv > 0:
            sigma_per_bar = hv / math.sqrt(annual_factor)
            expected_move_1bar_pct = sigma_per_bar * 100.0
            sigma_1d = hv / math.sqrt(365)
            expected_move_1d_pct = sigma_1d * 100.0

        # --- stop distance ---
        atr_mult = self._regime_atr_mult(regime)
        stop_distance_pct: float | None = None
        if atr_value is not None and atr_value > 0.0 and current_price > 0.0:
            stop_distance_pct = (atr_mult * atr_value) / current_price * 100.0
        elif expected_move_1bar_pct is not None:
            # ATR fallback: 2σ of one-bar move scaled by regime mult
            stop_distance_pct = max(2.0 * expected_move_1bar_pct, atr_mult * 0.5)
            warnings.append("stop_distance_atr_fallback")

        # --- leverage recommendation ---
        leverage_rec = self._regime_leverage(regime)
        # Liquidity haircut
        if liq_score is not None:
            leverage_rec *= _clamp(0.5 + 0.5 * liq_score, 0.5, 1.0)
        # Cluster haircut (strong clustering → cap leverage further)
        if clustering_label == CLUSTER_STRONG:
            leverage_rec *= 0.6
        # Cap by operator policy
        leverage_rec = _clamp(leverage_rec, 0.0, max_leverage_cap)

        # --- max position size ---
        regime_pos_cap = self._regime_max_position_pct(regime)
        risk_pct = (
            risk_per_trade_pct if risk_per_trade_pct is not None else cfg.risk_per_trade_pct_default
        )

        if stop_distance_pct is not None and stop_distance_pct > 0.0:
            kelly_pos = risk_pct / (stop_distance_pct / 100.0)
        else:
            kelly_pos = regime_pos_cap

        max_position_pct = min(regime_pos_cap, kelly_pos)
        # Liquidity haircut on size
        if liq_score is not None:
            max_position_pct *= _clamp(0.5 + 0.5 * liq_score, 0.5, 1.0)
        max_position_pct = max(max_position_pct, 0.0)
        if regime == REGIME_CRISIS or regime == REGIME_UNKNOWN:
            max_position_pct = 0.0
            leverage_rec = 0.0

        # --- liquidation risk score ---
        liq_risk = self._liquidation_risk_score(leverage_rec, hv)

        if hv is None:
            warnings.append("insufficient_data_for_volatility")
        if regime == REGIME_UNKNOWN:
            warnings.append("regime_unknown")

        return VolatilityRegimeOutput(
            symbol=symbol,
            timestamp_utc=last.timestamp_utc,
            timeframe=timeframe,
            atr=atr_value,
            historical_volatility=hv,
            realized_volatility=rv,
            intraday_volatility=gk,
            parkinson_volatility=park,
            ewma_volatility=ewma,
            clustering_score=clustering_score,
            clustering_label=clustering_label,
            volatility_regime=regime if regime in ALL_REGIMES else REGIME_UNKNOWN,
            regime_ratio=ratio,
            regime_confidence=regime_confidence,
            liquidity_score=liq_score,
            liquidity_adjusted_volatility=lav,
            expected_move_pct_1bar=expected_move_1bar_pct,
            expected_move_pct_1d=expected_move_1d_pct,
            stop_distance_pct=stop_distance_pct,
            leverage_recommendation=leverage_rec,
            max_position_size_pct=max_position_pct,
            liquidation_risk_score=liq_risk,
            sample_size=sample_size,
            inputs_hash=self._hash_inputs(candles),
            warnings=warnings,
            notes=notes,
        )

    def _empty_output(
        self,
        *,
        symbol: str,
        timeframe: str,
        timestamp_utc: str,
        inputs_hash: str,
        warnings: list[str],
    ) -> VolatilityRegimeOutput:
        return VolatilityRegimeOutput(
            symbol=symbol,
            timestamp_utc=timestamp_utc,
            timeframe=timeframe,
            atr=None,
            historical_volatility=None,
            realized_volatility=None,
            intraday_volatility=None,
            parkinson_volatility=None,
            ewma_volatility=None,
            clustering_score=0.0,
            clustering_label=CLUSTER_NONE,
            volatility_regime=REGIME_UNKNOWN,
            regime_ratio=None,
            regime_confidence=0.0,
            liquidity_score=None,
            liquidity_adjusted_volatility=None,
            expected_move_pct_1bar=None,
            expected_move_pct_1d=None,
            stop_distance_pct=None,
            leverage_recommendation=0.0,
            max_position_size_pct=0.0,
            liquidation_risk_score=0.0,
            sample_size=0,
            inputs_hash=inputs_hash,
            warnings=warnings,
        )
