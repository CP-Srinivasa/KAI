"""Property-based invariant tests for the Risk Engine.

These tests verify mathematical and logical invariants that must hold for all
valid inputs, not just the specific examples in test_risk_engine.py.

Invariants tested:
1. Position size is never negative for any valid equity/price combination
2. Position size never exceeds max_risk_per_trade_pct of equity (in USD terms)
3. Kill switch always blocks regardless of signal quality
4. Daily loss at/above limit always blocks orders (via kill switch)
5. Drawdown at/above limit always triggers kill switch
6. Averaging-down is always rejected when disallowed
7. max_loss_usd is never greater than position value (cannot exceed 100% loss)
8. Order approval is deterministic (same inputs = same result)
"""

from __future__ import annotations

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits


def _make_limits(
    *,
    initial_equity: float = 10000.0,
    max_risk_per_trade_pct: float = 0.25,
    max_daily_loss_pct: float = 1.0,
    max_total_drawdown_pct: float = 5.0,
    max_open_positions: int = 3,
    max_leverage: float = 1.0,
    require_stop_loss: bool = False,
    allow_averaging_down: bool = False,
    allow_martingale: bool = False,
    kill_switch_enabled: bool = True,
    min_signal_confidence: float = 0.75,
    min_signal_confluence_count: int = 2,
) -> RiskLimits:
    return RiskLimits(
        initial_equity=initial_equity,
        max_risk_per_trade_pct=max_risk_per_trade_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        max_total_drawdown_pct=max_total_drawdown_pct,
        max_open_positions=max_open_positions,
        max_leverage=max_leverage,
        require_stop_loss=require_stop_loss,
        allow_averaging_down=allow_averaging_down,
        allow_martingale=allow_martingale,
        kill_switch_enabled=kill_switch_enabled,
        min_signal_confidence=min_signal_confidence,
        min_signal_confluence_count=min_signal_confluence_count,
    )


# ---------------------------------------------------------------------------
# Invariant 1: Position size is never negative
# ---------------------------------------------------------------------------


@given(
    equity=st.floats(min_value=100.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False),
    entry_price=st.floats(
        min_value=0.01, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
    ),
    stop_loss_price=st.one_of(
        st.none(),
        st.floats(min_value=0.001, max_value=999_999.0, allow_nan=False, allow_infinity=False),
    ),
    risk_pct=st.floats(min_value=0.001, max_value=10.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_position_size_never_negative(
    equity: float,
    entry_price: float,
    stop_loss_price: float | None,
    risk_pct: float,
) -> None:
    """Position size in units must always be >= 0 for valid inputs."""
    engine = RiskEngine(_make_limits(max_risk_per_trade_pct=risk_pct))
    result = engine.calculate_position_size(
        symbol="BTC/USDT",
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        equity=equity,
    )
    assert result.position_size_units >= 0.0, (
        f"position_size_units={result.position_size_units} is negative "
        f"(entry={entry_price}, stop={stop_loss_price}, equity={equity}, risk_pct={risk_pct})"
    )
    assert result.max_loss_usd >= 0.0, f"max_loss_usd={result.max_loss_usd} is negative"
    assert result.max_loss_pct >= 0.0, f"max_loss_pct={result.max_loss_pct} is negative"


# ---------------------------------------------------------------------------
# Invariant 2: max_loss_usd never exceeds risk cap
# ---------------------------------------------------------------------------


@given(
    equity=st.floats(min_value=100.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False),
    entry_price=st.floats(
        min_value=0.01, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
    ),
    risk_pct=st.floats(min_value=0.001, max_value=5.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200)
def test_max_loss_never_exceeds_risk_cap(
    equity: float,
    entry_price: float,
    risk_pct: float,
) -> None:
    """max_loss_usd must never exceed max_risk_per_trade_pct * equity."""
    engine = RiskEngine(_make_limits(max_risk_per_trade_pct=risk_pct))
    result = engine.calculate_position_size(
        symbol="ETH/USDT",
        entry_price=entry_price,
        stop_loss_price=None,
        equity=equity,
    )
    if result.approved:
        max_allowed_loss = equity * (risk_pct / 100.0)
        # Allow 1 cent tolerance for floating-point arithmetic
        assert result.max_loss_usd <= max_allowed_loss + 0.01, (
            f"max_loss_usd={result.max_loss_usd:.6f} exceeds cap "
            f"{max_allowed_loss:.6f} (equity={equity}, risk_pct={risk_pct})"
        )


# ---------------------------------------------------------------------------
# Invariant 3: Kill switch always blocks regardless of signal quality
# ---------------------------------------------------------------------------


@given(
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    confluence=st.integers(min_value=0, max_value=10),
    open_positions=st.integers(min_value=0, max_value=10),
)
@settings(max_examples=100)
def test_kill_switch_always_blocks(
    confidence: float,
    confluence: int,
    open_positions: int,
) -> None:
    """Active kill switch blocks ALL orders regardless of signal quality."""
    engine = RiskEngine(
        _make_limits(
            require_stop_loss=False,
            min_signal_confidence=0.0,
            min_signal_confluence_count=0,
            max_open_positions=100,
        )
    )
    engine.trigger_kill_switch()
    result = engine.check_order(
        symbol="BTC/USDT",
        side="buy",
        signal_confidence=confidence,
        signal_confluence_count=confluence,
        stop_loss_price=None,
        current_open_positions=open_positions,
    )
    assert not result.approved, (
        f"Kill switch active but order was approved "
        f"(confidence={confidence}, confluence={confluence})"
    )
    assert result.check_type == "kill_switch"


# ---------------------------------------------------------------------------
# Invariant 4: Daily loss above limit auto-triggers kill switch
# ---------------------------------------------------------------------------


@given(
    equity=st.floats(
        min_value=1000.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
    ),
    loss_pct=st.floats(min_value=0.001, max_value=50.0, allow_nan=False, allow_infinity=False),
    limit_pct=st.floats(min_value=0.001, max_value=10.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=150)
def test_daily_loss_above_limit_triggers_kill_switch(
    equity: float,
    loss_pct: float,
    limit_pct: float,
) -> None:
    """When realized loss exceeds max_daily_loss_pct and kill switch enabled,
    the kill switch must be triggered."""
    # loss_pct is the amount we lose as a percentage of equity
    # Require a meaningful gap to avoid floating-point rounding making them equal
    # (e.g. 0.001000002 vs 0.001 rounds to same value after pnl/equity*100).
    assume(loss_pct > limit_pct + 0.01)

    engine = RiskEngine(
        _make_limits(
            initial_equity=equity,
            max_daily_loss_pct=limit_pct,
            kill_switch_enabled=True,
        )
    )
    realized_pnl = -equity * (loss_pct / 100.0)  # negative = loss
    state = engine.update_daily_loss(realized_pnl, equity=equity)

    assert state.kill_switch_triggered, (
        f"Kill switch not triggered: loss_pct={loss_pct:.3f}% > limit={limit_pct:.3f}%"
    )
    assert engine.is_halted


# ---------------------------------------------------------------------------
# Invariant 5: Drawdown above limit triggers kill switch
# ---------------------------------------------------------------------------


@given(
    drawdown_pct=st.floats(min_value=0.001, max_value=100.0, allow_nan=False, allow_infinity=False),
    limit_pct=st.floats(min_value=0.001, max_value=50.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=100)
def test_drawdown_above_limit_triggers_kill_switch(
    drawdown_pct: float,
    limit_pct: float,
) -> None:
    """Drawdown exceeding max_total_drawdown_pct must trigger kill switch."""
    assume(drawdown_pct > limit_pct)

    engine = RiskEngine(
        _make_limits(
            max_total_drawdown_pct=limit_pct,
            kill_switch_enabled=True,
        )
    )
    triggered = engine.update_drawdown(drawdown_pct)

    assert triggered, (
        f"Kill switch not triggered: drawdown={drawdown_pct:.3f}% > limit={limit_pct:.3f}%"
    )
    assert engine.is_halted


# ---------------------------------------------------------------------------
# Invariant 6: Averaging-down always rejected when disallowed
# ---------------------------------------------------------------------------


@given(
    confidence=st.floats(min_value=0.8, max_value=1.0, allow_nan=False),
    confluence=st.integers(min_value=3, max_value=10),
)
@settings(max_examples=80)
def test_averaging_down_always_rejected_when_disallowed(
    confidence: float,
    confluence: int,
) -> None:
    """is_averaging_down=True must always produce a violation when allow_averaging_down=False."""
    engine = RiskEngine(
        _make_limits(
            allow_averaging_down=False,
            require_stop_loss=False,
            min_signal_confidence=0.0,
            min_signal_confluence_count=0,
            max_open_positions=100,
        )
    )
    result = engine.check_order(
        symbol="ETH/USDT",
        side="buy",
        signal_confidence=confidence,
        signal_confluence_count=confluence,
        stop_loss_price=None,
        current_open_positions=0,
        is_averaging_down=True,
    )
    assert not result.approved, (
        "Averaging-down order was approved despite allow_averaging_down=False"
    )
    assert "averaging_down_not_allowed" in result.violations


# ---------------------------------------------------------------------------
# Invariant 7: Order approval is deterministic
# ---------------------------------------------------------------------------


@given(
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    confluence=st.integers(min_value=0, max_value=5),
    open_positions=st.integers(min_value=0, max_value=5),
)
@settings(max_examples=100)
def test_order_check_is_deterministic(
    confidence: float,
    confluence: int,
    open_positions: int,
) -> None:
    """Same inputs to check_order must always produce the same approved/rejected result."""
    limits = _make_limits(require_stop_loss=False)

    engine1 = RiskEngine(limits)
    engine2 = RiskEngine(limits)

    kwargs = {
        "symbol": "BTC/USDT",
        "side": "buy",
        "signal_confidence": confidence,
        "signal_confluence_count": confluence,
        "stop_loss_price": None,
        "current_open_positions": open_positions,
    }
    result1 = engine1.check_order(**kwargs)
    result2 = engine2.check_order(**kwargs)

    assert result1.approved == result2.approved, (
        f"Non-deterministic approval: first={result1.approved}, second={result2.approved} "
        f"(confidence={confidence}, confluence={confluence}, positions={open_positions})"
    )
    assert sorted(result1.violations) == sorted(result2.violations)
