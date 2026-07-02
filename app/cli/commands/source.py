"""Source-lifecycle operator CLI (``trading-bot source <cmd>``).

Currently exposes the operator-manual **RETIRE** action: a terminal kill of a
source feed. RETIRED is a one-way FSM state (``app.learning.source_lifecycle``)
with no outgoing edges, so once retired a source is never auto-resurrected by the
rotation policy and — via the status-blind onboarding dedup — never re-onboarded.
This is the only path that ever writes RETIRED; the autonomous rotation engine
(``decide_rotation``) never proposes it.

Every retire is FSM-terminal + audited: it appends a ``LifecycleEvent`` to the
lifecycle audit trail so the operator-kill is accountable.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from app.core.enums import SourceStatus
from app.core.settings import get_settings
from app.learning.source_lifecycle_audit import LifecycleEvent, append_lifecycle_event
from app.storage.db.session import build_session_factory
from app.storage.repositories.source_repo import SourceRepository
from app.storage.schemas.source import SourceUpdate

console = Console()

source_app = typer.Typer(
    name="source",
    help="Operator surface for the source lifecycle (retire/kill).",
    no_args_is_help=True,
)

# Audit trail lives next to the autonomous rotation audit (apply.py writes here too).
_DEFAULT_AUDIT_DIR = Path("artifacts")


async def retire_source(
    repo: SourceRepository,
    provider: str,
    *,
    reason: str,
    now: datetime,
    audit_path: Path,
) -> dict[str, Any]:
    """Operator-manual TERMINAL retire of a source feed (pure of CLI/DI plumbing).

    Sets the source's DB status to ``RETIRED`` and records an audited
    ``LifecycleEvent``. RETIRED is reachable from any state — this is the
    operator's authoritative kill, so it is intentionally NOT gated by
    ``can_transition`` on the write side; the terminal guarantee lives on the
    RETURN path (``can_transition(RETIRED, *) is False``), which blocks every
    automated resurrection.

    Returns a small result dict (``applied``/``reason``/``from``/``to``) instead
    of raising, so the CLI can report no-ops (unknown / already-retired) cleanly.
    """
    matches = await repo.list(provider=provider)
    if not matches:
        return {"applied": False, "reason": "no_such_source", "provider": provider}

    source = matches[0]
    if source.status == SourceStatus.RETIRED:
        return {
            "applied": False,
            "reason": "already_retired",
            "provider": provider,
            "source_id": source.source_id,
        }

    await repo.update(source.source_id, SourceUpdate(status=SourceStatus.RETIRED))
    append_lifecycle_event(
        LifecycleEvent(
            source=provider,
            from_status=source.status.value,
            to_status=SourceStatus.RETIRED.value,
            reason=f"operator_retire:{reason}",
            recorded_at_utc=now.isoformat(),
            evidence=None,
        ),
        audit_path,
    )
    return {
        "applied": True,
        "from": source.status.value,
        "to": SourceStatus.RETIRED.value,
        "provider": provider,
        "source_id": source.source_id,
    }


@source_app.command("retire")
def source_retire(
    provider: Annotated[str, typer.Argument(help="Provider key of the source to retire (kill).")],
    reason: Annotated[
        str,
        typer.Option("--reason", help="Operator reason recorded in the audit trail."),
    ] = "operator_manual_retire",
) -> None:
    """Terminally retire (kill) a source feed — never auto-resurrected/re-onboarded."""

    async def _run() -> dict[str, Any]:
        settings = get_settings()
        session_factory = build_session_factory(settings.db)
        async with session_factory.begin() as session:
            repo = SourceRepository(session)
            return await retire_source(
                repo,
                provider,
                reason=reason,
                now=datetime.now(UTC),
                audit_path=_DEFAULT_AUDIT_DIR,
            )

    result = asyncio.run(_run())
    if result["applied"]:
        console.print(
            f"[green bold]RETIRED[/green bold] '{provider}' "
            f"({result['from']} -> retired) — terminal, will not be auto-resurrected."
        )
    elif result["reason"] == "already_retired":
        console.print(f"[yellow]'{provider}' is already retired — no change.[/yellow]")
    else:
        console.print(f"[red]No source found for provider '{provider}'.[/red]")
        raise typer.Exit(code=1)
