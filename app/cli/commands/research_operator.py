"""Research operator CLI commands -- companion-ML subsystem removed.

Stub commands for backward compatibility (telegram bot command refs,
contract tests, etc.). All commands return stub payloads.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console

console = Console()
research_operator_app = typer.Typer(
    help="Operator research commands (companion-ML removed)",
    no_args_is_help=True,
)

_STUB_MSG = "companion-ML subsystem removed; tool returns stub payload."

# ---------------------------------------------------------------------------
# Constants (preserved for contract tests and telegram bot)
# ---------------------------------------------------------------------------

FINAL_RESEARCH_COMMAND_NAMES: tuple[str, ...] = (
    "brief",
    "watchlists",
    "signals",
    "readiness-summary",
    "provider-health",
    "paper-positions-summary",
    "paper-exposure-summary",
    "gate-summary",
    "signal-handoff",
    "review-journal-summary",
    "resolution-summary",
    "decision-pack-summary",
    "daily-summary",
    "alert-audit-summary",
    "escalation-summary",
    "review-journal-append",
)

RESEARCH_COMMAND_ALIASES: dict[str, str] = {}

SUPERSEDED_RESEARCH_COMMAND_NAMES: tuple[str, ...] = ()


def extract_runbook_command_refs(payload: dict[str, Any]) -> list[str]:
    """Extract all command_refs from a runbook payload."""
    refs: list[str] = []
    for step in payload.get("steps", []):
        refs.extend(step.get("command_refs", []))
    for step in payload.get("next_steps", []):
        refs.extend(step.get("command_refs", []))
    refs.extend(payload.get("command_refs", []))
    return list(dict.fromkeys(refs))


def get_invalid_research_command_refs(refs: list[str]) -> list[str]:
    """Return any command refs that are not registered research sub-commands."""
    from app.cli.research import get_registered_research_command_names

    registered = get_registered_research_command_names()
    invalid_refs: list[str] = []
    for ref in refs:
        parts = ref.strip().split()
        if len(parts) != 2 or parts[0] != "research" or parts[1] not in registered:
            invalid_refs.append(ref)
    return invalid_refs


# ---------------------------------------------------------------------------
# Stub commands (must be registered to satisfy telegram bot contract tests)
# ---------------------------------------------------------------------------


@research_operator_app.command("readiness-summary")
def research_readiness_summary() -> None:
    """Stub: companion-ML removed."""
    console.print(_STUB_MSG)


@research_operator_app.command("provider-health")
def research_provider_health() -> None:
    """Stub: companion-ML removed."""
    console.print(_STUB_MSG)


@research_operator_app.command("paper-positions-summary")
def research_paper_positions_summary() -> None:
    """Stub: companion-ML removed."""
    console.print(_STUB_MSG)


@research_operator_app.command("paper-exposure-summary")
def research_paper_exposure_summary() -> None:
    """Stub: companion-ML removed."""
    console.print(_STUB_MSG)


@research_operator_app.command("gate-summary")
def research_gate_summary() -> None:
    """Stub: companion-ML removed."""
    console.print(_STUB_MSG)


@research_operator_app.command("signal-handoff")
def research_signal_handoff() -> None:
    """Stub: companion-ML removed."""
    console.print(_STUB_MSG)


@research_operator_app.command("review-journal-summary")
def research_review_journal_summary() -> None:
    """Stub: companion-ML removed."""
    console.print(_STUB_MSG)


@research_operator_app.command("resolution-summary")
def research_resolution_summary() -> None:
    """Stub: companion-ML removed."""
    console.print(_STUB_MSG)


@research_operator_app.command("decision-pack-summary")
def research_decision_pack_summary() -> None:
    """Stub: companion-ML removed."""
    console.print(_STUB_MSG)


@research_operator_app.command("daily-summary")
def research_daily_summary() -> None:
    """Stub: companion-ML removed."""
    console.print(_STUB_MSG)


@research_operator_app.command("alert-audit-summary")
def research_alert_audit_summary() -> None:
    """Stub: companion-ML removed."""
    console.print(_STUB_MSG)


@research_operator_app.command("escalation-summary")
def research_escalation_summary() -> None:
    """Stub: companion-ML removed."""
    console.print(_STUB_MSG)


@research_operator_app.command("review-journal-append")
def research_review_journal_append() -> None:
    """Stub: companion-ML removed."""
    console.print(_STUB_MSG)
