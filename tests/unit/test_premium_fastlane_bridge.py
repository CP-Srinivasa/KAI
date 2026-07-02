"""Premium-Fastlane bridge integration (Goal 2026-06-05 §6/§9/§21).

Pins the central acceptance criterion: with the fastlane ENABLED, an authentic
premium signal fills in PAPER even though entry_mode=disabled AND
premium_paper_execution_enabled=false AND the premium source is NOT on the
classic allowlist — while every other guard stays intact and the classic
(non-premium) path is unchanged.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import app.execution.envelope_to_paper_bridge as bridge
from app.execution.envelope_to_paper_bridge import run_tick


@pytest.fixture
def tmp_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(bridge, "_ENVELOPE_LOG", tmp_path / "telegram_message_envelope.jsonl")
    monkeypatch.setattr(bridge, "_BRIDGE_LOG", tmp_path / "bridge_pending_orders.jsonl")
    monkeypatch.setattr(bridge, "_PAPER_AUDIT_LOG", tmp_path / "paper_execution_audit.jsonl")
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write(path: Path, env: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(env) + "\n")


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _premium_envelope(env_id: str = "env-prem-1", symbol: str = "BTCUSDT") -> dict[str, Any]:
    return {
        "envelope_id": env_id,
        "stage": "accepted",
        "status": "ok",
        "message_type": "signal",
        "source": "telegram_premium_channel",
        "source_uid": f"telegram:-1001:{env_id}",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "payload": {
            "direction": "long",
            "side": "buy",
            "symbol": symbol,
            "display_symbol": "BTC/USDT",
            "entry_type": "limit",
            "entry_value": 60000.0,
            "stop_loss": 58000.0,
            "targets": [62000.0, 64000.0],
            "leverage": 10,
            "source_uid": f"telegram:-1001:{env_id}",
            "source_chat_id": -1001,
            "source_message_id": 5001,
        },
    }


async def _price(_symbol: str) -> float:
    return 59950.0


def _enable_bridge_classic_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    # premium NOT on the classic allowlist → classic path would skip_source
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_TTL_HOURS", "24")
    # the two dashboard blockers
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.setenv("PREMIUM_PAPER_EXECUTION_ENABLED", "false")


def _arm_fastlane_bypasses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicitly arm the bypasses needed to fill despite the classic block.

    Issue #181 made every bypass fail-closed, so a fill now requires the
    source-allowlist bypass AND the two-flag entry-mode override (per-bypass flag
    + independent override arm). This is the deliberate, operator-readable opt-in.
    """
    monkeypatch.setenv("PREMIUM_FASTLANE_BYPASS_SOURCE_ALLOWLIST", "true")
    monkeypatch.setenv("PREMIUM_FASTLANE_BYPASS_ENTRY_MODE_FOR_PAPER", "true")
    monkeypatch.setenv("PREMIUM_FASTLANE_ALLOW_ENTRY_MODE_DISABLED_OVERRIDE", "true")


@pytest.mark.asyncio
async def test_fastlane_fills_premium_despite_classic_block(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_bridge_classic_blocked(monkeypatch)
    monkeypatch.setenv("PREMIUM_FASTLANE_ENABLED", "true")
    _arm_fastlane_bypasses(monkeypatch)
    _write(tmp_artifacts / "telegram_message_envelope.jsonl", _premium_envelope())

    result = await run_tick(price_provider=_price)

    assert result.filled == 1, result.to_dict()
    assert result.rejected_entry_mode == 0, result.to_dict()
    assert result.fastlane_bypassed_allowlist == 1, result.to_dict()
    assert result.fastlane_bypassed_entry_mode == 1, result.to_dict()
    assert result.fastlane_entry_mode_override_refused == 0, result.to_dict()
    assert result.fastlane_routed == 1, result.to_dict()

    records = _read(tmp_artifacts / "bridge_pending_orders.jsonl")
    stages = [r["stage"] for r in records]
    assert "fastlane_allowlist_bypassed" in stages
    assert "fastlane_entry_mode_bypassed_for_paper" in stages
    assert records[-1]["stage"] == "filled"
    # bypass audit carries live-protected truth
    bypass = next(r for r in records if r["stage"] == "fastlane_entry_mode_bypassed_for_paper")
    assert bypass["live_protected"] is True
    assert bypass["route"] == "paper"


@pytest.mark.asyncio
async def test_fastlane_off_keeps_classic_block(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the fastlane OFF the premium signal is blocked exactly as before."""
    _enable_bridge_classic_blocked(monkeypatch)
    monkeypatch.setenv("PREMIUM_FASTLANE_ENABLED", "false")
    _write(tmp_artifacts / "telegram_message_envelope.jsonl", _premium_envelope())

    result = await run_tick(price_provider=_price)

    assert result.filled == 0, result.to_dict()
    # premium is not allowlisted → classic skips at the allowlist gate
    assert result.skipped_source == 1, result.to_dict()
    assert result.fastlane_routed == 0


@pytest.mark.asyncio
async def test_fastlane_does_not_touch_classic_dashboard_source(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fastlane is premium-scoped: a dashboard signal under entry_mode=disabled
    is still hard-blocked even with the fastlane enabled."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.setenv("PREMIUM_FASTLANE_ENABLED", "true")
    env = _premium_envelope()
    env["source"] = "dashboard"
    _write(tmp_artifacts / "telegram_message_envelope.jsonl", env)

    result = await run_tick(price_provider=_price)

    assert result.filled == 0, result.to_dict()
    assert result.rejected_entry_mode == 1, result.to_dict()
    assert result.fastlane_routed == 0


@pytest.mark.asyncio
async def test_fastlane_still_rejects_incomplete_signal(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hard guard intact: a premium signal missing the stop-loss never fills.

    Premium is put ON the classic allowlist here so the signal passes the
    allowlist gate and reaches the completeness gate — proving the missing-SL
    guard fires (the fastlane router also refuses it, so it cannot bypass).
    """
    _enable_bridge_classic_blocked(monkeypatch)
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "telegram_premium_channel")
    monkeypatch.setenv("PREMIUM_FASTLANE_ENABLED", "true")
    env = _premium_envelope()
    env["payload"]["stop_loss"] = None
    _write(tmp_artifacts / "telegram_message_envelope.jsonl", env)

    result = await run_tick(price_provider=_price)

    assert result.filled == 0, result.to_dict()
    assert result.rejected_incomplete == 1, result.to_dict()


@pytest.mark.asyncio
async def test_fastlane_no_dust_or_zero_position(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The filled paper position carries a positive quantity (no 0-USD fill)."""
    _enable_bridge_classic_blocked(monkeypatch)
    monkeypatch.setenv("PREMIUM_FASTLANE_ENABLED", "true")
    _arm_fastlane_bypasses(monkeypatch)
    _write(tmp_artifacts / "telegram_message_envelope.jsonl", _premium_envelope())

    result = await run_tick(price_provider=_price)
    assert result.filled == 1

    records = _read(tmp_artifacts / "bridge_pending_orders.jsonl")
    filled = next(r for r in records if r["stage"] == "filled")
    assert filled["quantity"] > 0


@pytest.mark.asyncio
async def test_fastlane_enabled_defaults_do_not_bypass_entry_mode(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #181 §4: entry_mode=disabled + PREMIUM_FASTLANE_ENABLED=true with the
    fail-closed defaults (no bypass flags armed) → 0 fills / 0 orders / 0 positions.
    The kill-switch means disabled again, even for the premium paper path.

    Premium is allowlisted here so the signal passes Gate 1 and actually reaches
    the entry-mode gate — isolating the entry_mode fail-closed behaviour.
    """
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "telegram_premium_channel")
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.setenv("PREMIUM_PAPER_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("PREMIUM_FASTLANE_ENABLED", "true")
    # deliberately NO bypass / override flags → fail-closed
    _write(tmp_artifacts / "telegram_message_envelope.jsonl", _premium_envelope())

    result = await run_tick(price_provider=_price)

    assert result.filled == 0, result.to_dict()
    assert result.rejected_entry_mode == 1, result.to_dict()
    assert result.fastlane_bypassed_entry_mode == 0, result.to_dict()
    # the bypass flag itself is off → nothing was even requested to override
    assert result.fastlane_entry_mode_override_refused == 0, result.to_dict()

    records = _read(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert not any(r["stage"] == "filled" for r in records), records
    reject = next(r for r in records if r["stage"] == "rejected_entry_mode")
    assert "ENTRY_MODE_DISABLED" in reject["reason_codes"]


@pytest.mark.asyncio
async def test_fastlane_entry_mode_bypass_without_override_is_refused(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #181 §7: arming the per-bypass flag alone is NOT enough. Under
    entry_mode=disabled the bypass is honoured ONLY together with the independent
    ``allow_entry_mode_disabled_override``. With the override unarmed the signal
    is refused fail-closed and the refusal is recorded with a reason_code.
    """
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "telegram_premium_channel")
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.setenv("PREMIUM_PAPER_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("PREMIUM_FASTLANE_ENABLED", "true")
    # single flag armed, but the independent override is NOT → fail-closed
    monkeypatch.setenv("PREMIUM_FASTLANE_BYPASS_ENTRY_MODE_FOR_PAPER", "true")
    _write(tmp_artifacts / "telegram_message_envelope.jsonl", _premium_envelope())

    result = await run_tick(price_provider=_price)

    assert result.filled == 0, result.to_dict()
    assert result.rejected_entry_mode == 1, result.to_dict()
    assert result.fastlane_bypassed_entry_mode == 0, result.to_dict()
    assert result.fastlane_entry_mode_override_refused == 1, result.to_dict()

    records = _read(tmp_artifacts / "bridge_pending_orders.jsonl")
    refusal = next(r for r in records if r["stage"] == "fastlane_entry_mode_override_refused")
    assert refusal["reason"] == "fastlane_entry_mode_override_not_armed"
    assert "FASTLANE_ENTRY_MODE_OVERRIDE_NOT_ARMED" in refusal["reason_codes"]
    assert not any(r["stage"] == "filled" for r in records), records
