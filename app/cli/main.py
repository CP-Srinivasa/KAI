from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.ingestion.classifier import classify_url
from app.ingestion.resolvers.podcast import load_and_resolve_podcasts
from app.ingestion.resolvers.youtube import load_youtube_channels

app = typer.Typer(name="trading-bot", help="AI Analyst Trading Bot CLI", no_args_is_help=True)
console = Console()

sources_app = typer.Typer(help="Source management commands", no_args_is_help=True)
podcasts_app = typer.Typer(help="Podcast resolution commands", no_args_is_help=True)
youtube_app = typer.Typer(help="YouTube resolution commands", no_args_is_help=True)
query_app = typer.Typer(help="Query commands", no_args_is_help=True)
ingest_app = typer.Typer(help="Ingestion commands", no_args_is_help=True)

app.add_typer(sources_app, name="sources")
app.add_typer(podcasts_app, name="podcasts")
app.add_typer(youtube_app, name="youtube")
app.add_typer(query_app, name="query")
app.add_typer(ingest_app, name="ingest")


@app.callback()
def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)


# ── sources ──────────────────────────────────────────────────────────────────

@sources_app.command("classify")
def sources_classify(
    url: str = typer.Argument(..., help="URL to classify"),
) -> None:
    """Classify a single URL into its SourceType."""
    result = classify_url(url)
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("URL", url)
    table.add_row("Source Type", result.source_type.value)
    table.add_row("Status", result.status.value)
    table.add_row("Notes", result.notes or "—")
    console.print(table)


# ── podcasts ─────────────────────────────────────────────────────────────────

@podcasts_app.command("resolve")
def podcasts_resolve() -> None:
    """Resolve all podcast sources from monitor/podcast_feeds_raw.txt."""
    settings = get_settings()
    monitor_dir = Path(settings.monitor_dir)
    resolved, unresolved = load_and_resolve_podcasts(monitor_dir)

    console.print(f"\n[bold green]Resolved ({len(resolved)}):[/bold green]")
    for src in resolved:
        console.print(f"  [green]✓[/green] {src.resolved_url}")
        if src.notes:
            console.print(f"    [dim]{src.notes}[/dim]")

    console.print(f"\n[bold yellow]Unresolved ({len(unresolved)}):[/bold yellow]")
    for src in unresolved:
        console.print(f"  [yellow]✗[/yellow] {src.raw_url} [{src.status.value}]")
        if src.notes:
            console.print(f"    [dim]{src.notes}[/dim]")


# ── youtube ──────────────────────────────────────────────────────────────────

@youtube_app.command("resolve")
def youtube_resolve() -> None:
    """Resolve and normalize YouTube channels from monitor/youtube_channels.txt."""
    settings = get_settings()
    monitor_dir = Path(settings.monitor_dir)
    channels = load_youtube_channels(monitor_dir)

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Handle")
    table.add_column("Type")
    table.add_column("Normalized URL")
    table.add_column("Notes")

    for ch in channels:
        table.add_row(ch.handle or "?", ch.channel_type, ch.normalized_url, ch.notes or "")

    console.print(table)
    console.print(f"\n[bold]{len(channels)} channels[/bold] (deduplicated)")


# ── query ─────────────────────────────────────────────────────────────────────

@query_app.command("validate")
def query_validate(
    query: str = typer.Argument(..., help="Query string to validate"),
) -> None:
    """Validate a query string."""
    console.print(f"[green]Query received:[/green] {query}")
    console.print("[yellow]Full DSL validation — Phase 3[/yellow]")


# ── ingest ────────────────────────────────────────────────────────────────────

@ingest_app.command("rss")
def ingest_rss(
    url: str = typer.Argument(..., help="RSS feed URL to ingest"),
    source_id: str = typer.Option("manual", help="Source ID"),
    source_name: str = typer.Option("Manual Ingest", help="Source name"),
) -> None:
    """Fetch and display entries from an RSS feed."""
    import asyncio

    from app.core.enums import SourceStatus, SourceType
    from app.ingestion.base.interfaces import SourceMetadata
    from app.ingestion.rss.adapter import RSSFeedAdapter

    metadata = SourceMetadata(
        source_id=source_id,
        source_name=source_name,
        source_type=SourceType.RSS_FEED,
        url=url,
        status=SourceStatus.ACTIVE,
    )
    adapter = RSSFeedAdapter(metadata)

    async def run() -> None:
        result = await adapter.fetch()
        if not result.success:
            console.print(f"[red]Error:[/red] {result.error}")
            raise typer.Exit(1)
        console.print(f"[green]Fetched {len(result.documents)} entries from {url}[/green]\n")
        for doc in result.documents[:10]:
            console.print(f"  [bold]{doc.title}[/bold]")
            console.print(f"  {doc.url}")
            console.print(f"  Published: {doc.published_at}")
            console.print()

    asyncio.run(run())


if __name__ == "__main__":
    app()
