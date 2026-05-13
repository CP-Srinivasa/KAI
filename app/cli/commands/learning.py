"""Learning-specific CLI commands.

Provides the ``learning`` command group (``trading-bot learning <cmd>``).
All commands are read-only or operator-gated writes against the hash-chained
parameter journal at ``artifacts/learning/parameter_journal.jsonl``.

Operator workflow:

  trading-bot learning list
  trading-bot learning show pv_<version_id>
  trading-bot learning approve <path> pv_<id> --operator <name> [--notes ...]
  trading-bot learning reject  <path> pv_<id> --operator <name> --reason ...
  trading-bot learning rollback <path> pv_<id> --operator <name> --notes ...
  trading-bot learning verify
  trading-bot learning active   [--path PATH]
  trading-bot learning history  PATH
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from app.learning.active_threshold import DEFAULT_MIN_BAYES_CONFIDENCE_PATH
from app.learning.approval import (
    STATUS_ACTIVE,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SUPERSEDED,
    ApprovalService,
)
from app.learning.config_snapshot import DEFAULT_SNAPSHOT_DIR
from app.learning.parameter_version import (
    DEFAULT_PARAMETER_JOURNAL_PATH,
    ParameterVersionStore,
)
from app.learning.threshold_observations import (
    DEFAULT_SCORE_FIELD,
    observations_from_audit,
)
from app.learning.threshold_optimizer import (
    DEFAULT_GRID,
    ThresholdConfig,
    optimize_threshold,
)
from app.signals.bayes_journal import DEFAULT_BAYES_AUDIT_PATH

console = Console()

learning_app = typer.Typer(
    name="learning",
    help="Operator approval surface for the adaptive learning journal",
    no_args_is_help=True,
)


_STATUS_STYLES: dict[str, str] = {
    STATUS_PENDING: "yellow",
    STATUS_ACTIVE: "green bold",
    STATUS_SUPERSEDED: "dim",
    STATUS_REJECTED: "red",
}

JournalPath = Annotated[
    Path,
    typer.Option(
        "--journal",
        "-j",
        help="Override journal path (default: artifacts/learning/parameter_journal.jsonl)",
        envvar="KAI_LEARNING_JOURNAL",
    ),
]

SnapshotDir = Annotated[
    Path,
    typer.Option(
        "--snapshot-dir",
        "-s",
        help="Snapshot directory (default: config/learning)",
        envvar="KAI_LEARNING_SNAPSHOT_DIR",
    ),
]


def _service(journal: Path, snapshot_dir: Path | None = None) -> ApprovalService:
    return ApprovalService(
        ParameterVersionStore(journal), snapshot_dir=snapshot_dir
    )


# ─── list ─────────────────────────────────────────────────────────────────────


@learning_app.command("list")
def list_proposals(
    path: Annotated[
        str | None,
        typer.Option("--path", "-p", help="Filter by parameter_path"),
    ] = None,
    pending_only: Annotated[
        bool,
        typer.Option("--pending", help="Show only pending proposals"),
    ] = False,
    journal: JournalPath = DEFAULT_PARAMETER_JOURNAL_PATH,
) -> None:
    """List proposals (optionally filtered by path or pending status)."""
    svc = _service(journal)
    proposals = (
        svc.list_pending(parameter_path=path)
        if pending_only
        else svc.list_proposals(parameter_path=path)
    )
    if not proposals:
        console.print("[dim]No proposals found.[/dim]")
        raise typer.Exit(0)

    table = Table(title="Parameter version proposals", show_lines=False)
    table.add_column("version_id")
    table.add_column("path")
    table.add_column("status")
    table.add_column("created_at_utc")
    table.add_column("created_by")
    table.add_column("parent")

    for ps in proposals:
        p = ps.proposal
        style = _STATUS_STYLES.get(ps.status, "")
        table.add_row(
            p.version_id,
            p.parameter_path,
            f"[{style}]{ps.status}[/{style}]" if style else ps.status,
            p.timestamp_utc,
            p.created_by,
            p.parent_version_id or "-",
        )
    console.print(table)


# ─── show ─────────────────────────────────────────────────────────────────────


@learning_app.command("show")
def show_proposal(
    version_id: Annotated[str, typer.Argument(help="The pv_<id> to inspect")],
    journal: JournalPath = DEFAULT_PARAMETER_JOURNAL_PATH,
) -> None:
    """Show full detail for a single proposal."""
    svc = _service(journal)
    status = svc.get_status(version_id)
    if status is None:
        console.print(f"[red]No proposal with version_id {version_id}[/red]")
        raise typer.Exit(1)
    p = status.proposal
    style = _STATUS_STYLES.get(status.status, "")
    console.print(f"[bold]{p.version_id}[/bold]  path=[cyan]{p.parameter_path}[/cyan]")
    console.print(f"status:        [{style}]{status.status}[/{style}]")
    console.print(f"created_at:    {p.timestamp_utc}")
    console.print(f"created_by:    {p.created_by}")
    console.print(f"parent:        {p.parent_version_id or '-'}")
    if status.activated_at_utc:
        console.print(f"activated_at:  {status.activated_at_utc}")
    if status.rejected_at_utc:
        console.print(f"rejected_at:   {status.rejected_at_utc}")
    if status.superseded_by:
        console.print(f"superseded_by: [yellow]{status.superseded_by}[/yellow]")
    if p.notes:
        console.print(f"notes:         {p.notes}")

    console.print("[bold]parameter_set:[/bold]")
    for k, v in p.parameter_set.items():
        console.print(f"  {k} = {v}")

    if p.evidence:
        console.print("[bold]evidence:[/bold]")
        for k, v in p.evidence.items():
            console.print(f"  {k} = {v}")


# ─── approve / reject / rollback ──────────────────────────────────────────────


@learning_app.command("approve")
def approve(
    parameter_path: Annotated[str, typer.Argument(help="parameter_path of the proposal")],
    version_id: Annotated[str, typer.Argument(help="pv_<id> to activate")],
    operator: Annotated[str, typer.Option("--operator", "-o", help="Operator id")],
    notes: Annotated[
        str | None,
        typer.Option("--notes", "-n", help="Optional approval notes"),
    ] = None,
    journal: JournalPath = DEFAULT_PARAMETER_JOURNAL_PATH,
    snapshot_dir: SnapshotDir = DEFAULT_SNAPSHOT_DIR,
) -> None:
    """Activate a pending proposal (writes a YAML snapshot under snapshot-dir)."""
    svc = _service(journal, snapshot_dir)
    try:
        rec = svc.approve(
            parameter_path=parameter_path,
            version_id=version_id,
            operator_id=operator,
            notes=notes,
        )
    except ValueError as exc:
        console.print(f"[red]Approve refused: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Activated[/green] {rec.version_id} on {rec.parameter_path}")
    console.print(f"[dim]Snapshot: {snapshot_dir}[/dim]")


@learning_app.command("reject")
def reject(
    parameter_path: Annotated[str, typer.Argument(help="parameter_path of the proposal")],
    version_id: Annotated[str, typer.Argument(help="pv_<id> to reject")],
    operator: Annotated[str, typer.Option("--operator", "-o", help="Operator id")],
    reason: Annotated[str, typer.Option("--reason", "-r", help="Required reason")],
    journal: JournalPath = DEFAULT_PARAMETER_JOURNAL_PATH,
) -> None:
    """Mark a pending proposal as rejected with reason."""
    svc = _service(journal)
    try:
        rec = svc.reject(
            parameter_path=parameter_path,
            version_id=version_id,
            operator_id=operator,
            reason=reason,
        )
    except ValueError as exc:
        console.print(f"[red]Reject refused: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[red]Rejected[/red] {rec.version_id} on {rec.parameter_path}")


@learning_app.command("rollback")
def rollback(
    parameter_path: Annotated[str, typer.Argument(help="parameter_path of the proposal")],
    version_id: Annotated[str, typer.Argument(help="pv_<id> to roll back to")],
    operator: Annotated[str, typer.Option("--operator", "-o", help="Operator id")],
    notes: Annotated[str, typer.Option("--notes", "-n", help="Required rollback notes")],
    journal: JournalPath = DEFAULT_PARAMETER_JOURNAL_PATH,
    snapshot_dir: SnapshotDir = DEFAULT_SNAPSHOT_DIR,
) -> None:
    """Roll back to a previously-known proposal of the same path."""
    svc = _service(journal, snapshot_dir)
    try:
        rec = svc.rollback(
            parameter_path=parameter_path,
            version_id=version_id,
            operator_id=operator,
            notes=notes,
        )
    except ValueError as exc:
        console.print(f"[red]Rollback refused: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[yellow]Rolled back to[/yellow] {rec.version_id} on {rec.parameter_path}")
    console.print(f"[dim]Snapshot: {snapshot_dir}[/dim]")


# ─── verify / active / history ────────────────────────────────────────────────


@learning_app.command("verify")
def verify(
    journal: JournalPath = DEFAULT_PARAMETER_JOURNAL_PATH,
) -> None:
    """Verify the hash-chain integrity of the journal."""
    svc = _service(journal)
    ok, err = svc.verify_chain()
    if ok:
        console.print("[green]chain ok[/green]")
        raise typer.Exit(0)
    console.print(f"[red]chain BROKEN[/red]: {err}")
    raise typer.Exit(1)


@learning_app.command("active")
def show_active(
    path: Annotated[
        str | None,
        typer.Option("--path", "-p", help="Show active for this path only"),
    ] = None,
    journal: JournalPath = DEFAULT_PARAMETER_JOURNAL_PATH,
) -> None:
    """Show the currently active version per parameter_path."""
    svc = _service(journal)
    proposals = svc.list_proposals(parameter_path=path)
    actives = [ps for ps in proposals if ps.status == STATUS_ACTIVE]
    if not actives:
        console.print("[dim]No active versions.[/dim]")
        raise typer.Exit(0)
    table = Table(title="Active parameter versions", show_lines=False)
    table.add_column("path")
    table.add_column("version_id")
    table.add_column("activated_at_utc")
    table.add_column("parameter_set")
    for ps in actives:
        params_repr = ", ".join(f"{k}={v}" for k, v in ps.proposal.parameter_set.items())
        table.add_row(
            ps.proposal.parameter_path,
            ps.proposal.version_id,
            ps.activated_at_utc or "-",
            params_repr,
        )
    console.print(table)


@learning_app.command("history")
def history(
    parameter_path: Annotated[str, typer.Argument(help="parameter_path to trace")],
    journal: JournalPath = DEFAULT_PARAMETER_JOURNAL_PATH,
) -> None:
    """Show the full event history for a parameter_path."""
    svc = _service(journal)
    records = svc.history(parameter_path)
    if not records:
        console.print(f"[dim]No history for {parameter_path}.[/dim]")
        raise typer.Exit(0)
    table = Table(title=f"History — {parameter_path}", show_lines=False)
    table.add_column("timestamp_utc")
    table.add_column("event")
    table.add_column("version_id")
    table.add_column("by")
    table.add_column("notes")
    for r in records:
        table.add_row(
            r.timestamp_utc,
            r.record_type,
            r.version_id,
            r.created_by,
            (r.notes or "")[:80],
        )
    console.print(table)


# ─── optimize-threshold ───────────────────────────────────────────────────────


@learning_app.command("optimize-threshold")
def optimize_threshold_cmd(
    parameter_path: Annotated[
        str,
        typer.Option(
            "--path",
            "-p",
            help="Parameter path to optimize",
        ),
    ] = DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
    baseline_threshold: Annotated[
        float,
        typer.Option(
            "--baseline",
            "-b",
            help="Current production threshold (used as a reference)",
        ),
    ] = 0.30,
    loop_audit: Annotated[
        Path,
        typer.Option(
            "--loop-audit",
            help="Path to trading_loop_audit.jsonl",
        ),
    ] = Path("artifacts/trading_loop_audit.jsonl"),
    exec_audit: Annotated[
        Path,
        typer.Option(
            "--exec-audit",
            help="Path to paper_execution_audit.jsonl",
        ),
    ] = Path("artifacts/paper_execution_audit.jsonl"),
    bayes_audit: Annotated[
        Path,
        typer.Option(
            "--bayes-audit",
            help="Path to bayes_confidence_audit.jsonl",
        ),
    ] = DEFAULT_BAYES_AUDIT_PATH,
    score_field: Annotated[
        str,
        typer.Option(
            "--score-field",
            help=(
                "Which Bayes report field to use as the score "
                "(confidence_score | posterior_probability)"
            ),
        ),
    ] = DEFAULT_SCORE_FIELD,
    propose: Annotated[
        bool,
        typer.Option(
            "--propose",
            help=(
                "Write an `approve`-able proposal into the journal if the "
                "optimizer's decision is `approve`. Defaults to dry-run."
            ),
        ),
    ] = False,
    operator: Annotated[
        str | None,
        typer.Option("--operator", "-o", help="Operator id (required with --propose)"),
    ] = None,
    journal: JournalPath = DEFAULT_PARAMETER_JOURNAL_PATH,
) -> None:
    """Run the ThresholdOptimizer on realized paper-trade audits.

    Default is a dry-run that prints the grid + decision. Pass ``--propose
    --operator <name>`` to additionally append the best threshold to the
    hash-chained parameter journal (still requires a follow-up
    ``learning approve`` to activate).
    """
    observations = observations_from_audit(
        loop_audit_path=loop_audit,
        exec_audit_path=exec_audit,
        bayes_audit_path=bayes_audit,
        score_field=score_field,
    )
    cfg = ThresholdConfig(threshold_grid=DEFAULT_GRID)
    report = optimize_threshold(
        observations=observations,
        baseline_threshold=baseline_threshold,
        config=cfg,
    )

    console.print(
        f"[bold]ThresholdOptimizer[/bold]  path=[cyan]{parameter_path}[/cyan]  "
        f"score_field=[cyan]{score_field}[/cyan]  n_obs={report.n_observations}"
    )
    console.print(
        f"baseline={report.baseline_threshold:.4f}  "
        f"n_passing={report.baseline_n_passing}  "
        f"pnl=${report.baseline_pnl_usd:+,.2f}"
    )

    if report.grid:
        table = Table(title="Grid sweep", show_lines=False)
        table.add_column("threshold", justify="right")
        table.add_column("n_passing", justify="right")
        table.add_column("pnl_total_usd", justify="right")
        table.add_column("pnl_mean_usd", justify="right")
        for gp in report.grid:
            table.add_row(
                f"{gp.threshold:.4f}",
                f"{gp.n_passing}",
                f"${gp.pnl_total_usd:+,.2f}",
                f"${gp.pnl_mean_per_trade_usd:+,.2f}",
            )
        console.print(table)

    style = {
        "approve": "green bold",
        "reject": "red",
        "neutral": "yellow",
        "insufficient_data": "dim",
    }.get(report.decision, "")
    console.print(
        f"decision: [{style}]{report.decision}[/{style}]"
        if style
        else f"decision: {report.decision}"
    )
    for reason in report.decision_reasons:
        console.print(f"  • {reason}")

    if not propose:
        return

    if report.decision != "approve":
        console.print(
            f"[yellow]--propose set but decision is `{report.decision}` "
            f"— nothing to write.[/yellow]"
        )
        raise typer.Exit(0)

    if not operator:
        console.print("[red]--operator is required with --propose[/red]")
        raise typer.Exit(2)

    assert report.best_threshold is not None  # narrowed by `approve` decision
    svc = _service(journal)
    proposal = svc.store.propose_version(
        parameter_path=parameter_path,
        parameter_set={"value": report.best_threshold, "default": baseline_threshold},
        evidence={
            "n_observations": report.n_observations,
            "baseline_threshold": report.baseline_threshold,
            "baseline_pnl_usd": report.baseline_pnl_usd,
            "best_threshold": report.best_threshold,
            "best_pnl_usd": report.best_pnl_usd,
            "improvement_usd": report.pnl_improvement_usd,
            "score_field": score_field,
        },
        created_by=operator,
        notes="optimizer auto-proposal (run `learning approve` to activate)",
    )
    console.print(
        f"[green]Proposed[/green] {proposal.version_id} on {parameter_path} "
        f"(value={report.best_threshold:.4f}). "
        f"Activate via `trading-bot learning approve {parameter_path} "
        f"{proposal.version_id} --operator {operator}`."
    )


__all__ = ["learning_app"]
