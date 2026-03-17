from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.core.domain.document import CanonicalDocument
from app.core.enums import SourceStatus, SourceType
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.enrichment.deduplication.deduplicator import Deduplicator
from app.ingestion.base.interfaces import FetchResult, SourceMetadata
from app.ingestion.classifier import ClassificationResult, SourceClassifier, classify_url
from app.ingestion.resolvers.podcast import load_and_resolve_podcasts
from app.ingestion.resolvers.rss import RSSResolveResult, resolve_rss_feed
from app.ingestion.resolvers.youtube import load_youtube_channels
from app.ingestion.rss.adapter import RSSFeedAdapter
from app.storage.db.session import build_session_factory
from app.storage.repositories.document_repo import DocumentRepository

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


@dataclass(frozen=True)
class RSSIngestStats:
    fetched_count: int
    candidate_count: int
    batch_duplicates: int
    existing_duplicates: int
    saved_count: int
    preview_documents: list[CanonicalDocument]


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


# ── ingest ────────────────────────────────────────────────────────────────────


@ingest_app.command("rss")
def ingest_rss(
    url: str = typer.Argument(..., help="RSS feed URL to ingest"),
    source_id: str = typer.Option("manual", help="Source ID"),
    source_name: str = typer.Option("Manual Ingest", help="Source name"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch and deduplicate without storing"),
) -> None:
    """Classify, validate, fetch, deduplicate, and optionally store an RSS feed."""
    import asyncio

    async def run() -> None:
        try:
            classification, resolved_feed = await _validate_rss_input(url)
            result = await _fetch_rss_documents(
                resolved_feed=resolved_feed,
                source_id=source_id,
                source_name=source_name,
                classification=classification,
            )
            if not result.success:
                raise RuntimeError(result.error or "RSS fetch failed")
            stats = await _persist_rss_documents(result, dry_run=dry_run)
        except RuntimeError as err:
            console.print(f"[red]Error:[/red] {err}")
            raise typer.Exit(1) from err

        resolved_url = resolved_feed.resolved_url or url
        console.print(f"[green]RSS feed validated:[/green] {resolved_url}")
        console.print(
            f"[bold]Classification:[/bold] {classification.source_type.value} "
            f"({classification.status.value})"
        )
        if classification.notes:
            console.print(f"[bold]Classifier notes:[/bold] {classification.notes}")
        if resolved_feed.feed_title:
            console.print(f"[bold]Feed title:[/bold] {resolved_feed.feed_title}")

        console.print(f"[bold]Fetched:[/bold] {stats.fetched_count}")
        console.print(f"[bold]Batch duplicates skipped:[/bold] {stats.batch_duplicates}")

        if dry_run:
            console.print(
                f"[bold]Dry run:[/bold] would store up to {stats.candidate_count} documents"
            )
            console.print("[bold]Existing duplicates skipped:[/bold] not checked in dry-run")
        else:
            console.print(f"[bold]Existing duplicates skipped:[/bold] {stats.existing_duplicates}")
            console.print(f"[bold]Saved:[/bold] {stats.saved_count}")

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


async def _validate_rss_input(url: str) -> tuple[ClassificationResult, RSSResolveResult]:
    settings = get_settings()
    classifier = SourceClassifier.from_monitor_dir(Path(settings.monitor_dir))
    classification = classifier.classify(url)
    resolved_feed = await resolve_rss_feed(url, timeout=settings.sources.fetch_timeout)

    if not resolved_feed.is_valid:
        details = (
            f"Classified as {classification.source_type.value} ({classification.status.value}). "
            f"{resolved_feed.error or 'Feed validation failed.'}"
        )
        raise RuntimeError(f"URL is not a valid RSS/Atom feed. {details}")

    return classification, resolved_feed


async def _fetch_rss_documents(
    *,
    resolved_feed: RSSResolveResult,
    source_id: str,
    source_name: str,
    classification: ClassificationResult,
) -> FetchResult:
    settings = get_settings()
    metadata = SourceMetadata(
        source_id=source_id,
        source_name=source_name,
        source_type=SourceType.RSS_FEED,
        url=resolved_feed.resolved_url or resolved_feed.url,
        status=SourceStatus.ACTIVE,
        notes=classification.notes,
        metadata={"classified_source_type": classification.source_type.value},
    )
    adapter = RSSFeedAdapter(
        metadata,
        timeout=settings.sources.fetch_timeout,
        max_retries=settings.sources.max_retries,
    )
    return await adapter.fetch()


async def _persist_rss_documents(result: FetchResult, *, dry_run: bool) -> RSSIngestStats:
    deduplicator = Deduplicator()
    scored_documents = deduplicator.filter_scored(result.documents)
    preview_documents = [doc for doc, score in scored_documents if not score.is_duplicate]
    batch_duplicates = sum(1 for _, score in scored_documents if score.is_duplicate)

    if dry_run:
        return RSSIngestStats(
            fetched_count=len(result.documents),
            candidate_count=len(preview_documents),
            batch_duplicates=batch_duplicates,
            existing_duplicates=0,
            saved_count=0,
            preview_documents=preview_documents,
        )

    settings = get_settings()
    session_factory = build_session_factory(settings.db)
    saved_count = 0
    existing_duplicates = 0

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        for doc in preview_documents:
            if await repo.get_by_url(doc.url):
                existing_duplicates += 1
                continue
            if doc.content_hash and await repo.get_by_hash(doc.content_hash):
                existing_duplicates += 1
                continue
            await repo.save(doc)
            saved_count += 1

    return RSSIngestStats(
        fetched_count=len(result.documents),
        candidate_count=len(preview_documents),
        batch_duplicates=batch_duplicates,
        existing_duplicates=existing_duplicates,
        saved_count=saved_count,
        preview_documents=preview_documents,
    )


if __name__ == "__main__":
    app()
