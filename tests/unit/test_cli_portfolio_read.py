"""CLI tests for Sprint 40 paper portfolio read-only surfaces."""

from __future__ import annotations

from typer.testing import CliRunner

from app.cli.main import app
from app.execution.portfolio_read import ExposureSummary, PortfolioSnapshot, PositionSummary

runner = CliRunner()


def _snapshot(*, available: bool, error: str | None) -> PortfolioSnapshot:
    positions = (
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
    )
    exposure = ExposureSummary(
        priced_position_count=1,
        stale_position_count=0,
        unavailable_price_count=0,
        gross_exposure_usd=12000.0,
        net_exposure_usd=12000.0,
        largest_position_symbol="BTC/USDT",
        largest_position_weight_pct=100.0,
        mark_to_market_status="ok",
    )
    if not available:
        exposure = ExposureSummary(
            priced_position_count=0,
            stale_position_count=0,
            unavailable_price_count=1,
            gross_exposure_usd=0.0,
            net_exposure_usd=0.0,
            largest_position_symbol=None,
            largest_position_weight_pct=None,
            mark_to_market_status="degraded",
        )
    return PortfolioSnapshot(
        generated_at_utc="2026-03-21T12:00:00+00:00",
        source="paper_execution_audit_replay",
        audit_path="artifacts/paper_execution_audit.jsonl",
        cash_usd=5800.0,
        realized_pnl_usd=0.0,
        total_market_value_usd=(12000.0 if available else 0.0),
        total_equity_usd=(17800.0 if available else 5800.0),
        position_count=(1 if available else 1),
        positions=positions,
        exposure_summary=exposure,
        available=available,
        error=error,
    )


def test_paper_portfolio_commands_appear_in_research_help() -> None:
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "paper-portfolio-snapshot" in result.output
    assert "paper-positions-summary" in result.output
    assert "paper-exposure-summary" in result.output


def test_research_paper_positions_summary_prints_read_only(monkeypatch) -> None:
    async def fake_snapshot(**kwargs):  # noqa: ANN003
        assert kwargs["provider"] == "coingecko"
        return _snapshot(available=True, error=None)

    monkeypatch.setattr("app.execution.portfolio_read.build_portfolio_snapshot", fake_snapshot)

    result = runner.invoke(app, ["research", "paper-positions-summary"])

    assert result.exit_code == 0
    assert "Paper Positions Summary" in result.output
    assert "position_count=1" in result.output
    assert "symbol=BTC/USDT" in result.output
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output


def test_research_paper_exposure_summary_prints_read_only(monkeypatch) -> None:
    async def fake_snapshot(**kwargs):  # noqa: ANN003
        assert kwargs["provider"] == "coingecko"
        return _snapshot(available=True, error=None)

    monkeypatch.setattr("app.execution.portfolio_read.build_portfolio_snapshot", fake_snapshot)

    result = runner.invoke(app, ["research", "paper-exposure-summary"])

    assert result.exit_code == 0
    assert "Paper Exposure Summary" in result.output
    assert "gross_exposure_usd=12000.0" in result.output
    assert "available=True" in result.output
    assert "execution_enabled=False" in result.output


def test_research_paper_portfolio_snapshot_fail_closed(monkeypatch) -> None:
    async def fake_snapshot(**kwargs):  # noqa: ANN003
        return _snapshot(
            available=False,
            error="market_data_unavailable_for_open_positions",
        )

    monkeypatch.setattr("app.execution.portfolio_read.build_portfolio_snapshot", fake_snapshot)

    result = runner.invoke(app, ["research", "paper-portfolio-snapshot"])

    assert result.exit_code == 1
    assert '"report_type": "paper_portfolio_snapshot"' in result.output
    assert '"available": false' in result.output.lower()
    assert '"execution_enabled": false' in result.output.lower()
