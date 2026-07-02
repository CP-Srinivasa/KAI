"""Premium-Fastlane router decision (Goal 2026-06-05 §5/§21).

Pins the contract: a complete authentic premium signal routes to paper and the
fastlane only ever blocks on hard signal-integrity reasons — never on approval,
classic-allowlist, entry_mode, source-quality, premium-bonus, forward-precision
or priority-tier.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.settings import (
    LIVE_CANARY_ACK_SENTINEL,
    AppSettings,
    PremiumFastlaneSettings,
    PremiumSettings,
)
from app.execution.premium_fastlane import (
    is_authorized_premium_fastlane_source,
    resolve_leverage,
    resolve_notional,
    should_route_premium_fastlane,
)


def _settings(**fl_kwargs: Any) -> AppSettings:
    s = AppSettings()
    fl_kwargs.setdefault("start_date", "")
    # Bypasses are fail-closed by default (Issue #181); the router tests prove
    # they SHOW UP in bypassed_gates when explicitly armed, so arm them here.
    fl_kwargs.setdefault("bypass_entry_mode_for_paper", True)
    fl_kwargs.setdefault("bypass_source_allowlist", True)
    fl_kwargs.setdefault("bypass_manual_approval", True)
    fl_kwargs.setdefault("bypass_risk_quality_gates", True)
    fl_kwargs.setdefault("bypass_source_quality_gates", True)
    fl_kwargs.setdefault("bypass_priority_tier_gates", True)
    fl_kwargs.setdefault("bypass_forward_precision_gates", True)
    s.premium_fastlane = PremiumFastlaneSettings(enabled=True, **fl_kwargs)
    s.premium = PremiumSettings(paper_execution_enabled=True)
    return s


def _premium_envelope(**payload_overrides: Any) -> dict[str, Any]:
    payload = {
        "symbol": "BTCUSDT",
        "display_symbol": "BTC/USDT",
        "direction": "long",
        "side": "buy",
        "entry_type": "limit",
        "entry_value": 60000.0,
        "stop_loss": 58000.0,
        "targets": [62000.0, 64000.0],
        "leverage": 10,
        "source_uid": "telegram:-1001:5001",
        "source_chat_id": -1001,
        "source_message_id": 5001,
    }
    payload.update(payload_overrides)
    return {
        "envelope_id": "env-fl-1",
        "source": "telegram_premium_channel",
        "source_uid": "telegram:-1001:5001",
        "payload": payload,
    }


def test_complete_signal_routes_to_paper() -> None:
    decision = should_route_premium_fastlane(_premium_envelope(), _settings())
    assert decision.is_routable
    assert decision.route == "paper"
    assert decision.reason is None
    assert "entry_mode_for_paper" in decision.bypassed_gates
    assert "source_allowlist" in decision.bypassed_gates
    assert "manual_approval" in decision.bypassed_gates
    # live stays protected (triple-flag not armed)
    assert decision.live_protected is True


def test_entry_mode_disabled_is_not_a_router_block() -> None:
    # the router never looks at entry_mode; a disabled global kill-switch does
    # not change its verdict — the bridge is where the bypass is applied.
    s = _settings()
    s.execution.entry_mode = s.execution.entry_mode.DISABLED
    decision = should_route_premium_fastlane(_premium_envelope(), s)
    assert decision.is_routable


def test_quality_signals_do_not_block() -> None:
    env = _premium_envelope(
        premium_signal_bonus=-7.2,
        forward_precision=0.1,
        priority_tier=2,
        source_quality="weak",
    )
    decision = should_route_premium_fastlane(env, _settings())
    assert decision.is_routable


def test_missing_sl_blocks() -> None:
    env = _premium_envelope(stop_loss=None)
    decision = should_route_premium_fastlane(env, _settings())
    assert not decision.is_routable
    assert decision.reason is not None and "stop_loss" in decision.reason


def test_missing_targets_blocks() -> None:
    env = _premium_envelope(targets=[])
    decision = should_route_premium_fastlane(env, _settings())
    assert not decision.is_routable
    assert "targets" in (decision.reason or "")


def test_non_premium_source_blocks() -> None:
    env = _premium_envelope()
    env["source"] = "dashboard"
    decision = should_route_premium_fastlane(env, _settings())
    assert not decision.is_routable
    assert decision.reason == "not_premium_fastlane_source"


def test_unauthentic_premium_without_identity_blocks() -> None:
    env = _premium_envelope()
    env["source_uid"] = None
    env["payload"]["source_uid"] = None
    env["payload"]["source_chat_id"] = None
    env["payload"]["source_message_id"] = None
    assert is_authorized_premium_fastlane_source(env) is False
    assert not should_route_premium_fastlane(env, _settings()).is_routable


def test_disabled_fastlane_blocks() -> None:
    s = AppSettings()
    s.premium_fastlane = PremiumFastlaneSettings(enabled=False)
    decision = should_route_premium_fastlane(_premium_envelope(), s)
    assert not decision.is_routable
    assert decision.reason == "fastlane_disabled"


def test_expired_window_blocks() -> None:
    start = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    s = _settings(start_date=start, duration_days=30)
    decision = should_route_premium_fastlane(_premium_envelope(), s)
    assert not decision.is_routable
    assert decision.reason == "fastlane_window_expired"


def test_leverage_clamped_to_max() -> None:
    fl = PremiumFastlaneSettings()
    lev, note = resolve_leverage(25.0, fl)
    assert lev == 10.0
    assert note == "leverage_clamped_to_10x"


def test_leverage_defaulted_when_missing() -> None:
    fl = PremiumFastlaneSettings()
    lev, note = resolve_leverage(None, fl)
    assert lev == 10.0
    assert note == "leverage_defaulted_to_10x"


def test_notional_cap_and_quantity() -> None:
    fl = PremiumFastlaneSettings()
    notional, qty, reject = resolve_notional(50.0, fl)
    assert reject is None
    assert notional == 100.0  # clamped default within [10,250]
    assert qty == pytest.approx(2.0)  # 100 / 50
    assert qty > 0


def test_notional_rejects_zero_entry() -> None:
    fl = PremiumFastlaneSettings()
    _n, qty, reject = resolve_notional(0.0, fl)
    assert reject == "entry_price_invalid"
    assert qty == 0.0


def test_live_armed_does_not_force_live_route() -> None:
    s = _settings(live_enabled=True)
    s.premium = PremiumSettings(
        paper_execution_enabled=True,
        live_execution_enabled=True,
        live_canary_explicit_ack=LIVE_CANARY_ACK_SENTINEL,
    )
    decision = should_route_premium_fastlane(_premium_envelope(), s)
    # even fully armed, the routing list drives paper first — never auto-live
    assert decision.route == "paper"
    assert decision.live_protected is False
