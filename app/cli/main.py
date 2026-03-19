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
pipeline_app = typer.Typer(help="End-to-end pipeline commands", no_args_is_help=True)
alerts_app = typer.Typer(help="Alert commands", no_args_is_help=True)
research_app = typer.Typer(help="Research and signal generation commands", no_args_is_help=True)

app.add_typer(sources_app, name="sources")
app.add_typer(podcasts_app, name="podcasts")
app.add_typer(youtube_app, name="youtube")
app.add_typer(query_app, name="query")
app.add_typer(ingest_app, name="ingest")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(alerts_app, name="alerts")
app.add_typer(research_app, name="research")


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


# ── research ──────────────────────────────────────────────────────────────────


@research_app.command("brief")
def research_brief(
    watchlist: str = typer.Option(..., help="Watchlist/cluster to generate brief for (e.g. defi)"),
    watchlist_type: str = typer.Option(
        "assets",
        "--type",
        help="Watchlist type: assets, persons, topics, sources",
    ),
    limit: int = typer.Option(100, help="Number of recent documents to process"),
    output_format: str = typer.Option("md", "--format", help="Output format: md or json"),
) -> None:
    """Generate a Research Brief summarizing documents for a specific cluster."""
    import asyncio
    import json

    async def run() -> None:
        from app.research.briefs import ResearchBriefBuilder
        from app.research.watchlists import WatchlistRegistry, parse_watchlist_type
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        settings = get_settings()
        monitor_dir = Path(settings.monitor_dir)
        session_factory = build_session_factory(settings.db)

        registry = WatchlistRegistry.from_monitor_dir(monitor_dir)
        try:
            resolved_type = parse_watchlist_type(watchlist_type)
        except ValueError as err:
            console.print(f"[red]Error:[/red] {err}")
            raise typer.Exit(1) from err

        watchlist_items = registry.get_watchlist(watchlist, item_type=resolved_type)

        if not watchlist_items:
            console.print(
                f"[yellow]Warning: Watchlist '{watchlist}' produced no {resolved_type}.[/yellow]"
            )

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            # We fetch more than limit to ensure enough matches after symbol filtering
            docs = await repo.list(is_analyzed=True, limit=limit * 5)

        if watchlist_items:
            docs = registry.filter_documents(docs, watchlist, item_type=resolved_type)

        docs = docs[:limit]

        builder = ResearchBriefBuilder(cluster_name=watchlist)
        brief = builder.build(docs)

        if output_format.lower() == "json":
            console.print(json.dumps(brief.to_json_dict(), indent=2))
        else:
            console.print(brief.to_markdown())

    asyncio.run(run())


@research_app.command("watchlists")
def research_watchlists(
    watchlist_type: str = typer.Option(
        "assets",
        "--type",
        help="Watchlist type: assets, persons, topics, sources",
    ),
    watchlist: str | None = typer.Argument(None, help="Optional watchlist tag to inspect"),
) -> None:
    """List available research watchlists or show the members of one watchlist."""
    from app.research.watchlists import WatchlistRegistry, parse_watchlist_type

    settings = get_settings()
    registry = WatchlistRegistry.from_monitor_dir(Path(settings.monitor_dir))

    try:
        resolved_type = parse_watchlist_type(watchlist_type)
    except ValueError as err:
        console.print(f"[red]Error:[/red] {err}")
        raise typer.Exit(1) from err

    if watchlist:
        items = registry.get_watchlist(watchlist, item_type=resolved_type)
        if not items:
            console.print(
                f"[yellow]No watchlist entries found for '{watchlist}' ({resolved_type}).[/yellow]"
            )
            return

        console.print(
            f"[bold]{watchlist}[/bold] "
            f"([{resolved_type}] {len(items)} entries)"
        )
        for item in items:
            console.print(f"  - {item}")
        return

    all_watchlists = registry.get_all_watchlists(item_type=resolved_type)
    if not all_watchlists:
        console.print(f"[yellow]No watchlists found for type '{resolved_type}'.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Watchlist")
    table.add_column("Count", justify="right")
    table.add_column("Preview")

    for name, items in sorted(all_watchlists.items()):
        preview = ", ".join(items[:3])
        if len(items) > 3:
            preview += ", ..."
        table.add_row(name, str(len(items)), preview)

    console.print(table)
    console.print(f"\n[bold]{len(all_watchlists)} watchlists[/bold] ({resolved_type})")


@research_app.command("signals")
def research_signals(
    limit: int = typer.Option(100, help="Number of recent documents to search"),
    min_priority: int = typer.Option(8, help="Minimum priority score for signals"),
    watchlist: str = typer.Option(None, help="Watchlist name to boost priority of matching assets"),
    provider: str = typer.Option(None, help="Filter by provider (e.g. openai, fallback)"),
) -> None:
    """Extract and list strict Signal Candidates for automated trading."""
    import asyncio

    async def run() -> None:
        from app.research.signals import extract_signal_candidates
        from app.research.watchlists import WatchlistRegistry
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        settings = get_settings()
        monitor_dir = Path(settings.monitor_dir)
        session_factory = build_session_factory(settings.db)

        # Resolve watchlist boosts
        watchlist_boosts = {}
        if watchlist:
            registry = WatchlistRegistry.from_monitor_dir(monitor_dir)
            symbols = registry.get_watchlist(watchlist)
            if symbols:
                # Flat boost of +2 for matching watchlist items
                watchlist_boosts = {s.upper(): 2 for s in symbols}
                console.print(
                    f"[dim]Applying +2 priority boost to {len(symbols)} "
                    f"assets from '{watchlist}'[/dim]"
                )

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=True, limit=limit)

        if provider:
            docs = [d for d in docs if d.provider == provider]

        signals = extract_signal_candidates(
            docs,
            min_priority=min_priority,
            watchlist_boosts=watchlist_boosts
        )

        if not signals:
            console.print(f"[yellow]No signal candidates found in the last {limit} docs.[/yellow]")
            return

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("SigID", width=12)
        table.add_column("Dir", width=6)
        table.add_column("Pri", width=3)
        table.add_column("Asset", width=10)
        table.add_column("Conf", width=5)
        table.add_column("Evidence", width=60)

        for sig in signals:
            direction_color = (
                "green"
                if sig.direction_hint == "bullish"
                else "red"
                if sig.direction_hint == "bearish"
                else "yellow"
            )
            dir_str = f"[{direction_color}]{sig.direction_hint.upper()}[/{direction_color}]"

            # Truncate evidence text cleanly
            evidence = sig.supporting_evidence.replace("\n", " ")
            evidence = evidence[:60] + ("..." if len(evidence) > 60 else "")

            table.add_row(
                sig.signal_id[:12],
                dir_str,
                str(sig.priority),
                sig.target_asset,
                f"{sig.confidence:.2f}",
                evidence,
            )

        console.print(table)
        console.print(f"\n[bold]{len(signals)} Actionable Signals[/bold] ready for execution.")

    asyncio.run(run())


@research_app.command("dataset-export")
def research_dataset_export(
    output_file: str = typer.Argument(..., help="Path to output JSONL file"),
    source_type: str = typer.Option(
        "external_llm",
        help="Filter by analysis source, e.g. external_llm, internal, rule",
    ),
    teacher_only: bool = typer.Option(
        False,
        "--teacher-only",
        help="Export only EXTERNAL_LLM rows (strict mode, I-27)",
    ),
    limit: int = typer.Option(1000, help="Max documents to export"),
) -> None:
    """Export analyzed documents to JSONL for Companion Model tuning."""
    import asyncio
    from pathlib import Path

    async def run() -> None:
        from app.research.datasets import export_training_data
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        settings = get_settings()
        session_factory = build_session_factory(settings.db)

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=True, limit=limit)

        if source_type and source_type != "all":
            docs = [d for d in docs if d.effective_analysis_source.value == source_type]

        if not docs:
            console.print(
                f"[yellow]No analyzed documents found for source_type {source_type}.[/yellow]"
            )
            return

        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        count = export_training_data(docs, out_path, teacher_only=teacher_only)
        console.print(
            f"[green]Successfully exported {count} documents to {out_path.absolute()}[/green]"
        )

    asyncio.run(run())


def _normalize_dataset_type(dataset_type: str) -> str:
    normalized_type = dataset_type.strip().lower()
    allowed_types = {"rule_baseline", "internal_benchmark", "custom"}
    if normalized_type not in allowed_types:
        console.print(
            f"[red]Error:[/red] Unsupported dataset type '{dataset_type}'. "
            "Use rule_baseline, internal_benchmark, or custom."
        )
        raise typer.Exit(1)
    return normalized_type


def _load_dataset_rows(label: str, path_str: str) -> list[dict[str, object]]:
    import json

    from app.research.evaluation import load_jsonl

    try:
        return load_jsonl(path_str)
    except FileNotFoundError as err:
        console.print(f"[red]Error:[/red] {label} dataset file not found: {path_str}")
        raise typer.Exit(1) from err
    except json.JSONDecodeError as err:
        console.print(
            f"[red]Error:[/red] Invalid JSONL content in {label} dataset "
            f"'{path_str}': {err.msg}"
        )
        raise typer.Exit(1) from err
    except OSError as err:
        console.print(
            f"[red]Error:[/red] Could not read {label} dataset '{path_str}': {err}"
        )
        raise typer.Exit(1) from err


def _build_dataset_evaluation_table(title: str, report) -> Table:
    metrics = report.metrics

    table = Table(title=title)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Dataset Type", report.dataset_type)
    table.add_row("Teacher Rows", str(report.teacher_count))
    table.add_row("Candidate Rows", str(report.baseline_count))
    table.add_row("Paired Documents", str(report.paired_count))
    table.add_row("Missing Pairs", str(metrics.missing_pairs))
    table.add_row("Sentiment Agreement", f"{metrics.sentiment_agreement:.2%}")
    table.add_row("Priority MAE", f"{metrics.priority_mae:.4f}")
    table.add_row("Relevance MAE", f"{metrics.relevance_mae:.4f}")
    table.add_row("Impact MAE", f"{metrics.impact_mae:.4f}")
    table.add_row("Tag Overlap Mean", f"{metrics.tag_overlap_mean:.4f}")
    return table


def _print_dataset_warnings(
    teacher_rows: list[dict[str, object]],
    candidate_rows: list[dict[str, object]],
    paired_count: int,
) -> None:
    if not teacher_rows:
        console.print("[yellow]Teacher dataset is empty.[/yellow]")
    if not candidate_rows:
        console.print("[yellow]Candidate dataset is empty.[/yellow]")
    if paired_count == 0:
        console.print("[yellow]No overlapping document_id pairs found.[/yellow]")


def _get_companion_provider(settings, endpoint_override: str | None = None):
    from app.analysis.factory import create_provider
    from app.core.settings import ProviderSettings

    effective_settings = settings.model_copy(deep=True)
    if endpoint_override:
        validated = ProviderSettings(companion_model_endpoint=endpoint_override)
        effective_settings.providers.companion_model_endpoint = validated.companion_model_endpoint

    provider = create_provider("companion", effective_settings)
    if provider is None:
        console.print(
            "[red]Error:[/red] No local companion endpoint configured. "
            "Set COMPANION_MODEL_ENDPOINT or pass --endpoint."
        )
        raise typer.Exit(1)
    return provider


def _print_companion_promotion_readiness(report) -> None:
    if report.dataset_type != "internal_benchmark" or report.paired_count == 0:
        return

    from app.research.evaluation import validate_promotion

    promotion = validate_promotion(report.metrics)

    prom_table = Table(title="Companion Promotion Readiness (Sprint 7 Gates)")
    prom_table.add_column("Gate", style="cyan")
    prom_table.add_column("Required", style="yellow")
    prom_table.add_column("Status", style="bold")

    def status_str(passed: bool) -> str:
        return "[green]PASS[/green]" if passed else "[red]FAIL[/red]"

    prom_table.add_row("Sentiment", ">= 0.85", status_str(promotion.sentiment_pass))
    prom_table.add_row("Priority MAE", "<= 1.50", status_str(promotion.priority_pass))
    prom_table.add_row("Relevance MAE", "<= 0.15", status_str(promotion.relevance_pass))
    prom_table.add_row("Impact MAE", "<= 0.20", status_str(promotion.impact_pass))
    prom_table.add_row("Tag Overlap", ">= 0.30", status_str(promotion.tag_overlap_pass))

    console.print(prom_table)

    if promotion.is_promotable:
        console.print("\n[bold green]PROMOTABLE[/bold green] — quantitative gates passed.")
    else:
        console.print("\n[bold red]NOT PROMOTABLE[/bold red] — one or more gates failed.")

    console.print(
        "[dim]Manual I-34 verification remains required; no automatic promotion occurs.[/dim]"
    )


@research_app.command("evaluate-datasets")
def research_evaluate_datasets(
    teacher_file: str = typer.Argument(..., help="Path to teacher JSONL file"),
    candidate_file: str = typer.Argument(..., help="Path to candidate JSONL file"),
    dataset_type: str = typer.Option(
        "rule_baseline",
        "--dataset-type",
        help="Dataset comparison type: rule_baseline, internal_benchmark, custom",
    ),
    save_report: str | None = typer.Option(
        None,
        "--save-report",
        help="Path to persist EvaluationReport as JSON (for check-promotion and audit trail)",
    ),
    save_artifact: str | None = typer.Option(
        None,
        "--save-artifact",
        help="Path to persist companion benchmark manifest JSON",
    ),
) -> None:
    """Compare two exported JSONL datasets and print offline evaluation metrics."""
    import json

    from app.research.evaluation import compare_datasets, load_jsonl

    normalized_type = dataset_type.strip().lower()
    allowed_types = {"rule_baseline", "internal_benchmark", "custom"}
    if normalized_type not in allowed_types:
        console.print(
            f"[red]Error:[/red] Unsupported dataset type '{dataset_type}'. "
            "Use rule_baseline, internal_benchmark, or custom."
        )
        raise typer.Exit(1)

    def load_rows(label: str, path_str: str) -> list[dict[str, object]]:
        try:
            return load_jsonl(path_str)
        except FileNotFoundError as err:
            console.print(f"[red]Error:[/red] {label} dataset file not found: {path_str}")
            raise typer.Exit(1) from err
        except json.JSONDecodeError as err:
            console.print(
                f"[red]Error:[/red] Invalid JSONL content in {label} dataset "
                f"'{path_str}': {err.msg}"
            )
            raise typer.Exit(1) from err
        except OSError as err:
            console.print(
                f"[red]Error:[/red] Could not read {label} dataset '{path_str}': {err}"
            )
            raise typer.Exit(1) from err

    teacher_rows = load_rows("Teacher", teacher_file)
    candidate_rows = load_rows("Candidate", candidate_file)

    if not teacher_rows:
        console.print("[yellow]Teacher dataset is empty.[/yellow]")
    if not candidate_rows:
        console.print("[yellow]Candidate dataset is empty.[/yellow]")

    report = compare_datasets(teacher_rows, candidate_rows, dataset_type=normalized_type)
    if report.paired_count == 0:
        console.print("[yellow]No overlapping document_id pairs found.[/yellow]")

    metrics = report.metrics

    table = Table(title="Dataset Evaluation Metrics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Dataset Type", report.dataset_type)
    table.add_row("Teacher Rows", str(report.teacher_count))
    table.add_row("Candidate Rows", str(report.baseline_count))
    table.add_row("Paired Documents", str(report.paired_count))
    table.add_row("Missing Pairs", str(metrics.missing_pairs))
    table.add_row("Sentiment Agreement", f"{metrics.sentiment_agreement:.2%}")
    table.add_row("Priority MAE", f"{metrics.priority_mae:.4f}")
    table.add_row("Relevance MAE", f"{metrics.relevance_mae:.4f}")
    table.add_row("Impact MAE", f"{metrics.impact_mae:.4f}")
    table.add_row("Tag Overlap Mean", f"{metrics.tag_overlap_mean:.4f}")

    console.print(table)

    if report.dataset_type == "internal_benchmark" and report.paired_count > 0:
        from app.research.evaluation import validate_promotion
        promotion = validate_promotion(metrics)

        prom_table = Table(title="Companion Promotion Readiness (Sprint 7 Gates)")
        prom_table.add_column("Gate", style="cyan")
        prom_table.add_column("Required", style="yellow")
        prom_table.add_column("Status", style="bold")

        def status_str(passed: bool) -> str:
            return "[green]PASS[/green]" if passed else "[red]FAIL[/red]"

        prom_table.add_row("Sentiment", ">= 0.85", status_str(promotion.sentiment_pass))
        prom_table.add_row("Priority MAE", "<= 1.50", status_str(promotion.priority_pass))
        prom_table.add_row("Relevance MAE", "<= 0.15", status_str(promotion.relevance_pass))
        prom_table.add_row("Impact MAE", "<= 0.20", status_str(promotion.impact_pass))
        prom_table.add_row("Tag Overlap", ">= 0.30", status_str(promotion.tag_overlap_pass))

        console.print(prom_table)

        if promotion.is_promotable:
            console.print("\n[bold green]PROMOTABLE — all gates passed.[/bold green]")
        else:
            console.print("\n[bold red]NOT PROMOTABLE — one or more gates failed.[/bold red]")

    if save_report:
        from app.research.evaluation import save_evaluation_report
        saved = save_evaluation_report(
            report,
            save_report,
            teacher_dataset=teacher_file,
            candidate_dataset=candidate_file,
        )
        console.print(f"[dim]Evaluation report saved: {saved}[/dim]")

    if save_artifact:
        from app.research.evaluation import save_benchmark_artifact
        artifact = save_benchmark_artifact(
            save_artifact,
            teacher_dataset=teacher_file,
            candidate_dataset=candidate_file,
            report=report,
            report_path=save_report,
        )
        console.print(f"[dim]Benchmark artifact saved: {artifact}[/dim]")


@research_app.command("benchmark-companion")
def research_benchmark_companion(
    teacher_file: str = typer.Argument(..., help="Path to teacher JSONL file"),
    candidate_file: str = typer.Argument(..., help="Path to candidate/internal JSONL file"),
    dataset_type: str = typer.Option(
        "internal_benchmark",
        "--dataset-type",
        help="Dataset comparison type: internal_benchmark, rule_baseline, custom",
    ),
    report_out: str | None = typer.Option(
        None,
        "--report-out",
        help="Optional path to save a structured benchmark report JSON",
    ),
    artifact_out: str | None = typer.Option(
        None,
        "--artifact-out",
        help="Optional path to save a benchmark artifact manifest JSON",
    ),
) -> None:
    """Benchmark companion outputs against teacher datasets and optionally save artifacts."""
    from pathlib import Path

    from app.research.evaluation import (
        compare_datasets,
        save_benchmark_artifact,
        save_evaluation_report,
    )

    normalized_type = _normalize_dataset_type(dataset_type)
    teacher_rows = _load_dataset_rows("Teacher", teacher_file)
    candidate_rows = _load_dataset_rows("Candidate", candidate_file)

    report = compare_datasets(teacher_rows, candidate_rows, dataset_type=normalized_type)
    _print_dataset_warnings(teacher_rows, candidate_rows, report.paired_count)
    console.print(_build_dataset_evaluation_table("Companion Benchmark Metrics", report))
    _print_companion_promotion_readiness(report)

    saved_report_path: Path | None = None
    if report_out:
        saved_report_path = save_evaluation_report(
            report,
            report_out,
            teacher_dataset=teacher_file,
            candidate_dataset=candidate_file,
        )
        console.print(f"[green]Saved benchmark report to {saved_report_path.resolve()}[/green]")

    if artifact_out:
        saved_artifact_path = save_benchmark_artifact(
            artifact_out,
            teacher_dataset=teacher_file,
            candidate_dataset=candidate_file,
            report=report,
            report_path=saved_report_path,
        )
        console.print(
            f"[green]Saved benchmark artifact to {saved_artifact_path.resolve()}[/green]"
        )


@research_app.command("check-promotion")
def research_check_promotion(
    report_file: str = typer.Argument(
        ..., help="Path to evaluation_report.json produced by evaluate-datasets --save-report"
    ),
) -> None:
    """Check whether a saved evaluation report meets companion promotion thresholds.

    Exits 0 if all five quantitative gates pass (promotable).
    Exits 1 if any gate fails — human review required.

    Note: Gate I-34 (false-actionable rate) requires separate manual verification
    via `research evaluate`. See docs/benchmark_promotion_contract.md.
    """
    import json
    from pathlib import Path

    from app.research.evaluation import EvaluationMetrics, validate_promotion

    report_path = Path(report_file)
    if not report_path.exists():
        console.print(f"[red]Report file not found: {report_path}[/red]")
        raise typer.Exit(1)

    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
        m_raw = data["metrics"]
        metrics = EvaluationMetrics(
            sentiment_agreement=float(m_raw["sentiment_agreement"]),
            priority_mae=float(m_raw["priority_mae"]),
            relevance_mae=float(m_raw["relevance_mae"]),
            impact_mae=float(m_raw["impact_mae"]),
            tag_overlap_mean=float(m_raw["tag_overlap_mean"]),
            sample_count=int(m_raw.get("sample_count", 0)),
            missing_pairs=int(m_raw.get("missing_pairs", 0)),
        )
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        console.print(f"[red]Could not parse report file:[/red] {e}")
        raise typer.Exit(1) from e

    validation = validate_promotion(metrics)

    gate_table = Table(title="Promotion Gate Check")
    gate_table.add_column("Gate", style="cyan")
    gate_table.add_column("Threshold", justify="right")
    gate_table.add_column("Actual", justify="right")
    gate_table.add_column("Status", justify="center")

    def _gate_status(passed: bool) -> str:
        return "[green]PASS[/green]" if passed else "[red]FAIL[/red]"

    gate_table.add_row(
        "Sentiment Agreement", ">= 0.850",
        f"{metrics.sentiment_agreement:.3f}",
        _gate_status(validation.sentiment_pass),
    )
    gate_table.add_row(
        "Priority MAE", "<= 1.500",
        f"{metrics.priority_mae:.3f}",
        _gate_status(validation.priority_pass),
    )
    gate_table.add_row(
        "Relevance MAE", "<= 0.150",
        f"{metrics.relevance_mae:.3f}",
        _gate_status(validation.relevance_pass),
    )
    gate_table.add_row(
        "Impact MAE", "<= 0.200",
        f"{metrics.impact_mae:.3f}",
        _gate_status(validation.impact_pass),
    )
    gate_table.add_row(
        "Tag Overlap", ">= 0.300",
        f"{metrics.tag_overlap_mean:.3f}",
        _gate_status(validation.tag_overlap_pass),
    )

    console.print(gate_table)
    console.print(f"\nSamples evaluated: {metrics.sample_count}")
    console.print(
        "[yellow]Note: Gate I-34 (actionable false-positive rate) requires manual "
        "verification via `research evaluate`. See benchmark_promotion_contract.md.[/yellow]"
    )

    if validation.is_promotable:
        console.print("\n[bold green]PROMOTABLE[/bold green] - all quantitative gates passed.")
        console.print(
            "[dim]Reminder: Manual I-34 verification still required before promotion.[/dim]"
        )
    else:
        failed = sum([
            not validation.sentiment_pass,
            not validation.priority_pass,
            not validation.relevance_pass,
            not validation.impact_pass,
            not validation.tag_overlap_pass,
        ])
        console.print(f"\n[bold red]NOT PROMOTABLE[/bold red] - {failed} gate(s) failed.")
        raise typer.Exit(1)


@research_app.command("evaluate")
def research_evaluate(
    teacher_source: str = typer.Option("external_llm", help="The baseline extraction source"),
    limit: int = typer.Option(50, help="Number of documents to evaluate over"),
) -> None:
    """Run the internal companion model against teacher outputs and print metrics."""
    import asyncio

    async def run() -> None:
        from app.analysis.keywords.engine import KeywordEngine
        from app.analysis.pipeline import AnalysisPipeline
        from app.research.evaluation import compare_outputs
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        settings = get_settings()
        monitor_dir = Path(settings.monitor_dir)
        session_factory = build_session_factory(settings.db)

        console.print(f"[bold]Loading {limit} teacher documents...[/bold]")
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=True, limit=limit)

        teacher_docs = [d for d in docs if d.effective_analysis_source.value == teacher_source]
        if not teacher_docs:
            console.print(
                f"[yellow]No documents analyzed by source '{teacher_source}' found.[/yellow]"
            )
            return

        keyword_engine = KeywordEngine.from_monitor_dir(monitor_dir)
        pipeline = AnalysisPipeline(keyword_engine, provider=None, run_llm=False)

        companion_docs = []
        for d in teacher_docs:
            comp_doc = d.model_copy()
            # Erase existing scores so we know we are running fresh
            comp_doc.is_analyzed = False
            comp_doc.sentiment_score = None
            comp_doc.priority_score = None

            res = await pipeline.run(comp_doc)
            res.apply_to_document()
            companion_docs.append(res.document)

        console.print(f"[bold]Evaluating {len(teacher_docs)} outputs...[/bold]")
        metrics = compare_outputs(teacher_docs, companion_docs)

        from rich.table import Table
        table = Table(title="Companion Evaluation Metrics")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green", justify="right")

        table.add_row("Document Count", str(metrics.document_count))
        table.add_row("Matched Sentiments", str(metrics.matched_sentiments))
        table.add_row("Matched Actionable", str(metrics.matched_actionable))
        table.add_row("Sentiment Accuracy", f"{metrics.sentiment_accuracy:.2%}")
        table.add_row("Actionable Accuracy", f"{metrics.actionable_accuracy:.2%}")
        table.add_row("Priority MSE", f"{metrics.priority_mse:.4f}")
        table.add_row("Relevance MSE", f"{metrics.relevance_mse:.4f}")
        table.add_row("Impact MSE", f"{metrics.impact_mse:.4f}")
        table.add_row("Novelty MSE", f"{metrics.novelty_mse:.4f}")

        console.print(table)

    asyncio.run(run())


if __name__ == "__main__":
    app()
