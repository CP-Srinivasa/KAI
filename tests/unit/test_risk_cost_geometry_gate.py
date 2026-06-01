"""V1 (P0): cost-aware SL geometry gate in RiskEngine.check_order.

Root-cause (NEO-F-201): the paper venue's round-trip taker fee (~1.2%) can
exceed the ATR-derived stop distance (~0.8-1.0%), so a stopped trade is a
structurally guaranteed net loss. This gate rejects an order whose stop is too
tight to ever clear the round-trip transaction cost.

Contract under test (behaviour, not implementation):
- default-OFF: min_sl_cost_multiple <= 0 means the gate never fires
  (backward-compatible with all legacy callers/tests).
- when enabled: reject with violation token `sub_cost_geometry_rejected` iff
  |entry - SL| / entry < k * round_trip_fee_pct/100.
- additive: it does not relax any existing gate; an otherwise-valid order with
  a wide-enough stop still passes.
- symmetric for long and short.
"""

from __future__ import annotations

from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits

_ROUND_TRIP_FEE_PCT = 1.2  # 2 x 60 bps taker (paper venue worst-case)


def _limits(**overrides) -> RiskLimits:
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
        # regime filter off so SL geometry is isolated in these tests
        "regime_filter_enabled": False,
    }
    defaults.update(overrides)
    return RiskLimits(**defaults)


def _order(engine: RiskEngine, *, entry: float, sl: float, side: str = "buy"):
    return engine.check_order(
        symbol="BTC/USDT",
        side=side,
        signal_confidence=0.9,
        signal_confluence_count=3,
        stop_loss_price=sl,
        current_open_positions=0,
        entry_price=entry,
    )


# --- default-off (backward compatibility) ---


def test_gate_disabled_by_default_allows_tight_stop():
    """RiskLimits() without the new field => gate off => tight stop still passes.

    This is the contract that keeps every legacy unit test green."""
    engine = RiskEngine(_limits())  # min_sl_cost_multiple defaults to 0.0
    # 0.5% stop, far below round-trip 1.2% — would be rejected if gate were on.
    result = _order(engine, entry=100.0, sl=99.5)
    assert result.approved
    assert all("sub_cost_geometry" not in v for v in result.violations)


def test_explicit_zero_multiple_disables_gate():
    engine = RiskEngine(_limits(min_sl_cost_multiple=0.0, round_trip_fee_pct=_ROUND_TRIP_FEE_PCT))
    result = _order(engine, entry=100.0, sl=99.9)  # 0.1% stop
    assert result.approved


# --- normal case: enabled gate rejects sub-cost geometry ---


def test_tight_long_stop_rejected_when_enabled():
    engine = RiskEngine(_limits(min_sl_cost_multiple=1.5, round_trip_fee_pct=_ROUND_TRIP_FEE_PCT))
    # 0.8% stop. Threshold = 1.5 * 1.2% = 1.8%. 0.8 < 1.8 => reject.
    result = _order(engine, entry=100.0, sl=99.2)
    assert not result.approved
    assert any(v.startswith("sub_cost_geometry_rejected") for v in result.violations)


def test_tight_short_stop_rejected_when_enabled():
    engine = RiskEngine(_limits(min_sl_cost_multiple=1.5, round_trip_fee_pct=_ROUND_TRIP_FEE_PCT))
    # short: SL above entry. 1.0% distance < 1.8% threshold => reject.
    result = _order(engine, entry=100.0, sl=101.0, side="sell")
    assert not result.approved
    assert any(v.startswith("sub_cost_geometry_rejected") for v in result.violations)


def test_wide_stop_passes_when_enabled():
    engine = RiskEngine(_limits(min_sl_cost_multiple=1.5, round_trip_fee_pct=_ROUND_TRIP_FEE_PCT))
    # 2.5% stop > 1.8% threshold => pass.
    result = _order(engine, entry=100.0, sl=97.5)
    assert result.approved
    assert all("sub_cost_geometry" not in v for v in result.violations)


# --- boundary cases ---


def test_stop_at_or_above_threshold_passes():
    """Boundary: a stop that meets/exceeds k*fee is NOT sub-cost (strict <).

    NOTE: exact float equality at 1.8% is numerically fragile (98.2 -> 1.79999...%
    in binary), and a guardrail erring micro-conservatively there is harmless.
    We assert the intended behaviour with a value unambiguously >= threshold."""
    engine = RiskEngine(_limits(min_sl_cost_multiple=1.5, round_trip_fee_pct=_ROUND_TRIP_FEE_PCT))
    result = _order(engine, entry=100.0, sl=98.19)  # 1.81% > 1.8%
    assert result.approved


def test_just_below_threshold_rejected():
    engine = RiskEngine(_limits(min_sl_cost_multiple=1.5, round_trip_fee_pct=_ROUND_TRIP_FEE_PCT))
    result = _order(engine, entry=100.0, sl=98.21)  # 1.79% < 1.8%
    assert not result.approved
    assert any(v.startswith("sub_cost_geometry_rejected") for v in result.violations)


# --- error / robustness cases ---


def test_gate_skipped_without_entry_price():
    """No entry_price => geometry cannot be evaluated => gate must not fire
    (backward-compatible with callers that omit entry_price)."""
    engine = RiskEngine(_limits(min_sl_cost_multiple=1.5, round_trip_fee_pct=_ROUND_TRIP_FEE_PCT))
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.9,
        signal_confluence_count=3,
        stop_loss_price=99.9,
        current_open_positions=0,
        # entry_price omitted
    )
    assert all("sub_cost_geometry" not in v for v in result.violations)


def test_missing_stop_loss_does_not_raise_cost_gate():
    """A missing SL is already caught by the stop_loss_required gate; the cost
    gate must not double-report or crash on None."""
    engine = RiskEngine(_limits(min_sl_cost_multiple=1.5, round_trip_fee_pct=_ROUND_TRIP_FEE_PCT))
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.9,
        signal_confluence_count=3,
        stop_loss_price=None,
        current_open_positions=0,
        entry_price=100.0,
    )
    assert not result.approved
    assert "stop_loss_required_but_missing" in result.violations
    assert all("sub_cost_geometry" not in v for v in result.violations)


def test_existing_gates_still_enforced_with_cost_gate_on():
    """Additive contract: enabling the cost gate must not weaken max_open_positions."""
    engine = RiskEngine(
        _limits(
            min_sl_cost_multiple=1.5,
            round_trip_fee_pct=_ROUND_TRIP_FEE_PCT,
            max_open_positions=3,
        )
    )
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=0.9,
        signal_confluence_count=3,
        stop_loss_price=97.5,  # wide stop, passes cost gate
        current_open_positions=3,  # at cap
        entry_price=100.0,
    )
    assert not result.approved
    assert any(v.startswith("max_open_positions_reached") for v in result.violations)
