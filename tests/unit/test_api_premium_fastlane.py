"""Premium-Fastlane runtime endpoint (Goal 2026-06-05 §18/§23).

The /api/premium-signals/runtime endpoint must stop reporting
``premium_paper_execution_disabled`` / ``entry_mode=disabled`` as the FINAL
verdict once the fastlane is active: the classic reasons stay visible, but the
effective verdict becomes "can open paper positions" with a Fastlane warning,
and live stays protected.
"""

from __future__ import annotations

import pytest

from app.api.routers.premium_signals import runtime_status


@pytest.mark.asyncio
async def test_runtime_blocked_when_fastlane_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.setenv("PREMIUM_PAPER_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("PREMIUM_FASTLANE_ENABLED", "false")

    out = await runtime_status()

    assert out["can_open_paper_positions"] is False
    assert "premium_paper_execution_disabled" in out["blocking_reasons"]
    assert out["warning"] and "blockiert" in out["warning"]
    assert out["premium_fastlane"]["overrides_classic_block"] is False


@pytest.mark.asyncio
async def test_runtime_fastlane_overrides_classic_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.setenv("PREMIUM_PAPER_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("PREMIUM_FASTLANE_ENABLED", "true")

    out = await runtime_status()

    # classic reasons still TRUE + visible …
    assert "premium_paper_execution_disabled" in out["blocking_reasons"]
    assert "entry_mode=disabled" in out["blocking_reasons"]
    # … but no longer the final verdict
    assert out["can_open_paper_positions"] is True
    assert out["classic_can_open_paper_positions"] is False
    fl = out["premium_fastlane"]
    assert fl["enabled"] is True
    assert fl["active"] is True
    assert fl["overrides_classic_block"] is True
    assert fl["route"] == "paper"
    assert fl["live_protected"] is True
    # operator-facing copy frames it correctly
    assert out["warning"]
    assert "Fastlane" in out["warning"]
    assert "Live" in out["warning"]


@pytest.mark.asyncio
async def test_runtime_reports_live_protected_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PREMIUM_FASTLANE_ENABLED", "true")
    out = await runtime_status()
    assert out["premium_fastlane"]["live_armed"] is False
    assert out["premium_fastlane"]["live_protected"] is True
