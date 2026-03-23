"""Research and signal generation commands for KAI CLI.

Aggregator module: registers all research sub-command groups from focused submodules.
Each submodule owns a coherent domain of commands:

- research_core     : briefs, watchlists, signals, datasets, companion, handoff
- research_readiness: operational readiness, provider health, drift, gates, artifacts
- research_operator : escalation, blocking, actions, decision-pack, runbook, review journal
- research_trading  : market data, portfolio, backtesting, decision journal, trading loop
"""
from __future__ import annotations

import typer
from rich.console import Console

import app.cli.commands.research_companion  # noqa: F401 — registers companion commands on research_core_app
from app.cli.commands.research_core import research_core_app
from app.cli.commands.research_operator import (
    FINAL_RESEARCH_COMMAND_NAMES,
    RESEARCH_COMMAND_ALIASES,
    SUPERSEDED_RESEARCH_COMMAND_NAMES,
    extract_runbook_command_refs,
    get_invalid_research_command_refs,
    research_operator_app,
)
from app.cli.commands.research_readiness import research_readiness_app
from app.cli.commands.research_trading import research_trading_app

console = Console()
research_app = typer.Typer(help="Research and signal generation commands", no_args_is_help=True)

# Register all sub-command groups as flat commands (name="" = no namespace prefix)
research_app.add_typer(research_core_app, name="")
research_app.add_typer(research_readiness_app, name="")
research_app.add_typer(research_operator_app, name="")
research_app.add_typer(research_trading_app, name="")


# ---------------------------------------------------------------------------
# Inventory and contract functions (must have access to aggregated research_app)
# ---------------------------------------------------------------------------


def get_registered_research_command_names() -> set[str]:
    """Return all currently registered research command names across all sub-apps."""
    names: set[str] = set()
    # Collect from all sub-apps directly so this works before full Typer resolution
    for sub_app in (
        research_core_app,
        research_readiness_app,
        research_operator_app,
        research_trading_app,
    ):
        for command in sub_app.registered_commands:
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
