"""Tests for the default (core-only) CLI surface."""

from typer.testing import CliRunner

from app.cli.main import app

runner = CliRunner()


def test_default_root_help_exposes_only_core_groups() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ingest" in result.output
    assert "analyze" in result.output
    assert "signals" in result.output
    assert "pipeline-run" in result.output
    assert "alerts" in result.output
    assert "pipeline  " not in result.output
    assert "query" not in result.output
    assert "sources" not in result.output
    assert "podcasts" not in result.output
    assert "youtube" not in result.output
    assert "research" not in result.output
    assert "trading" in result.output


def test_default_analyze_help_only_exposes_pending() -> None:
    result = runner.invoke(app, ["analyze", "--help"])
    assert result.exit_code == 0
    assert "pending" in result.output
    assert "validate" not in result.output
    assert "list" not in result.output
    assert "analyze-pending-shadow" not in result.output


def test_default_signals_help_exposes_extract() -> None:
    result = runner.invoke(app, ["signals", "--help"])
    assert result.exit_code == 0
    assert "extract" in result.output


def test_default_alerts_help_exposes_core_hold_ops() -> None:
    result = runner.invoke(app, ["alerts", "--help"])
    assert result.exit_code == 0
    assert "evaluate-pending" in result.output
    assert "send-test" in result.output
    assert "hold-report" in result.output
    assert "pending-annotations" in result.output
    assert "annotate" in result.output
    assert "hit-rate" not in result.output
