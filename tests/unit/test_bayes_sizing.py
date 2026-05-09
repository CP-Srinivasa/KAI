from __future__ import annotations

from app.risk.bayes_sizing import (
    BayesSizingInput,
    compute_bayes_sized_position,
)


def test_bayes_sizing_rejects_negative_edge() -> None:
    decision = compute_bayes_sized_position(
        BayesSizingInput(
            win_probability=0.30,
            expected_reward_pct=2.0,
            stop_loss_pct=2.0,
            equity=10_000.0,
        )
    )

    assert decision.approved is False
    assert decision.position_fraction == 0.0
    assert decision.capped_by == "negative_edge"


def test_bayes_sizing_caps_by_max_risk_per_trade() -> None:
    decision = compute_bayes_sized_position(
        BayesSizingInput(
            win_probability=0.75,
            expected_reward_pct=6.0,
            stop_loss_pct=2.0,
            bayes_confidence=0.9,
            bayes_uncertainty=0.1,
            regime_anomaly=0.0,
            equity=20_000.0,
            max_risk_per_trade_pct=1.0,
        )
    )

    assert decision.approved is True
    assert decision.position_fraction == 0.01
    assert decision.position_size_usd == 200.0
    assert decision.capped_by == "max_risk_per_trade"
    assert {m.name for m in decision.multipliers} == {
        "kelly_fraction",
        "bayes_confidence",
        "one_minus_uncertainty",
        "one_minus_anomaly",
    }


def test_bayes_sizing_drawdown_exhausted_blocks_trade() -> None:
    decision = compute_bayes_sized_position(
        BayesSizingInput(
            win_probability=0.80,
            expected_reward_pct=4.0,
            stop_loss_pct=1.0,
            equity=10_000.0,
            drawdown_remaining_pct=0.0,
        )
    )

    assert decision.approved is False
    assert decision.capped_by == "drawdown_exhausted"

