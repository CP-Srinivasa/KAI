"""Audit-specific CLI commands.

Provides the ``audit`` command group (``trading-bot audit <cmd>``) for the
structured-reasoning trail:

  trading-bot audit trail   <decision_id>     [--journal PATH]
  trading-bot audit verify                    [--journal PATH]

The trail joins three streams:
  1. ``artifacts/structured_reasoning.jsonl``  (this CLI's primary input)
  2. ``artifacts/decision_journal.jsonl``      (final DecisionRecord, if any)
  3. ``artifacts/bayes_confidence_audit.jsonl`` (raw Bayes report, if any)

All read-only. No write commands here — operator writes happen via
``trading-bot learning approve`` / ``reject`` / ``rollback``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, cast

import typer
from rich.console import Console
from rich.table import Table

from app.audit.structured_reasoning import (
    DEFAULT_REASONING_JOURNAL_PATH,
    ReasoningJournal,
    ReasoningStep,
)

console = Console()

audit_app = typer.Typer(
    name="audit",
    help="Read-only access to the structured reasoning trail",
    no_args_is_help=True,
)

JournalPath = Annotated[
    Path,
    typer.Option(
        "--journal",
        "-j",
        help="Override path to structured_reasoning.jsonl",
        envvar="KAI_REASONING_JOURNAL",
    ),
]


_PHASE_STYLES: dict[str, str] = {
    "trigger": "blue",
    "evidence": "cyan",
    "scoring": "magenta",
    "risk_adjustment": "yellow",
    "confidence_change": "green",
    "invalidation": "red",
}


def _phase_label(phase: str) -> str:
    style = _PHASE_STYLES.get(phase, "")
    return f"[{style}]{phase}[/{style}]" if style else phase


def _format_kv(payload: dict[str, object]) -> str:
    if not payload:
        return "-"
    parts = [f"{k}={v}" for k, v in payload.items()]
    return ", ".join(parts)


def _decision_journal_lookup(decision_id: str) -> dict[str, object] | None:
    """Best-effort lookup in the canonical decision journal."""
    path = Path("artifacts/decision_journal.jsonl")
    if not path.exists():
        return None
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("decision_id") == decision_id:
                return cast(dict[str, object], obj)
    except OSError:
        pass
    return None


def _bayes_audit_lookup(decision_id: str) -> list[dict[str, object]]:
    """Best-effort lookup in the Bayes confidence audit (raw report rows)."""
    path = Path("artifacts/bayes_confidence_audit.jsonl")
    if not path.exists():
        return []
    out: list[dict[str, object]] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if obj.get("decision_id") == decision_id:
                out.append(obj)
    except OSError:
        pass
    return out


# ─── trail ────────────────────────────────────────────────────────────────────


@audit_app.command("trail")
def trail(
    decision_id: Annotated[str, typer.Argument(help="The decision_id (dec_*) to inspect")],
    journal: JournalPath = DEFAULT_REASONING_JOURNAL_PATH,
) -> None:
    """Show the full structured reasoning trail for a decision_id."""
    rj = ReasoningJournal(journal)
    steps: list[ReasoningStep] = rj.steps_for_decision(decision_id)

    if not steps:
        console.print(f"[yellow]No reasoning steps for {decision_id} in {journal}.[/yellow]")
        # Continue to show cross-stream lookups even when reasoning is empty
        # — the operator may want to see the canonical record anyway.

    if steps:
        table = Table(title=f"Reasoning trail — {decision_id}", show_lines=False)
        table.add_column("ts_utc")
        table.add_column("phase")
        table.add_column("actor")
        table.add_column("Δ confidence")
        table.add_column("rationale")
        for step in steps:
            delta = "-"
            if step.confidence_before is not None and step.confidence_after is not None:
                delta = f"{step.confidence_before:.4f} → {step.confidence_after:.4f}"
            table.add_row(
                step.timestamp_utc[:19],
                _phase_label(step.phase),
                step.actor,
                delta,
                step.rationale_summary[:80],
            )
        console.print(table)

    # Cross-stream: canonical decision record
    record = _decision_journal_lookup(decision_id)
    if record is not None:
        console.print(
            f"[bold]Decision Journal[/bold]   "
            f"symbol={record.get('symbol')}  "
            f"mode={record.get('mode')}  "
            f"approval={record.get('approval_state')}  "
            f"execution={record.get('execution_state')}"
        )

    # Cross-stream: Bayes audit
    bayes_rows = _bayes_audit_lookup(decision_id)
    if bayes_rows:
        for row in bayes_rows:
            r = row.get("report") or {}
            if not isinstance(r, dict):
                continue
            console.print(
                f"[bold]Bayes Audit[/bold]        "
                f"prior={r.get('prior_probability')}  "
                f"posterior={r.get('posterior_probability')}  "
                f"confidence={r.get('confidence_score')}  "
                f"uncertainty={r.get('uncertainty_score')}"
            )

    if not steps and record is None and not bayes_rows:
        console.print(f"[red]No data found for {decision_id} in any stream.[/red]")
        raise typer.Exit(1)


# ─── verify ───────────────────────────────────────────────────────────────────


@audit_app.command("verify")
def verify(
    journal: JournalPath = DEFAULT_REASONING_JOURNAL_PATH,
) -> None:
    """Verify the hash-chain integrity of the structured-reasoning journal."""
    rj = ReasoningJournal(journal)
    ok, err = rj.verify_chain()
    if ok:
        console.print("[green]reasoning chain ok[/green]")
        raise typer.Exit(0)
    console.print(f"[red]reasoning chain BROKEN[/red]: {err}")
    raise typer.Exit(1)


# ─── list (utility: most recent decision_ids) ────────────────────────────────


@audit_app.command("list")
def list_recent(
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="How many distinct decision_ids to show"),
    ] = 20,
    phase: Annotated[
        str | None,
        typer.Option("--phase", "-p", help="Filter by reasoning phase"),
    ] = None,
    journal: JournalPath = DEFAULT_REASONING_JOURNAL_PATH,
) -> None:
    """List recent decision_ids that appear in the reasoning trail."""
    rj = ReasoningJournal(journal)
    seen: dict[str, ReasoningStep] = {}
    for step in rj.iter_steps():
        if phase is not None and step.phase != phase:
            continue
        seen[step.decision_id] = step  # last step per decision_id wins
    items = list(seen.items())[-limit:]
    if not items:
        console.print("[dim]No reasoning steps found.[/dim]")
        raise typer.Exit(0)
    table = Table(title=f"Recent decisions ({len(items)})", show_lines=False)
    table.add_column("decision_id")
    table.add_column("last_ts_utc")
    table.add_column("last_phase")
    table.add_column("actor")
    for decision_id, step in items:
        table.add_row(
            decision_id,
            step.timestamp_utc[:19],
            _phase_label(step.phase),
            step.actor,
        )
    console.print(table)


__all__ = ["audit_app"]
