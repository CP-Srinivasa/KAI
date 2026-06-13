"""A-Fix 2026-06-13: premium signals execute 1:1 with stated leverage.

Operator-Befund: paper PnL was systematically too small because the stated
leverage was audit-only (leverage_mode="paper_audit_only"). COAI: 4 targets,
avg +1.30% spot, 10x → must be ~13% on the margin, not 1.3%. This pins:
  - sizing: risk-based size = margin, leverage multiplies it (SL-distance branch)
  - the per-position notional cap is skipped in this mode (leverage not undone)
  - leverage stays clamped to max_leverage
  - liquidation price + monitor_positions force-close (total margin loss)
"""

from __future__ import annotations

import pytest

from app.execution.paper_engine import PaperExecutionEngine, _liquidation_price
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits


def _limits(**overrides) -> RiskLimits:
    defaults = {
        "initial_equity": 10000.0,
        "max_risk_per_trade_pct": 0.25,
        "max_daily_loss_pct": 1.0,
        "max_total_drawdown_pct": 5.0,
        "max_open_positions": 3,
        "max_leverage": 10.0,
        "require_stop_loss": True,
        "allow_averaging_down": False,
        "allow_martingale": False,
        "kill_switch_enabled": True,
        "min_signal_confidence": 0.75,
        "min_signal_confluence_count": 2,
        "max_position_size_pct": 20.0,
    }
    defaults.update(overrides)
    return RiskLimits(**defaults)


# ── sizing ───────────────────────────────────────────────────────────────────


def test_sl_distance_branch_applies_leverage_when_flagged() -> None:
    eng = RiskEngine(_limits())
    base = eng.calculate_position_size(
        symbol="COAI/USDT",
        entry_price=0.365,
        stop_loss_price=0.35,
        equity=20000.0,
        leverage=10.0,
        apply_signal_leverage=False,
    )
    levd = eng.calculate_position_size(
        symbol="COAI/USDT",
        entry_price=0.365,
        stop_loss_price=0.35,
        equity=20000.0,
        leverage=10.0,
        apply_signal_leverage=True,
    )
    # leverage multiplies the risk-based (margin) size by exactly 10x …
    assert levd.position_size_units == base.position_size_units * 10.0
    # … so the leveraged max-loss is 10x the conservative one
    assert levd.max_loss_usd > base.max_loss_usd * 9.9


def test_leverage_clamped_to_max_leverage() -> None:
    eng = RiskEngine(_limits(max_leverage=5.0))
    base = eng.calculate_position_size(
        symbol="X/USDT",
        entry_price=10.0,
        stop_loss_price=9.5,
        equity=20000.0,
        leverage=1.0,
        apply_signal_leverage=True,
    )
    levd = eng.calculate_position_size(
        symbol="X/USDT",
        entry_price=10.0,
        stop_loss_price=9.5,
        equity=20000.0,
        leverage=50.0,
        apply_signal_leverage=True,
    )
    # 50x requested but clamped to 5x → exactly 5x the 1x size
    assert levd.position_size_units == base.position_size_units * 5.0


def test_signal_leverage_skips_position_size_cap() -> None:
    # 10x notional would be ~62% equity, far over the 20% cap; flag skips it
    eng = RiskEngine(_limits(max_position_size_pct=20.0))
    levd = eng.calculate_position_size(
        symbol="COAI/USDT",
        entry_price=0.365,
        stop_loss_price=0.35,
        equity=20000.0,
        leverage=10.0,
        apply_signal_leverage=True,
    )
    notional = levd.position_size_units * 0.365
    assert notional > 20000.0 * 0.20  # exceeds the cap → cap was skipped
    assert "position_capped" not in levd.rationale


def test_default_off_keeps_conservative_sizing() -> None:
    eng = RiskEngine(_limits())
    res = eng.calculate_position_size(
        symbol="X/USDT",
        entry_price=10.0,
        stop_loss_price=9.5,
        equity=20000.0,
        leverage=10.0,  # leverage given but flag OFF → ignored
    )
    # unchanged: max-loss ≈ max_risk_per_trade (0.25% of equity)
    assert abs(res.max_loss_usd - 20000.0 * 0.0025) < 1e-6


# ── liquidation ──────────────────────────────────────────────────────────────


def test_liquidation_price_long_and_short() -> None:
    # 10x long: liquidation ~10% below entry
    assert _liquidation_price(100.0, 10.0, "long") == pytest.approx(90.0)
    # 10x short: ~10% above entry
    assert _liquidation_price(100.0, 10.0, "short") == pytest.approx(110.0)
    # spot / 1x → no liquidation
    assert _liquidation_price(100.0, 1.0, "long") is None
    assert _liquidation_price(100.0, None, "long") is None


def test_monitor_liquidates_leveraged_position_before_wide_stop() -> None:
    """A 10x long with a wide 20% stop liquidates at ~-10% (margin wiped) BEFORE
    the stop would ever trigger — the realistic futures outcome."""
    eng = PaperExecutionEngine(initial_equity=100000.0, live_enabled=False)
    order = eng.create_order(
        symbol="WIDE/USDT",
        side="buy",
        quantity=100.0,
        order_type="market",
        stop_loss=80.0,
        take_profit=130.0,
        position_side="long",
        leverage=10.0,
    )
    eng.fill_order(order, 100.0)
    # price drops 12% — past the 10% liquidation, but not yet the 20% stop
    fills = eng.monitor_positions({"WIDE/USDT": 88.0})
    assert len(fills) == 1
    assert "WIDE/USDT" not in eng.portfolio.positions  # force-closed
    # closed at liquidation price (~90), not the stop (80) or the 88 spot
    assert fills[0].fill_price <= 90.0 * 1.01


def test_monitor_stop_still_wins_when_nearer_than_liquidation() -> None:
    """COAI-like: 10x but a tight 4% stop fires before the 10% liquidation."""
    eng = PaperExecutionEngine(initial_equity=100000.0, live_enabled=False)
    order = eng.create_order(
        symbol="TIGHT/USDT",
        side="buy",
        quantity=100.0,
        order_type="market",
        stop_loss=96.0,
        take_profit=110.0,
        position_side="long",
        leverage=10.0,
    )
    eng.fill_order(order, 100.0)
    fills = eng.monitor_positions({"TIGHT/USDT": 95.5})  # past 96 stop, not 90 liq
    assert len(fills) == 1
    # closed near the stop trigger (~95.5), NOT at the ~90 liquidation price —
    # proves the stop fired first (PaperFill carries no reason; price is the tell)
    assert fills[0].fill_price > 93.0
