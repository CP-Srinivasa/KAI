"""Tests for alerts hold/annotation operational CLI commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from app.alerts.audit import (
    AlertAuditRecord,
    append_alert_audit,
    load_outcome_annotations,
)
from app.cli.main import app
from app.messaging.exchange_relay import RelayStats

runner = CliRunner()


def _write_directional_alert(
    artifacts_dir: Path,
    *,
    doc_id: str,
    sentiment: str,
    dispatched_at: str = "2026-03-25T10:00:00+00:00",
) -> None:
    append_alert_audit(
        AlertAuditRecord(
            document_id=doc_id,
            channel="telegram",
            message_id="dry_run",
            is_digest=False,
            dispatched_at=dispatched_at,
            sentiment_label=sentiment,
            affected_assets=["BTC"],
            priority=8,
            actionable=True,
        ),
        artifacts_dir,
    )


def _append_jsonl(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def test_alerts_hold_report_writes_outputs(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    output_dir = artifacts_dir / "ph5_hold"
    artifacts_dir.mkdir(parents=True)
    _write_directional_alert(artifacts_dir, doc_id="doc-1", sentiment="bullish")

    result = runner.invoke(
        app,
        [
            "alerts",
            "hold-report",
            "--artifacts-dir",
            str(artifacts_dir),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (output_dir / "ph5_hold_metrics_report.json").exists()
    assert (output_dir / "ph5_hold_operator_summary.md").exists()


def test_alerts_baseline_report_writes_outputs_with_missing_input(tmp_path: Path) -> None:
    output_dir = tmp_path / "artifacts" / "ph5_baseline"
    missing_input = tmp_path / "artifacts" / "missing.jsonl"

    result = runner.invoke(
        app,
        [
            "alerts",
            "baseline-report",
            "--input-path",
            str(missing_input),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (output_dir / "ph5_offline_signal_baseline.json").exists()
    assert (output_dir / "ph5_offline_signal_baseline.md").exists()


def test_alerts_annotate_writes_outcome(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True)
    _write_directional_alert(artifacts_dir, doc_id="doc-1", sentiment="bullish")

    result = runner.invoke(
        app,
        [
            "alerts",
            "annotate",
            "doc-1",
            "hit",
            "--asset",
            "BTC",
            "--note",
            "validated",
            "--artifacts-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    anns = load_outcome_annotations(artifacts_dir)
    assert len(anns) == 1
    assert anns[0].document_id == "doc-1"
    assert anns[0].outcome == "hit"
    assert anns[0].asset == "BTC"


def test_alerts_pending_annotations_lists_unannotated_only(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True)

    _write_directional_alert(artifacts_dir, doc_id="doc-1", sentiment="bullish")
    _write_directional_alert(artifacts_dir, doc_id="doc-2", sentiment="bearish")
    # Mark doc-2 as annotated so only doc-1 remains pending.
    runner.invoke(
        app,
        [
            "alerts",
            "annotate",
            "doc-2",
            "miss",
            "--artifacts-dir",
            str(artifacts_dir),
        ],
    )

    result = runner.invoke(
        app,
        [
            "alerts",
            "pending-annotations",
            "--limit",
            "20",
            "--artifacts-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "doc-1" in result.output
    assert "doc-2" not in result.output


def test_alerts_auto_check_skips_too_fresh_alerts_by_default(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True)
    _write_directional_alert(
        artifacts_dir,
        doc_id="doc-future",
        sentiment="bullish",
        dispatched_at="2999-01-01T00:00:00+00:00",
    )

    result = runner.invoke(
        app,
        [
            "alerts",
            "auto-check",
            "--artifacts-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "No pending directional alerts to check." in result.output


def test_alerts_signal_status_reports_pipeline_counts(tmp_path: Path) -> None:
    handoff = tmp_path / "handoff.jsonl"
    outbox = tmp_path / "outbox.jsonl"
    sent = tmp_path / "sent.jsonl"
    dead = tmp_path / "dead.jsonl"
    now = datetime.now(UTC).isoformat()

    _append_jsonl(handoff, {"event": "telegram_signal_handoff", "timestamp_utc": now})
    _append_jsonl(
        outbox,
        {"event": "telegram_signal_exchange_forward_queued", "status": "queued"},
    )
    _append_jsonl(sent, {"event": "telegram_signal_exchange_forward_sent", "relayed_at_utc": now})
    _append_jsonl(
        dead,
        {
            "event": "telegram_signal_exchange_forward_dead_letter",
            "dead_lettered_at_utc": now,
        },
    )

    result = runner.invoke(
        app,
        [
            "alerts",
            "signal-status",
            "--lookback-hours",
            "24",
            "--handoff-log-path",
            str(handoff),
            "--outbox-log-path",
            str(outbox),
            "--sent-log-path",
            str(sent),
            "--dead-letter-log-path",
            str(dead),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Signal Pipeline Status" in result.output
    assert "handoff_total=1" in result.output
    assert "outbox_queued_total=1" in result.output
    assert "exchange_sent_total=1" in result.output
    assert "exchange_dead_letter_total=1" in result.output


def test_alerts_exchange_relay_invokes_worker_with_overrides(tmp_path: Path, monkeypatch) -> None:
    outbox = tmp_path / "outbox.jsonl"
    sent = tmp_path / "sent.jsonl"
    dead = tmp_path / "dead.jsonl"
    captured: dict[str, object] = {}

    async def _fake_relay_exchange_outbox_once(**kwargs: object) -> RelayStats:
        captured.update(kwargs)
        return RelayStats(processed=2, sent=1, requeued=1, dead_lettered=0, skipped=0)

    monkeypatch.setattr("app.cli.main.relay_exchange_outbox_once", _fake_relay_exchange_outbox_once)

    result = runner.invoke(
        app,
        [
            "alerts",
            "exchange-relay",
            "--endpoint",
            "https://example.invalid/relay",
            "--batch-size",
            "25",
            "--timeout-seconds",
            "7",
            "--max-attempts",
            "4",
            "--outbox-log-path",
            str(outbox),
            "--sent-log-path",
            str(sent),
            "--dead-letter-log-path",
            str(dead),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["endpoint"] == "https://example.invalid/relay"
    assert captured["batch_size"] == 25
    assert captured["timeout_seconds"] == 7
    assert captured["max_attempts"] == 4
    assert str(captured["outbox_path"]) == str(outbox)
    assert str(captured["sent_log_path"]) == str(sent)
    assert str(captured["dead_letter_log_path"]) == str(dead)
    assert "Exchange Relay Run" in result.output
    assert "processed=2" in result.output
    assert "sent=1" in result.output
    assert "requeued=1" in result.output
