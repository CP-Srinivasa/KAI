"""Unit tests for the Volatility Engine.

Test layers:
- estimator correctness (HV, RV, Parkinson, Garman-Klass, EWMA, autocorr)
- regime classification (low / normal / elevated / high / crisis)
- liquidity adjustment + recommendations (leverage, position size)
- liquidation risk monotonicity
- robustness on degenerate data (empty, single bar, NaN-like inputs)
- property invariants (output bounded, regime escalates with vol)
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from app.market_data.models import OHLCV, Ticker
from app.risk.volatility import (
    VolatilityEngine,
    _autocorr_lag1,
    _norm_cdf,
    ewma_volatility,
    garman_klass_volatility,
    historical_volatility,
    parkinson_volatility,
    realized_volatility,
)
from app.risk.volatility_models import (
    CLUSTER_NONE,
    CLUSTER_STRONG,
    REGIME_CRISIS,
    REGIME_LOW,
    REGIME_NORMAL,
    REGIME_UNKNOWN,
    VolatilityConfig,
    VolatilityRegimeOutput,
)

# --------------------------------------------------------------------- helpers


def _make_candles(
    closes: list[float],
    *,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    high_low_pct: float = 0.01,
    open_offset_pct: float = 0.0,
) -> list[OHLCV]:
    """Build a chronological OHLCV series from a list of closes."""
    base_ts = datetime(2026, 5, 1, tzinfo=UTC)
    out: list[OHLCV] = []
    for i, c in enumerate(closes):
        ts = (base_ts + timedelta(hours=i)).isoformat()
        high = c * (1.0 + high_low_pct)
        low = c * (1.0 - high_low_pct)
        opn = c * (1.0 + open_offset_pct) if i > 0 else c
        out.append(
            OHLCV(
                symbol=symbol,
                timestamp_utc=ts,
                timeframe=timeframe,
                open=opn,
                high=max(high, opn, c),
                low=min(low, opn, c),
                close=c,
                volume=1000.0,
            )
        )
    return out


def _gbm_closes(
    n: int,
    *,
    sigma_per_bar: float,
    start: float = 100.0,
    seed: int = 42,
) -> list[float]:
    """Geometric Brownian motion close series with controlled vol per bar."""
    rng = random.Random(seed)
    closes = [start]
    for _ in range(n - 1):
        z = rng.gauss(0.0, 1.0)
        closes.append(closes[-1] * math.exp(sigma_per_bar * z))
    return closes


# ============================================================================
# Estimator unit tests
# ============================================================================


def test_norm_cdf_matches_known_values():
    assert _norm_cdf(0.0) == pytest.approx(0.5, abs=1e-9)
    assert _norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    assert _norm_cdf(-1.96) == pytest.approx(0.025, abs=1e-3)


def test_autocorr_lag1_constant_series_returns_zero():
    assert _autocorr_lag1([1.0, 1.0, 1.0, 1.0]) == 0.0


def test_autocorr_lag1_perfectly_correlated_series():
    # Strictly increasing series has positive lag-1 autocorr that approaches 1
    # as N grows. For finite N (20 → ~0.85, 200 → ~0.99). Test on a long series
    # to nail the boundary, and check the short-N behaviour separately.
    long_series = [float(i) for i in range(200)]
    assert _autocorr_lag1(long_series) > 0.95
    short_series = [float(i) for i in range(20)]
    assert _autocorr_lag1(short_series) > 0.7


def test_historical_volatility_recovers_known_sigma():
    sigma_per_bar = 0.02  # 2 % per bar
    closes = _gbm_closes(500, sigma_per_bar=sigma_per_bar, seed=7)
    candles = _make_candles(closes, timeframe="1h")
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    annual = 365 * 24
    hv = historical_volatility(returns, window=200, bars_per_year=annual)
    expected_annual = sigma_per_bar * math.sqrt(annual)
    # Sample-based estimate: be permissive on tolerance
    assert hv is not None
    assert hv == pytest.approx(expected_annual, rel=0.20)
    assert candles  # sanity


def test_realized_volatility_close_to_hv_on_well_behaved_series():
    sigma_per_bar = 0.015
    closes = _gbm_closes(400, sigma_per_bar=sigma_per_bar, seed=11)
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    annual = 365 * 24
    hv = historical_volatility(returns, window=200, bars_per_year=annual)
    rv = realized_volatility(returns, window=200, bars_per_year=annual)
    assert hv is not None and rv is not None
    # RV uses Σr², HV uses pstdev — for zero-mean returns these track closely
    assert abs(rv - hv) / hv < 0.10


def test_parkinson_and_gk_return_positive_floats_on_real_bars():
    closes = _gbm_closes(100, sigma_per_bar=0.02, seed=3)
    candles = _make_candles(closes, timeframe="1h", high_low_pct=0.01)
    pk = parkinson_volatility(candles, window=50, bars_per_year=365 * 24)
    gk = garman_klass_volatility(candles, window=50, bars_per_year=365 * 24)
    assert pk is not None and pk > 0.0
    assert gk is not None and gk >= 0.0


def test_ewma_returns_none_on_too_few_returns():
    assert ewma_volatility([0.01, 0.02], lam=0.94, bars_per_year=365) is None


def test_ewma_responds_to_recent_shock():
    base = [0.001] * 100
    shocked = base + [0.10] * 5  # large recent shocks
    annual = 365
    base_vol = ewma_volatility(base, lam=0.94, bars_per_year=annual)
    shocked_vol = ewma_volatility(shocked, lam=0.94, bars_per_year=annual)
    assert base_vol is not None and shocked_vol is not None
    assert shocked_vol > base_vol * 5


# ============================================================================
# Engine: regime classification
# ============================================================================


@pytest.fixture
def engine() -> VolatilityEngine:
    return VolatilityEngine(VolatilityConfig())


def test_compute_returns_unknown_regime_when_no_candles(engine: VolatilityEngine):
    out = engine.compute(candles=[])
    assert isinstance(out, VolatilityRegimeOutput)
    assert out.volatility_regime == REGIME_UNKNOWN
    assert out.leverage_recommendation == 0.0
    assert out.max_position_size_pct == 0.0
    assert out.liquidation_risk_score == 0.0
    assert "no_candles_provided" in out.warnings


def test_compute_unknown_regime_on_insufficient_history(engine: VolatilityEngine):
    closes = _gbm_closes(20, sigma_per_bar=0.01, seed=1)
    candles = _make_candles(closes)
    out = engine.compute(candles=candles)
    # Not enough bars for HV(30); regime stays unknown
    assert out.volatility_regime == REGIME_UNKNOWN
    assert out.historical_volatility is None
    assert out.leverage_recommendation == 0.0
    assert out.max_position_size_pct == 0.0


def test_compute_normal_regime_for_stable_series():
    # Use a longer baseline window and slightly noisy GBM.
    cfg = VolatilityConfig(hv_window=30, baseline_window=120, cluster_window=30)
    eng = VolatilityEngine(cfg)
    closes = _gbm_closes(300, sigma_per_bar=0.01, seed=5)
    candles = _make_candles(closes)
    out = eng.compute(candles=candles, max_leverage_cap=10.0)
    assert out.volatility_regime in {REGIME_LOW, REGIME_NORMAL}
    assert out.historical_volatility is not None and out.historical_volatility > 0
    assert out.expected_move_pct_1bar is not None
    assert out.stop_distance_pct is not None and out.stop_distance_pct > 0
    assert 0 < out.leverage_recommendation <= 10.0
    assert 0 < out.max_position_size_pct


def test_compute_crisis_regime_when_recent_vol_spikes():
    """Long quiet baseline + very strong, short, recent shock → crisis regime.

    Math: ratio = current_vol / baseline_vol. The storm contaminates both
    windows, so ratios saturate when storm length is comparable to baseline
    length. We pick baseline_window=900 with only 10 storm bars so storm's
    weight in the baseline is ≈ 1.1 % — keeping the ratio well above 3.5.
    """
    cfg = VolatilityConfig(hv_window=10, baseline_window=900, cluster_window=10)
    eng = VolatilityEngine(cfg)
    quiet = _gbm_closes(1000, sigma_per_bar=0.005, seed=2)
    storm = _gbm_closes(10, sigma_per_bar=0.30, seed=3, start=quiet[-1])
    candles = _make_candles(quiet + storm[1:])
    out = eng.compute(candles=candles, max_leverage_cap=10.0)
    assert out.volatility_regime == REGIME_CRISIS, (
        f"got {out.volatility_regime} ratio={out.regime_ratio}"
    )
    assert out.leverage_recommendation == 0.0
    assert out.max_position_size_pct == 0.0


# ============================================================================
# Engine: leverage / position size / liquidation risk
# ============================================================================


def test_max_position_pct_bounded_by_regime_cap():
    cfg = VolatilityConfig()
    eng = VolatilityEngine(cfg)
    closes = _gbm_closes(300, sigma_per_bar=0.005, seed=8)
    candles = _make_candles(closes)
    out = eng.compute(candles=candles, max_leverage_cap=10.0, risk_per_trade_pct=10.0)
    # Even with absurdly generous risk_per_trade, the regime cap dominates
    assert out.max_position_size_pct <= cfg.max_position_low_pct + 0.1


def test_leverage_recommendation_respects_operator_cap():
    eng = VolatilityEngine()
    closes = _gbm_closes(300, sigma_per_bar=0.005, seed=9)
    candles = _make_candles(closes)
    out = eng.compute(candles=candles, max_leverage_cap=1.5)
    assert out.leverage_recommendation <= 1.5


def test_liquidation_risk_zero_at_no_leverage():
    eng = VolatilityEngine()
    closes = _gbm_closes(300, sigma_per_bar=0.02, seed=10)
    candles = _make_candles(closes)
    out = eng.compute(candles=candles, max_leverage_cap=1.0)
    assert out.liquidation_risk_score == 0.0


def test_liquidation_risk_increases_with_leverage_cap():
    """Holding vol fixed, raising the leverage cap raises (or holds) the
    liquidation risk score — never lowers it."""
    closes = _gbm_closes(300, sigma_per_bar=0.02, seed=11)
    candles = _make_candles(closes)
    eng = VolatilityEngine()
    low_cap = eng.compute(candles=candles, max_leverage_cap=1.0)
    high_cap = eng.compute(candles=candles, max_leverage_cap=10.0)
    assert high_cap.liquidation_risk_score >= low_cap.liquidation_risk_score


# ============================================================================
# Engine: liquidity adjustment
# ============================================================================


def test_liquidity_adjustment_inflates_vol_for_thin_books():
    eng = VolatilityEngine()
    closes = _gbm_closes(300, sigma_per_bar=0.01, seed=12)
    candles = _make_candles(closes)

    thick = Ticker(
        symbol="BTC/USDT",
        timestamp_utc="2026-05-09T00:00:00+00:00",
        bid=99.99,
        ask=100.01,
        last=100.0,
        volume_24h=10_000_000_000.0,  # very deep
    )
    thin = Ticker(
        symbol="ALT/USDT",
        timestamp_utc="2026-05-09T00:00:00+00:00",
        bid=99.0,
        ask=101.0,  # 2% spread
        last=100.0,
        volume_24h=10_000.0,  # very thin
    )
    deep = eng.compute(candles=candles, ticker=thick)
    shallow = eng.compute(candles=candles, ticker=thin)

    assert deep.liquidity_score is not None and shallow.liquidity_score is not None
    assert deep.liquidity_score > shallow.liquidity_score
    assert (
        shallow.liquidity_adjusted_volatility > deep.liquidity_adjusted_volatility  # type: ignore[operator]
    )
    # Thin-book leverage should be cut at least as hard as thick-book
    assert shallow.leverage_recommendation <= deep.leverage_recommendation
    assert shallow.max_position_size_pct <= deep.max_position_size_pct


def test_missing_liquidity_emits_warning_and_keeps_running():
    eng = VolatilityEngine()
    closes = _gbm_closes(150, sigma_per_bar=0.01, seed=13)
    candles = _make_candles(closes)
    out = eng.compute(candles=candles)
    assert "liquidity_data_missing" in out.warnings
    assert out.liquidity_adjusted_volatility is not None  # conservative penalty applied


# ============================================================================
# Property invariants
# ============================================================================


def test_output_bounded_fields():
    eng = VolatilityEngine()
    closes = _gbm_closes(200, sigma_per_bar=0.01, seed=14)
    candles = _make_candles(closes)
    out = eng.compute(candles=candles, max_leverage_cap=5.0)
    assert 0.0 <= out.liquidation_risk_score <= 1.0
    assert 0.0 <= out.leverage_recommendation <= 5.0
    assert 0.0 <= out.max_position_size_pct
    assert -1.0 <= out.clustering_score <= 1.0
    assert 0.0 <= out.regime_confidence <= 1.0
    if out.liquidity_score is not None:
        assert 0.0 <= out.liquidity_score <= 1.0


def test_regime_monotonic_in_recent_volatility():
    """Higher recent volatility ⇒ regime no easier than for a quieter series."""
    eng = VolatilityEngine(VolatilityConfig(hv_window=20, baseline_window=200))
    rank = {
        REGIME_LOW: 0,
        REGIME_NORMAL: 1,
        "elevated": 2,
        "high_vol": 3,
        REGIME_CRISIS: 4,
        REGIME_UNKNOWN: -1,
    }
    quiet = _gbm_closes(220, sigma_per_bar=0.003, seed=15)
    quiet_tail = _gbm_closes(40, sigma_per_bar=0.003, seed=16, start=quiet[-1])
    loud_tail = _gbm_closes(40, sigma_per_bar=0.06, seed=17, start=quiet[-1])
    out_quiet = eng.compute(candles=_make_candles(quiet + quiet_tail[1:]))
    out_loud = eng.compute(candles=_make_candles(quiet + loud_tail[1:]))
    assert rank[out_loud.volatility_regime] >= rank[out_quiet.volatility_regime]


def test_clustering_label_strong_for_volatility_clusters():
    """Squared returns with strong serial dependence → non-trivial cluster score.

    We engineer regime-switching volatility (alternating quiet/loud blocks)
    so squared returns within blocks are similar → high lag-1 autocorr of r².
    GARCH simulators can produce noisy short samples; deterministic block
    structure avoids that flakiness.
    """
    cfg = VolatilityConfig(hv_window=30, baseline_window=200, cluster_window=120)
    eng = VolatilityEngine(cfg)
    rng = random.Random(99)
    closes = [100.0]
    sigma_high = 0.04
    sigma_low = 0.003
    block_len = 40
    for i in range(400):
        sigma = sigma_high if (i // block_len) % 2 == 0 else sigma_low
        z = rng.gauss(0.0, 1.0)
        closes.append(closes[-1] * math.exp(sigma * z))
    out = eng.compute(candles=_make_candles(closes))
    assert out.clustering_label != CLUSTER_NONE, (
        f"score={out.clustering_score} label={out.clustering_label}"
    )
    assert out.clustering_score > 0.05


def test_to_json_dict_contains_all_required_recommendation_fields():
    eng = VolatilityEngine()
    closes = _gbm_closes(300, sigma_per_bar=0.01, seed=20)
    candles = _make_candles(closes)
    out = eng.compute(candles=candles)
    payload = out.to_json_dict()
    for key in (
        "volatility_regime",
        "expected_move_pct_1d",
        "stop_distance_pct",
        "leverage_recommendation",
        "max_position_size_pct",
        "liquidation_risk_score",
        "liquidity_adjusted_volatility",
        "clustering_label",
        "inputs_hash",
    ):
        assert key in payload
    assert payload["report_type"] == "volatility_regime"
    assert payload["inputs_hash"].startswith("sha256:")


def test_strong_clustering_haircuts_strong_leverage():
    """Strong volatility clustering should not increase recommended leverage."""
    cfg = VolatilityConfig()
    eng = VolatilityEngine(cfg)

    quiet = _gbm_closes(300, sigma_per_bar=0.005, seed=22)
    out_quiet = eng.compute(candles=_make_candles(quiet))

    rng = random.Random(23)
    closes = [100.0]
    var = 0.005**2
    for _ in range(300):
        r = rng.gauss(0.0, math.sqrt(var))
        closes.append(closes[-1] * math.exp(r))
        var = 0.000005 + 0.90 * var + 0.09 * r * r
    out_clustered = eng.compute(candles=_make_candles(closes))

    if out_clustered.clustering_label == CLUSTER_STRONG:
        assert out_clustered.leverage_recommendation <= out_quiet.leverage_recommendation + 1e-9
