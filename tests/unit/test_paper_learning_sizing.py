"""Tests for the Paper-Learning sizing patch (2026-06-18).

Goal: collect MORE paper outcomes for edge measurement without raising the daily
notional cap. Two knobs on the risk-based sizing path (both default-OFF):
  - min_stop_pct_for_sizing: floor on the stop distance USED FOR SIZING so a tight
    ATR stop cannot inflate notional (the REAL stop is unchanged).
  - max_notional_per_trade_usd: absolute per-trade notional ceiling.
"""

from __future__ import annotations

from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits


def _engine(**overrides) -> RiskEngine:
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
    return RiskEngine(RiskLimits(**defaults))


def _size(engine: RiskEngine, *, entry=65000.0, stop=64740.0, equity=10000.0):
    # default stop is 0.4% away (tight) → risk-based notional inflates without a floor
    return engine.calculate_position_size(
        symbol="BTC/USDT", entry_price=entry, stop_loss_price=stop, equity=equity
    )


def test_defaults_off_is_noop() -> None:
    # Both knobs default 0.0 → classic risk-based sizing (max_risk_usd / stop_dist),
    # capped only by the existing 20% position cap.
    r = _size(_engine(max_position_size_pct=0.0))
    # max_risk_usd = 10000 * 0.25/100 = 25 ; stop_dist = 260 ; units = 25/260
    assert abs(r.position_size_units - (25.0 / 260.0)) < 1e-6


def test_stop_floor_shrinks_position_on_tight_stop() -> None:
    base = _size(_engine(max_position_size_pct=0.0))
    floored = _size(_engine(max_position_size_pct=0.0, min_stop_pct_for_sizing=4.0))
    # sizing stop floored to 4% of 65000 = 2600 (>> real 260) → ~10x fewer units
    assert floored.position_size_units < base.position_size_units
    assert abs(floored.position_size_units - (25.0 / 2600.0)) < 1e-6
    # REAL stop is unchanged → real max loss still measured off the tight 260 stop
    assert floored.max_loss_usd < base.max_loss_usd


def test_stop_floor_noop_when_actual_stop_already_wider() -> None:
    # real stop 5000 away (~7.7%) already exceeds the 4% floor → no change
    base = _size(_engine(max_position_size_pct=0.0), stop=60000.0)
    floored = _size(_engine(max_position_size_pct=0.0, min_stop_pct_for_sizing=4.0), stop=60000.0)
    assert abs(floored.position_size_units - base.position_size_units) < 1e-9


def test_per_trade_notional_cap_clamps_position() -> None:
    # big risk-based size, % cap disabled → isolate the per-trade USD cap
    r = _size(
        _engine(
            max_risk_per_trade_pct=10.0,
            max_position_size_pct=0.0,
            max_notional_per_trade_usd=300.0,
        ),
        stop=64000.0,  # 1000 risk/unit → units 1.0 → notional 65000 uncapped
    )
    notional = r.position_size_units * 65000.0
    assert abs(notional - 300.0) < 1e-6


def test_per_trade_cap_off_when_zero() -> None:
    r = _size(
        _engine(max_risk_per_trade_pct=10.0, max_position_size_pct=0.0),
        stop=64000.0,
    )
    notional = r.position_size_units * 65000.0
    assert notional > 300.0  # uncapped (units 1.0 → 65000)
