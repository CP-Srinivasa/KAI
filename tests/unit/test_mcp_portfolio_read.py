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
from app.execution.portfolio_read import ExposureSummary, PortfolioSnapshot, PositionSummary


def _snapshot(*, available: bool = True, error: str | None = None) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        generated_at_utc="2026-03-21T12:00:00+00:00",
        source="paper_execution_audit_replay",
        audit_path="artifacts/paper_execution_audit.jsonl",
        cash_usd=5800.0,
        realized_pnl_usd=0.0,
        total_market_value_usd=12000.0,
        total_equity_usd=17800.0,
        position_count=1,
        positions=(
            PositionSummary(
                symbol="BTC/USDT",
                quantity=0.2,
                avg_entry_price=50000.0,
                stop_loss=48000.0,
                take_profit=70000.0,
                market_price=60000.0,
                market_value_usd=12000.0,
                unrealized_pnl_usd=2000.0,
                provider="coingecko",
                market_data_retrieved_at_utc="2026-03-21T12:00:00+00:00",
                market_data_source_timestamp_utc="2026-03-21T11:59:00+00:00",
                market_data_is_stale=False,
                market_data_freshness_seconds=60.0,
                market_data_available=True,
                market_data_error=None,
            ),
        ),
        exposure_summary=ExposureSummary(
            priced_position_count=1,
            stale_position_count=0,
            unavailable_price_count=0,
            gross_exposure_usd=12000.0,
            net_exposure_usd=12000.0,
            largest_position_symbol="BTC/USDT",
            largest_position_weight_pct=100.0,
            mark_to_market_status="ok",
        ),
        available=available,
        error=error,
    )


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
        "app.agents.mcp_server._build_paper_portfolio_snapshot",
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
        "app.agents.mcp_server._build_paper_portfolio_snapshot",
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
    monkeypatch.setattr(mcp_server_module, "_WORKSPACE_ROOT", tmp_path.resolve())
    outside = tmp_path.parent / "outside.jsonl"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_paper_portfolio_snapshot(audit_path=str(outside))
