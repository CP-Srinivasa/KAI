"""Smoke tests für die universe eligibility CLI-Commands."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from app.cli.commands.universe import universe_app

runner = CliRunner()


def test_eligibility_show_no_snapshot(tmp_path: Path) -> None:
    result = runner.invoke(universe_app, ["eligibility-show", "--ledger", str(tmp_path / "x.jsonl")])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["available"] is False


def test_eligibility_show_reads_latest(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from app.observability.symbol_eligibility_ledger import append_eligibility_snapshot
    from app.trading.symbol_eligibility import EligibilityVerdict

    p = tmp_path / "elig.jsonl"
    append_eligibility_snapshot(
        p, [EligibilityVerdict("BTC/USDT", True, [])], now=datetime(2026, 6, 29, tzinfo=UTC)
    )
    result = runner.invoke(universe_app, ["eligibility-show", "--ledger", str(p)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["count"] == 1
