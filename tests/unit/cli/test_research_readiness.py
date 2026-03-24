"""Tests for research readiness, provider-health, drift, gate, remediation,
artifact inventory/rotation/retention, cleanup, escalation, runbook,
review-journal, resolution, command inventory.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from app.cli import main as cli_main
from app.cli.commands import research_operator as cli_research_operator
from app.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Sprint 21: research readiness-summary
# ---------------------------------------------------------------------------


def test_research_readiness_summary_prints_status(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "readiness-summary",
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
            "--alert-audit-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0
    assert "Operational Readiness Summary" in result.output
    assert "Status" in result.output
    assert "Execution Enabled" in result.output


def test_research_readiness_summary_saves_report(tmp_path) -> None:
    out_file = tmp_path / "readiness.json"
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "readiness-summary",
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
            "--alert-audit-dir",
            str(artifacts_dir),
            "--out",
            str(out_file),
        ],
    )

    assert result.exit_code == 0
    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["report_type"] == "operational_readiness"
    assert payload["execution_enabled"] is False


# ---------------------------------------------------------------------------
# Sprint 22: research provider-health / drift-summary
# ---------------------------------------------------------------------------


def test_research_provider_health_prints_table(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            "research",
            "provider-health",
            "--state-path",
            str(tmp_path / "nope.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Provider Health" in result.output
    assert "execution_enabled=False" in result.output


def test_research_drift_summary_prints_table(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            "research",
            "drift-summary",
            "--state-path",
            str(tmp_path / "nope.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Distribution Drift Summary" in result.output
    assert "Status" in result.output


# ---------------------------------------------------------------------------
# Sprint 23: research gate-summary / remediation-recommendations
# ---------------------------------------------------------------------------


def test_research_gate_summary_prints_status(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "gate-summary",
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
            "--alert-audit-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0
    assert "Protective Gate Summary" in result.output
    assert "Gate Status" in result.output
    assert "Execution Enabled" in result.output


def test_research_remediation_recommendations_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "remediation-recommendations",
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
            "--alert-audit-dir",
            str(artifacts_dir),
        ],
    )

    assert result.exit_code == 0
    assert "Remediation Recommendations" in result.output
    assert "gate_status=" in result.output
    assert "execution_enabled=False" in result.output


# ---------------------------------------------------------------------------
# Sprint 24: research artifact-inventory
# ---------------------------------------------------------------------------


def test_research_artifact_inventory_empty_dir(tmp_path) -> None:
    artifacts_dir = tmp_path / "empty_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        ["research", "artifact-inventory", "--artifacts-dir", str(artifacts_dir)],
    )

    assert result.exit_code == 0
    assert "Artifact Inventory" in result.output
    assert "Execution Enabled" in result.output


def test_research_artifact_inventory_with_files(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "report.json").write_text('{"x": 1}', encoding="utf-8")
    (artifacts_dir / "data.jsonl").write_text('{"y": 2}\n', encoding="utf-8")

    result = runner.invoke(
        app,
        ["research", "artifact-inventory", "--artifacts-dir", str(artifacts_dir)],
    )

    assert result.exit_code == 0
    assert "Artifact Inventory" in result.output
    assert "Total Files" in result.output


def test_research_artifact_inventory_saves_report(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_file = tmp_path / "inventory.json"

    result = runner.invoke(
        app,
        [
            "research",
            "artifact-inventory",
            "--artifacts-dir",
            str(artifacts_dir),
            "--out",
            str(out_file),
        ],
    )

    assert result.exit_code == 0
    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["report_type"] == "artifact_inventory"
    assert payload["execution_enabled"] is False


# ---------------------------------------------------------------------------
# Sprint 25: research artifact-rotate
# ---------------------------------------------------------------------------


def test_research_artifact_rotate_dry_run_default(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "old_report.json").write_text('{"x": 1}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "artifact-rotate",
            "--artifacts-dir",
            str(artifacts_dir),
            "--stale-after-days",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert "Artifact Rotation Summary" in result.output
    assert "Dry Run" in result.output
    assert "Dry-run mode: no files were moved." in result.output
    assert (artifacts_dir / "old_report.json").exists()


def test_research_artifact_rotate_no_dry_run_moves_stale(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    stale_file = artifacts_dir / "evaluation_report.json"
    stale_file.write_text('{"x": 1}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "artifact-rotate",
            "--artifacts-dir",
            str(artifacts_dir),
            "--stale-after-days",
            "0",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "Archived" in result.output
    assert not stale_file.exists()
    archive_files = list((artifacts_dir / "archive").rglob("evaluation_report.json"))
    assert len(archive_files) == 1


def test_research_artifact_rotate_skips_protected_artifacts(tmp_path) -> None:
    """Protected artifacts (e.g. mcp_write_audit.jsonl) must never be rotated (I-155)."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    protected_file = artifacts_dir / "mcp_write_audit.jsonl"
    protected_file.write_text('{"x": 1}\n', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "artifact-rotate",
            "--artifacts-dir",
            str(artifacts_dir),
            "--stale-after-days",
            "0",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == 0
    assert protected_file.exists()


# ---------------------------------------------------------------------------
# Sprint 26: research artifact-retention
# ---------------------------------------------------------------------------


def test_research_artifact_retention_empty_dir(tmp_path) -> None:
    artifacts_dir = tmp_path / "empty_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        ["research", "artifact-retention", "--artifacts-dir", str(artifacts_dir)],
    )

    assert result.exit_code == 0
    assert "Artifact Retention" in result.output


def test_research_artifact_retention_protected_marked(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "companion_benchmark_artifact.json").write_text(
        '{"artifact_type": "companion_benchmark"}', encoding="utf-8"
    )

    result = runner.invoke(
        app,
        ["research", "artifact-retention", "--artifacts-dir", str(artifacts_dir)],
    )

    assert result.exit_code == 0
    assert "Artifact Retention" in result.output
    assert "Protected" in result.output


def test_research_artifact_retention_json_output(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_file = tmp_path / "retention.json"

    result = runner.invoke(
        app,
        [
            "research",
            "artifact-retention",
            "--artifacts-dir",
            str(artifacts_dir),
            "--out",
            str(out_file),
        ],
    )

    assert result.exit_code == 0
    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["report_type"] == "artifact_retention_report"


# ---------------------------------------------------------------------------
# Sprint 27: cleanup-eligibility / protected-artifact-summary / review-required-summary
# ---------------------------------------------------------------------------


def test_research_cleanup_eligibility_summary_stale_files(tmp_path) -> None:
    import os
    import time

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    stale_file = artifacts_dir / "stale.json"
    stale_file.write_text('{"x": 1}', encoding="utf-8")
    old_time = time.time() - (40 * 24 * 60 * 60)
    os.utime(str(stale_file), (old_time, old_time))

    result = runner.invoke(
        app,
        ["research", "cleanup-eligibility-summary", "--artifacts-dir", str(artifacts_dir)],
    )

    assert result.exit_code == 0
    assert "Cleanup Eligibility" in result.output


def test_research_protected_artifact_summary_lists_protected(tmp_path) -> None:
    """mcp_write_audit.jsonl and promotion_record.json must appear as protected."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "mcp_write_audit.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (artifacts_dir / "promotion_record.json").write_text('{"x":1}', encoding="utf-8")

    result = runner.invoke(
        app,
        ["research", "protected-artifact-summary", "--artifacts-dir", str(artifacts_dir)],
    )

    assert result.exit_code == 0
    assert "Protected Artifact Summary" in result.output
    assert "mcp_write_audit.jsonl" in result.output or "promotion_record.json" in result.output


def test_research_review_required_summary_lists_unknown(tmp_path) -> None:
    """Unknown artifact filenames should be marked review_required."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "unknown_report.json").write_text('{"x": 1}', encoding="utf-8")

    result = runner.invoke(
        app,
        ["research", "review-required-summary", "--artifacts-dir", str(artifacts_dir)],
    )

    assert result.exit_code == 0
    assert "Review Required Artifact Summary" in result.output
    assert "Review Required Count" in result.output


# ---------------------------------------------------------------------------
# Sprint 30: operator-runbook / runbook-summary / runbook-next-steps
# ---------------------------------------------------------------------------


def test_research_governance_summary_not_in_help() -> None:
    """governance-summary is NOT a CLI command — must not appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "governance-summary" not in result.output


def test_get_invalid_research_command_refs_uses_registered_cli_state() -> None:
    refs = [
        "research handoff-collector-summary",
        "research handoff-summary",
        "research consumer-ack",
        "research decision-pack-summary",
        "research operator-decision-pack",
        "research daily-summary",
        "research runbook-summary",
        "research runbook-next-steps",
        "research operator-runbook",
        "research blocking-actions",
    ]

    assert cli_main.get_invalid_research_command_refs(refs) == []
    assert cli_main.get_invalid_research_command_refs(
        [
            "research governance-summary",
            "research made-up-command",
            "operator-runbook",
        ]
    ) == [
        "research governance-summary",
        "research made-up-command",
        "operator-runbook",
    ]


def test_research_command_inventory_matches_registration_and_help() -> None:
    inventory = cli_main.get_research_command_inventory()
    registered = cli_main.get_registered_research_command_names()
    help_result = runner.invoke(app, ["research", "--help"])

    assert help_result.exit_code == 0

    for name in inventory["final_commands"]:
        assert name in registered
        assert name in help_result.output

    for alias, target in inventory["aliases"].items():
        assert alias in registered
        assert alias in help_result.output
        assert target in inventory["final_commands"]

    for name in inventory["superseded_commands"]:
        assert name not in registered
        assert name not in help_result.output

    classified = (
        set(inventory["final_commands"])
        | set(inventory["aliases"])
        | set(inventory["superseded_commands"])
    )
    assert set(inventory["provisional_commands"]) == registered - classified


def test_research_operator_runbook_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out_file = tmp_path / "runbook.json"

    result = runner.invoke(
        app,
        [
            "research",
            "operator-runbook",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
            "--out",
            str(out_file),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Runbook" in result.output
    assert "status=" in result.output
    assert "steps=" in result.output

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["report_type"] == "operator_runbook_summary"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False


def test_research_runbook_summary_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "runbook-summary",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Runbook Summary" in result.output
    assert "status=" in result.output
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output


def test_research_runbook_next_steps_prints(tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "research",
            "runbook-next-steps",
            "--artifacts-dir",
            str(artifacts_dir),
            "--state-path",
            str(artifacts_dir / "active_route_profile.json"),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Runbook Next Steps" in result.output
    assert "status=" in result.output
    assert "execution_enabled=False" in result.output
    assert "write_back_allowed=False" in result.output


def test_research_runbook_next_steps_with_blocking_actions(tmp_path) -> None:
    """When there are action queue items, next_steps should have priority and command refs."""
    from app.research.operational_readiness import (
        OperatorRunbookSummary,
        RunbookStep,
    )

    step = RunbookStep(
        step_id="step-001",
        title="Review blocking issues",
        summary="There are blocking items in the queue.",
        severity="critical",
        priority="p1",
        blocking=True,
        queue_status="blocking",
        subsystem="readiness",
        operator_action_required=True,
        command_refs=["research blocking-actions"],
    )
    runbook = OperatorRunbookSummary(
        overall_status="blocking",
        blocking_count=1,
        steps=[step],
        next_steps=[step],
    )

    payload = runbook.to_json_dict()
    assert payload["report_type"] == "operator_runbook_summary"
    assert len(payload["next_steps"]) >= 1
    next_step = payload["next_steps"][0]
    assert next_step["priority"] == "p1"
    assert "research blocking-actions" in next_step["command_refs"]


# ---------------------------------------------------------------------------
# Sprint 31: review-journal-append / review-journal-summary / resolution-summary
# ---------------------------------------------------------------------------


def test_research_review_journal_append_writes_append_only_jsonl(tmp_path) -> None:
    from app.research.operational_readiness import load_review_journal_entries

    journal_path = tmp_path / "operator_review_journal.jsonl"

    first = runner.invoke(
        app,
        [
            "research",
            "review-journal-append",
            "rbk_123",
            "--operator-id",
            "ops-1",
            "--review-action",
            "note",
            "--review-note",
            "First note.",
            "--journal-path",
            str(journal_path),
        ],
    )
    second = runner.invoke(
        app,
        [
            "research",
            "review-journal-append",
            "rbk_123",
            "--operator-id",
            "ops-1",
            "--review-action",
            "resolve",
            "--review-note",
            "Resolved later.",
            "--journal-path",
            str(journal_path),
        ],
    )

    entries = load_review_journal_entries(journal_path)

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "core_state_unchanged=True" in second.output
    assert len(entries) == 2
    assert entries[0].review_action == "note"
    assert entries[1].review_action == "resolve"


def test_research_review_journal_summary_prints_counts(tmp_path) -> None:
    journal_path = tmp_path / "operator_review_journal.jsonl"
    runner.invoke(
        app,
        [
            "research",
            "review-journal-append",
            "rbk_123",
            "--operator-id",
            "ops-1",
            "--review-action",
            "note",
            "--review-note",
            "Still open.",
            "--journal-path",
            str(journal_path),
        ],
    )

    result = runner.invoke(
        app,
        [
            "research",
            "review-journal-summary",
            "--journal-path",
            str(journal_path),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Review Journal Summary" in result.output
    assert "journal_status=open" in result.output
    assert "open_count=1" in result.output
    assert "execution_enabled=False" in result.output


def test_research_resolution_summary_prints_latest_source_statuses(tmp_path) -> None:
    journal_path = tmp_path / "operator_review_journal.jsonl"
    runner.invoke(
        app,
        [
            "research",
            "review-journal-append",
            "rbk_123",
            "--operator-id",
            "ops-1",
            "--review-action",
            "note",
            "--review-note",
            "Initial note.",
            "--journal-path",
            str(journal_path),
        ],
    )
    runner.invoke(
        app,
        [
            "research",
            "review-journal-append",
            "rbk_123",
            "--operator-id",
            "ops-1",
            "--review-action",
            "resolve",
            "--review-note",
            "Resolved.",
            "--journal-path",
            str(journal_path),
        ],
    )

    result = runner.invoke(
        app,
        [
            "research",
            "resolution-summary",
            "--journal-path",
            str(journal_path),
        ],
    )

    assert result.exit_code == 0
    assert "Operator Resolution Summary" in result.output
    assert "resolved_count=1" in result.output
    assert "resolved=rbk_123" in result.output


@pytest.mark.parametrize(
    "command_name",
    ["operator-runbook", "runbook-summary", "runbook-next-steps"],
)
def test_runbook_cli_commands_fail_closed_on_invalid_command_refs(
    monkeypatch,
    command_name: str,
) -> None:
    from app.research.operational_readiness import OperatorRunbookSummary, RunbookStep

    invalid_step = RunbookStep(
        step_id="step-invalid",
        title="Invalid ref",
        summary="This step intentionally carries a superseded command ref.",
        severity="warning",
        priority="p2",
        queue_status="review_required",
        subsystem="artifacts",
        operator_action_required=True,
        command_refs=["research governance-summary"],
    )
    runbook = OperatorRunbookSummary(
        overall_status="review_required",
        review_required_count=1,
        command_refs=["research governance-summary"],
        steps=[invalid_step],
        next_steps=[invalid_step],
    )

    monkeypatch.setattr(
        cli_research_operator,
        "_build_runbook_from_artifacts",
        lambda **_: runbook,
    )

    result = runner.invoke(app, ["research", command_name])

    assert result.exit_code == 1
    assert "invalid command references" in result.output.lower()
