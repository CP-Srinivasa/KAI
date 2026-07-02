"""Tests for Phase-0 live-trading hard caps."""

from __future__ import annotations

import pytest

from app.security.live_caps import (
    LIVE_MODE_IDLE_LOCK_SECONDS,
    LIVE_TRADING_DEFAULT_ENABLED,
    MAX_OPEN_POSITIONS,
    MAX_POSITION_USD,
    LiveCapBreach,
    LiveOrderCapsView,
    caps_summary,
    verify_live_order,
)


def _view(**overrides: object) -> LiveOrderCapsView:
    base = {
        "notional_usd": 100.0,
        "current_open_positions": 0,
        "symbol": "BTC/USDT",
        "side": "buy",
    }
    base.update(overrides)
    return LiveOrderCapsView(**base)  # type: ignore[arg-type]


def test_verify_live_order_accepts_phase0_compliant_order() -> None:
    verify_live_order(_view(notional_usd=MAX_POSITION_USD, current_open_positions=1))


@pytest.mark.parametrize("notional", [0.0, -1.0])
def test_verify_live_order_rejects_non_positive_notional(notional: float) -> None:
    with pytest.raises(LiveCapBreach, match="invalid_notional"):
        verify_live_order(_view(notional_usd=notional))


def test_verify_live_order_rejects_position_size_above_hard_cap() -> None:
    with pytest.raises(LiveCapBreach, match="position_size_exceeds_cap"):
        verify_live_order(_view(notional_usd=MAX_POSITION_USD + 0.01))


def test_verify_live_order_rejects_negative_open_position_count() -> None:
    with pytest.raises(LiveCapBreach, match="invalid_open_count"):
        verify_live_order(_view(current_open_positions=-1))


def test_verify_live_order_rejects_when_max_open_positions_already_reached() -> None:
    with pytest.raises(LiveCapBreach, match="max_open_positions_exceeded"):
        verify_live_order(_view(current_open_positions=MAX_OPEN_POSITIONS))


def test_caps_summary_exposes_fail_closed_phase0_contract() -> None:
    summary = caps_summary()

    assert summary["max_position_usd"] == MAX_POSITION_USD
    assert summary["max_open_positions"] == MAX_OPEN_POSITIONS
    assert summary["live_default_enabled"] is LIVE_TRADING_DEFAULT_ENABLED is False
    assert summary["live_idle_lock_seconds"] == LIVE_MODE_IDLE_LOCK_SECONDS
    assert summary["phase"] == "phase-0-light-live"
    assert summary["spec_doc"] == "docs/security/kai_light_live_phase0_spec.md"
