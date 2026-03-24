"""CLI tests for Sprint 41 trading-loop control-plane surfaces."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from app.cli.main import app

runner = CliRunner()


def test_trading_loop_commands_are_visible_in_research_help() -> None:
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "trading-loop-status" in result.output
    assert "trading-loop-recent-cycles" in result.output
    assert "trading-loop-run-once" in result.output
    assert "loop-cycle-summary" in result.output


def test_research_trading_loop_status_prints_read_only_fields(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "research",
            "trading-loop-status",
            "--audit-path",
            str(tmp_path / "missing_loop.jsonl"),
            "--mode",
            "paper",
        ],
    )

    assert result.exit_code == 0
    assert "Trading Loop Status" in result.output
    assert "run_once_allowed=True" in result.output
    assert "auto_loop_enabled=False" in result.output
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output


def test_research_trading_loop_recent_cycles_alias_and_canonical_match(tmp_path: Path) -> None:
    audit_path = tmp_path / "loop.jsonl"

    canonical = runner.invoke(
        app,
        [
            "research",
            "trading-loop-recent-cycles",
            "--audit-path",
            str(audit_path),
        ],
    )
    alias = runner.invoke(
        app,
        [
            "research",
            "loop-cycle-summary",
            "--audit-path",
            str(audit_path),
        ],
    )

    assert canonical.exit_code == 0
    assert alias.exit_code == 0
    assert "Trading Loop Recent Cycles" in canonical.output
    assert "Trading Loop Recent Cycles" in alias.output
    assert "auto_loop_enabled=False" in canonical.output


def test_research_trading_loop_run_once_paper_mode_is_guarded(tmp_path: Path) -> None:
    loop_audit = tmp_path / "artifacts" / "trading_loop_audit.jsonl"
    exec_audit = tmp_path / "artifacts" / "paper_execution_audit.jsonl"

    result = runner.invoke(
        app,
        [
            "research",
            "trading-loop-run-once",
            "--symbol",
            "BTC/USDT",
            "--mode",
            "paper",
            "--provider",
            "mock",
            "--analysis-profile",
            "conservative",
            "--loop-audit-path",
            str(loop_audit),
            "--execution-audit-path",
            str(exec_audit),
        ],
    )

    assert result.exit_code == 0
    assert "Trading Loop Run Once" in result.output
    assert "status=no_signal" in result.output
    assert "auto_loop_enabled=False" in result.output
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output
    assert loop_audit.exists()
    assert not exec_audit.exists(), "conservative profile must avoid paper execution writes"


def test_research_trading_loop_run_once_shadow_mode_is_allowed(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "research",
            "trading-loop-run-once",
            "--symbol",
            "ETH/USDT",
            "--mode",
            "shadow",
            "--provider",
            "mock",
            "--analysis-profile",
            "conservative",
            "--loop-audit-path",
            str(tmp_path / "shadow_loop.jsonl"),
            "--execution-audit-path",
            str(tmp_path / "shadow_exec.jsonl"),
        ],
    )

    assert result.exit_code == 0
    assert "mode=shadow" in result.output
    assert "status=no_signal" in result.output


def test_research_trading_loop_run_once_live_mode_fails_closed(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "research",
            "trading-loop-run-once",
            "--symbol",
            "BTC/USDT",
            "--mode",
            "live",
            "--provider",
            "mock",
            "--loop-audit-path",
            str(tmp_path / "live_loop.jsonl"),
            "--execution-audit-path",
            str(tmp_path / "live_exec.jsonl"),
        ],
    )

    assert result.exit_code == 1
    assert "blocked" in result.output.lower()
    assert "allowed: paper, shadow" in result.output
