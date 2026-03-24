"""MCP tests for Sprint 36: get_decision_journal_summary, append_decision_instance, get_loop_cycle_summary."""  # noqa: E501

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.agents.mcp_server as mcp_server_module
from app.agents.mcp_server import (
    append_decision_instance,
    get_decision_journal_summary,
    get_loop_cycle_summary,
    get_mcp_tool_inventory,
    mcp,
)


def _patch_workspace_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    import app.agents.tools._helpers as _helpers_module

    resolved = root.resolve()
    monkeypatch.setattr(mcp_server_module, "_WORKSPACE_ROOT", resolved)
    monkeypatch.setattr(_helpers_module, "WORKSPACE_ROOT", resolved)


# ── get_decision_journal_summary ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_decision_journal_summary_empty_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    result = await get_decision_journal_summary()
    assert result["report_type"] == "decision_journal_summary"
    assert result["total_count"] == 0
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_get_decision_journal_summary_counts_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    await append_decision_instance(
        symbol="BTC/USDT",
        thesis="BTC breaking out on ETF inflow data confirmed by volume.",
        mode="research",
    )
    await append_decision_instance(
        symbol="ETH/USDT",
        thesis="ETH staking yield increase attracting institutional demand.",
        mode="paper",
    )
    result = await get_decision_journal_summary()
    assert result["total_count"] == 2
    assert "BTC/USDT" in result["symbols"]
    assert "ETH/USDT" in result["symbols"]
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_get_decision_journal_summary_blocks_path_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    with pytest.raises((ValueError, FileNotFoundError)):
        await get_decision_journal_summary(journal_path="/etc/shadow.jsonl")


@pytest.mark.asyncio
async def test_get_decision_journal_summary_in_read_tools() -> None:
    inventory = get_mcp_tool_inventory()
    assert "get_decision_journal_summary" in inventory["canonical_read_tools"]
    assert "get_decision_journal_summary" not in inventory["guarded_write_tools"]


# ── append_decision_instance ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_append_decision_instance_returns_decision_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    result = await append_decision_instance(
        symbol="BTC/USDT",
        thesis="BTC ETF approval is a major macro catalyst for price discovery.",
    )
    assert result["status"] == "decision_appended"
    assert "decision_id" in result
    assert len(result["decision_id"]) == 16


@pytest.mark.asyncio
async def test_append_decision_instance_execution_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    result = await append_decision_instance(
        symbol="ETH/USDT",
        thesis="ETH upgrade roadmap reduces inflation and increases staking.",
    )
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_append_decision_instance_writes_jsonl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    await append_decision_instance(
        symbol="SOL/USDT",
        thesis="SOL DeFi TVL all-time high signals institutional confidence.",
    )
    journal_path = tmp_path / "artifacts" / "decision_journal.jsonl"
    assert journal_path.exists()
    lines = journal_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["symbol"] == "SOL/USDT"
    assert "report_type" not in record
    assert isinstance(record["entry_logic"], dict)
    assert record["approval_state"] == "audit_only"


@pytest.mark.asyncio
async def test_append_decision_instance_is_additive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    for i in range(3):
        await append_decision_instance(
            symbol="BTC/USDT",
            thesis=f"Thesis {i}: BTC showing strength above key resistance level.",
        )
    journal_path = tmp_path / "artifacts" / "decision_journal.jsonl"
    lines = journal_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


@pytest.mark.asyncio
async def test_append_decision_instance_blocks_path_outside_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    with pytest.raises((ValueError, FileNotFoundError)):
        await append_decision_instance(
            symbol="BTC/USDT",
            thesis="BTC breakout confirmed with high volume and macro tailwinds.",
            journal_output_path="/tmp/evil_decision.jsonl",
        )


@pytest.mark.asyncio
async def test_append_decision_instance_in_guarded_write_tools() -> None:
    inventory = get_mcp_tool_inventory()
    assert "append_decision_instance" in inventory["guarded_write_tools"]
    assert "append_decision_instance" not in inventory["canonical_read_tools"]


@pytest.mark.asyncio
async def test_append_decision_instance_round_trip_via_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """append then summarize — round-trip contract."""
    _patch_workspace_root(monkeypatch, tmp_path)
    r1 = await append_decision_instance(
        symbol="BTC/USDT",
        thesis="BTC macro support confirmed with on-chain accumulation data.",
        mode="research",
    )
    r2 = await append_decision_instance(
        symbol="BTC/USDT",
        thesis="BTC miners are holding — supply squeeze is incoming signal.",
        mode="backtest",
    )
    summary = await get_decision_journal_summary()
    assert summary["total_count"] == 2
    assert r1["decision_id"] != r2["decision_id"]
    assert summary["by_mode"].get("research", 0) == 1
    assert summary["by_mode"].get("backtest", 0) == 1


@pytest.mark.asyncio
async def test_get_decision_journal_summary_fails_closed_on_malformed_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    journal_path = tmp_path / "artifacts" / "decision_journal.jsonl"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text('{"decision_id":"bad"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="line 1"):
        await get_decision_journal_summary()


# ── get_loop_cycle_summary ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_loop_cycle_summary_missing_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    result = await get_loop_cycle_summary()
    assert result["total_cycles"] == 0
    assert result["status_counts"] == {}
    assert result["recent_cycles"] == []
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_get_loop_cycle_summary_reads_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    audit_path = tmp_path / "artifacts" / "trading_loop_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"cycle_id": f"cyc_{i}", "status": "completed", "symbol": "BTC/USDT"} for i in range(5)
    ]
    audit_path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    result = await get_loop_cycle_summary()
    assert result["total_cycles"] == 5
    assert result["status_counts"] == {"completed": 5}
    assert len(result["recent_cycles"]) == 5


@pytest.mark.asyncio
async def test_get_loop_cycle_summary_last_n_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    audit_path = tmp_path / "artifacts" / "trading_loop_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    records = [{"cycle_id": f"c{i}", "status": "completed"} for i in range(30)]
    audit_path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    result = await get_loop_cycle_summary(last_n=5)
    assert result["total_cycles"] == 30
    assert len(result["recent_cycles"]) == 5
    assert result["recent_cycles"][-1]["cycle_id"] == "c29"


@pytest.mark.asyncio
async def test_get_loop_cycle_summary_mixed_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    audit_path = tmp_path / "artifacts" / "trading_loop_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    statuses = ["completed", "completed", "no_signal", "risk_rejected", "no_signal"]
    records = [{"cycle_id": f"c{i}", "status": s} for i, s in enumerate(statuses)]
    audit_path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    result = await get_loop_cycle_summary()
    assert result["status_counts"]["completed"] == 2
    assert result["status_counts"]["no_signal"] == 2
    assert result["status_counts"]["risk_rejected"] == 1
    assert result["execution_enabled"] is False


@pytest.mark.asyncio
async def test_get_loop_cycle_summary_blocks_path_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    with pytest.raises((ValueError, FileNotFoundError)):
        await get_loop_cycle_summary(audit_path="/etc/shadow.jsonl")


@pytest.mark.asyncio
async def test_get_loop_cycle_summary_in_read_tools() -> None:
    inventory = get_mcp_tool_inventory()
    assert "get_recent_trading_cycles" in inventory["canonical_read_tools"]
    assert "get_loop_cycle_summary" in inventory["aliases"]
    assert inventory["aliases"]["get_loop_cycle_summary"]["canonical_tool"] == (
        "get_recent_trading_cycles"
    )
    assert "get_loop_cycle_summary" not in inventory["guarded_write_tools"]


# ── inventory consistency ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_tool_inventory_includes_sprint36_tools() -> None:
    inventory = get_mcp_tool_inventory()
    assert "get_decision_journal_summary" in inventory["canonical_read_tools"]
    assert "get_recent_trading_cycles" in inventory["canonical_read_tools"]
    assert "get_loop_cycle_summary" in inventory["aliases"]
    assert "append_decision_instance" in inventory["guarded_write_tools"]


@pytest.mark.asyncio
async def test_mcp_registered_tools_include_sprint36() -> None:
    """All Sprint 36 tools appear in FastMCP registered tool list."""
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "get_decision_journal_summary" in names
    assert "get_loop_cycle_summary" in names
    assert "append_decision_instance" in names
