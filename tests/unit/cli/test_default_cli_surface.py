"""Tests for the default (core-only) CLI surface."""

from typer.testing import CliRunner

from app.cli.main import app

runner = CliRunner()


def test_default_root_help_exposes_only_core_groups() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ingest" in result.output
    assert "pipeline" in result.output
    assert "query" in result.output
    assert "alerts" in result.output
    assert "sources" not in result.output
    assert "podcasts" not in result.output
    assert "youtube" not in result.output
    assert "research" not in result.output
    # "trading-bot" in app name is fine; a subcommand would show with trailing spaces
    assert "trading  " not in result.output


def test_default_query_help_only_exposes_analyze_pending() -> None:
    result = runner.invoke(app, ["query", "--help"])
    assert result.exit_code == 0
    assert "analyze-pending" in result.output
    assert "validate" not in result.output
    assert "list" not in result.output
    assert "analyze-pending-shadow" not in result.output


def test_default_alerts_help_exposes_core_hold_ops() -> None:
    result = runner.invoke(app, ["alerts", "--help"])
    assert result.exit_code == 0
    assert "evaluate-pending" in result.output
    assert "send-test" in result.output
    assert "hold-report" in result.output
    assert "pending-annotations" in result.output
    assert "annotate" in result.output
    assert "hit-rate" not in result.output
