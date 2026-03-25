"""Research and signal generation commands for KAI CLI."""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console

from app.cli.commands.research_core import research_core_app

console = Console()
research_app = typer.Typer(help="Research and signal generation commands", no_args_is_help=True)

research_app.add_typer(research_core_app, name="")

# ---------------------------------------------------------------------------
# Command inventory constants (kept for telegram bot contract tests)
# ---------------------------------------------------------------------------

FINAL_RESEARCH_COMMAND_NAMES: tuple[str, ...] = (
    "brief",
    "watchlists",
    "signals",
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
    registered = get_registered_research_command_names()
    invalid_refs: list[str] = []
    for ref in refs:
        parts = ref.strip().split()
        if len(parts) != 2 or parts[0] != "research" or parts[1] not in registered:
            invalid_refs.append(ref)
    return invalid_refs


def get_registered_research_command_names() -> set[str]:
    """Return all currently registered research command names."""
    names: set[str] = set()
    for command in research_core_app.registered_commands:
        name = getattr(command, "name", None)
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def get_research_command_inventory() -> dict[str, object]:
    """Return the locked research command inventory for contract tests."""
    return {
        "final_commands": list(FINAL_RESEARCH_COMMAND_NAMES),
        "aliases": dict(RESEARCH_COMMAND_ALIASES),
        "superseded_commands": list(SUPERSEDED_RESEARCH_COMMAND_NAMES),
        "provisional_commands": list(get_provisional_research_command_names()),
    }


def get_provisional_research_command_names() -> tuple[str, ...]:
    """Return registered research commands outside the locked final/alias set."""
    classified = (
        set(FINAL_RESEARCH_COMMAND_NAMES)
        | set(RESEARCH_COMMAND_ALIASES)
        | set(SUPERSEDED_RESEARCH_COMMAND_NAMES)
    )
    provisional = sorted(get_registered_research_command_names() - classified)
    return tuple(provisional)


__all__ = [
    "research_app",
    "get_research_command_inventory",
    "get_registered_research_command_names",
    "get_provisional_research_command_names",
    "extract_runbook_command_refs",
    "get_invalid_research_command_refs",
]


