"""Tests for alerts hold/annotation operational CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from app.alerts.audit import (
    AlertAuditRecord,
    append_alert_audit,
    load_outcome_annotations,
)
from app.cli.main import app

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
