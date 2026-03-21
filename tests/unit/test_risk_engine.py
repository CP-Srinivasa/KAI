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
        symbol="BTC/USDT", side="buy",
        signal_confidence=0.9, signal_confluence_count=3,
        stop_loss_price=60000.0, current_open_positions=0,
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
        symbol="ETH/USDT", side="buy",
        signal_confidence=0.9, signal_confluence_count=3,
        stop_loss_price=3000.0, current_open_positions=0,
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
        symbol="BTC/USDT", side="buy",
        signal_confidence=0.5,  # below 0.75
        signal_confluence_count=3,
        stop_loss_price=60000.0, current_open_positions=0,
    )
    assert not result.approved
    assert any("signal_confidence_too_low" in v for v in result.violations)


def test_low_confluence_rejected():
    engine = _default_engine()
    result = engine.check_order(
        symbol="BTC/USDT", side="buy",
        signal_confidence=0.9,
        signal_confluence_count=1,  # below 2
        stop_loss_price=60000.0, current_open_positions=0,
    )
    assert not result.approved
    assert any("signal_confluence_too_low" in v for v in result.violations)


# --- stop loss required ---

def test_missing_stop_loss_rejected_when_required():
    engine = _default_engine(require_stop_loss=True)
    result = engine.check_order(
        symbol="BTC/USDT", side="buy",
        signal_confidence=0.9, signal_confluence_count=3,
        stop_loss_price=None,  # missing
        current_open_positions=0,
    )
    assert not result.approved
    assert "stop_loss_required_but_missing" in result.violations


def test_no_stop_loss_ok_when_not_required():
    engine = _default_engine(require_stop_loss=False)
    result = engine.check_order(
        symbol="BTC/USDT", side="buy",
        signal_confidence=0.9, signal_confluence_count=3,
        stop_loss_price=None,
        current_open_positions=0,
    )
    assert result.approved


# --- max open positions ---

def test_max_positions_rejected():
    engine = _default_engine(max_open_positions=3)
    result = engine.check_order(
        symbol="SOL/USDT", side="buy",
        signal_confidence=0.9, signal_confluence_count=3,
        stop_loss_price=140.0, current_open_positions=3,  # at limit
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
        stop_loss_price=64000.0,   # $1000 risk per unit
        equity=10000.0,
    )
    assert result.approved
    # max_risk_usd = 10000 * 0.25/100 = 25 USD
    # units = 25 / 1000 = 0.025
    assert abs(result.position_size_units - 0.025) < 0.001
    assert result.max_loss_usd <= 25.5  # allow tiny rounding


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
        symbol="BTC/USDT", side="buy",
        signal_confidence=0.85,
        signal_confluence_count=3,
        stop_loss_price=60000.0,
        current_open_positions=1,
    )
    assert result.approved
    assert result.violations == []
    assert result.check_id.startswith("rck_")
