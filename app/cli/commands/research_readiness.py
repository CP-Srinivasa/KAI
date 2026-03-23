"""Readiness and artifact health commands.

Covers: operational readiness, provider health, drift, gates, artifact lifecycle.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

console = Console()
research_readiness_app = typer.Typer()

@research_readiness_app.command("readiness-summary")
def research_readiness_summary(
    handoff_path: str | None = typer.Option(
        None, "--handoff-path", help="Signal handoff artifact path"
    ),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state path",
    ),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir", help="Alert audit dir"),
    out: str | None = typer.Option(None, "--out", help="Optional path to save the JSON report"),
) -> None:
    """Print read-only operational readiness summary (Sprint 21)."""
    from pathlib import Path

    from app.alerts.audit import load_alert_audits
    from app.research.active_route import load_active_route_state
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_handoff_acknowledgements, load_signal_handoffs
    from app.research.operational_readiness import (
        ArtifactRef,
        OperationalArtifactRefs,
        build_operational_readiness_report,
        save_operational_readiness_report,
    )

    handoffs = []
    resolved_handoff: Path | None = None
    if handoff_path:
        resolved_handoff = Path(handoff_path)
        if resolved_handoff.exists():
            handoffs = load_signal_handoffs(resolved_handoff)

    ack_path = Path("artifacts/consumer_acknowledgements.jsonl")
    acknowledgements = load_handoff_acknowledgements(ack_path) if ack_path.exists() else []
    collector_summary = build_handoff_collector_summary(handoffs, acknowledgements)

    resolved_state = Path(state_path)
    active_route_state = (
        load_active_route_state(resolved_state) if resolved_state.exists() else None
    )

    alert_dir = Path(alert_audit_dir)
    alert_audits = load_alert_audits(alert_dir) if alert_dir.exists() else []

    artifacts = OperationalArtifactRefs(
        handoff=ArtifactRef(
            path=str(resolved_handoff) if resolved_handoff else None,
            present=bool(resolved_handoff and resolved_handoff.exists()),
        ),
        acknowledgements=ArtifactRef(path=str(ack_path), present=ack_path.exists()),
        active_route_state=ArtifactRef(path=str(resolved_state), present=resolved_state.exists()),
        alert_audit_dir=ArtifactRef(path=str(alert_dir), present=alert_dir.exists()),
    )
    report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=alert_audits,
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=artifacts,
    )

    payload = report.to_json_dict()
    table = Table(title="Operational Readiness Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Status", str(payload.get("readiness_status", "")))
    table.add_row("Issues", str(payload.get("issue_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)

    if out:
        out_path = Path(out)
        save_operational_readiness_report(report, out_path)
        console.print(f"[dim]Saved readiness report to {out_path.resolve()}[/dim]")


# ---------------------------------------------------------------------------
# Sprint 22: provider-health / drift-summary
# ---------------------------------------------------------------------------


@research_readiness_app.command("provider-health")
def research_provider_health(
    handoff_path: str | None = typer.Option(
        None, "--handoff-path", help="Signal handoff artifact path"
    ),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state path",
    ),
) -> None:
    """Print read-only provider health derived from readiness artifacts (Sprint 22)."""
    from pathlib import Path

    from app.research.active_route import load_active_route_state
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_signal_handoffs
    from app.research.operational_readiness import (
        OperationalArtifactRefs,
        build_operational_readiness_report,
    )

    handoffs = []
    if handoff_path and Path(handoff_path).exists():
        handoffs = load_signal_handoffs(Path(handoff_path))

    collector_summary = build_handoff_collector_summary(handoffs, [])

    resolved_state = Path(state_path)
    active_route_state = (
        load_active_route_state(resolved_state) if resolved_state.exists() else None
    )
    alert_audits: list[Any] = []

    report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=alert_audits,
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=OperationalArtifactRefs(),
    )
    health = report.provider_health_summary.to_json_dict()
    table = Table(title="Provider Health")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Provider Count", str(health.get("provider_count", 0)))
    table.add_row("Healthy", str(health.get("healthy_count", 0)))
    table.add_row("Degraded", str(health.get("degraded_count", 0)))
    table.add_row("Unavailable", str(health.get("unavailable_count", 0)))
    console.print(table)
    console.print("execution_enabled=False")


@research_readiness_app.command("drift-summary")
def research_drift_summary(
    handoff_path: str | None = typer.Option(
        None, "--handoff-path", help="Signal handoff artifact path"
    ),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state path",
    ),
) -> None:
    """Print distribution drift summary derived from readiness artifacts (Sprint 22)."""
    from pathlib import Path

    from app.research.active_route import load_active_route_state
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_signal_handoffs
    from app.research.operational_readiness import (
        OperationalArtifactRefs,
        build_operational_readiness_report,
    )

    handoffs = []
    if handoff_path and Path(handoff_path).exists():
        handoffs = load_signal_handoffs(Path(handoff_path))

    collector_summary = build_handoff_collector_summary(handoffs, [])
    resolved_state = Path(state_path)
    active_route_state = (
        load_active_route_state(resolved_state) if resolved_state.exists() else None
    )

    report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=[],
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=OperationalArtifactRefs(),
    )
    drift = report.distribution_drift_summary.to_json_dict()
    table = Table(title="Distribution Drift Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Status", str(drift.get("status", "")))
    table.add_row("Production Handoffs", str(drift.get("production_handoff_count", 0)))
    console.print(table)


# ---------------------------------------------------------------------------
# Sprint 23: gate-summary / remediation-recommendations
# ---------------------------------------------------------------------------


@research_readiness_app.command("gate-summary")
def research_gate_summary(
    handoff_path: str | None = typer.Option(
        None, "--handoff-path", help="Signal handoff artifact path"
    ),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state path",
    ),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir", help="Alert audit dir"),
) -> None:
    """Print read-only protective gate summary derived from readiness (Sprint 23)."""
    from pathlib import Path

    from app.alerts.audit import load_alert_audits
    from app.research.active_route import load_active_route_state
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_handoff_acknowledgements, load_signal_handoffs
    from app.research.operational_readiness import (
        OperationalArtifactRefs,
        build_operational_readiness_report,
    )

    handoffs = []
    if handoff_path and Path(handoff_path).exists():
        handoffs = load_signal_handoffs(Path(handoff_path))

    ack_path = Path("artifacts/consumer_acknowledgements.jsonl")
    acknowledgements = load_handoff_acknowledgements(ack_path) if ack_path.exists() else []
    collector_summary = build_handoff_collector_summary(handoffs, acknowledgements)

    resolved_state = Path(state_path)
    active_route_state = (
        load_active_route_state(resolved_state) if resolved_state.exists() else None
    )

    alert_dir = Path(alert_audit_dir)
    alert_audits = load_alert_audits(alert_dir) if alert_dir.exists() else []

    report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=alert_audits,
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=OperationalArtifactRefs(),
    )
    gate = report.protective_gate_summary.to_json_dict()
    table = Table(title="Protective Gate Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Gate Status", str(gate.get("gate_status", "")))
    table.add_row("Blocking Count", str(gate.get("blocking_count", 0)))
    table.add_row("Execution Enabled", str(gate.get("execution_enabled", False)))
    console.print(table)


@research_readiness_app.command("remediation-recommendations")
def research_remediation_recommendations(
    handoff_path: str | None = typer.Option(
        None, "--handoff-path", help="Signal handoff artifact path"
    ),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state path",
    ),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir", help="Alert audit dir"),
) -> None:
    """Print read-only remediation recommendations from protective gate items (Sprint 23)."""
    from pathlib import Path

    from app.alerts.audit import load_alert_audits
    from app.research.active_route import load_active_route_state
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_handoff_acknowledgements, load_signal_handoffs
    from app.research.operational_readiness import (
        OperationalArtifactRefs,
        build_operational_readiness_report,
    )

    handoffs = []
    if handoff_path and Path(handoff_path).exists():
        handoffs = load_signal_handoffs(Path(handoff_path))

    ack_path = Path("artifacts/consumer_acknowledgements.jsonl")
    acknowledgements = load_handoff_acknowledgements(ack_path) if ack_path.exists() else []
    collector_summary = build_handoff_collector_summary(handoffs, acknowledgements)

    resolved_state = Path(state_path)
    active_route_state = (
        load_active_route_state(resolved_state) if resolved_state.exists() else None
    )

    alert_dir = Path(alert_audit_dir)
    alert_audits = load_alert_audits(alert_dir) if alert_dir.exists() else []

    report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=alert_audits,
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=OperationalArtifactRefs(),
    )
    gate = report.protective_gate_summary

    console.print("[bold]Remediation Recommendations[/bold]")
    console.print(f"gate_status={gate.gate_status}")
    console.print(f"blocking_count={gate.blocking_count}")
    console.print("execution_enabled=False")
    for item in gate.items:
        for action in item.recommended_actions:
            console.print(f"  - {action}")


# ---------------------------------------------------------------------------
# Sprint 24: artifact-inventory
# ---------------------------------------------------------------------------


@research_readiness_app.command("artifact-inventory")
def research_artifact_inventory(
    artifacts_dir: str = typer.Option(
        "artifacts",
        "--artifacts-dir",
        help="Path to artifacts directory",
    ),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days", help="Stale threshold"),
    out: str | None = typer.Option(None, "--out", help="Optional path to save the JSON report"),
) -> None:
    """Print read-only artifact inventory (Sprint 24). execution_enabled always False (I-150)."""
    from pathlib import Path

    from app.research.artifact_lifecycle import build_artifact_inventory, save_artifact_inventory

    artifacts_path = Path(artifacts_dir)
    report = build_artifact_inventory(artifacts_path, stale_after_days=stale_after_days)
    payload = report.to_json_dict()

    table = Table(title="Artifact Inventory")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Artifacts Dir", str(payload.get("artifacts_dir", "")))
    table.add_row("Total Files", str(payload.get("entry_count", 0)))
    table.add_row("Stale", str(payload.get("stale_count", 0)))
    table.add_row("Current", str(payload.get("current_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)

    if out:
        out_path = Path(out)
        save_artifact_inventory(report, out_path)
        console.print(f"[dim]Saved artifact inventory to {out_path.resolve()}[/dim]")


# ---------------------------------------------------------------------------
# Sprint 25: artifact-rotate
# ---------------------------------------------------------------------------


@research_readiness_app.command("artifact-rotate")
def research_artifact_rotate(
    artifacts_dir: str = typer.Option(
        "artifacts",
        "--artifacts-dir",
        help="Path to artifacts directory",
    ),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days", help="Stale threshold"),
    dry_run: bool = typer.Option(
        True, "--dry-run/--no-dry-run", help="Dry-run mode (default True, I-152)"
    ),
    out: str | None = typer.Option(
        None, "--out", help="Optional path to save the rotation summary JSON"
    ),
) -> None:
    """Archive stale artifact files. Dry-run by default (I-152). Protected files skipped (I-155)."""
    from pathlib import Path

    from app.research.artifact_lifecycle import (
        rotate_stale_artifacts,
        save_artifact_rotation_summary,
    )

    artifacts_path = Path(artifacts_dir)
    summary = rotate_stale_artifacts(
        artifacts_path, stale_after_days=stale_after_days, dry_run=dry_run
    )

    table = Table(title="Artifact Rotation Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Dry Run", str(summary.dry_run))
    table.add_row("Archived Count", str(summary.archived_count))
    table.add_row("Skipped Count", str(summary.skipped_count))
    table.add_row("Archive Dir", summary.archive_dir)
    console.print(table)

    if dry_run:
        console.print("[yellow]Dry-run mode: no files were moved.[/yellow]")
    else:
        console.print(f"[green]Archived {summary.archived_count} file(s).[/green]")

    if out:
        out_path = Path(out)
        save_artifact_rotation_summary(summary, out_path)
        console.print(f"[dim]Saved rotation summary to {out_path.resolve()}[/dim]")


# ---------------------------------------------------------------------------
# Sprint 26: artifact-retention / cleanup-eligibility-summary /
#            protected-artifact-summary / review-required-summary
# ---------------------------------------------------------------------------


@research_readiness_app.command("artifact-retention")
def research_artifact_retention(
    artifacts_dir: str = typer.Option(
        "artifacts",
        "--artifacts-dir",
        help="Path to artifacts directory",
    ),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days", help="Stale threshold"),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state file path",
    ),
    out: str | None = typer.Option(None, "--out", help="Optional path to save the JSON report"),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON instead of table"),
) -> None:
    """Classify artifact files into retention categories (Sprint 26). No mutations (I-160).

    execution_enabled=False, delete_eligible=False are guaranteed invariants (I-154, I-161).
    Protected artifacts are marked as 'protected' and skipped in rotation (I-155).
    """
    import json as _json
    from pathlib import Path

    from app.research.artifact_lifecycle import build_retention_report

    artifacts_path = Path(artifacts_dir)
    resolved_state = Path(state_path)
    active_route_active = resolved_state.exists()

    report = build_retention_report(
        artifacts_path,
        stale_after_days=stale_after_days,
        active_route_active=active_route_active,
    )
    payload = report.to_json_dict()

    if json_output:
        typer.echo(_json.dumps(payload, indent=2))
    else:
        table = Table(title="Artifact Retention Report")
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        table.add_row("Total", str(payload.get("total_count", 0)))
        table.add_row("Protected", str(payload.get("protected_count", 0)))
        table.add_row("Rotatable", str(payload.get("rotatable_count", 0)))
        table.add_row("Review Required", str(payload.get("review_required_count", 0)))
        table.add_row("execution_enabled", str(payload.get("execution_enabled", False)))
        table.add_row("delete_eligible_count", str(payload.get("delete_eligible_count", 0)))
        console.print(table)

        for entry in report.entries:
            if entry.protected:
                console.print(f"  [cyan]protected[/cyan]: {entry.name}")

    if out:
        from pathlib import Path as _Path
        out_path = _Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_json.dumps(payload, indent=2), encoding="utf-8")
        console.print(f"[dim]Saved retention report to {out_path.resolve()}[/dim]")


@research_readiness_app.command("cleanup-eligibility-summary")
def research_cleanup_eligibility_summary(
    artifacts_dir: str = typer.Option(
        "artifacts",
        "--artifacts-dir",
        help="Path to artifacts directory",
    ),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days", help="Stale threshold"),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state path",
    ),
) -> None:
    """Print cleanup/archive eligibility derived from the retention report (Sprint 26)."""
    from pathlib import Path

    from app.research.artifact_lifecycle import (
        build_cleanup_eligibility_summary,
        build_retention_report,
    )

    artifacts_path = Path(artifacts_dir)
    resolved_state = Path(state_path)
    report = build_retention_report(
        artifacts_path,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    summary = build_cleanup_eligibility_summary(report)
    payload = summary.to_json_dict()

    table = Table(title="Cleanup Eligibility Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Cleanup Eligible", str(payload.get("cleanup_eligible_count", 0)))
    table.add_row("Protected", str(payload.get("protected_count", 0)))
    table.add_row("Review Required", str(payload.get("review_required_count", 0)))
    table.add_row("Dry Run Default", str(payload.get("dry_run_default", True)))
    table.add_row("Delete Eligible", str(payload.get("delete_eligible_count", 0)))
    console.print(table)
    for candidate in summary.candidates:
        console.print(f"  eligible: {candidate.name}")


@research_readiness_app.command("protected-artifact-summary")
def research_protected_artifact_summary(
    artifacts_dir: str = typer.Option(
        "artifacts",
        "--artifacts-dir",
        help="Path to artifacts directory",
    ),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
    ),
) -> None:
    """Print protected artifact summary derived from the retention report (Sprint 26)."""
    from pathlib import Path

    from app.research.artifact_lifecycle import (
        build_protected_artifact_summary,
        build_retention_report,
    )

    artifacts_path = Path(artifacts_dir)
    resolved_state = Path(state_path)
    report = build_retention_report(
        artifacts_path,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    summary = build_protected_artifact_summary(report)
    payload = summary.to_json_dict()

    table = Table(title="Protected Artifact Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Protected Count", str(payload.get("protected_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)
    for entry in summary.entries:
        console.print(f"  protected: {entry.name}")


@research_readiness_app.command("review-required-summary")
def research_review_required_summary(
    artifacts_dir: str = typer.Option(
        "artifacts",
        "--artifacts-dir",
        help="Path to artifacts directory",
    ),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
    ),
) -> None:
    """Print review-required artifact summary derived from the retention report (Sprint 26)."""
    from pathlib import Path

    from app.research.artifact_lifecycle import (
        build_retention_report,
        build_review_required_summary,
    )

    artifacts_path = Path(artifacts_dir)
    resolved_state = Path(state_path)
    report = build_retention_report(
        artifacts_path,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    summary = build_review_required_summary(report)
    payload = summary.to_json_dict()

    table = Table(title="Review Required Artifact Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Review Required Count", str(payload.get("review_required_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)
    for entry in summary.entries:
        console.print(f"  review_required: {entry.name}")


# ---------------------------------------------------------------------------
# Sprint 27: escalation-summary / blocking-summary / operator-action-summary
# ---------------------------------------------------------------------------


def _build_escalation_from_readiness_artifacts(
    handoff_path: str | None = None,
    state_path: str = "artifacts/active_route_profile.json",
    alert_audit_dir: str = "artifacts",
    artifacts_dir: str = "artifacts",
    stale_after_days: float = 30.0,
) -> Any:
    """Shared helper: build escalation summary from readiness artifacts."""
    from pathlib import Path

    from app.alerts.audit import load_alert_audits
    from app.research.active_route import load_active_route_state
    from app.research.artifact_lifecycle import (
        build_retention_report,
        build_review_required_summary,
    )
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_handoff_acknowledgements, load_signal_handoffs
    from app.research.operational_readiness import (
        ArtifactRef,
        OperationalArtifactRefs,
        build_operational_escalation_summary,
        build_operational_readiness_report,
    )

    handoffs = []
    if handoff_path and Path(handoff_path).exists():
        handoffs = load_signal_handoffs(Path(handoff_path))

    ack_path = Path("artifacts/consumer_acknowledgements.jsonl")
    acknowledgements = load_handoff_acknowledgements(ack_path) if ack_path.exists() else []
    collector_summary = build_handoff_collector_summary(handoffs, acknowledgements)

    resolved_state = Path(state_path)
    active_route_state = (
        load_active_route_state(resolved_state) if resolved_state.exists() else None
    )

    alert_dir = Path(alert_audit_dir)
    alert_audits = load_alert_audits(alert_dir) if alert_dir.exists() else []

    report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=alert_audits,
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=OperationalArtifactRefs(
            active_route_state=ArtifactRef(
                path=str(resolved_state), present=resolved_state.exists()
            ),
            alert_audit_dir=ArtifactRef(path=str(alert_dir), present=alert_dir.exists()),
        ),
    )

    artifacts_path = Path(artifacts_dir)
    retention_report = build_retention_report(
        artifacts_path,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    review_required_summary = build_review_required_summary(retention_report)
    return build_operational_escalation_summary(
        report, review_required_summary=review_required_summary
    )


