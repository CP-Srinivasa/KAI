"""G5 Task 4: reject streams have one real, read-only operator consumer."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from app.alerts.health_check import _FRESHNESS_PER_FILE_MIN, _check_input_contract_rejection_streams
from app.audit.input_contract_rejections import (
    inspect_input_rejection_streams,
    read_recent_input_rejections,
)
from app.cli.commands.audit import audit_app

runner = CliRunner()


def _write(path: Path, rows: list[dict[str, object] | str]) -> None:
    encoded = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    path.write_text("\n".join(encoded) + "\n", encoding="utf-8")


def test_consumer_reads_both_reject_streams_and_skips_malformed_rows(tmp_path: Path) -> None:
    ln_path = tmp_path / "ln_input_contract_rejections.jsonl"
    analysis_path = tmp_path / "analysis_input_contract_rejections.jsonl"
    _write(
        ln_path,
        [
            "not-json",
            {
                "ts": "2026-08-31T12:00:00+00:00",
                "contract": "money_journal_input/v1",
                "reasons": ["node_pubkey_hex:invalid_pubkey_length"],
                "unexpected_secret": "must-not-be-echoed",
            },
        ],
    )
    _write(
        analysis_path,
        [
            {
                "ts": "2026-08-31T12:01:00+00:00",
                "contract": "analysis_input/v1",
                "reason": "youtube_content_below_measured_minimum",
            }
        ],
    )

    rows = read_recent_input_rejections(
        ln_path=ln_path,
        analysis_path=analysis_path,
        limit=10,
    )

    assert [row["stream"] for row in rows] == [
        "ln_input_contract_rejections.jsonl",
        "analysis_input_contract_rejections.jsonl",
    ]
    assert rows[0]["record"]["contract"] == "money_journal_input/v1"
    assert rows[1]["record"]["contract"] == "analysis_input/v1"
    assert "unexpected_secret" not in rows[0]["record"]


def test_audit_cli_exposes_raw_reject_records_for_operator_decision(tmp_path: Path) -> None:
    ln_path = tmp_path / "ln_input_contract_rejections.jsonl"
    analysis_path = tmp_path / "analysis_input_contract_rejections.jsonl"
    _write(
        ln_path,
        [
            {
                "ts": "2026-08-31T12:00:00+00:00",
                "contract": "money_journal_input/v1",
                "reasons": ["funding_txid_str:invalid_txid_hex"],
            }
        ],
    )

    result = runner.invoke(
        audit_app,
        [
            "input-rejections",
            "--ln-path",
            str(ln_path),
            "--analysis-path",
            str(analysis_path),
            "--limit",
            "10",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload[0]["stream"] == "ln_input_contract_rejections.jsonl"
    assert payload[0]["record"]["reasons"] == ["funding_txid_str:invalid_txid_hex"]


def test_consumer_returns_empty_when_reject_streams_do_not_exist(tmp_path: Path) -> None:
    assert (
        read_recent_input_rejections(
            ln_path=tmp_path / "ln_input_contract_rejections.jsonl",
            analysis_path=tmp_path / "analysis_input_contract_rejections.jsonl",
            limit=10,
        )
        == []
    )


def test_health_probe_reports_malformed_existing_stream_without_requiring_silence(
    tmp_path: Path,
) -> None:
    ln_path = tmp_path / "ln_input_contract_rejections.jsonl"
    analysis_path = tmp_path / "analysis_input_contract_rejections.jsonl"

    assert (
        inspect_input_rejection_streams(
            ln_path=ln_path,
            analysis_path=analysis_path,
        )
        == []
    )
    assert _check_input_contract_rejection_streams(tmp_path) == []

    _write(ln_path, ["not-json"])
    problems = inspect_input_rejection_streams(
        ln_path=ln_path,
        analysis_path=analysis_path,
    )
    issues = _check_input_contract_rejection_streams(tmp_path)

    assert [(problem.stream, problem.detail) for problem in problems] == [
        ("ln_input_contract_rejections.jsonl", "malformed JSON in recent record 1")
    ]
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].component == "input_contract_rejection_stream"


def test_reject_stream_freshness_entries_are_synchronous_sentinels() -> None:
    assert _FRESHNESS_PER_FILE_MIN["ln_input_contract_rejections.jsonl"] == 0
    assert _FRESHNESS_PER_FILE_MIN["analysis_input_contract_rejections.jsonl"] == 0
