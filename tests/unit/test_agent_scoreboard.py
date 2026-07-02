"""Tests for the risk-adjusted Agent Scoreboard.

The load-bearing behaviour is the operator's two requirements:
  * an agent with high PnL but a TOXIC drawdown must NOT rank top, and
  * an agent with good calibration and a smaller STABLE edge may rank higher.

Encoded as the four operator scenarios A/B/C/D plus normaliser unit tests.
"""

from __future__ import annotations

import pytest

from app.observability.agent_scoreboard import (
    WEIGHTS,
    AgentMetricInputs,
    build_agent_scoreboard,
    normalize_calibration,
    normalize_drawdown,
    normalize_ev,
    normalize_ic_stability,
    normalize_regime_robustness,
    normalize_sharpe,
    score_agent,
)

# --- Operator scenarios -----------------------------------------------------

# Agent A: high PnL, high (toxic) drawdown.
AGENT_A = AgentMetricInputs(
    agent_id="A_high_pnl_toxic_dd",
    ev_after_costs_bps=40.0,
    sharpe=0.5,
    sortino=0.6,
    brier=0.18,
    calibration_error=0.10,
    ic_by_horizon={"1h": 0.05, "4h": 0.04},
    max_drawdown=-0.45,  # toxic
    regime_ev_bps={"bull": 50.0, "bear": -10.0},
    overtrading_penalty=0.2,
    pnl_usd=12_000.0,
    hit_rate=0.55,
    n_trades=120,
)

# Agent B: low PnL, stable EV, well calibrated, shallow drawdown.
AGENT_B = AgentMetricInputs(
    agent_id="B_low_pnl_stable_ev",
    ev_after_costs_bps=12.0,
    sharpe=1.2,
    sortino=1.6,
    brier=0.10,
    calibration_error=0.04,
    ic_by_horizon={"1h": 0.06, "4h": 0.05, "1d": 0.055},
    max_drawdown=-0.08,  # shallow
    regime_ev_bps={"bull": 15.0, "bear": 8.0, "chop": 10.0},
    overtrading_penalty=0.1,
    pnl_usd=1_800.0,
    hit_rate=0.58,
    n_trades=90,
)

# Agent C: high confidence, poor hit-rate → bad calibration.
AGENT_C = AgentMetricInputs(
    agent_id="C_overconfident",
    ev_after_costs_bps=5.0,
    sharpe=0.3,
    sortino=0.4,
    brier=0.35,  # worse than a coin flip → overconfident
    calibration_error=0.22,
    ic_by_horizon={"1h": -0.02, "4h": 0.0},
    max_drawdown=-0.20,
    regime_ev_bps={"bull": 10.0, "bear": -5.0},
    overtrading_penalty=0.3,
    pnl_usd=600.0,
    hit_rate=0.30,
    n_trades=80,
)

# Agent D: many signals, no EV after costs.
AGENT_D = AgentMetricInputs(
    agent_id="D_overtrading_no_edge",
    ev_after_costs_bps=-15.0,  # negative after costs
    sharpe=-0.2,
    sortino=-0.1,
    brier=0.22,
    calibration_error=0.12,
    ic_by_horizon={"1h": 0.01, "4h": 0.0},
    max_drawdown=-0.25,
    regime_ev_bps={"bull": 5.0, "bear": -20.0},
    overtrading_penalty=0.85,  # heavy overtrading
    pnl_usd=-400.0,
    hit_rate=0.48,
    n_trades=900,
    n_signals=5000,
)


def test_weights_match_operator_spec() -> None:
    assert WEIGHTS == {
        "ev_after_costs": 0.20,
        "sharpe": 0.15,
        "sortino": 0.15,
        "calibration_quality": 0.15,
        "ic_stability": 0.10,
        "drawdown_quality": 0.10,
        "regime_robustness": 0.10,
        "overtrading_penalty": -0.05,
    }


def test_stable_calibrated_agent_outranks_high_pnl_toxic_drawdown() -> None:
    """Core requirement: B (stable, calibrated) beats A (high PnL, toxic DD)."""
    a = score_agent(AGENT_A)
    b = score_agent(AGENT_B)
    assert b.agent_score > a.agent_score


def test_high_pnl_toxic_drawdown_agent_is_not_top() -> None:
    report = build_agent_scoreboard([AGENT_A, AGENT_B, AGENT_C, AGENT_D])
    ranking = report["ranking"]
    assert isinstance(ranking, list)
    top = ranking[0]
    # A has the highest PnL but must not sit at the top.
    assert top["agent_id"] != "A_high_pnl_toxic_dd"
    assert top["agent_id"] == "B_low_pnl_stable_ev"


def test_full_ranking_order() -> None:
    report = build_agent_scoreboard([AGENT_A, AGENT_B, AGENT_C, AGENT_D])
    order = [row["agent_id"] for row in report["ranking"]]  # type: ignore[index]
    # B best (stable+calibrated), D worst (no edge + overtrading).
    assert order[0] == "B_low_pnl_stable_ev"
    assert order[-1] == "D_overtrading_no_edge"


def test_toxic_drawdown_is_flagged() -> None:
    a = score_agent(AGENT_A)
    assert any("toxic_drawdown" in f for f in a.risk_flags)


def test_overconfident_agent_has_poor_calibration_subscore() -> None:
    c = score_agent(AGENT_C)
    assert c.subscores["calibration_quality"] < 0.35
    assert any("poor_calibration" in f for f in c.risk_flags)


def test_no_edge_agent_is_flagged_for_ev_and_overtrading() -> None:
    d = score_agent(AGENT_D)
    assert any("no_edge_after_costs" in f for f in d.risk_flags)
    assert any("overtrading" in f for f in d.risk_flags)
    # Negative EV maps below the neutral 0.5 EV-quality.
    assert d.subscores["ev_after_costs"] < 0.5


# --- Normaliser unit tests --------------------------------------------------


def test_normalize_ev_anchors() -> None:
    assert normalize_ev(0.0) == pytest.approx(0.5)
    assert normalize_ev(50.0) == pytest.approx(1.0)
    assert normalize_ev(-50.0) == pytest.approx(0.0)
    assert normalize_ev(-200.0) == pytest.approx(0.0)  # clamped
    assert normalize_ev(None) == pytest.approx(0.5)


def test_normalize_sharpe_anchors() -> None:
    assert normalize_sharpe(0.0) == pytest.approx(0.5)
    assert normalize_sharpe(2.0) == pytest.approx(1.0)
    assert normalize_sharpe(-2.0) == pytest.approx(0.0)


def test_normalize_drawdown_anchors() -> None:
    assert normalize_drawdown(0.0) == pytest.approx(1.0)
    assert normalize_drawdown(-0.5) == pytest.approx(0.0)
    assert normalize_drawdown(-0.25) == pytest.approx(0.5)
    # Toxic drawdown produces a near-zero quality.
    assert normalize_drawdown(-0.45) < 0.15


def test_normalize_calibration_perfect_vs_coinflip() -> None:
    assert normalize_calibration(0.0, 0.0) == pytest.approx(1.0)
    # Brier 0.25 == coin flip, ECE 0.25 == fully miscalibrated → 0.
    assert normalize_calibration(0.25, 0.25) == pytest.approx(0.0)
    # No calibration data → unknown 0.5, not a reward.
    assert normalize_calibration(None, None) == pytest.approx(0.5)


def test_normalize_ic_stability_penalises_sign_flips() -> None:
    consistent = normalize_ic_stability({"1h": 0.06, "4h": 0.055, "1d": 0.05})
    flipping = normalize_ic_stability({"1h": 0.30, "4h": -0.30})
    # Same-ish magnitude but the sign-flipping IC must score lower.
    assert consistent > flipping


def test_normalize_regime_robustness_rewards_worst_case() -> None:
    robust = normalize_regime_robustness({"bull": 15.0, "bear": 12.0})
    fragile = normalize_regime_robustness({"bull": 60.0, "bear": -40.0})
    # A fragile agent that collapses in one regime scores below a steady one,
    # despite a higher peak.
    assert robust > fragile


def test_score_is_clamped_for_display_but_raw_preserved() -> None:
    d = score_agent(AGENT_D)
    assert 0.0 <= d.agent_score_clamped <= 1.0
    # Raw can differ from clamped only at the [0,1] edges; here it stays inside.
    assert d.agent_score == pytest.approx(d.agent_score_clamped, abs=1e-9)
