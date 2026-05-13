"""Unit tests for counterfactual calibrator evaluation."""

from __future__ import annotations

import random

import pytest

from app.learning.counterfactual import (
    CounterfactualConfig,
    CounterfactualReport,
    TradeOutcome,
    evaluate_calibrator_counterfactual,
)
from app.learning.recalibration import (
    CalibratorParameters,
    IdentityCalibrator,
    LogitLinearCalibrator,
)

# --------------------------------------------------------------------- helpers


def _make_calibrator(
    intercept: float, slope: float, *, n_fitted: int = 100
) -> LogitLinearCalibrator:
    return LogitLinearCalibrator(
        CalibratorParameters(
            intercept=intercept,
            slope=slope,
            n_fitted=n_fitted,
            is_identity=(abs(intercept) < 1e-6 and abs(slope - 1.0) < 1e-6),
            fit_notes=("test",),
        )
    )


def _trade(
    *,
    decision_id: str,
    posterior: float,
    pnl: float,
    direction: str = "long",
) -> TradeOutcome:
    return TradeOutcome(
        decision_id=decision_id,
        direction=direction,  # type: ignore[arg-type]
        raw_posterior=posterior,
        realized_pnl_usd=pnl,
    )


def _mixed_trades(
    n: int,
    *,
    seed: int = 1,
    high_conf_winners: bool = True,
    low_conf_losers: bool = True,
) -> list[TradeOutcome]:
    """A historical trade book where higher-posterior trades tend to win,
    lower-posterior trades tend to lose. Calibrator that squashes confidence
    won't change anything if it's still above threshold.
    """
    rng = random.Random(seed)
    trades: list[TradeOutcome] = []
    for i in range(n):
        # All historical trades passed threshold of 0.75 — simulate that.
        p = rng.uniform(0.76, 0.95)
        # Higher posterior → higher win probability
        win_prob = 0.40 + 0.60 * (p - 0.75) / 0.20  # 0.40..1.0 across [0.75..0.95]
        is_winner = rng.random() < win_prob
        if is_winner and high_conf_winners:
            pnl = rng.uniform(20, 200)
        elif not is_winner and low_conf_losers:
            pnl = -rng.uniform(20, 150)
        else:
            pnl = rng.uniform(-50, 50)
        trades.append(_trade(decision_id=f"d_{i}", posterior=p, pnl=pnl))
    return trades


# ============================================================================
# Decision: approve / reject / neutral / insufficient_data
# ============================================================================


def test_calibrator_that_filters_low_confidence_losers_is_approved():
    """A calibrator that pulls posteriors hard down → marginal trades fall
    below threshold. If those marginal trades were dominantly losers, the
    counterfactual approves."""
    # Build trades where posteriors near 0.76 are losers, near 0.92 are winners.
    trades = [_trade(decision_id=f"loser_{i}", posterior=0.78, pnl=-100.0) for i in range(20)] + [
        _trade(decision_id=f"winner_{i}", posterior=0.92, pnl=80.0) for i in range(20)
    ]
    # Calibrator: slope 0.6, intercept 0.0  ⇒ p_new = 0.6 * p
    #   0.78 → 0.468 (below 0.75 → SKIP, avoid loss)
    #   0.92 → 0.552 (also below — uh, calibrator too aggressive)
    # So we use slope 1.0, intercept −0.10 → 0.78 → 0.68 (skip), 0.92 → 0.82 (keep)
    calibrator = _make_calibrator(intercept=-0.10, slope=1.0)
    report = evaluate_calibrator_counterfactual(trade_outcomes=trades, new_calibrator=calibrator)
    assert report.decision == "approve", report.decision_reasons
    assert report.n_would_skip == 20  # all losers skipped
    assert report.n_would_still_trade == 20
    assert report.avoided_loss_count == 20
    assert report.pnl_delta_usd > 0


def test_calibrator_that_filters_winners_is_rejected():
    """A calibrator that pulls posteriors *down* through the threshold for
    high-conf winners → costs realized P&L → reject."""
    trades = [_trade(decision_id=f"winner_{i}", posterior=0.78, pnl=80.0) for i in range(40)]
    # Slope 1.0, intercept −0.10 → 0.78 → 0.68 → all skipped
    calibrator = _make_calibrator(intercept=-0.10, slope=1.0)
    report = evaluate_calibrator_counterfactual(trade_outcomes=trades, new_calibrator=calibrator)
    assert report.decision == "reject", report.decision_reasons
    assert report.n_would_skip == 40
    assert report.skipped_gain_count == 40
    assert report.pnl_delta_usd < 0


def test_identity_calibrator_changes_nothing():
    trades = _mixed_trades(50, seed=11)
    report = evaluate_calibrator_counterfactual(
        trade_outcomes=trades, new_calibrator=IdentityCalibrator()
    )
    assert report.n_would_skip == 0
    assert report.n_would_still_trade == 50
    assert report.pnl_delta_usd == 0.0
    assert "calibrator would not have changed any decision" in report.decision_reasons


def test_neutral_decision_when_pnl_delta_within_band():
    """Calibrator filters one winner of $10 and one loser of $-10 → net 0."""
    trades = [
        _trade(decision_id="d_winner_borderline", posterior=0.76, pnl=10.0),
        _trade(decision_id="d_loser_borderline", posterior=0.76, pnl=-10.0),
    ] + [_trade(decision_id=f"d_safe_{i}", posterior=0.95, pnl=5.0) for i in range(40)]
    # Calibrator pulls 0.76 → 0.74 (skip), 0.95 → 0.93 (keep).
    calibrator = _make_calibrator(intercept=-0.02, slope=1.0)
    cfg = CounterfactualConfig(min_pnl_improvement_usd=5.0)
    report = evaluate_calibrator_counterfactual(
        trade_outcomes=trades, new_calibrator=calibrator, config=cfg
    )
    assert report.decision == "neutral"
    assert report.n_would_skip == 2
    assert abs(report.pnl_delta_usd) < cfg.min_pnl_improvement_usd


def test_insufficient_data_returns_dedicated_decision():
    trades = _mixed_trades(5, seed=17)
    report = evaluate_calibrator_counterfactual(
        trade_outcomes=trades, new_calibrator=IdentityCalibrator()
    )
    assert report.decision == "insufficient_data"
    assert report.trades == ()  # detail trail only on real evaluation


def test_empty_input_returns_insufficient_data():
    report = evaluate_calibrator_counterfactual(
        trade_outcomes=[], new_calibrator=IdentityCalibrator()
    )
    assert report.decision == "insufficient_data"
    assert report.n_trades == 0


# ============================================================================
# Side-aware long/short
# ============================================================================


def test_short_signal_is_side_aware_by_default():
    """Short signal with raw posterior 0.20 → side-aware p = 0.80 (above
    threshold). Identity calibrator → keeps it."""
    trade = _trade(decision_id="s_1", posterior=0.20, pnl=50.0, direction="short")
    cfg = CounterfactualConfig(min_trades=1)
    report = evaluate_calibrator_counterfactual(
        trade_outcomes=[trade] * 30,
        new_calibrator=IdentityCalibrator(),
        config=cfg,
    )
    # All 30 short-trades (p_signal=0.80) above threshold → kept
    assert report.n_would_still_trade == 30
    assert report.trades[0].signal_posterior == pytest.approx(0.80)


def test_side_aware_off_treats_short_posterior_directly():
    """With side_aware=False, posterior=0.20 stays 0.20 → below 0.75 → skip."""
    trades = [
        _trade(decision_id=f"s_{i}", posterior=0.20, pnl=50.0, direction="short") for i in range(30)
    ]
    cfg = CounterfactualConfig(side_aware=False)
    report = evaluate_calibrator_counterfactual(
        trade_outcomes=trades,
        new_calibrator=IdentityCalibrator(),
        config=cfg,
    )
    assert report.n_would_still_trade == 0
    assert report.n_would_skip == 30


# ============================================================================
# Output structure + edge cases
# ============================================================================


def test_per_trade_detail_records_signal_and_calibrated_posteriors():
    trade = _trade(decision_id="t1", posterior=0.85, pnl=100.0)
    calibrator = _make_calibrator(intercept=-0.05, slope=0.95)
    cfg = CounterfactualConfig(min_trades=1)
    report = evaluate_calibrator_counterfactual(
        trade_outcomes=[trade] * 30, new_calibrator=calibrator, config=cfg
    )
    detail = report.trades[0]
    assert detail.signal_posterior == pytest.approx(0.85)
    assert detail.calibrated_posterior == pytest.approx(-0.05 + 0.95 * 0.85)
    assert detail.would_still_trade is (detail.calibrated_posterior >= cfg.threshold)


def test_pnl_aggregates_match_per_trade_sums():
    trades = _mixed_trades(80, seed=22)
    calibrator = _make_calibrator(intercept=-0.05, slope=0.95)
    report = evaluate_calibrator_counterfactual(trade_outcomes=trades, new_calibrator=calibrator)
    expected_total = round(sum(t.realized_pnl_usd for t in trades), 2)
    assert report.pnl_realized_total_usd == pytest.approx(expected_total, abs=0.01)
    assert report.pnl_realized_kept_usd + report.pnl_realized_skipped_usd == pytest.approx(
        report.pnl_realized_total_usd, abs=0.02
    )


def test_decision_reasons_are_populated_on_every_outcome():
    trades = _mixed_trades(80, seed=23)
    for cal in [IdentityCalibrator(), _make_calibrator(intercept=-0.10, slope=1.0)]:
        report = evaluate_calibrator_counterfactual(trade_outcomes=trades, new_calibrator=cal)
        assert report.decision_reasons


def test_report_round_trips_through_pydantic():
    trades = _mixed_trades(80, seed=24)
    report = evaluate_calibrator_counterfactual(
        trade_outcomes=trades, new_calibrator=_make_calibrator(-0.08, 1.0)
    )
    payload = report.model_dump(mode="json")
    rebuilt = CounterfactualReport.model_validate(payload)
    assert rebuilt.decision == report.decision
    assert rebuilt.pnl_delta_usd == report.pnl_delta_usd
    assert len(rebuilt.trades) == len(report.trades)


def test_threshold_is_respected_strictly():
    """Calibrated posterior exactly == threshold → keep (>=)."""
    cfg = CounterfactualConfig(threshold=0.75, min_trades=1)
    trades = [_trade(decision_id=f"d_{i}", posterior=0.75, pnl=10.0) for i in range(30)]
    report = evaluate_calibrator_counterfactual(
        trade_outcomes=trades, new_calibrator=IdentityCalibrator(), config=cfg
    )
    assert report.n_would_still_trade == 30


def test_avoided_loss_and_skipped_gain_are_disjoint():
    """Each skipped trade contributes to either avoided_loss OR skipped_gain,
    never both. Trades with pnl == 0 contribute to neither."""
    trades = (
        [_trade(decision_id=f"loss_{i}", posterior=0.76, pnl=-50.0) for i in range(10)]
        + [_trade(decision_id=f"win_{i}", posterior=0.76, pnl=50.0) for i in range(10)]
        + [_trade(decision_id=f"flat_{i}", posterior=0.76, pnl=0.0) for i in range(10)]
        + [_trade(decision_id=f"keep_{i}", posterior=0.95, pnl=10.0) for i in range(20)]
    )
    calibrator = _make_calibrator(intercept=-0.05, slope=1.0)
    report = evaluate_calibrator_counterfactual(trade_outcomes=trades, new_calibrator=calibrator)
    # 30 trades skipped (all 0.76 trades): 10 losers + 10 winners + 10 flat
    assert report.n_would_skip == 30
    assert report.avoided_loss_count == 10
    assert report.skipped_gain_count == 10
    # 10 zero-PnL trades skipped but in neither bucket
    assert (
        report.avoided_loss_count + report.skipped_gain_count + 10  # flat
        == report.n_would_skip
    )
