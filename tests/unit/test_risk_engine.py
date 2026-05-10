"""Unit tests for the Risk Engine."""

from __future__ import annotations

from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits


def _default_limits(**overrides) -> RiskLimits:
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
    }
    defaults.update(overrides)
    return RiskLimits(**defaults)


def _default_engine(**limit_overrides) -> RiskEngine:
    return RiskEngine(_default_limits(**limit_overrides))


# --- kill switch ---


def test_kill_switch_blocks_all_orders():
    engine = _default_engine()
    engine.trigger_kill_switch()
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.9,
        signal_confluence_count=3,
        stop_loss_price=60000.0,
        current_open_positions=0,
    )
    assert not result.approved
    assert "kill_switch_active" in result.violations


def test_kill_switch_reset():
    engine = _default_engine()
    engine.trigger_kill_switch()
    assert engine.is_halted
    engine.reset_kill_switch()
    assert not engine.is_halted


def test_pause_blocks_orders():
    engine = _default_engine()
    engine.pause()
    result = engine.check_order(
        symbol="ETH/USDT",
        side="buy",
        signal_confidence=0.9,
        signal_confluence_count=3,
        stop_loss_price=3000.0,
        current_open_positions=0,
    )
    assert not result.approved
    assert "system_paused" in result.violations


def test_resume_after_pause():
    engine = _default_engine()
    engine.pause()
    engine.resume()
    assert not engine._paused


def test_resume_fails_when_kill_switch_active():
    engine = _default_engine()
    engine.trigger_kill_switch()
    engine.resume()
    assert engine._kill_switch_active


# --- signal quality gates ---


def test_low_confidence_rejected():
    engine = _default_engine()
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.5,  # below 0.75
        signal_confluence_count=3,
        stop_loss_price=60000.0,
        current_open_positions=0,
    )
    assert not result.approved
    assert any("signal_confidence_too_low" in v for v in result.violations)


def test_low_confluence_rejected():
    engine = _default_engine()
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.9,
        signal_confluence_count=1,  # below 2
        stop_loss_price=60000.0,
        current_open_positions=0,
    )
    assert not result.approved
    assert any("signal_confluence_too_low" in v for v in result.violations)


# --- stop loss required ---


def test_missing_stop_loss_rejected_when_required():
    engine = _default_engine(require_stop_loss=True)
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.9,
        signal_confluence_count=3,
        stop_loss_price=None,  # missing
        current_open_positions=0,
    )
    assert not result.approved
    assert "stop_loss_required_but_missing" in result.violations


def test_no_stop_loss_ok_when_not_required():
    engine = _default_engine(require_stop_loss=False)
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.9,
        signal_confluence_count=3,
        stop_loss_price=None,
        current_open_positions=0,
    )
    assert result.approved


# --- max open positions ---


def test_max_positions_rejected():
    engine = _default_engine(max_open_positions=3)
    result = engine.check_order(
        symbol="SOL/USDT",
        side="buy",
        signal_confidence=0.9,
        signal_confluence_count=3,
        stop_loss_price=140.0,
        current_open_positions=3,  # at limit
    )
    assert not result.approved
    assert any("max_open_positions_reached" in v for v in result.violations)


# --- daily loss kill switch ---


def test_daily_loss_triggers_kill_switch():
    engine = _default_engine(max_daily_loss_pct=1.0, kill_switch_enabled=True)
    state = engine.update_daily_loss(-150.0, equity=10000.0)  # -1.5% loss
    assert state.kill_switch_triggered
    assert engine._kill_switch_active


def test_daily_loss_within_limit_ok():
    engine = _default_engine(max_daily_loss_pct=1.0)
    state = engine.update_daily_loss(-50.0, equity=10000.0)  # -0.5% loss
    assert not state.kill_switch_triggered
    assert not engine._kill_switch_active


# --- position sizing ---


def test_position_size_with_stop_loss():
    engine = _default_engine(max_risk_per_trade_pct=0.25)
    result = engine.calculate_position_size(
        symbol="BTC/USDT",
        entry_price=65000.0,
        stop_loss_price=64000.0,  # $1000 risk per unit
        equity=10000.0,
    )
    assert result.approved
    # max_risk_usd = 10000 * 0.25/100 = 25 USD
    # units = 25 / 1000 = 0.025
    assert abs(result.position_size_units - 0.025) < 0.001
    assert result.max_loss_usd <= 25.5  # allow tiny rounding


def test_position_size_uses_signal_margin_and_leverage_when_safe():
    engine = _default_engine(max_risk_per_trade_pct=10.0, max_leverage=20.0)
    result = engine.calculate_position_size(
        symbol="BTC/USDT",
        entry_price=100.0,
        stop_loss_price=99.0,
        equity=10000.0,
        leverage=10.0,
        risk_allocation_pct=5.0,
    )
    assert result.approved
    # 10_000 equity * 5% margin * 10x leverage = 5_000 notional.
    assert result.position_size_units == 50.0
    assert result.position_size_pct == 50.0
    assert result.max_loss_usd == 50.0
    assert "signal_margin_leverage" in result.rationale


def test_position_size_caps_signal_leverage_at_limit():
    engine = _default_engine(max_risk_per_trade_pct=10.0, max_leverage=2.0)
    result = engine.calculate_position_size(
        symbol="BTC/USDT",
        entry_price=100.0,
        stop_loss_price=99.0,
        equity=10000.0,
        leverage=10.0,
        risk_allocation_pct=5.0,
    )
    assert result.approved
    # Leverage capped to 2x: 10_000 * 5% * 2 / 100 = 10 units.
    assert result.position_size_units == 10.0
    assert "leverage=2x (capped)" in result.rationale


def test_position_size_caps_signal_size_by_stop_loss_risk():
    engine = _default_engine(max_risk_per_trade_pct=0.25, max_leverage=20.0)
    result = engine.calculate_position_size(
        symbol="BTC/USDT",
        entry_price=100.0,
        stop_loss_price=90.0,
        equity=10000.0,
        leverage=10.0,
        risk_allocation_pct=5.0,
    )
    assert result.approved
    # Requested = 50 units, but risk cap = $25 max loss / $10 risk = 2.5 units.
    assert result.position_size_units == 2.5
    assert result.max_loss_usd == 25.0
    assert result.max_loss_pct == 0.25
    assert "risk_capped" in result.rationale


def test_position_size_invalid_price():
    engine = _default_engine()
    result = engine.calculate_position_size(
        symbol="BTC/USDT",
        entry_price=0.0,  # invalid
        stop_loss_price=None,
        equity=10000.0,
    )
    assert not result.approved


# --- full approved order ---


def test_approved_order():
    engine = _default_engine()
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.85,
        signal_confluence_count=3,
        stop_loss_price=60000.0,
        current_open_positions=1,
    )
    assert result.approved
    assert result.violations == []
    assert result.check_id.startswith("rck_")


# --- SL/TP geometry gate (defense against inverted stops) ---


def test_long_sl_above_entry_rejected():
    engine = _default_engine()
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.85,
        signal_confluence_count=3,
        stop_loss_price=73718.0,
        current_open_positions=0,
        entry_price=73238.0,
        take_profit_price=79000.0,
    )
    assert not result.approved
    assert any("sl_geometry_invalid:long_sl_at_or_above_entry" in v for v in result.violations)


def test_long_sl_equal_to_entry_rejected():
    engine = _default_engine()
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.85,
        signal_confluence_count=3,
        stop_loss_price=73238.0,
        current_open_positions=0,
        entry_price=73238.0,
    )
    assert not result.approved
    assert any("sl_geometry_invalid:long_sl_at_or_above_entry" in v for v in result.violations)


def test_long_tp_below_entry_rejected():
    engine = _default_engine()
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.85,
        signal_confluence_count=3,
        stop_loss_price=60000.0,
        current_open_positions=0,
        entry_price=65000.0,
        take_profit_price=64000.0,
    )
    assert not result.approved
    assert any("tp_geometry_invalid:long_tp_at_or_below_entry" in v for v in result.violations)


def test_short_sl_below_entry_rejected():
    engine = _default_engine()
    result = engine.check_order(
        symbol="BTC/USDT",
        side="sell",
        signal_confidence=0.85,
        signal_confluence_count=3,
        stop_loss_price=64000.0,
        current_open_positions=0,
        entry_price=65000.0,
        take_profit_price=60000.0,
    )
    assert not result.approved
    assert any("sl_geometry_invalid:short_sl_at_or_below_entry" in v for v in result.violations)


def test_short_tp_above_entry_rejected():
    engine = _default_engine()
    result = engine.check_order(
        symbol="BTC/USDT",
        side="sell",
        signal_confidence=0.85,
        signal_confluence_count=3,
        stop_loss_price=70000.0,
        current_open_positions=0,
        entry_price=65000.0,
        take_profit_price=66000.0,
    )
    assert not result.approved
    assert any("tp_geometry_invalid:short_tp_at_or_above_entry" in v for v in result.violations)


def test_long_valid_geometry_approved():
    engine = _default_engine()
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.85,
        signal_confluence_count=3,
        stop_loss_price=60000.0,
        current_open_positions=0,
        entry_price=65000.0,
        take_profit_price=72000.0,
    )
    assert result.approved
    assert result.violations == []


def test_short_valid_geometry_approved():
    engine = _default_engine()
    result = engine.check_order(
        symbol="BTC/USDT",
        side="sell",
        signal_confidence=0.85,
        signal_confluence_count=3,
        stop_loss_price=70000.0,
        current_open_positions=0,
        entry_price=65000.0,
        take_profit_price=60000.0,
    )
    assert result.approved
    assert result.violations == []


def test_geometry_check_skipped_when_entry_price_omitted():
    """Backwards-compat: no entry_price → no geometry validation (legacy callers)."""
    engine = _default_engine()
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.85,
        signal_confluence_count=3,
        stop_loss_price=73718.0,  # would be inverted if entry were 73238
        current_open_positions=0,
    )
    assert result.approved
    assert not any("geometry_invalid" in v for v in result.violations)


def test_geometry_check_skipped_when_sl_omitted():
    engine = _default_engine(require_stop_loss=False)
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.85,
        signal_confluence_count=3,
        stop_loss_price=None,
        current_open_positions=0,
        entry_price=65000.0,
        take_profit_price=72000.0,
    )
    assert result.approved
    assert result.violations == []


def test_position_size_with_leverage_and_margin():
    engine = _default_engine(max_leverage=10.0, max_risk_per_trade_pct=10.0)
    result = engine.calculate_position_size(
        symbol="BTC/USDT",
        entry_price=10000.0,
        stop_loss_price=9000.0,
        equity=10000.0,
        leverage=5.0,
        risk_allocation_pct=2.0,  # 2% margin
    )
    assert result.approved
    # 2% of 10000 equity = 200 margin
    # notional = 200 * 5.0 leverage = 1000
    # units = 1000 / 10000 entry_price = 0.1
    assert abs(result.position_size_units - 0.1) < 0.001


def test_position_size_leverage_cap():
    engine = _default_engine(max_leverage=3.0, max_risk_per_trade_pct=10.0)
    result = engine.calculate_position_size(
        symbol="BTC/USDT",
        entry_price=10000.0,
        stop_loss_price=9000.0,
        equity=10000.0,
        leverage=10.0,  # Should be capped at 3.0
        risk_allocation_pct=2.0,  # 2% margin
    )
    assert result.approved
    # capped leverage = 3.0
    # 2% of 10000 = 200 margin
    # notional = 200 * 3.0 = 600
    # units = 600 / 10000 = 0.06
    assert abs(result.position_size_units - 0.06) < 0.001
