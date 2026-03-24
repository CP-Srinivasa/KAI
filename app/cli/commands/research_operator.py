"""Operator action and decision commands.

Covers: escalation, blocking, actions, decision-pack, daily summary,
runbook, review journal, resolution, and alert audit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from app.cli.commands.research_readiness import _build_escalation_from_readiness_artifacts

console = Console()
research_operator_app = typer.Typer()


@research_operator_app.command("escalation-summary")
def research_escalation_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print operational escalation summary (Sprint 27)."""
    escalation = _build_escalation_from_readiness_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    payload = escalation.to_json_dict()
    table = Table(title="Escalation Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Escalation Status", payload.get("escalation_status", ""))
    table.add_row("Blocking Count", str(payload.get("blocking_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


@research_operator_app.command("blocking-summary")
def research_blocking_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print blocking-only slice of the escalation summary (Sprint 27)."""
    from app.research.operational_readiness import build_blocking_summary

    escalation = _build_escalation_from_readiness_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    summary = build_blocking_summary(escalation)
    payload = summary.to_json_dict()
    table = Table(title="Blocking Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Escalation Status", str(payload.get("escalation_status", "")))
    table.add_row("Blocking Count", str(payload.get("blocking_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


@research_operator_app.command("operator-action-summary")
def research_operator_action_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print operator-action-required slice of the escalation summary (Sprint 27)."""
    from app.research.operational_readiness import build_operator_action_summary

    escalation = _build_escalation_from_readiness_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    summary = build_operator_action_summary(escalation)
    payload = summary.to_json_dict()
    table = Table(title="Operator Action Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Operator Action Count", str(payload.get("operator_action_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


# ---------------------------------------------------------------------------
# Sprint 28: action-queue-summary / blocking-actions / prioritized-actions /
#            review-required-actions
# ---------------------------------------------------------------------------


def _build_action_queue_from_escalation(
    handoff_path: str | None = None,
    state_path: str = "artifacts/active_route_profile.json",
    alert_audit_dir: str = "artifacts",
    artifacts_dir: str = "artifacts",
    stale_after_days: float = 30.0,
) -> Any:
    """Shared helper: build action queue summary from escalation."""
    from app.research.operational_readiness import build_action_queue_summary

    escalation = _build_escalation_from_readiness_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    return build_action_queue_summary(escalation)


@research_operator_app.command("action-queue-summary")
def research_action_queue_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print prioritized operator action queue (Sprint 28)."""
    action_queue = _build_action_queue_from_escalation(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    payload = action_queue.to_json_dict()
    table = Table(title="Action Queue Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Queue Status", payload.get("queue_status", ""))
    table.add_row("Total", str(payload.get("total_count", 0)))
    table.add_row("Blocking", str(payload.get("blocking_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


@research_operator_app.command("blocking-actions")
def research_blocking_actions(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print blocking-only action queue items (Sprint 28)."""
    from app.research.operational_readiness import build_blocking_actions

    action_queue = _build_action_queue_from_escalation(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    summary = build_blocking_actions(action_queue)
    payload = summary.to_json_dict()
    table = Table(title="Blocking Actions")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Blocking Count", str(payload.get("blocking_count", 0)))
    table.add_row("Queue Status", str(payload.get("queue_status", "")))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


@research_operator_app.command("prioritized-actions")
def research_prioritized_actions(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print operator action queue in priority order (Sprint 28)."""
    from app.research.operational_readiness import build_prioritized_actions

    action_queue = _build_action_queue_from_escalation(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    summary = build_prioritized_actions(action_queue)
    payload = summary.to_json_dict()
    table = Table(title="Prioritized Actions")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Queue Status", str(payload.get("queue_status", "")))
    table.add_row("Action Count", str(payload.get("action_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


@research_operator_app.command("review-required-actions")
def research_review_required_actions(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print review-required items from the operator action queue (Sprint 28)."""
    from app.research.operational_readiness import build_review_required_actions

    action_queue = _build_action_queue_from_escalation(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    summary = build_review_required_actions(action_queue)
    payload = summary.to_json_dict()
    table = Table(title="Review Required Actions")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Review Required Count", str(payload.get("review_required_count", 0)))
    table.add_row("Queue Status", str(payload.get("queue_status", "")))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


# ---------------------------------------------------------------------------
# Sprint 29: decision-pack-summary
# ---------------------------------------------------------------------------


def _build_decision_pack_from_artifacts(
    handoff_path: str | None = None,
    state_path: str = "artifacts/active_route_profile.json",
    alert_audit_dir: str = "artifacts",
    artifacts_dir: str = "artifacts",
    stale_after_days: float = 30.0,
) -> Any:
    """Shared helper: build operator decision pack from readiness artifacts."""
    from pathlib import Path

    from app.research.artifact_lifecycle import (
        build_retention_report,
        build_review_required_summary,
    )
    from app.research.operational_readiness import (
        build_action_queue_summary,
        build_blocking_summary,
        build_operator_decision_pack,
    )

    escalation = _build_escalation_from_readiness_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    blocking_summary = build_blocking_summary(escalation)
    action_queue_summary = build_action_queue_summary(escalation)

    resolved_state = Path(state_path)
    artifacts_path = Path(artifacts_dir)
    retention_report = build_retention_report(
        artifacts_path,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    review_required_summary = build_review_required_summary(retention_report)

    # Build a minimal readiness report for the pack
    from app.alerts.audit import load_alert_audits
    from app.research.active_route import load_active_route_state
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_handoff_acknowledgements, load_signal_handoffs
    from app.research.operational_readiness import (
        ArtifactRef,
        OperationalArtifactRefs,
        build_operational_readiness_report,
    )

    handoffs = []
    if handoff_path and Path(handoff_path).exists():
        handoffs = load_signal_handoffs(Path(handoff_path))
    ack_path = Path("artifacts/consumer_acknowledgements.jsonl")
    acknowledgements = load_handoff_acknowledgements(ack_path) if ack_path.exists() else []
    collector_summary = build_handoff_collector_summary(handoffs, acknowledgements)
    r_state = Path(state_path)
    active_route_state = load_active_route_state(r_state) if r_state.exists() else None
    alert_dir = Path(alert_audit_dir)
    alert_audits = load_alert_audits(alert_dir) if alert_dir.exists() else []

    readiness_report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=alert_audits,
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=OperationalArtifactRefs(
            active_route_state=ArtifactRef(path=str(r_state), present=r_state.exists()),
        ),
    )

    return build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking_summary,
        action_queue_summary=action_queue_summary,
        review_required_summary=review_required_summary,
    )


@research_operator_app.command("operator-decision-pack")
@research_operator_app.command("decision-pack-summary")
def research_decision_pack_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
    out: str | None = typer.Option(None, "--out", help="Optional path to save the JSON report"),
) -> None:
    """Print operator decision pack summary (Sprint 29). Advisory only — no execution authority."""
    from pathlib import Path

    from app.research.operational_readiness import save_operator_decision_pack

    pack = _build_decision_pack_from_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    payload = pack.to_json_dict()

    table = Table(title="Operator Decision Pack Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Overall Status", payload.get("overall_status", ""))
    table.add_row("Blocking Count", str(payload.get("blocking_count", 0)))
    table.add_row("Review Required", str(payload.get("review_required_count", 0)))
    table.add_row("Action Queue Count", str(payload.get("action_queue_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)

    if out:
        out_path = Path(out)
        save_operator_decision_pack(pack, out_path)
        console.print(f"[dim]Saved decision pack to {out_path.resolve()}[/dim]")


# ---------------------------------------------------------------------------
# Sprint 45: daily-summary
# ---------------------------------------------------------------------------


@research_operator_app.command("daily-summary")
def research_daily_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_hours: int = typer.Option(24, "--stale-after-hours"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
    loop_audit_path: str = typer.Option(
        "artifacts/trading_loop_audit.jsonl",
        "--loop-audit-path",
    ),
    loop_last_n: int = typer.Option(50, "--loop-last-n"),
    portfolio_audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--portfolio-audit-path",
    ),
    market_data_provider: str = typer.Option("coingecko", "--market-data-provider"),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
    ),
    timeout_seconds: int = typer.Option(10, "--timeout-seconds"),
    review_journal_path: str = typer.Option(
        "artifacts/operator_review_journal.jsonl",
        "--review-journal-path",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print canonical JSON payload"),
) -> None:
    """Print canonical daily operator summary derived from existing read-only surfaces."""
    import asyncio
    import json as _json

    from app.agents.mcp_server import get_daily_operator_summary

    payload = asyncio.run(
        get_daily_operator_summary(
            handoff_path=handoff_path,
            state_path=state_path,
            alert_audit_dir=alert_audit_dir,
            artifacts_dir=artifacts_dir,
            stale_after_hours=stale_after_hours,
            retention_stale_after_days=stale_after_days,
            loop_audit_path=loop_audit_path,
            loop_last_n=loop_last_n,
            portfolio_audit_path=portfolio_audit_path,
            market_data_provider=market_data_provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
            review_journal_path=review_journal_path,
        )
    )

    if as_json:
        console.print(_json.dumps(payload, indent=2))
        return

    cycle_status = payload.get("last_cycle_status")
    cycle_symbol = payload.get("last_cycle_symbol")
    cycle_at = payload.get("last_cycle_at")
    cycle_suffix = "last: none"
    if isinstance(cycle_status, str) and cycle_status:
        cycle_suffix = f"last: {cycle_status}"
        if isinstance(cycle_symbol, str) and cycle_symbol:
            cycle_suffix += f" | {cycle_symbol}"
        if isinstance(cycle_at, str) and cycle_at:
            cycle_suffix += f" | {cycle_at}"

    exposure_pct = payload.get("total_exposure_pct", 0.0)
    if isinstance(exposure_pct, (int, float)):
        exposure_text = f"{float(exposure_pct):.2f}%"
    else:
        exposure_text = "0.00%"

    console.print("[bold]Daily Operator View[/bold]")
    console.print(f"Readiness:      {payload.get('readiness_status', 'unknown')}")
    console.print(f"Cycles today:   {payload.get('cycle_count_today', 0)}  ({cycle_suffix})")
    console.print(
        "Portfolio:      "
        f"{payload.get('position_count', 0)} positions"
        f" | {exposure_text} exposure"
        f" | MTM: {payload.get('mark_to_market_status', 'unknown')}"
    )
    console.print(f"Decision Pack:  {payload.get('decision_pack_status', 'unknown')}")
    console.print(f"Incidents:      {payload.get('open_incidents', 0)} open")
    console.print(f"Aggregated at:  {payload.get('aggregated_at', 'unknown')}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


# ---------------------------------------------------------------------------
# Sprint 30: operator-runbook / runbook-summary / runbook-next-steps
# ---------------------------------------------------------------------------


FINAL_RESEARCH_COMMAND_NAMES: tuple[str, ...] = (
    "signal-handoff",
    "handoff-acknowledge",
    "handoff-collector-summary",
    "readiness-summary",
    "provider-health",
    "drift-summary",
    "gate-summary",
    "remediation-recommendations",
    "artifact-inventory",
    "artifact-rotate",
    "artifact-retention",
    "cleanup-eligibility-summary",
    "protected-artifact-summary",
    "review-required-summary",
    "escalation-summary",
    "blocking-summary",
    "operator-action-summary",
    "action-queue-summary",
    "blocking-actions",
    "prioritized-actions",
    "review-required-actions",
    "decision-pack-summary",
    "daily-summary",
    "operator-runbook",
    "runbook-summary",
    "runbook-next-steps",
    "review-journal-append",
    "review-journal-summary",
    "resolution-summary",
    "market-data-quote",
    "market-data-snapshot",
    "paper-portfolio-snapshot",
    "paper-positions-summary",
    "paper-exposure-summary",
    "trading-loop-status",
    "trading-loop-recent-cycles",
    "trading-loop-run-once",
    "alert-audit-summary",
)

RESEARCH_COMMAND_ALIASES: dict[str, str] = {
    "consumer-ack": "handoff-acknowledge",
    "handoff-summary": "handoff-collector-summary",
    "operator-decision-pack": "decision-pack-summary",
    "loop-cycle-summary": "trading-loop-recent-cycles",
}

SUPERSEDED_RESEARCH_COMMAND_NAMES: tuple[str, ...] = ("governance-summary",)


def extract_runbook_command_refs(payload: dict[str, Any]) -> list[str]:
    """Extract all command_refs from a runbook payload (used by MCP server validation)."""
    refs: list[str] = []
    for step in payload.get("steps", []):
        refs.extend(step.get("command_refs", []))
    for step in payload.get("next_steps", []):
        refs.extend(step.get("command_refs", []))
    refs.extend(payload.get("command_refs", []))
    return list(dict.fromkeys(refs))  # deduplicated, order preserved


def get_invalid_research_command_refs(refs: list[str]) -> list[str]:
    """Return any command refs that are not registered research sub-commands.

    Resolves registered names lazily from the aggregated research_app to avoid
    circular imports with app.cli.research.
    """
    # Lazy import to avoid circular dependency: research_operator <- research (aggregator)
    from app.cli.research import get_registered_research_command_names

    registered = get_registered_research_command_names()
    invalid_refs: list[str] = []
    for ref in refs:
        parts = ref.strip().split()
        if len(parts) != 2 or parts[0] != "research" or parts[1] not in registered:
            invalid_refs.append(ref)
    return invalid_refs


def _require_valid_runbook_command_refs(payload: dict[str, Any]) -> None:
    """Fail closed when runbook payload references non-canonical CLI commands."""
    invalid_refs = get_invalid_research_command_refs(extract_runbook_command_refs(payload))
    if invalid_refs:
        console.print(f"[red]Runbook contains invalid command references: {invalid_refs}[/red]")
        raise typer.Exit(1)


def _build_runbook_from_artifacts(
    handoff_path: str | None = None,
    state_path: str = "artifacts/active_route_profile.json",
    alert_audit_dir: str = "artifacts",
    artifacts_dir: str = "artifacts",
    stale_after_days: float = 30.0,
) -> Any:
    """Shared helper: build operator runbook from readiness artifacts."""
    from app.research.operational_readiness import build_operator_runbook

    pack = _build_decision_pack_from_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    return build_operator_runbook(decision_pack=pack)


def _load_review_journal_summary(
    journal_path: str = "artifacts/operator_review_journal.jsonl",
) -> Any:
    from app.research.operational_readiness import (
        build_review_journal_summary,
        load_review_journal_entries,
    )

    path = Path(journal_path)
    entries = load_review_journal_entries(path)
    return build_review_journal_summary(entries, journal_path=path)


def _load_review_resolution_summary(
    journal_path: str = "artifacts/operator_review_journal.jsonl",
) -> Any:
    from app.research.operational_readiness import build_review_resolution_summary

    summary = _load_review_journal_summary(journal_path=journal_path)
    return build_review_resolution_summary(summary)


@research_operator_app.command("operator-runbook")
def research_operator_runbook(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
    out: str | None = typer.Option(None, "--out", help="Optional path to save the JSON runbook"),
) -> None:
    """Print canonical operator runbook with validated commands (Sprint 30)."""
    from pathlib import Path

    from app.research.operational_readiness import save_operator_runbook

    runbook = _build_runbook_from_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    payload = runbook.to_json_dict()
    _require_valid_runbook_command_refs(payload)

    console.print("[bold]Operator Runbook[/bold]")
    console.print(f"status={runbook.overall_status}")
    console.print(f"steps={len(runbook.steps)}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")

    for i, step in enumerate(runbook.steps, 1):
        console.print(f"\n[cyan]{i}. priority={step.priority}[/cyan]  {step.title}")
        console.print(f"   {step.summary}")
        for ref in step.command_refs:
            console.print(f"   Command: {ref}")

    if out:
        out_path = Path(out)
        save_operator_runbook(runbook, out_path)
        console.print(f"\n[dim]Saved runbook to {out_path.resolve()}[/dim]")


@research_operator_app.command("runbook-summary")
def research_runbook_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print a compact operator runbook summary (Sprint 30)."""
    runbook = _build_runbook_from_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    _require_valid_runbook_command_refs(runbook.to_json_dict())
    console.print("[bold]Operator Runbook Summary[/bold]")
    console.print(f"status={runbook.overall_status}")
    console.print(f"steps={len(runbook.steps)}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_operator_app.command("runbook-next-steps")
def research_runbook_next_steps(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print the next-steps runbook surface (Sprint 30)."""
    runbook = _build_runbook_from_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    _require_valid_runbook_command_refs(runbook.to_json_dict())
    console.print("[bold]Operator Runbook Next Steps[/bold]")
    console.print(f"status={runbook.overall_status}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")
    for i, step in enumerate(runbook.next_steps, 1):
        console.print(f"\n{i}. priority={step.priority}  {step.title}")
        for ref in step.command_refs:
            console.print(f"   Command: {ref}")


@research_operator_app.command("review-journal-append")
def research_review_journal_append(
    source_ref: str = typer.Argument(..., help="Referenced runbook/action/decision-pack source"),
    operator_id: str = typer.Option(..., "--operator-id", help="Operator identifier"),
    review_action: str = typer.Option(..., "--review-action", help="One of: note, defer, resolve"),
    review_note: str = typer.Option(..., "--review-note", help="Append-only review note"),
    evidence_refs: Annotated[
        list[str] | None,
        typer.Option(
            "--evidence-ref",
            help="Optional evidence reference; repeat flag for multiple values",
        ),
    ] = None,
    journal_path: str = typer.Option(
        "artifacts/operator_review_journal.jsonl",
        "--journal-path",
        help="Append-only review journal JSONL path",
    ),
) -> None:
    """Append a review journal entry without mutating core operator state."""
    from app.research.operational_readiness import (
        append_review_journal_entry_jsonl,
        create_review_journal_entry,
    )

    entry = create_review_journal_entry(
        source_ref=source_ref,
        operator_id=operator_id,
        review_action=review_action,
        review_note=review_note,
        evidence_refs=list(evidence_refs or []),
    )
    out_path = Path(journal_path)
    append_review_journal_entry_jsonl(entry, out_path)

    console.print(f"[green]Review journal appended to {out_path.resolve()}[/green]")
    console.print(f"review_id={entry.review_id}")
    console.print(f"journal_status={entry.journal_status}")
    console.print("core_state_unchanged=True")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_operator_app.command("review-journal-summary")
def research_review_journal_summary(
    journal_path: str = typer.Option(
        "artifacts/operator_review_journal.jsonl",
        "--journal-path",
        help="Append-only review journal JSONL path",
    ),
) -> None:
    """Print the append-only review journal summary."""
    summary = _load_review_journal_summary(journal_path=journal_path)
    console.print("[bold]Operator Review Journal Summary[/bold]")
    console.print(f"journal_status={summary.journal_status}")
    console.print(f"total_count={summary.total_count}")
    console.print(f"open_count={summary.open_count}")
    console.print(f"resolved_count={summary.resolved_count}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_operator_app.command("resolution-summary")
def research_resolution_summary(
    journal_path: str = typer.Option(
        "artifacts/operator_review_journal.jsonl",
        "--journal-path",
        help="Append-only review journal JSONL path",
    ),
) -> None:
    """Print latest per-source resolution state from the review journal."""
    summary = _load_review_resolution_summary(journal_path=journal_path)
    console.print("[bold]Operator Resolution Summary[/bold]")
    console.print(f"journal_status={summary.journal_status}")
    console.print(f"open_count={summary.open_count}")
    console.print(f"resolved_count={summary.resolved_count}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")
    for source_ref in summary.open_source_refs:
        console.print(f"open={source_ref}")
    for source_ref in summary.resolved_source_refs:
        console.print(f"resolved={source_ref}")


@research_operator_app.command("alert-audit-summary")
def research_alert_audit_summary(
    audit_dir: str = typer.Option(
        "artifacts",
        "--audit-dir",
        help="Directory containing alert_audit.jsonl",
    ),
) -> None:
    """Print operator-facing alert audit summary (read-only)."""
    import asyncio

    from app.agents.mcp_server import get_alert_audit_summary

    result = asyncio.run(get_alert_audit_summary(audit_dir=audit_dir))
    console.print("[bold]Operator Alert Audit Summary[/bold]")
    console.print(f"total_count={result.get('total_count', 0)}")
    console.print(f"digest_count={result.get('digest_count', 0)}")
    console.print(f"latest_dispatched_at={result.get('latest_dispatched_at', 'none')}")
    by_channel = result.get("by_channel", {})
    if isinstance(by_channel, dict):
        for channel, count in by_channel.items():
            console.print(f"channel_{channel}={count}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")
