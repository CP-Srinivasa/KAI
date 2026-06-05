"""Premium-Fastlane LIVE protection (Goal 2026-06-05 §4/§22).

Live must NEVER auto-arm. It is only armed when ALL THREE hold:
  premium_fastlane.live_enabled
  premium.live_execution_enabled
  premium.live_canary_explicit_ack == LIVE_CANARY_ACK_SENTINEL

And even when armed, the paper bridge never submits a live order — these tests
prove no fill record ever carries a live venue.
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import app.execution.envelope_to_paper_bridge as bridge
from app.core.settings import (
    LIVE_CANARY_ACK_SENTINEL,
    AppSettings,
    PremiumFastlaneSettings,
    PremiumSettings,
)
from app.execution.envelope_to_paper_bridge import run_tick
from app.execution.premium_fastlane import live_fastlane_armed


def _armed(fastlane_live: bool, premium_live: bool, ack: str) -> bool:
    s = AppSettings()
    s.premium_fastlane = PremiumFastlaneSettings(enabled=True, live_enabled=fastlane_live)
    s.premium = PremiumSettings(live_execution_enabled=premium_live, live_canary_explicit_ack=ack)
    return live_fastlane_armed(s)


def test_live_requires_all_three_flags() -> None:
    # Every combination short of all-three must NOT arm live.
    for fl_live, prem_live, ack_ok in itertools.product([True, False], repeat=3):
        ack = LIVE_CANARY_ACK_SENTINEL if ack_ok else ""
        expected = fl_live and prem_live and ack_ok
        assert _armed(fl_live, prem_live, ack) is expected, (fl_live, prem_live, ack_ok)


def test_two_of_three_flags_still_protected() -> None:
    assert _armed(True, True, "") is False
    assert _armed(True, False, LIVE_CANARY_ACK_SENTINEL) is False
    assert _armed(False, True, LIVE_CANARY_ACK_SENTINEL) is False


# --- bridge harness: fastlane fill never produces a live order -------------- #


@pytest.fixture
def tmp_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(bridge, "_ENVELOPE_LOG", tmp_path / "telegram_message_envelope.jsonl")
    monkeypatch.setattr(bridge, "_BRIDGE_LOG", tmp_path / "bridge_pending_orders.jsonl")
    monkeypatch.setattr(bridge, "_PAPER_AUDIT_LOG", tmp_path / "paper_execution_audit.jsonl")
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


async def _price(_symbol: str) -> float:
    return 59950.0


def _premium_envelope() -> dict[str, Any]:
    return {
        "envelope_id": "env-live-1",
        "stage": "accepted",
        "status": "ok",
        "message_type": "signal",
        "source": "telegram_premium_channel",
        "source_uid": "telegram:-1001:9001",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "payload": {
            "direction": "long",
            "side": "buy",
            "symbol": "BTCUSDT",
            "display_symbol": "BTC/USDT",
            "entry_type": "limit",
            "entry_value": 60000.0,
            "stop_loss": 58000.0,
            "targets": [62000.0],
            "leverage": 10,
            "source_uid": "telegram:-1001:9001",
            "source_chat_id": -1001,
            "source_message_id": 9001,
        },
    }


@pytest.mark.asyncio
async def test_fastlane_fill_is_paper_only_even_with_live_flags(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even if a live triple-flag were set, the paper bridge stays paper: the
    execution venue is paper and no live order is ever written."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.setenv("PREMIUM_FASTLANE_ENABLED", "true")
    # NB: execution venue stays paper (default). The premium-fastlane live flags
    # only gate a *future* live router; this bridge is paper by construction.
    monkeypatch.setenv("PREMIUM_FASTLANE_LIVE_ENABLED", "true")
    _write = tmp_artifacts / "telegram_message_envelope.jsonl"
    _write.parent.mkdir(parents=True, exist_ok=True)
    _write.write_text(json.dumps(_premium_envelope()) + "\n")

    result = await run_tick(price_provider=_price)
    assert result.filled == 1, result.to_dict()

    # the paper audit must only contain paper events — no live venue marker.
    paper = tmp_artifacts / "artifacts" / "paper_execution_audit.jsonl"
    text = paper.read_text() if paper.exists() else ""
    assert '"venue": "live"' not in text
    assert '"live": true' not in text
