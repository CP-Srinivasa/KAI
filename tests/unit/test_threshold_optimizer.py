"""Unit tests for the generic threshold optimizer."""

from __future__ import annotations

import random

import pytest

from app.learning.threshold_optimizer import (
    DEFAULT_GRID,
    ThresholdConfig,
    ThresholdObservation,
    ThresholdOptimizationReport,
    optimize_threshold,
)

# --------------------------------------------------------------------- helpers


def _obs(score: float, pnl: float, *, oid: str | None = None) -> ThresholdObservation:
    return ThresholdObservation(
        observation_id=oid or f"o_{score:.2f}_{pnl:+.0f}",
        score=score,
        realized_pnl_usd=pnl,
    )


def _mixed_observations(n: int = 80, *, seed: int = 1) -> list[ThresholdObservation]:
    """High-confidence trades win, low-confidence trades lose. Optimal cut
    sits somewhere in the middle of the grid."""
    rng = random.Random(seed)
    out: list[ThresholdObservation] = []
    for i in range(n):
        score = rng.uniform(0.50, 0.95)
        # Above 0.75 → win prob 0.85; below → win prob 0.30
        win = rng.random() < (0.85 if score >= 0.75 else 0.30)
        pnl = rng.uniform(50, 150) if win else -rng.uniform(50, 150)
        out.append(_obs(score, pnl, oid=f"o_{i}"))
    return out


# ============================================================================
# Decision: approve / reject / neutral / insufficient_data
# ============================================================================


def test_optimizer_lifts_threshold_when_higher_score_means_better_pnl():
    obs = _mixed_observations(n=120, seed=1)
    report = optimize_threshold(observations=obs, baseline_threshold=0.50)
    assert isinstance(report, ThresholdOptimizationReport)
    assert report.decision == "approve"
    assert report.best_threshold is not None
    # Improvement should be positive
    assert report.pnl_improvement_usd > 0
    # Best threshold should sit at or above the noisy boundary at 0.75
    assert report.best_threshold >= 0.65


def test_optimizer_neutral_when_score_carries_no_signal():
    """Score uncorrelated with P&L → no in-grid threshold materially improves."""
    rng = random.Random(11)
    obs = [_obs(rng.uniform(0.50, 0.95), rng.uniform(-50, 50), oid=f"o_{i}") for i in range(120)]
    cfg = ThresholdConfig(min_pnl_improvement_usd=200.0)
    report = optimize_threshold(observations=obs, baseline_threshold=0.50, config=cfg)
    assert report.decision in ("neutral", "reject")


def test_optimizer_insufficient_data_below_minimum():
    obs = _mixed_observations(n=10, seed=2)
    report = optimize_threshold(observations=obs, baseline_threshold=0.50)
    assert report.decision == "insufficient_data"
    assert report.best_threshold is None
    assert any("need >=" in r for r in report.decision_reasons)


def test_optimizer_insufficient_data_when_no_threshold_meets_min_trades():
    cfg = ThresholdConfig(min_trades_for_threshold=50)
    # 30 obs, no threshold reaches 50 passing
    obs = _mixed_observations(n=30, seed=3)
    report = optimize_threshold(observations=obs, baseline_threshold=0.50, config=cfg)
    assert report.decision == "insufficient_data"
    assert any("min_trades_for_threshold" in r or "passes" in r for r in report.decision_reasons)


def test_optimizer_empty_input_is_insufficient_data():
    report = optimize_threshold(observations=[], baseline_threshold=0.50)
    assert report.decision == "insufficient_data"
    assert report.n_observations == 0


# ============================================================================
# Selection-bias guardrails
# ============================================================================


def test_only_thresholds_at_or_above_baseline_are_considered_by_default():
    """Selection-bias guardrail: we don't extrapolate below the baseline."""
    obs = _mixed_observations(n=120, seed=4)
    report = optimize_threshold(observations=obs, baseline_threshold=0.80)
    for gp in report.grid:
        assert gp.threshold >= 0.80


def test_can_explicitly_consider_lower_thresholds():
    obs = _mixed_observations(n=120, seed=5)
    cfg = ThresholdConfig(only_consider_at_or_above_baseline=False)
    report = optimize_threshold(observations=obs, baseline_threshold=0.80, config=cfg)
    grid_min = min(gp.threshold for gp in report.grid)
    assert grid_min < 0.80


# ============================================================================
# Output structure
# ============================================================================


def test_grid_points_carry_per_threshold_metrics():
    obs = _mixed_observations(n=120, seed=6)
    report = optimize_threshold(observations=obs, baseline_threshold=0.50)
    for gp in report.grid:
        assert gp.threshold in DEFAULT_GRID
        assert gp.n_passing >= 0
        if gp.n_passing > 0:
            expected_mean = gp.pnl_total_usd / gp.n_passing
            assert gp.pnl_mean_per_trade_usd == pytest.approx(expected_mean, abs=0.01)


def test_baseline_metrics_match_baseline_threshold_in_grid():
    obs = _mixed_observations(n=120, seed=7)
    baseline = 0.65
    report = optimize_threshold(observations=obs, baseline_threshold=baseline)
    # baseline shows up in grid (since grid contains 0.65 + only_above_baseline)
    matching = [gp for gp in report.grid if gp.threshold == pytest.approx(baseline)]
    assert matching
    assert matching[0].n_passing == report.baseline_n_passing
    assert matching[0].pnl_total_usd == pytest.approx(report.baseline_pnl_usd, abs=0.01)


def test_report_round_trips_through_pydantic():
    obs = _mixed_observations(n=80, seed=8)
    report = optimize_threshold(observations=obs, baseline_threshold=0.50)
    payload = report.model_dump(mode="json")
    rebuilt = ThresholdOptimizationReport.model_validate(payload)
    assert rebuilt.decision == report.decision
    assert rebuilt.best_threshold == report.best_threshold
    assert len(rebuilt.grid) == len(report.grid)


# ============================================================================
# Decision reasons populated
# ============================================================================


def test_decision_reasons_always_populated():
    for n_obs in (5, 80):
        obs = _mixed_observations(n=n_obs, seed=9)
        report = optimize_threshold(observations=obs, baseline_threshold=0.50)
        assert report.decision_reasons


def test_reject_when_best_in_grid_underperforms_baseline():
    """Construct a degenerate case: baseline already has all the winners."""
    obs = [_obs(0.50, +100.0, oid=f"low_{i}") for i in range(40)] + [
        _obs(0.95, -50.0, oid=f"high_{i}") for i in range(40)
    ]
    cfg = ThresholdConfig(only_consider_at_or_above_baseline=False)
    report = optimize_threshold(observations=obs, baseline_threshold=0.50, config=cfg)
    assert report.decision in ("reject", "neutral")
