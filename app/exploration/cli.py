"""Standalone CLI for the exploration sandbox.

Deliberately NOT registered into the production ``trading-bot`` Typer app — that
would make ``app/cli/main.py`` import the sandbox and break isolation. Run it
directly:

    python -m app.exploration.cli list
    python -m app.exploration.cli probe dummy
    python -m app.exploration.cli run
    python -m app.exploration.cli run --only coinglass,messari
    python -m app.exploration.cli report
"""

from __future__ import annotations

import asyncio
import logging

import typer
from rich.console import Console
from rich.table import Table

from app.exploration.report import write_report
from app.exploration.runner import run_probes
from app.exploration.settings import get_exploration_settings
from app.exploration.sources import build_registry

app = typer.Typer(
    name="exploration",
    help="KAI source-intake exploration sandbox (isolated, default-off).",
    no_args_is_help=True,
)
console = Console()


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.command("list")
def list_probes() -> None:
    """List eligible probes given the current settings."""
    settings = get_exploration_settings()
    registry = build_registry(settings)
    console.print(
        f"[bold]Exploration[/bold] enabled={settings.enabled} "
        f"artifacts={settings.artifacts_dir} probes={len(registry)}"
    )
    if not registry:
        console.print(
            "[yellow]No eligible probes. Set EXPLORATION_ENABLED=true + a source flag.[/yellow]"
        )
        return
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Probe ID")
    table.add_column("Mode")
    table.add_column("Needs key")
    for probe_id in sorted(registry):
        p = registry[probe_id]
        table.add_row(probe_id, p.access_mode, "yes" if p.requires_key else "no")
    console.print(table)


@app.command("probe")
def probe_one(
    name: str = typer.Argument(..., help="probe_id (e.g. dummy:api) or source name (e.g. dummy)"),
    capture: bool = typer.Option(True, "--capture/--no-capture", help="Write artifacts"),
) -> None:
    """Run a single probe (or all probes of a source) and print the result."""
    _configure_logging()
    results = asyncio.run(run_probes(only=[name], capture=capture))
    if not results:
        console.print(f"[yellow]No eligible probe matched '{name}'.[/yellow]")
        raise typer.Exit(1)
    _print_results(results)


@app.command("run")
def run_all(
    only: str | None = typer.Option(
        None, "--only", help="Comma-separated probe_ids or source names to restrict the run"
    ),
    capture: bool = typer.Option(True, "--capture/--no-capture", help="Write artifacts"),
) -> None:
    """Run all eligible probes (or a restricted set)."""
    _configure_logging()
    only_list = list(only.split(",")) if only else None
    results = asyncio.run(run_probes(only=only_list, capture=capture))
    if not results:
        console.print("[yellow]No eligible probes ran. Check EXPLORATION_* flags.[/yellow]")
        raise typer.Exit(1)
    _print_results(results)


@app.command("report")
def report() -> None:
    """Build the coverage report from captured artifacts."""
    settings = get_exploration_settings()
    json_path, md_path = write_report(artifacts_dir=settings.artifacts_dir)
    console.print(f"[green]Coverage report written:[/green]\n  {json_path}\n  {md_path}")


def _print_results(results: list) -> None:  # type: ignore[type-arg]
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Probe ID")
    table.add_column("Status")
    table.add_column("Records", justify="right")
    table.add_column("Latency", justify="right")
    table.add_column("Error")
    for r in results:
        status = "[green]ok[/green]" if r.success else "[red]FAIL[/red]"
        lat = f"{r.meta.latency_ms}ms" if r.meta.latency_ms is not None else "–"
        table.add_row(r.probe_id, status, str(r.record_count), lat, r.error or "")
    console.print(table)


if __name__ == "__main__":
    app()
