"""CLI smoke tests for the truth-infra trading commands (prereg + counterfactual-report)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from app.cli.commands.trading import trading_app

runner = CliRunner()


def test_prereg_register_then_list_json(tmp_path: Path) -> None:
    ledger = tmp_path / "prereg.jsonl"
    reg = runner.invoke(
        trading_app,
        [
            "prereg-register",
            "--name",
            "funding_carry_long",
            "--direction",
            "long",
            "--horizon",
            "24h",
            "--success-criteria",
            "net_mean_bps>0 at n>=200",
            "--sample-target",
            "200",
            "--ledger-path",
            str(ledger),
            "--json",
        ],
    )
    assert reg.exit_code == 0, reg.output
    payload = json.loads(reg.stdout)
    assert payload["direction"] == "long"
    assert payload["already_registered"] is False
    assert len(payload["prereg_id"]) == 16
    assert ledger.exists()

    listed = runner.invoke(trading_app, ["prereg-list", "--ledger-path", str(ledger), "--json"])
    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.stdout)
    assert len(rows) == 1
    assert rows[0]["name"] == "funding_carry_long"


def test_prereg_register_rejects_bad_direction(tmp_path: Path) -> None:
    result = runner.invoke(
        trading_app,
        [
            "prereg-register",
            "--name",
            "x",
            "--direction",
            "sideways",
            "--horizon",
            "24h",
            "--success-criteria",
            "net>0",
            "--sample-target",
            "200",
            "--ledger-path",
            str(tmp_path / "p.jsonl"),
        ],
    )
    assert result.exit_code == 2, result.output


def test_prereg_list_empty_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(
        trading_app, ["prereg-list", "--ledger-path", str(tmp_path / "none.jsonl"), "--json"]
    )
    assert result.exit_code == 1


def test_counterfactual_report_json(tmp_path: Path) -> None:
    cf = tmp_path / "cf.jsonl"
    cf.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "symbol": "BTCUSDT",
                    "source": "technical_paper",
                    "in_settled_range": True,
                    "drift_to_range_bps": 0.0,
                    "drift_exceeded": False,
                    "data_quality_suspect": False,
                    "gate_would_reject": False,
                },
                {
                    "symbol": "ETHUSDT",
                    "source": "momentum",
                    "in_settled_range": False,
                    "drift_to_range_bps": 90.0,
                    "drift_exceeded": True,
                    "data_quality_suspect": False,
                    "gate_would_reject": None,
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        trading_app, ["counterfactual-report", "--comparison-path", str(cf), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["total"] == 2
    assert payload["drift_exceeded"] == 1
    assert payload["gate_unknown"] == 1


def test_counterfactual_report_empty_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(
        trading_app, ["counterfactual-report", "--comparison-path", str(tmp_path / "x.jsonl")]
    )
    assert result.exit_code == 1
