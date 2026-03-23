from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.cli.commands.trading import trading_app
from app.cli.research import (
    extract_runbook_command_refs,
    get_invalid_research_command_refs,
    get_provisional_research_command_names,
    get_registered_research_command_names,
    get_research_command_inventory,
    research_app,
)
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.ingestion.base.interfaces import FetchResult
from app.ingestion.classifier import classify_url
from app.ingestion.resolvers.podcast import load_and_resolve_podcasts
from app.ingestion.resolvers.youtube import load_youtube_channels
from app.ingestion.rss.service import RSSCollectedFeed, collect_rss_feed
from app.storage.db.session import build_session_factory
from app.storage.document_ingest import IngestPersistStats, persist_fetch_result

__all__ = [
    "app",
    "extract_runbook_command_refs",
    "get_invalid_research_command_refs",
    "get_provisional_research_command_names",
    "get_registered_research_command_names",
    "get_research_command_inventory",
]

app = typer.Typer(name="trading-bot", help="AI Analyst Trading Bot CLI", no_args_is_help=True)
console = Console()

sources_app = typer.Typer(help="Source management commands", no_args_is_help=True)
podcasts_app = typer.Typer(help="Podcast resolution commands", no_args_is_help=True)
youtube_app = typer.Typer(help="YouTube resolution commands", no_args_is_help=True)
query_app = typer.Typer(help="Query commands", no_args_is_help=True)
ingest_app = typer.Typer(help="Ingestion commands", no_args_is_help=True)
pipeline_app = typer.Typer(help="End-to-end pipeline commands", no_args_is_help=True)
alerts_app = typer.Typer(help="Alert commands", no_args_is_help=True)

app.add_typer(sources_app, name="sources")
app.add_typer(podcasts_app, name="podcasts")
app.add_typer(youtube_app, name="youtube")
app.add_typer(query_app, name="query")
app.add_typer(ingest_app, name="ingest")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(alerts_app, name="alerts")
app.add_typer(research_app, name="research")
app.add_typer(trading_app, name="trading")


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
def analyze_pending(
    limit: int = typer.Option(50, help="Max documents to analyze"),
    provider: str = typer.Option("openai", help="LLM Provider to use (openai, anthropic, gemini)"),
) -> None:
    """Run the analysis pipeline on all pending (unanalyzed) documents."""
    import asyncio

    async def run() -> None:
        settings = get_settings()
        monitor_dir = Path(settings.monitor_dir)

        from app.analysis.factory import create_provider
        from app.analysis.keywords.engine import KeywordEngine
        from app.analysis.pipeline import AnalysisPipeline
        from app.core.enums import DocumentStatus
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        console.print("[bold]Initializing Analysis Engine...[/bold]")
        keyword_engine = KeywordEngine.from_monitor_dir(monitor_dir)

        try:
            provider_obj = create_provider(provider, settings)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1) from e

        if not provider_obj:
            console.print(
                f"[yellow]Warning:[/yellow] No API key found for provider '{provider}'."
                " LLM Analysis will be skipped."
            )
        else:
            console.print(
                f"[cyan]Using LLM Provider:[/cyan]"
                f" {provider_obj.provider_name} ({provider_obj.model})"
            )

        pipeline = AnalysisPipeline(keyword_engine, provider_obj, run_llm=bool(provider_obj))
        session_factory = build_session_factory(settings.db)

        # Phase 1: Read pending docs — session committed immediately after fetch
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.get_pending_documents(limit=limit)

        if not docs:
            console.print("[green]No pending documents to analyze.[/green]")
            return

        console.print(f"[bold]Analyzing {len(docs)} documents...[/bold]")

        # Phase 2: Run analysis pipeline — LLM HTTP calls happen outside any DB session
        results = await pipeline.run_batch(docs)

        # Phase 3: Write results — new session, no LLM calls inside
        success_count = 0
        error_count = 0

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)

            for res in results:
                if not res.success:
                    console.print(f"[red]Failed doc {res.document.id}:[/red] {res.error}")
                    error_count += 1
                    try:
                        await repo.update_status(str(res.document.id), DocumentStatus.FAILED)
                    except Exception:
                        pass  # best-effort — do not mask the original error
                    continue

                # apply_to_document() merges entities + scores + priority onto doc
                res.apply_to_document()

                # I-12: analysis_result=None MUST NOT produce status=ANALYZED
                # In normal operation this is unreachable (fallback always builds a result),
                # but defensive guard prevents silent data corruption on future code changes.
                if res.analysis_result is None:
                    console.print(
                        f"[yellow]Skipped {res.document.id}:"
                        " no analysis result (no provider configured?)[/yellow]"
                    )
                    error_count += 1
                    try:
                        await repo.update_status(str(res.document.id), DocumentStatus.FAILED)
                    except Exception:
                        pass
                    continue

                try:
                    await repo.update_analysis(
                        str(res.document.id),
                        res.analysis_result,
                        provider_name=res.document.provider,
                        metadata_updates=res.trace_metadata,
                    )
                    success_count += 1
                except Exception as e:
                    console.print(f"[red]Failed to save doc {res.document.id}:[/red] {e}")
                    error_count += 1
                    try:
                        await repo.update_status(str(res.document.id), DocumentStatus.FAILED)
                    except Exception:
                        pass  # best-effort — do not mask the original error

        console.print(
            f"[bold green]Analysis complete![/bold green] "
            f"{success_count} success, {error_count} failed."
        )

    asyncio.run(run())


# ── query list ────────────────────────────────────────────────────────────────


@query_app.command("list")
def query_list(
    limit: int = typer.Option(20, help="Max documents to return"),
    min_priority: int = typer.Option(1, help="Minimum priority score filter (1-10)"),
    source_id: str = typer.Option(None, help="Filter by source ID"),
    asset: str = typer.Option(None, help="Filter to specific asset/ticker (e.g. BTC)"),
    watchlist: str = typer.Option(None, help="Filter using a named watchlist tag (e.g. defi)"),
) -> None:
    """List analyzed documents sorted by priority score (highest first)."""
    import asyncio

    async def run() -> None:
        from app.research.watchlists import WatchlistRegistry
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        settings = get_settings()
        monitor_dir = Path(settings.monitor_dir)
        session_factory = build_session_factory(settings.db)

        # Resolve watchlist symbols
        allowed_assets = set()
        if asset:
            allowed_assets.add(asset.upper())
        if watchlist:
            registry = WatchlistRegistry.from_monitor_dir(monitor_dir)
            allowed_assets.update(s.upper() for s in registry.get_watchlist(watchlist))

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(
                is_analyzed=True,
                source_id=source_id,
                limit=limit * 5 if allowed_assets else limit,  # Grab more if filtering in-memory
            )

        filtered = [d for d in docs if (d.priority_score or 0) >= min_priority]

        if allowed_assets:
            filtered = [
                d
                for d in filtered
                if any(t.upper() in allowed_assets for t in (d.tickers + d.crypto_assets))
            ]

        filtered.sort(key=lambda d: d.priority_score or 0, reverse=True)
        filtered = filtered[:limit]

        if not filtered:
            console.print("[yellow]No analyzed documents found.[/yellow]")
            return

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Pri", style="bold", width=4)
        table.add_column("Rel", width=5)
        table.add_column("Imp", width=5)
        table.add_column("Sentiment", width=10)
        table.add_column("Source", width=10)
        table.add_column("Title")

        for doc in filtered:
            pri = str(doc.priority_score or "–")
            rel = f"{doc.relevance_score:.2f}" if doc.relevance_score is not None else "–"
            imp = f"{doc.impact_score:.2f}" if doc.impact_score is not None else "–"
            sentiment = doc.sentiment_label.value if doc.sentiment_label else "–"
            source = (doc.source_id or "–")[:10]
            table.add_row(pri, rel, imp, sentiment, source, doc.title or "–")

        console.print(table)
        console.print(f"\n[bold]{len(filtered)} documents[/bold] (min priority {min_priority})")

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


# ── pipeline ──────────────────────────────────────────────────────────────────


@pipeline_app.command("run")
def pipeline_run(
    url: str = typer.Argument(..., help="RSS feed URL to process end-to-end"),
    source_id: str = typer.Option("manual", help="Source ID"),
    source_name: str = typer.Option("Manual", help="Source name"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip DB writes; preview only"),
    top_n: int = typer.Option(5, help="Top results to display by priority score"),
) -> None:
    """Fetch, persist, analyze, and score an RSS feed in one step."""
    import asyncio

    async def run() -> None:
        from app.analysis.keywords.engine import KeywordEngine
        from app.integrations.openai.provider import OpenAIAnalysisProvider
        from app.pipeline.service import run_rss_pipeline

        settings = get_settings()
        monitor_dir = Path(settings.monitor_dir)

        keyword_engine = KeywordEngine.from_monitor_dir(monitor_dir)

        provider = None
        if settings.providers.openai_api_key:
            provider = OpenAIAnalysisProvider.from_settings(settings.providers)
        else:
            console.print("[yellow]Warning:[/yellow] No OpenAI API key — LLM analysis skipped.")

        session_factory = build_session_factory(settings.db)

        stats = await run_rss_pipeline(
            url,
            session_factory=session_factory,
            keyword_engine=keyword_engine,
            provider=provider,
            source_id=source_id,
            source_name=source_name,
            monitor_dir=monitor_dir,
            timeout=settings.sources.fetch_timeout,
            max_retries=settings.sources.max_retries,
            dry_run=dry_run,
        )

        console.print(f"\n[bold green]Pipeline complete:[/bold green] {url}")
        console.print(f"  Fetched:   {stats.fetched_count}")
        console.print(f"  Saved:     {stats.saved_count}")
        console.print(f"  Analyzed:  {stats.analyzed_count}")
        console.print(f"  Skipped:   {stats.skipped_count}")
        if stats.failed_count:
            console.print(f"  [red]Failed:  {stats.failed_count}[/red]")

        if dry_run:
            console.print("[dim](dry-run — no data written)[/dim]")

        if not stats.top_results:
            return

        console.print(f"\n[bold]Top {min(top_n, len(stats.top_results))} results:[/bold]")
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Pri", width=4)
        table.add_column("Rel", width=5)
        table.add_column("Imp", width=5)
        table.add_column("Sentiment", width=10)
        table.add_column("Title")

        for res in stats.top_results[:top_n]:
            doc = res.document
            pri = str(doc.priority_score or "–")
            rel = f"{doc.relevance_score:.2f}" if doc.relevance_score is not None else "–"
            imp = f"{doc.impact_score:.2f}" if doc.impact_score is not None else "–"
            sentiment = doc.sentiment_label.value if doc.sentiment_label else "–"
            table.add_row(pri, rel, imp, sentiment, doc.title or "–")

        console.print(table)

    asyncio.run(run())


# ── alerts ────────────────────────────────────────────────────────────────────


@alerts_app.command("send-test")
def alerts_send_test() -> None:
    """Send a synthetic test alert through all configured channels (dry-run safe)."""
    import asyncio
    from datetime import UTC, datetime

    from app.alerts.base.interfaces import AlertMessage
    from app.alerts.service import AlertService

    async def run() -> None:
        settings = get_settings()
        service = AlertService.from_settings(settings)
        msg = AlertMessage(
            document_id="test-000",
            title="KAI Alert System — Test Message",
            url="https://example.com/test",
            priority=8,
            sentiment_label="bullish",
            actionable=True,
            explanation=(
                "This is a test alert from the KAI CLI. "
                "If you see this, alerting is configured correctly."
            ),
            affected_assets=["BTC", "ETH"],
            source_name="KAI Test",
            tags=["test"],
            published_at=datetime.now(UTC),
        )
        results = await service.send_digest([msg], "test")
        if not results:
            console.print(
                "[yellow]No channels active. "
                "Set ALERT_DRY_RUN=true or configure Telegram/Email.[/yellow]"
            )
            return
        for r in results:
            status = "[green]✓ sent[/green]" if r.success else f"[red]✗ failed: {r.error}[/red]"
            console.print(f"  [{r.channel}] {status}")

    asyncio.run(run())


@alerts_app.command("evaluate-pending")
def alerts_evaluate_pending(
    limit: int = typer.Option(50, help="Max analyzed documents to evaluate"),
    min_priority: int = typer.Option(0, help="Override minimum priority (0 = use settings)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview only — do not send"),
) -> None:
    """Evaluate analyzed documents and dispatch alerts for those above the threshold."""
    import asyncio

    from app.alerts.base.interfaces import AlertMessage
    from app.alerts.service import AlertService

    async def run() -> None:
        settings = get_settings()
        effective_min = min_priority if min_priority > 0 else settings.alerts.min_priority

        session_factory = build_session_factory(settings.db)
        async with session_factory.begin() as session:
            from app.storage.repositories.document_repo import DocumentRepository

            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=True, limit=limit)

        alert_worthy = [d for d in docs if (d.priority_score or 0) >= effective_min]
        console.print(
            f"[bold]{len(docs)} analyzed, "
            f"{len(alert_worthy)} above threshold (>={effective_min})[/bold]"
        )

        if not alert_worthy:
            console.print("[green]Nothing to alert.[/green]")
            return

        if dry_run:
            for d in alert_worthy:
                console.print(f"  [dim]P{d.priority_score}[/dim] {d.title or '—'}")
            console.print(f"[dim](dry-run — {len(alert_worthy)} alerts not sent)[/dim]")
            return

        service = AlertService.from_settings(settings)
        messages = []
        for d in alert_worthy:
            affected = list((d.tickers or []) + (d.crypto_assets or []))
            msg = AlertMessage(
                document_id=str(d.id),
                title=d.title or "—",
                url=d.url,
                priority=d.priority_score or 1,
                sentiment_label=d.sentiment_label.value if d.sentiment_label else "neutral",
                actionable=(d.priority_score or 0) >= 7,
                explanation=d.summary or d.title or "—",
                affected_assets=affected,
                published_at=d.published_at,
                source_name=d.source_name,
            )
            messages.append(msg)

        results = await service.send_digest(messages, f"{len(messages)} analyzed documents")
        for r in results:
            status = "[green]✓ sent[/green]" if r.success else f"[red]✗ failed: {r.error}[/red]"
            console.print(f"  [{r.channel}] {status}")

    asyncio.run(run())

    asyncio.run(run())




# ---------------------------------------------------------------------------
# Sprint 29: analyze-pending --shadow-companion flag
# ---------------------------------------------------------------------------


@query_app.command("analyze-pending-shadow")
def analyze_pending_shadow(
    limit: int = typer.Option(50, help="Max documents to analyze"),
    shadow_companion: bool = typer.Option(
        False,
        "--shadow-companion",
        help="Run companion model as shadow alongside primary provider (I-55, Sprint 29)",
    ),
) -> None:
    """Analyze pending documents; optionally run shadow companion alongside primary (Sprint 29)."""
    import asyncio

    async def run() -> None:
        from app.analysis.keywords.engine import KeywordEngine
        from app.analysis.pipeline import AnalysisPipeline
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        settings = get_settings()
        monitor_dir = Path(settings.monitor_dir)
        keyword_engine = KeywordEngine.from_monitor_dir(monitor_dir)
        provider_obj = None
        session_factory = build_session_factory(settings.db)

        pipeline = AnalysisPipeline(keyword_engine, provider_obj, run_llm=False)

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.get_pending_documents(limit=limit)

        if not docs:
            console.print("[green]No pending documents to analyze.[/green]")
            return

        console.print(
            f"[bold]Analyzing {len(docs)} documents "
            f"(shadow_companion={shadow_companion})...[/bold]"
        )

        results = await pipeline.run_batch(docs)
        success_count = 0
        error_count = 0

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            for res in results:
                if not res.success:
                    error_count += 1
                    continue
                res.apply_to_document()
                if res.analysis_result is None:
                    error_count += 1
                    continue
                try:
                    metadata_updates = dict(res.trace_metadata or {})
                    if shadow_companion:
                        metadata_updates["shadow_companion_active"] = True
                    await repo.update_analysis(
                        str(res.document.id),
                        res.analysis_result,
                        provider_name=res.document.provider,
                        metadata_updates=metadata_updates,
                    )
                    success_count += 1
                except Exception:
                    error_count += 1

        console.print(
            f"[bold green]Analysis complete![/bold green] "
            f"{success_count} success, {error_count} failed."
        )

    asyncio.run(run())



if __name__ == "__main__":
    app()
