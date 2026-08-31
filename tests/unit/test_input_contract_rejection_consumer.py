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


REJECT_STREAMS = (
    "ln_input_contract_rejections.jsonl",
    "analysis_input_contract_rejections.jsonl",
)


def test_reject_streams_are_not_in_the_freshness_map() -> None:
    """G5_REJECT_STREAMS_NOT_IN_FRESHNESS_MAP.

    Diese Stroeme haben keine Schreibkadenz — Stille ist der gesunde Zustand.
    Eine Zeile mit Schwelle 0 waere heute wirkungslos und spaeter ein
    Daueralarm; der Vertrag drueckt die Ueberwachung jetzt direkt aus.
    """
    for stream in REJECT_STREAMS:
        assert stream not in _FRESHNESS_PER_FILE_MIN


def test_all_freshness_thresholds_are_positive() -> None:
    """ALL_FRESHNESS_THRESHOLDS_POSITIVE — kein Sentinel, kein Platzhalter."""
    non_positive = {k: v for k, v in _FRESHNESS_PER_FILE_MIN.items() if v <= 0}
    assert non_positive == {}


def test_reject_streams_declare_an_alternative_watcher() -> None:
    """G5_ALTERNATIVE_WATCHER_DECLARED + G5_WATCHER_EXISTS."""
    import json
    from pathlib import Path

    contracts = json.loads(
        (Path(__file__).resolve().parents[2] / "config" / "stream_contracts.json").read_text(
            encoding="utf-8"
        )
    )
    for stream in REJECT_STREAMS:
        entry = contracts["streams"][stream]
        assert entry["monitoring"] == "alternative_watcher"
        assert entry["watcher"] == "_check_input_contract_rejection_streams"
        assert "freshness_check" not in entry
    assert callable(_check_input_contract_rejection_streams)


def test_watcher_is_called_from_the_health_report() -> None:
    """G5_WATCHER_CALLED_FROM_HEALTH — ein Waechter, den niemand ruft, ist eine Behauptung."""
    import inspect

    from app.alerts.health_check import run_health_check_report

    source = inspect.getsource(run_health_check_report)
    assert "_check_input_contract_rejection_streams(" in source
