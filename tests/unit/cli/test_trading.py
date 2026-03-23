"""Tests for the trading CLI command group (app/cli/commands/trading.py).

These tests verify the new `trading-bot trading <cmd>` surface introduced
by V-1 CLI split. The existing `trading-bot research <cmd>` surface is
tested in tests/unit/test_cli_market_data.py, test_cli_portfolio_read.py,
test_cli_trading_loop_control.py, and test_cli_decision_journal.py.
"""
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


def test_trading_group_is_registered() -> None:
    """trading command group is registered in the root app."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "trading" in result.output


def test_trading_group_help() -> None:
    """trading --help shows expected subcommands."""
    result = runner.invoke(app, ["trading", "--help"])
    assert result.exit_code == 0
    assert "market-data-quote" in result.output
    assert "loop-status" in result.output
    assert "run-once" in result.output


def test_trading_market_data_quote_read_only(monkeypatch) -> None:
    """trading market-data-quote prints snapshot and exits 0 when available."""
    snap = _snapshot(available=True, error=None)

    async def fake_snapshot(**_):
        return snap

    from app.market_data import service as mds_mod

    monkeypatch.setattr(mds_mod, "get_market_data_snapshot", fake_snapshot)

    result = runner.invoke(app, ["trading", "market-data-quote", "BTC/USDT"])
    assert result.exit_code == 0
    assert "price=65000.0" in result.output
    assert "write_back_allowed=False" in result.output


def test_trading_market_data_quote_fail_closed(monkeypatch) -> None:
    """trading market-data-quote exits 1 when snapshot unavailable."""
    from app.market_data import service as mds_mod

    snap = _snapshot(available=False, error="connection_error")

    async def fake_snapshot(**_):
        return snap

    monkeypatch.setattr(mds_mod, "get_market_data_snapshot", fake_snapshot)

    result = runner.invoke(app, ["trading", "market-data-quote", "BTC/USDT"])
    assert result.exit_code == 1


def test_trading_loop_status_read_only(monkeypatch) -> None:
    """trading loop-status prints status and exits 0."""
    from app.orchestrator import trading_loop as tl_mod
    from app.orchestrator.trading_loop import LoopStatusSummary

    summary = LoopStatusSummary(
        mode="paper",
        audit_path="artifacts/trading_loop_audit.jsonl",
        run_once_allowed=True,
        run_once_block_reason=None,
        total_cycles=0,
        last_cycle_id=None,
        last_cycle_status=None,
        last_cycle_symbol=None,
        last_cycle_completed_at=None,
    )

    monkeypatch.setattr(tl_mod, "build_loop_status_summary", lambda **_: summary)

    result = runner.invoke(app, ["trading", "loop-status"])
    assert result.exit_code == 0
    assert "run_once_allowed=True" in result.output
    assert "execution_enabled=False" in result.output


def test_trading_recent_cycles_read_only(monkeypatch) -> None:
    """trading recent-cycles prints cycle table and exits 0."""
    from app.orchestrator import trading_loop as tl_mod
    from app.orchestrator.trading_loop import RecentCyclesSummary

    summary = RecentCyclesSummary(
        audit_path="artifacts/trading_loop_audit.jsonl",
        total_cycles=0,
        status_counts={},
        recent_cycles=(),
        last_n=20,
    )

    monkeypatch.setattr(tl_mod, "build_recent_cycles_summary", lambda **_: summary)

    result = runner.invoke(app, ["trading", "recent-cycles"])
    assert result.exit_code == 0
    assert "execution_enabled=False" in result.output


def test_trading_decision_journal_summary_read_only(monkeypatch, tmp_path) -> None:
    """trading decision-journal-summary exits 0 on empty journal."""
    from app.decisions import journal as journal_mod

    monkeypatch.setattr(journal_mod, "load_decision_journal", lambda _path: [])

    from app.decisions.journal import DecisionJournalSummary

    summary = DecisionJournalSummary(
        generated_at="2026-03-23T00:00:00+00:00",
        total_count=0,
        symbols=[],
        by_mode={},
        by_approval={},
        by_execution={},
        avg_confidence=None,
        journal_path=str(tmp_path / "decision_journal.jsonl"),
    )
    monkeypatch.setattr(
        journal_mod,
        "build_decision_journal_summary",
        lambda entries, journal_path: summary,
    )

    result = runner.invoke(
        app,
        ["trading", "decision-journal-summary", "--journal-path", str(tmp_path / "j.jsonl")],
    )
    assert result.exit_code == 0
    assert "total_count=0" in result.output
    assert "execution_enabled=False" in result.output
