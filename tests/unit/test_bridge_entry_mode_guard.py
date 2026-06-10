"""Safety-contract invariant: EXECUTION_ENTRY_MODE is a GLOBAL kill-switch.

Background (2026-06-02): the autonomous loop honoured ``entry_mode`` but the
premium/promoted ``envelope_to_paper_bridge`` did not check it at all — so
``disabled`` was only a *partial* kill-switch (autonomous blocked, premium
through). These tests pin the closed behaviour:

  disabled  -> risk gate still EVALUATED (audit lives), but NO paper fill,
               NO position, terminal stage=rejected_entry_mode +
               reason_codes=[ENTRY_MODE_DISABLED].
  paper     -> unchanged: premium signal still fills (backward-compatible).

The bridge handles risk-INCREASING premium entries only; exits/risk-reductions
are managed elsewhere and are never routed through this path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import app.execution.envelope_to_paper_bridge as bridge
from app.core.enums import EntryMode
from app.execution.envelope_to_paper_bridge import run_tick

# --- enum-level kill-switch truth (source-agnostic) ------------------------- #


def test_entry_mode_disabled_blocks_risk_increasing_entry() -> None:
    assert EntryMode.DISABLED.allows_risk_increasing_entry is False
    # everything above DISABLED permits entries (cadence is a separate gate)
    for mode in (EntryMode.PAPER, EntryMode.PROBE, EntryMode.LIVE_LIMITED, EntryMode.LIVE_NORMAL):
        assert mode.allows_risk_increasing_entry is True
    # mirrors the loop-specific alias exactly — no partial kill-switch
    for mode in EntryMode:
        assert mode.allows_risk_increasing_entry == mode.allows_autonomous_loop_entry


# --- bridge integration harness (mirrors test_envelope_to_paper_bridge) ----- #


@pytest.fixture
def tmp_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(bridge, "_ENVELOPE_LOG", tmp_path / "telegram_message_envelope.jsonl")
    monkeypatch.setattr(bridge, "_BRIDGE_LOG", tmp_path / "bridge_pending_orders.jsonl")
    monkeypatch.setattr(bridge, "_PAPER_AUDIT_LOG", tmp_path / "paper_execution_audit.jsonl")
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_envelope(path: Path, envelope: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(envelope) + "\n")


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _accepted_envelope() -> dict[str, Any]:
    return {
        "envelope_id": "env-001",
        "stage": "accepted",
        "status": "ok",
        "message_type": "signal",
        "source": "dashboard",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "payload": {
            "direction": "long",
            "side": "buy",
            "symbol": "BTCUSDT",
            "display_symbol": "BTC/USDT",
            "entry_type": "limit",
            "entry_value": 60000.0,
            "stop_loss": 58000.0,
            "targets": [62000.0, 64000.0],
            "leverage": 5,
            "margin_pct": 5.0,
        },
    }


def _enable_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_TTL_HOURS", "24")


@pytest.mark.asyncio
async def test_disabled_blocks_premium_entry_but_keeps_diagnostics(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """disabled: no fill, no position — but the gate is still evaluated/recorded."""
    _enable_bridge(monkeypatch)
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.setenv("RISK_GATES_MODE", "audit")
    _write_envelope(tmp_artifacts / "telegram_message_envelope.jsonl", _accepted_envelope())

    with patch.object(bridge, "_fetch_price", new=AsyncMock(return_value=59950.0)):
        result = await run_tick()

    assert result.filled == 0, result.to_dict()
    assert result.rejected_entry_mode == 1, result.to_dict()

    records = _read_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    terminal = records[-1]
    assert terminal["stage"] == "rejected_entry_mode"
    assert terminal["reason"] == "entry_mode_disabled"
    assert terminal["reason_codes"] == ["ENTRY_MODE_DISABLED"]
    assert terminal["entry_mode"] == "disabled"
    # report-vs-act: risk-gate diagnostics are present even though we refused
    assert "risk_gate_would_reject" in terminal
    assert "signal_geometry" in terminal

    # hard invariant: NO paper fill / position created
    paper_audit = _read_records(tmp_artifacts / "artifacts" / "paper_execution_audit.jsonl")
    assert not any(r.get("event_type") == "order_filled" for r in paper_audit)


@pytest.mark.asyncio
async def test_paper_mode_still_fills_premium_entry(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """paper: behaviour unchanged — premium signal still fills (backward-compat)."""
    _enable_bridge(monkeypatch)
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "paper")
    _write_envelope(tmp_artifacts / "telegram_message_envelope.jsonl", _accepted_envelope())

    with patch.object(bridge, "_fetch_price", new=AsyncMock(return_value=59950.0)):
        result = await run_tick()

    assert result.filled == 1, result.to_dict()
    assert result.rejected_entry_mode == 0
    records = _read_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert records[-1]["stage"] == "filled"


# --- Pfad-3 decoupling: classic premium paper while entry_mode=disabled ------ #

_PREMIUM_SOURCE = "telegram_premium_channel_approved"
_ACK = "I_UNDERSTAND_PREMIUM_PAPER_WHILE_DISABLED"


def _premium_envelope() -> dict[str, Any]:
    env = _accepted_envelope()
    env["source"] = _PREMIUM_SOURCE
    return env


def _enable_premium_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", _PREMIUM_SOURCE)
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_TTL_HOURS", "24")
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.setenv("PREMIUM_PAPER_EXECUTION_ENABLED", "true")


@pytest.mark.asyncio
async def test_pfad3_fills_premium_paper_while_entry_disabled_when_fully_armed(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two-arm override fully set → classic premium signal opens PAPER even
    though entry_mode=disabled; the autonomous loop is untouched (kill-switch
    value is unchanged)."""
    _enable_premium_bridge(monkeypatch)
    monkeypatch.setenv("PREMIUM_ALLOW_PAPER_WHILE_ENTRY_DISABLED", "true")
    monkeypatch.setenv("PREMIUM_ENTRY_DISABLED_OVERRIDE_ACK", _ACK)
    _write_envelope(tmp_artifacts / "telegram_message_envelope.jsonl", _premium_envelope())

    with patch.object(bridge, "_fetch_price", new=AsyncMock(return_value=59950.0)):
        result = await run_tick()

    assert result.filled == 1, result.to_dict()
    assert result.premium_paper_entry_disabled_bypassed == 1, result.to_dict()
    assert result.rejected_entry_mode == 0, result.to_dict()
    records = _read_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert any(r["stage"] == "premium_paper_entry_disabled_bypassed" for r in records)
    assert records[-1]["stage"] == "filled"


@pytest.mark.asyncio
async def test_pfad3_failclosed_without_ack(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opt-in bool set but ack sentinel missing → kill-switch HOLDS (fail-closed):
    no fill, refusal recorded, terminal rejected_entry_mode."""
    _enable_premium_bridge(monkeypatch)
    monkeypatch.setenv("PREMIUM_ALLOW_PAPER_WHILE_ENTRY_DISABLED", "true")
    # no PREMIUM_ENTRY_DISABLED_OVERRIDE_ACK
    _write_envelope(tmp_artifacts / "telegram_message_envelope.jsonl", _premium_envelope())

    with patch.object(bridge, "_fetch_price", new=AsyncMock(return_value=59950.0)):
        result = await run_tick()

    assert result.filled == 0, result.to_dict()
    assert result.premium_paper_entry_disabled_bypassed == 0, result.to_dict()
    assert result.premium_paper_entry_disabled_refused == 1, result.to_dict()
    assert result.rejected_entry_mode == 1, result.to_dict()
    records = _read_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert any(r["stage"] == "premium_paper_entry_disabled_override_refused" for r in records)
    assert records[-1]["stage"] == "rejected_entry_mode"


@pytest.mark.asyncio
async def test_pfad3_single_bool_insufficient(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ack set but opt-in bool off → still fail-closed (a single flag can never
    neuter the kill-switch)."""
    _enable_premium_bridge(monkeypatch)
    monkeypatch.setenv("PREMIUM_ENTRY_DISABLED_OVERRIDE_ACK", _ACK)
    # PREMIUM_ALLOW_PAPER_WHILE_ENTRY_DISABLED stays default false
    _write_envelope(tmp_artifacts / "telegram_message_envelope.jsonl", _premium_envelope())

    with patch.object(bridge, "_fetch_price", new=AsyncMock(return_value=59950.0)):
        result = await run_tick()

    assert result.filled == 0, result.to_dict()
    assert result.rejected_entry_mode == 1, result.to_dict()


@pytest.mark.asyncio
async def test_pfad3_does_not_leak_to_non_premium_source(
    tmp_artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critical invariant: the premium-paper decoupling is is_premium-gated. A
    NON-premium (e.g. dashboard/autonomous-style) source stays blocked by
    entry_mode=disabled even with the premium override fully armed — so the
    autonomous loop's kill-switch is never weakened by this flag."""
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_SOURCE_ALLOWLIST", "dashboard")
    monkeypatch.setenv("EXECUTION_OPERATOR_SIGNAL_TTL_HOURS", "24")
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.setenv("PREMIUM_PAPER_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PREMIUM_ALLOW_PAPER_WHILE_ENTRY_DISABLED", "true")
    monkeypatch.setenv("PREMIUM_ENTRY_DISABLED_OVERRIDE_ACK", _ACK)
    _write_envelope(tmp_artifacts / "telegram_message_envelope.jsonl", _accepted_envelope())

    with patch.object(bridge, "_fetch_price", new=AsyncMock(return_value=59950.0)):
        result = await run_tick()

    assert result.filled == 0, result.to_dict()
    assert result.premium_paper_entry_disabled_bypassed == 0, result.to_dict()
    assert result.rejected_entry_mode == 1, result.to_dict()
    records = _read_records(tmp_artifacts / "bridge_pending_orders.jsonl")
    assert records[-1]["stage"] == "rejected_entry_mode"
