"""Premium-Fastlane settings (Goal 2026-06-05 §3/§21).

Behaviour pinned:
- fail-closed default (enabled=False)
- paper-enabled / live-disabled posture maps cleanly
- 30-day default window
- live triple-flag arming token constant + refusal when incomplete
- notional / leverage bound validation
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.settings import (
    LIVE_CANARY_ACK_SENTINEL,
    AppSettings,
    PremiumFastlaneSettings,
    PremiumSettings,
)
from app.execution.premium_fastlane import live_fastlane_armed


def test_fastlane_defaults_are_fail_closed() -> None:
    fl = PremiumFastlaneSettings()
    assert fl.enabled is False
    assert fl.live_enabled is False
    assert fl.duration_days == 30
    assert fl.mode == "paper_testnet_demo"
    # bypasses default ON (the lane's purpose) but only ever apply to authentic
    # premium / non-live — proven in the router + bridge tests.
    assert fl.bypass_entry_mode_for_paper is True
    assert fl.bypass_source_allowlist is True
    assert fl.bypass_manual_approval is True
    # hard guards default ON
    assert fl.require_schema_valid is True
    assert fl.require_sl is True
    assert fl.require_targets is True


def test_leverage_and_notional_policy_defaults() -> None:
    fl = PremiumFastlaneSettings()
    assert fl.default_leverage == 10.0
    assert fl.max_leverage == 10.0
    assert fl.default_notional_usdt == 100.0
    assert fl.min_notional_usdt == 10.0
    assert fl.max_notional_usdt == 250.0
    assert fl.paper_equity_usdt == 10000.0


def test_routing_priority_and_allowed_exchanges_parse() -> None:
    fl = PremiumFastlaneSettings()
    assert fl.routing_priority_list == ["paper", "testnet", "demo", "simulated_exchange"]
    assert "bybit" in fl.allowed_exchange_list
    assert "okx" in fl.allowed_exchange_list


def test_notional_bounds_validation() -> None:
    with pytest.raises(ValidationError):
        PremiumFastlaneSettings(min_notional_usdt=500.0, max_notional_usdt=250.0)


def test_default_leverage_above_cap_rejected() -> None:
    with pytest.raises(ValidationError):
        PremiumFastlaneSettings(default_leverage=25.0, max_leverage=10.0)


def test_live_canary_sentinel_constant() -> None:
    assert LIVE_CANARY_ACK_SENTINEL == "I_UNDERSTAND_REAL_CAPITAL_RISK"


def test_live_not_armed_without_all_three_flags() -> None:
    # only fastlane.live_enabled
    s = AppSettings()
    s.premium_fastlane = PremiumFastlaneSettings(enabled=True, live_enabled=True)
    s.premium = PremiumSettings(live_execution_enabled=False, live_canary_explicit_ack="")
    assert live_fastlane_armed(s) is False

    # fastlane + premium live but wrong ack
    s.premium = PremiumSettings(live_execution_enabled=True, live_canary_explicit_ack="nope")
    assert live_fastlane_armed(s) is False

    # all three present
    s.premium = PremiumSettings(
        live_execution_enabled=True, live_canary_explicit_ack=LIVE_CANARY_ACK_SENTINEL
    )
    assert live_fastlane_armed(s) is True


def test_env_enables_fastlane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PREMIUM_FASTLANE_ENABLED", "true")
    monkeypatch.setenv("PREMIUM_PAPER_EXECUTION_ENABLED", "true")
    fl = PremiumFastlaneSettings()
    assert fl.enabled is True
