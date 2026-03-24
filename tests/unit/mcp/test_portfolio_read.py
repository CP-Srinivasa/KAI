"""MCP tests for Sprint 40 paper portfolio read-only surfaces."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.agents.mcp_server as mcp_server_module
from app.agents.mcp_server import (
    get_mcp_capabilities,
    get_mcp_tool_inventory,
    get_paper_exposure_summary,
    get_paper_portfolio_snapshot,
    get_paper_positions_summary,
)
from tests.unit.mcp._helpers import _snapshot


@pytest.mark.asyncio
async def test_mcp_inventory_includes_paper_portfolio_tools() -> None:
    inventory = get_mcp_tool_inventory()
    assert "get_paper_portfolio_snapshot" in inventory["canonical_read_tools"]
    assert "get_paper_positions_summary" in inventory["canonical_read_tools"]
    assert "get_paper_exposure_summary" in inventory["canonical_read_tools"]
    assert "get_paper_portfolio_snapshot" not in inventory["guarded_write_tools"]


@pytest.mark.asyncio
async def test_mcp_capabilities_list_paper_portfolio_tools_as_read() -> None:
    payload = json.loads(await get_mcp_capabilities())
    assert "get_paper_portfolio_snapshot" in payload["read_tools"]
    assert "get_paper_positions_summary" in payload["read_tools"]
    assert "get_paper_exposure_summary" in payload["read_tools"]
    assert "get_paper_positions_summary" not in payload["write_tools"]


@pytest.mark.asyncio
async def test_get_paper_positions_summary_returns_read_only_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_snapshot_builder(**kwargs):  # noqa: ANN003
        assert kwargs["provider"] == "coingecko"
        return _snapshot()

    monkeypatch.setattr(
        "app.agents.tools.canonical_read.build_paper_portfolio_snapshot_helper",
        fake_snapshot_builder,
    )

    payload = await get_paper_positions_summary(provider="coingecko")

    assert payload["report_type"] == "paper_positions_summary"
    assert payload["position_count"] == 1
    assert payload["positions"][0]["symbol"] == "BTC/USDT"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_get_paper_exposure_summary_returns_read_only_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_snapshot_builder(**kwargs):  # noqa: ANN003
        return _snapshot()

    monkeypatch.setattr(
        "app.agents.tools.canonical_read.build_paper_portfolio_snapshot_helper",
        fake_snapshot_builder,
    )

    payload = await get_paper_exposure_summary(provider="coingecko")

    assert payload["report_type"] == "paper_exposure_summary"
    assert payload["gross_exposure_usd"] == pytest.approx(12000.0)
    assert payload["available"] is True
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_get_paper_portfolio_snapshot_blocks_path_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agents.tools._helpers as _helpers_module

    resolved = tmp_path.resolve()
    monkeypatch.setattr(mcp_server_module, "_WORKSPACE_ROOT", resolved)
    monkeypatch.setattr(_helpers_module, "WORKSPACE_ROOT", resolved)
    outside = tmp_path.parent / "outside.jsonl"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_paper_portfolio_snapshot(audit_path=str(outside))
