"""Research readiness CLI commands -- companion-ML subsystem removed."""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()
research_readiness_app = typer.Typer(
    help="Readiness commands (companion-ML removed)",
    no_args_is_help=True,
)
