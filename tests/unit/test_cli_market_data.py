"""CLI tests for Sprint 39 read-only market data surfaces."""
from __future__ import annotations

from typer.testing import CliRunner

from app.cli.main import app
from app.market_data.models import MarketDataSnapshot

runner = CliRunner()


def _snapshot(*, available: bool, error: str | None) -> MarketDataSnapshot:
    return MarketDataSnapshot(
        symbol="BTC/USDT",
        provider="coingecko",
        retrieved_at_utc="2026-03-21T12:00:00+00:00",
        source_timestamp_utc=("2026-03-21T11:59:30+00:00" if available else None),
        price=(65000.0 if available else None),
        is_stale=False if available else True,
        freshness_seconds=30.0 if available else None,
        available=available,
        error=error,
    )


def test_market_data_commands_in_research_help() -> None:
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "market-data-quote" in result.output
    assert "market-data-snapshot" in result.output


def test_research_market_data_quote_read_only(monkeypatch) -> None:
    async def fake_snapshot(**kwargs):  # noqa: ANN003
        assert kwargs["symbol"] == "BTC/USDT"
        assert kwargs["provider"] == "coingecko"
        return _snapshot(available=True, error=None)

    monkeypatch.setattr("app.market_data.service.get_market_data_snapshot", fake_snapshot)

    result = runner.invoke(
        app,
        ["research", "market-data-quote", "BTC/USDT", "--provider", "coingecko"],
    )

    assert result.exit_code == 0
    assert "Market Data Quote" in result.output
    assert "symbol=BTC/USDT" in result.output
    assert "provider=coingecko" in result.output
    assert "available=True" in result.output
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output


def test_research_market_data_quote_fail_closed_on_unavailable(monkeypatch) -> None:
    async def fake_snapshot(**kwargs):  # noqa: ANN003
        assert kwargs["symbol"] == "BTC/USDT"
        return _snapshot(available=False, error="timeout")

    monkeypatch.setattr("app.market_data.service.get_market_data_snapshot", fake_snapshot)

    result = runner.invoke(app, ["research", "market-data-quote", "BTC/USDT"])

    assert result.exit_code == 1
    assert "available=False" in result.output
    assert "error=timeout" in result.output


def test_research_market_data_snapshot_prints_json(monkeypatch) -> None:
    async def fake_snapshot(**kwargs):  # noqa: ANN003
        assert kwargs["provider"] == "coingecko"
        return _snapshot(available=True, error=None)

    monkeypatch.setattr("app.market_data.service.get_market_data_snapshot", fake_snapshot)

    result = runner.invoke(app, ["research", "market-data-snapshot", "BTC/USDT"])

    assert result.exit_code == 0
    assert '"report_type": "market_data_snapshot"' in result.output
    assert '"execution_enabled": false' in result.output.lower()
    assert '"write_back_allowed": false' in result.output.lower()
