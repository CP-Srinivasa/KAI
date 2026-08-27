from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from app.cli.commands.trading import trading_app

runner = CliRunner()


def test_close_evidence_shadow_cli_requires_shadow_guard(tmp_path: Path) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    audit.write_text(json.dumps({"event_type": "order_filled"}) + "\n", encoding="utf-8")

    result = runner.invoke(trading_app, ["close-evidence-shadow", "--audit", str(audit)])

    assert result.exit_code == 2
    assert "--shadow is mandatory" in result.output


def test_close_evidence_shadow_cli_writes_canonical_report_without_classification(
    tmp_path: Path,
) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    output = tmp_path / "close_evidence_shadow.json"
    audit.write_text(json.dumps({"event_type": "order_filled"}) + "\n", encoding="utf-8")

    result = runner.invoke(
        trading_app,
        ["close-evidence-shadow", "--shadow", "--audit", str(audit), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    encoded = output.read_bytes()
    assert encoded.endswith(b"\n")
    report = json.loads(encoded)
    assert report["mode"] == "shadow_read_only"
    assert report["input_rows"] == 1
    assert report["eligible_closes"] == 0
    assert set(report["by_venue"]) == {"binance", "bybit"}

    source = Path("app/cli/commands/close_evidence_cli.py").read_text(encoding="utf-8")
    assert "close_classification" not in source
    assert "publish_evidence" not in source
