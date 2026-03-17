from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.ingestion.base.interfaces import FetchResult
from app.ingestion.classifier import classify_url
from app.ingestion.resolvers.podcast import load_and_resolve_podcasts
from app.ingestion.resolvers.youtube import load_youtube_channels
from app.ingestion.rss.service import RSSCollectedFeed, collect_rss_feed
from app.storage.db.session import build_session_factory
from app.storage.document_ingest import IngestPersistStats, persist_fetch_result

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
    """Validate a query string against the DSL parser."""
    from app.core.query import QueryParser, QueryParserError

    console.print(f"[bold green]Query received:[/bold green] {query}\n")
    try:
        parser = QueryParser(query)
        ast = parser.parse()
        console.print("[bold blue]✓ Valid Syntax! AST:[/bold blue]")
        console.print(f"[cyan]{ast}[/cyan]")
    except QueryParserError as err:
        console.print(f"[bold red]✗ Syntax Error:[/bold red] {err}")
        raise typer.Exit(1) from err


@query_app.command("analyze-pending")
def query_analyze_pending(
    limit: int = typer.Option(50, help="Max documents to process in this run"),
) -> None:
    """Run the analysis pipeline on all pending (unanalyzed) documents."""
    import asyncio

    async def run() -> None:
        settings = get_settings()
        monitor_dir = Path(settings.monitor_dir)

        from app.analysis.keywords.engine import KeywordEngine
        from app.analysis.pipeline import AnalysisPipeline
        from app.integrations.openai.provider import OpenAIAnalysisProvider
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        console.print("[bold]Initializing Analysis Engine...[/bold]")
        keyword_engine = KeywordEngine.from_monitor_dir(monitor_dir)

        provider = None
        if settings.providers.openai_api_key:
            provider = OpenAIAnalysisProvider.from_settings(settings.providers)
        else:
            console.print(
                "[yellow]Warning:[/yellow] No OpenAI API key found. LLM Analysis will be skipped."
            )

        pipeline = AnalysisPipeline(keyword_engine, provider, run_llm=bool(provider))
        session_factory = build_session_factory(settings.db)

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=False, limit=limit)

            if not docs:
                console.print("[green]No pending documents to analyze.[/green]")
                return

            console.print(f"[bold]Analyzing {len(docs)} documents...[/bold]")
            results = await pipeline.run_batch(docs)

            success_count = 0
            error_count = 0

            for res in results:
                if not res.success:
                    console.print(f"[red]Failed doc {res.document.id}:[/red] {res.error}")
                    error_count += 1
                    continue

                # apply_to_document() merges entities + scores + priority onto doc
                res.apply_to_document()
                doc = res.document

                try:
                    await repo.update_analysis(doc)
                    success_count += 1
                except Exception as e:
                    console.print(f"[red]Failed to save doc {doc.id}:[/red] {e}")
                    error_count += 1

            console.print(
                f"[bold green]Analysis complete![/bold green] "
                f"{success_count} success, {error_count} failed."
            )

    asyncio.run(run())


# ── ingest ────────────────────────────────────────────────────────────────────


@ingest_app.command("rss")
def ingest_rss(
    url: str = typer.Argument(..., help="RSS feed URL to ingest"),
    source_id: str = typer.Option("manual", help="Source ID"),
    source_name: str = typer.Option("Manual Ingest", help="Source name"),
    persist: bool = typer.Option(
        True,
        "--persist/--dry-run",
        help="Persist fetched documents; use --dry-run to skip storage",
    ),
) -> None:
    """Classify, validate, fetch, deduplicate, and optionally store an RSS feed."""
    import asyncio

    async def run() -> None:
        collected = await _collect_rss_feed(url, source_id=source_id, source_name=source_name)
        result = collected.fetch_result
        if not result.success:
            console.print(f"[red]Error:[/red] {result.error}")
            raise typer.Exit(1)

        stats = await _persist_rss_documents(result, dry_run=not persist)

        resolved_url = collected.resolved_feed.resolved_url or url
        console.print(f"[green]RSS feed validated:[/green] {resolved_url}")
        console.print(
            f"[bold]Classification:[/bold] {collected.classification.source_type.value} "
            f"({collected.classification.status.value})"
        )
        if collected.classification.notes:
            console.print(f"[bold]Classifier notes:[/bold] {collected.classification.notes}")
        if collected.resolved_feed.feed_title:
            console.print(f"[bold]Feed title:[/bold] {collected.resolved_feed.feed_title}")

        console.print(f"[bold]Fetched:[/bold] {stats.fetched_count}")
        console.print(f"[bold]Batch duplicates skipped:[/bold] {stats.batch_duplicates}")

        if not persist:
            console.print(
                f"[bold]Dry run:[/bold] would store up to {stats.candidate_count} documents"
            )
            console.print("[bold]Existing duplicates skipped:[/bold] not checked in dry-run")
        else:
            console.print(f"[bold]Existing duplicates skipped:[/bold] {stats.existing_duplicates}")
            console.print(f"[bold]Saved:[/bold] {stats.saved_count}")
            if stats.failed_count:
                console.print(f"[bold red]Save errors:[/bold red] {stats.failed_count}")

        if not stats.preview_documents:
            console.print("[yellow]No new document previews available.[/yellow]")
            return

        console.print("\n[bold]Preview:[/bold]")
        for doc in stats.preview_documents[:10]:
            console.print(f"  [bold]{doc.title}[/bold]")
            console.print(f"  {doc.url}")
            console.print(f"  Published: {doc.published_at}")
            console.print()

    asyncio.run(run())


async def _collect_rss_feed(
    url: str,
    *,
    source_id: str,
    source_name: str,
) -> RSSCollectedFeed:
    settings = get_settings()
    return await collect_rss_feed(
        url=url,
        source_id=source_id,
        source_name=source_name,
        monitor_dir=Path(settings.monitor_dir),
        timeout=settings.sources.fetch_timeout,
        max_retries=settings.sources.max_retries,
    )


async def _persist_rss_documents(result: FetchResult, *, dry_run: bool) -> IngestPersistStats:
    if dry_run:
        return await persist_fetch_result(None, result, dry_run=True)

    settings = get_settings()
    session_factory = build_session_factory(settings.db)
    return await persist_fetch_result(session_factory, result, dry_run=dry_run)


if __name__ == "__main__":
    app()
