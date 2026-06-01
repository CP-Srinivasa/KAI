"""Sprint B (NEO-F-301): CostModel is the single source of round-trip cost.

Contract: the V1 cost-geometry gate's round-trip fee, the paper engine's fee,
and the backtest engine's fee must all derive from the SAME CostModel — no
divergent RISK_ROUND_TRIP_FEE_PCT path. With the realistic paper default
(10 bp/side -> 0.2% round-trip) the V1 gate becomes nearly inert, which is the
intended outcome per the bleed diagnosis (cost is not the primary driver).
"""

from __future__ import annotations

from app.execution.cost_model import CostModel
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits


def _limits(**overrides) -> RiskLimits:
    """Mirror the helper in test_risk_cost_geometry_gate so we build a valid
    RiskLimits with the same defaults and only override what we test."""
    defaults = {
        "initial_equity": 10000.0,
        "max_risk_per_trade_pct": 0.25,
        "max_daily_loss_pct": 1.0,
        "max_total_drawdown_pct": 5.0,
        "max_open_positions": 3,
        "max_leverage": 1.0,
        "require_stop_loss": True,
        "allow_averaging_down": False,
        "allow_martingale": False,
        "kill_switch_enabled": True,
        "min_signal_confidence": 0.75,
        "min_signal_confluence_count": 2,
        "regime_filter_enabled": False,
    }
    defaults.update(overrides)
    return RiskLimits(**defaults)


def test_settings_default_round_trip_matches_cost_model_paper():
    """The productive Settings default for round_trip_fee_pct must equal the
    CostModel-derived paper round-trip — they cannot drift apart."""
    from app.core.settings import RiskSettings

    cm = CostModel()
    assert RiskSettings().round_trip_fee_pct == cm.round_trip_fee_pct(venue="paper")


def test_v1_gate_is_nearly_inert_with_realistic_paper_cost():
    """A 0.8% stop used to be rejected against the legacy 1.2% round-trip
    (threshold 1.8%). Against the realistic 0.2% round-trip the threshold is
    1.5 * 0.2% = 0.3% — the 0.8% stop now passes. This is the intended
    behavioural change."""
    cm = CostModel()
    rt_pct = cm.round_trip_fee_pct(venue="paper")
    engine = RiskEngine(_limits(min_sl_cost_multiple=1.5, round_trip_fee_pct=rt_pct))
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.9,
        signal_confluence_count=3,
        stop_loss_price=99.2,  # 0.8% stop
        current_open_positions=0,
        entry_price=100.0,
    )
    assert result.approved
    assert all("sub_cost_geometry" not in v for v in result.violations)


def test_v1_gate_still_rejects_truly_microscopic_stop():
    """Even with realistic cost the gate is not dead: a 0.1% stop is still below
    the 0.3% threshold and must be rejected — the structural floor still works."""
    cm = CostModel()
    rt_pct = cm.round_trip_fee_pct(venue="paper")
    engine = RiskEngine(_limits(min_sl_cost_multiple=1.5, round_trip_fee_pct=rt_pct))
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.9,
        signal_confluence_count=3,
        stop_loss_price=99.9,  # 0.1% stop, below 0.3% threshold
        current_open_positions=0,
        entry_price=100.0,
    )
    assert not result.approved
    assert any(v.startswith("sub_cost_geometry_rejected") for v in result.violations)
