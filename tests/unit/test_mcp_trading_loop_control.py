"""MCP tests for Sprint 41 trading-loop control-plane surfaces."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.agents.mcp_server as mcp_server_module
from app.agents.mcp_server import (
    get_loop_cycle_summary,
    get_mcp_capabilities,
    get_mcp_tool_inventory,
    get_recent_trading_cycles,
    get_trading_loop_status,
    run_trading_loop_once,
)


def _patch_workspace_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(mcp_server_module, "_WORKSPACE_ROOT", root.resolve())


@pytest.mark.asyncio
async def test_mcp_inventory_contains_trading_loop_control_surfaces() -> None:
    inventory = get_mcp_tool_inventory()

    assert "get_trading_loop_status" in inventory["canonical_read_tools"]
    assert "get_recent_trading_cycles" in inventory["canonical_read_tools"]
    assert "run_trading_loop_once" in inventory["guarded_write_tools"]
    assert (
        inventory["aliases"]["get_loop_cycle_summary"]["canonical_tool"]
        == "get_recent_trading_cycles"
    )
    assert "get_loop_cycle_summary" not in inventory["canonical_read_tools"]


@pytest.mark.asyncio
async def test_mcp_capabilities_expose_trading_loop_control_tools() -> None:
    payload = json.loads(await get_mcp_capabilities())

    assert "get_trading_loop_status" in payload["read_tools"]
    assert "get_recent_trading_cycles" in payload["read_tools"]
    assert "run_trading_loop_once" in payload["write_tools"]
    assert payload["aliases"]["get_loop_cycle_summary"]["canonical_tool"] == (
        "get_recent_trading_cycles"
    )


@pytest.mark.asyncio
async def test_get_trading_loop_status_reports_read_only_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)

    payload = await get_trading_loop_status(
        audit_path="artifacts/trading_loop_audit.jsonl",
        mode="paper",
    )

    assert payload["report_type"] == "trading_loop_status_summary"
    assert payload["run_once_allowed"] is True
    assert payload["total_cycles"] == 0
    assert payload["auto_loop_enabled"] is False
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_get_recent_trading_cycles_alias_matches_canonical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)

    await run_trading_loop_once(
        symbol="BTC/USDT",
        mode="paper",
        provider="mock",
        analysis_profile="conservative",
        loop_audit_path="artifacts/trading_loop_audit.jsonl",
        execution_audit_path="artifacts/paper_execution_audit.jsonl",
    )

    canonical = await get_recent_trading_cycles(
        audit_path="artifacts/trading_loop_audit.jsonl",
        last_n=10,
    )
    alias = await get_loop_cycle_summary(
        audit_path="artifacts/trading_loop_audit.jsonl",
        last_n=10,
    )

    assert canonical == alias
    assert canonical["total_cycles"] == 1
    assert canonical["status_counts"]["no_signal"] == 1
    assert canonical["auto_loop_enabled"] is False


@pytest.mark.asyncio
async def test_run_trading_loop_once_writes_guarded_audits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)

    payload = await run_trading_loop_once(
        symbol="BTC/USDT",
        mode="paper",
        provider="mock",
        analysis_profile="conservative",
        loop_audit_path="artifacts/trading_loop_audit.jsonl",
        execution_audit_path="artifacts/paper_execution_audit.jsonl",
    )

    assert payload["status"] == "cycle_completed"
    assert payload["cycle"]["status"] == "no_signal"
    assert payload["cycle"]["order_created"] is False
    assert payload["auto_loop_enabled"] is False
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False

    loop_audit = tmp_path / "artifacts" / "trading_loop_audit.jsonl"
    assert loop_audit.exists()
    lines = [line for line in loop_audit.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1

    mcp_write_audit = tmp_path / "artifacts" / "mcp_write_audit.jsonl"
    assert mcp_write_audit.exists()
    write_rows = [
        json.loads(line)
        for line in mcp_write_audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(row["tool"] == "run_trading_loop_once" for row in write_rows)


@pytest.mark.asyncio
async def test_run_trading_loop_once_rejects_live_mode_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="allowed: paper, shadow"):
        await run_trading_loop_once(
            symbol="BTC/USDT",
            mode="live",
            provider="mock",
            loop_audit_path="artifacts/trading_loop_audit.jsonl",
            execution_audit_path="artifacts/paper_execution_audit.jsonl",
        )
