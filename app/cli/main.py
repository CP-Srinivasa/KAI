import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import typer
from rich.console import Console
from rich.table import Table

from app.alerts.audit import (
    AlertAuditRecord,
    AlertOutcomeAnnotation,
    OutcomeLabel,
    append_outcome_annotation,
    load_alert_audits,
    load_outcome_annotations,
)
from app.alerts.eligibility import evaluate_directional_eligibility
from app.alerts.hold_metrics import build_hold_metrics_report, write_hold_metrics_report
from app.alerts.offline_baseline import (
    build_offline_baseline_report,
    write_offline_baseline_report,
)
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.ingestion.base.interfaces import FetchResult
from app.ingestion.rss.service import RSSCollectedFeed
from app.messaging.exchange_relay import (
    build_signal_pipeline_status,
    relay_exchange_outbox_once,
)
from app.storage.db.session import build_session_factory
from app.storage.document_ingest import IngestPersistStats, persist_fetch_result

__all__ = ["app"]

logger = logging.getLogger(__name__)

app = typer.Typer(name="trading-bot", help="AI Analyst Trading Bot CLI", no_args_is_help=True)
console = Console()

ingest_app = typer.Typer(help="Ingestion commands", no_args_is_help=True)
pipeline_app = typer.Typer(help="End-to-end pipeline commands", no_args_is_help=True)
analyze_app = typer.Typer(help="Analysis commands", no_args_is_help=True)
signals_app = typer.Typer(help="Signal commands", no_args_is_help=True)
query_app = typer.Typer(help="Compatibility alias for analysis commands", no_args_is_help=True)
alerts_app = typer.Typer(help="Alert commands", no_args_is_help=True)

app.add_typer(ingest_app, name="ingest")
app.add_typer(pipeline_app, name="pipeline", hidden=True)
app.add_typer(analyze_app, name="analyze")
app.add_typer(signals_app, name="signals")
app.add_typer(query_app, name="query", hidden=True)
app.add_typer(alerts_app, name="alerts")

# Lazy import to avoid heavy trading deps at top-level
from app.cli.commands.trading import trading_app  # noqa: E402

app.add_typer(trading_app, name="trading")


@app.callback()
def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)


# ── ingest rss ────────────────────────────────────────────────────────────────


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
    from app.pipeline.service import collect_feed_for_pipeline

    return await collect_feed_for_pipeline(
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


# ── pipeline run ──────────────────────────────────────────────────────────────


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
        console.print(f"  Alerts:    {stats.alerts_fired_count}")
        console.print(f"  Skipped:   {stats.skipped_count}")
        if stats.failed_count:
            console.print(f"  [red]Failed:  {stats.failed_count}[/red]")
        if stats.priority_distribution:
            dist = ", ".join(
                f"P{score}:{count}" for score, count in sorted(stats.priority_distribution.items())
            )
            console.print(f"  Priority:  {dist}")

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


@pipeline_app.command("youtube")
def pipeline_youtube(
    channel_url: str = typer.Argument(..., help="YouTube channel URL (e.g. https://youtube.com/@Bankless)"),
    source_id: str = typer.Option("youtube", help="Source ID"),
    source_name: str = typer.Option("YouTube", help="Source name"),
    max_results: int = typer.Option(5, help="Max videos to fetch per channel"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip DB writes"),
    top_n: int = typer.Option(5, help="Top results to display"),
) -> None:
    """Fetch YouTube channel videos, extract transcripts, analyze, and alert."""
    import asyncio

    async def run() -> None:
        from app.analysis.keywords.engine import KeywordEngine
        from app.integrations.openai.provider import OpenAIAnalysisProvider
        from app.pipeline.service import run_youtube_pipeline

        settings = get_settings()
        monitor_dir = Path(settings.monitor_dir)

        if not settings.providers.youtube_api_key:
            console.print("[red]Error:[/red] YOUTUBE_API_KEY not set in .env")
            raise typer.Exit(1)

        keyword_engine = KeywordEngine.from_monitor_dir(monitor_dir)

        provider = None
        if settings.providers.openai_api_key:
            provider = OpenAIAnalysisProvider.from_settings(settings.providers)

        session_factory = build_session_factory(settings.db)

        stats = await run_youtube_pipeline(
            channel_url,
            session_factory=session_factory,
            keyword_engine=keyword_engine,
            provider=provider,
            api_key=settings.providers.youtube_api_key,
            source_id=source_id,
            source_name=source_name,
            max_results=max_results,
            dry_run=dry_run,
        )

        console.print(f"\n[bold green]YouTube pipeline complete:[/bold green] {channel_url}")
        console.print(f"  Fetched:   {stats.fetched_count}")
        console.print(f"  Saved:     {stats.saved_count}")
        console.print(f"  Analyzed:  {stats.analyzed_count}")
        console.print(f"  Alerts:    {stats.alerts_fired_count}")
        console.print(f"  Skipped:   {stats.skipped_count}")
        if stats.priority_distribution:
            dist = ", ".join(
                f"P{score}:{count}" for score, count in sorted(stats.priority_distribution.items())
            )
            console.print(f"  Priority:  {dist}")

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


@pipeline_app.command("newsdata")
def pipeline_newsdata(
    query: str = typer.Argument(..., help="Search query (e.g. 'crypto bitcoin ethereum')"),
    source_id: str = typer.Option("newsdata", help="Source ID"),
    source_name: str = typer.Option("NewsData.io", help="Source name"),
    language: str = typer.Option("en", help="Language code (en, de, etc.)"),
    category: str = typer.Option("business", help="Category filter (business, technology, etc.)"),
    size: int = typer.Option(10, help="Number of articles to fetch"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip DB writes"),
    top_n: int = typer.Option(5, help="Top results to display"),
) -> None:
    """Fetch articles from NewsData.io API, analyze, and alert."""
    import asyncio

    async def run() -> None:
        from app.analysis.keywords.engine import KeywordEngine
        from app.integrations.openai.provider import OpenAIAnalysisProvider
        from app.pipeline.service import run_newsdata_pipeline

        settings = get_settings()
        monitor_dir = Path(settings.monitor_dir)

        if not settings.providers.newsdata_api_key:
            console.print("[red]Error:[/red] NEWSDATA_API_KEY not set in .env")
            raise typer.Exit(1)

        keyword_engine = KeywordEngine.from_monitor_dir(monitor_dir)

        provider = None
        if settings.providers.openai_api_key:
            provider = OpenAIAnalysisProvider.from_settings(settings.providers)

        session_factory = build_session_factory(settings.db)

        stats = await run_newsdata_pipeline(
            query,
            session_factory=session_factory,
            keyword_engine=keyword_engine,
            provider=provider,
            api_key=settings.providers.newsdata_api_key,
            source_id=source_id,
            source_name=source_name,
            language=language,
            category=category,
            size=size,
            dry_run=dry_run,
        )

        console.print(f"\n[bold green]NewsData.io pipeline complete:[/bold green] query='{query}'")
        console.print(f"  Fetched:   {stats.fetched_count}")
        console.print(f"  Saved:     {stats.saved_count}")
        console.print(f"  Analyzed:  {stats.analyzed_count}")
        console.print(f"  Alerts:    {stats.alerts_fired_count}")
        console.print(f"  Skipped:   {stats.skipped_count}")
        if stats.priority_distribution:
            dist = ", ".join(
                f"P{score}:{count}" for score, count in sorted(stats.priority_distribution.items())
            )
            console.print(f"  Priority:  {dist}")

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


# ── query analyze-pending ─────────────────────────────────────────────────────


@app.command("pipeline-run")
def pipeline_run_alias(
    url: str = typer.Argument(..., help="RSS feed URL to process end-to-end"),
    source_id: str = typer.Option("manual", help="Source ID"),
    source_name: str = typer.Option("Manual", help="Source name"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip DB writes; preview only"),
    top_n: int = typer.Option(5, help="Top results to display by priority score"),
) -> None:
    """Top-level alias for `pipeline run`."""
    pipeline_run(
        url=url,
        source_id=source_id,
        source_name=source_name,
        dry_run=dry_run,
        top_n=top_n,
    )


@analyze_app.command("pending")
@query_app.command("analyze-pending")
def analyze_pending(
    limit: int = typer.Option(50, help="Max documents to analyze"),
    provider: str = typer.Option("openai", help="LLM Provider to use (openai, anthropic, gemini)"),
    no_alerts: bool = typer.Option(False, "--no-alerts", help="Skip alert dispatch after analysis"),
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

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.get_pending_documents(limit=limit)

        if not docs:
            console.print("[green]No pending documents to analyze.[/green]")
            return

        console.print(f"[bold]Analyzing {len(docs)} documents...[/bold]")

        results = await pipeline.run_batch(docs)

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
                        pass
                    continue

                res.apply_to_document()

                # I-12: defensive guard — analysis_result=None MUST NOT produce status=ANALYZED
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
                        pass

        console.print(
            f"[bold green]Analysis complete![/bold green] "
            f"{success_count} success, {error_count} failed."
        )

        if not no_alerts and success_count > 0:
            from app.alerts.service import AlertService

            alert_service = AlertService.from_settings(settings)
            alert_count = 0
            for res in results:
                if not res.success or res.analysis_result is None:
                    continue
                try:
                    deliveries = await alert_service.process_document(
                        res.document,
                        res.analysis_result,
                        spam_probability=res.document.spam_probability or 0.0,
                    )
                    if deliveries:
                        alert_count += 1
                except Exception as exc:
                    logger.warning("Alert dispatch failed for doc %s: %s", res.document.id, exc)

            if alert_count:
                console.print(f"[cyan]Alerts dispatched: {alert_count}[/cyan]")

    asyncio.run(run())


# ── alerts send-test ──────────────────────────────────────────────────────────


@signals_app.command("extract")
def signals_extract(
    limit: int = typer.Option(50, help="Max signal candidates to display"),
    min_priority: int = typer.Option(8, help="Minimum effective priority"),
) -> None:
    """Extract signal candidates from analyzed documents."""
    import asyncio

    async def run() -> None:
        from app.core.signals import extract_signal_candidates
        from app.storage.repositories.document_repo import DocumentRepository

        settings = get_settings()
        session_factory = build_session_factory(settings.db)
        fetch_limit = max(limit * 5, limit)

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=True, limit=fetch_limit)

        candidates = extract_signal_candidates(docs, min_priority=min_priority)
        console.print(
            f"[bold]{len(candidates)} signal candidates[/bold] "
            f"(from {len(docs)} analyzed docs, min_priority={min_priority})"
        )
        if not candidates:
            console.print("[yellow]No signal candidates found.[/yellow]")
            return

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Priority", width=8)
        table.add_column("Direction", width=10)
        table.add_column("Asset", width=12)
        table.add_column("Confidence", width=10)
        table.add_column("Document ID")

        for c in candidates[:limit]:
            table.add_row(
                str(c.priority),
                c.direction_hint,
                c.target_asset,
                f"{c.confidence:.2f}",
                c.document_id,
            )
        console.print(table)

    asyncio.run(run())


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
            status = "[green]OK sent[/green]" if r.success else f"[red]FAIL: {r.error}[/red]"
            console.print(f"  [{r.channel}] {status}")

    asyncio.run(run())


# ── alerts evaluate-pending ───────────────────────────────────────────────────


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
            status = "[green]OK sent[/green]" if r.success else f"[red]FAIL: {r.error}[/red]"
            console.print(f"  [{r.channel}] {status}")

    asyncio.run(run())


@alerts_app.command("hold-report")
def alerts_hold_report(
    artifacts_dir: str = typer.Option("artifacts", help="Artifacts directory"),
    output_dir: str = typer.Option(
        "artifacts/ph5_hold", help="Output directory for hold report artifacts"
    ),
) -> None:
    """Build and write PH5 hold metrics report from local artifact files."""
    artifacts_path = Path(artifacts_dir)
    report = build_hold_metrics_report(
        alert_audit_path=artifacts_path / "alert_audit.jsonl",
        alert_outcomes_path=artifacts_path / "alert_outcomes.jsonl",
        trading_loop_audit_path=artifacts_path / "trading_loop_audit.jsonl",
        paper_execution_audit_path=artifacts_path / "paper_execution_audit.jsonl",
    )
    json_out, md_out = write_hold_metrics_report(report, output_dir=Path(output_dir))

    gate = report["hold_gate_evaluation"]
    hit = report["alert_hit_rate_evidence"]
    quality = report["signal_quality_validation"]
    paper = report["paper_trading_evidence"]
    console.print(f"[green]Report written:[/green] {json_out}")
    console.print(f"[green]Summary written:[/green] {md_out}")
    console.print(
        f"[bold]Gate:[/bold] {gate['overall_status']} | "
        f"resolved directional {hit['resolved_directional_documents']}/"
        f"{hit['minimum_resolved_directional_alerts_for_gate']} | "
        f"paper cycles {paper['loop_metrics']['total_cycles']}"
    )
    console.print(
        "[bold]Quality:[/bold] "
        f"actionable_rate={quality['directional_actionable_rate_pct']}% | "
        f"precision={quality['resolved_precision_pct']}% | "
        f"false_positive={quality['resolved_false_positive_rate_pct']}% | "
        f"priority_corr={quality['priority_hit_correlation']} | "
        f"real_price_cycles={quality['paper_real_price_cycle_count']}"
    )
    console.print(
        "[bold]Validation gaps:[/bold] " + ", ".join(quality["validation_gaps"])
    )


@alerts_app.command("baseline-report")
def alerts_baseline_report(
    input_path: str = typer.Option(
        "artifacts/ph4b_tier3_shadow.jsonl", help="Input JSONL dataset path"
    ),
    output_dir: str = typer.Option(
        "artifacts/ph5_baseline", help="Output directory for baseline artifacts"
    ),
    threshold_pct: float = typer.Option(5.0, help="Absolute move threshold (percent)"),
    horizon_hours: int = typer.Option(24, help="Evaluation horizon in hours"),
    timeout_seconds: int = typer.Option(10, help="CoinGecko request timeout"),
    max_rows: int | None = typer.Option(None, help="Optional cap on candidate rows"),
) -> None:
    """Build offline baseline report from historical CoinGecko move data."""
    import asyncio

    report = asyncio.run(
        build_offline_baseline_report(
            input_path=Path(input_path),
            threshold_pct=threshold_pct,
            horizon_hours=horizon_hours,
            timeout_seconds=timeout_seconds,
            max_rows=max_rows,
        )
    )
    json_out, md_out = write_offline_baseline_report(
        report,
        output_dir=Path(output_dir),
    )

    console.print(f"[green]Baseline report written:[/green] {json_out}")
    console.print(f"[green]Baseline summary written:[/green] {md_out}")
    console.print(
        f"[bold]Status:[/bold] {report.get('status')} | "
        f"resolved={report.get('resolved_candidates')} | "
        f"priority_abs_corr={report.get('priority_abs_move_correlation')}"
    )


@alerts_app.command("pending-annotations")
def alerts_pending_annotations(
    limit: int = typer.Option(20, help="Max rows to print"),
    min_age_hours: float = typer.Option(
        0.0, help="Only include alerts at least this many hours old"
    ),
    artifacts_dir: str = typer.Option("artifacts", help="Artifacts directory"),
) -> None:
    """List directional alerts without outcome annotation (deduped by document_id)."""
    records = load_alert_audits(Path(artifacts_dir))
    annotations = load_outcome_annotations(Path(artifacts_dir))
    latest_ann_by_doc = {a.document_id: a.outcome for a in annotations}

    latest_directional_by_doc: dict[str, Any] = {}
    for rec in records:
        sentiment = (rec.sentiment_label or "").lower()
        if rec.is_digest or sentiment not in {"bullish", "bearish"}:
            continue
        if rec.directional_eligible is False:
            continue
        if rec.directional_eligible is None:
            legacy_check = evaluate_directional_eligibility(
                sentiment_label=rec.sentiment_label,
                affected_assets=list(rec.affected_assets or []),
            )
            if legacy_check.directional_eligible is not True:
                continue
        prev = latest_directional_by_doc.get(rec.document_id)
        if prev is None or rec.dispatched_at > prev.dispatched_at:
            latest_directional_by_doc[rec.document_id] = rec

    pending = [
        rec
        for rec in latest_directional_by_doc.values()
        if rec.document_id not in latest_ann_by_doc
    ]
    if min_age_hours > 0:
        now = datetime.now(UTC)

        def _is_old_enough(rec: Any) -> bool:
            try:
                ts = datetime.fromisoformat(rec.dispatched_at.replace("Z", "+00:00"))
            except ValueError:
                return False
            age_h = (now - ts).total_seconds() / 3600.0
            return age_h >= min_age_hours

        pending = [rec for rec in pending if _is_old_enough(rec)]

    pending.sort(key=lambda r: r.dispatched_at, reverse=True)

    console.print(
        f"[bold]{len(pending)} pending directional alerts[/bold] "
        f"(limit={limit}, total directional={len(latest_directional_by_doc)}, "
        f"min_age_hours={min_age_hours:g})"
    )
    if not pending:
        console.print("[green]No pending annotations.[/green]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Document ID")
    table.add_column("Dispatched At")
    table.add_column("Age(h)", width=8)
    table.add_column("Sentiment", width=10)
    table.add_column("Priority", width=8)
    table.add_column("Assets")
    now = datetime.now(UTC)
    for rec in pending[:limit]:
        age_h = "-"
        try:
            ts = datetime.fromisoformat(rec.dispatched_at.replace("Z", "+00:00"))
            age_h = f"{((now - ts).total_seconds() / 3600.0):.1f}"
        except ValueError:
            pass
        table.add_row(
            rec.document_id,
            rec.dispatched_at,
            age_h,
            rec.sentiment_label or "-",
            str(rec.priority) if rec.priority is not None else "-",
            ", ".join(rec.affected_assets) if rec.affected_assets else "-",
        )
    console.print(table)


@alerts_app.command("annotate")
def alerts_annotate(
    document_id: str = typer.Argument(..., help="Document ID from alert_audit"),
    outcome: str = typer.Argument(..., help="One of: hit, miss, inconclusive"),
    asset: str | None = typer.Option(None, help="Optional asset symbol"),
    note: str | None = typer.Option(None, help="Optional operator note"),
    artifacts_dir: str = typer.Option("artifacts", help="Artifacts directory"),
) -> None:
    """Append an outcome annotation for a directional alert document."""
    normalized = outcome.strip().lower()
    if normalized not in {"hit", "miss", "inconclusive"}:
        console.print(
            "[red]Invalid outcome.[/red] Use one of: hit, miss, inconclusive."
        )
        raise typer.Exit(2)

    existing = [
        a for a in load_outcome_annotations(Path(artifacts_dir)) if a.document_id == document_id
    ]
    if existing:
        console.print(
            "[yellow]Existing annotation found for this document. "
            "A new append-only entry will be used as latest value.[/yellow]"
        )

    annotation = AlertOutcomeAnnotation(
        document_id=document_id,
        outcome=cast(OutcomeLabel, normalized),
        asset=asset,
        note=note,
    )
    append_outcome_annotation(annotation, Path(artifacts_dir))
    console.print(
        "[green]Annotation written.[/green] "
        f"document_id={document_id} outcome={normalized}"
    )


@alerts_app.command("auto-check")
def alerts_auto_check(
    threshold_pct: float = typer.Option(
        2.0, "--threshold-pct", help="Min absolute price change (%) for hit/miss"
    ),
    horizon_hours: int = typer.Option(
        24, "--horizon-hours", help="Evaluation window in hours from alert dispatch"
    ),
    min_age_hours: float = typer.Option(
        24.0,
        "--min-age-hours",
        help="Only check alerts at least this many hours old (default: 24h window)",
    ),
    dry_run: bool = typer.Option(
        True, "--dry-run/--apply", help="Preview only (default) or apply annotations"
    ),
    artifacts_dir: str = typer.Option("artifacts", help="Artifacts directory"),
    timeout_seconds: int = typer.Option(10, help="CoinGecko request timeout"),
) -> None:
    """Check pending directional alerts against CoinGecko price moves.

    Preferred path: compare historical price at dispatch vs dispatch+horizon.
    Fallback path: use ticker 24h move when historical range data is unavailable.
    """
    import asyncio

    from app.alerts.price_check import check_alert_price_moves

    records = load_alert_audits(Path(artifacts_dir))
    annotations = load_outcome_annotations(Path(artifacts_dir))
    annotated_ids = {a.document_id for a in annotations}

    # Filter to pending directional alerts (deduplicated by document_id)
    latest_by_doc: dict[str, AlertAuditRecord] = {}
    for rec in records:
        sentiment = (rec.sentiment_label or "").lower()
        if rec.is_digest or sentiment not in {"bullish", "bearish"}:
            continue
        if rec.directional_eligible is False:
            continue
        if rec.directional_eligible is None:
            legacy_check = evaluate_directional_eligibility(
                sentiment_label=rec.sentiment_label,
                affected_assets=list(rec.affected_assets or []),
            )
            if legacy_check.directional_eligible is not True:
                continue
        if rec.document_id in annotated_ids:
            continue
        prev = latest_by_doc.get(rec.document_id)
        if prev is None or rec.dispatched_at > prev.dispatched_at:
            latest_by_doc[rec.document_id] = rec

    pending = list(latest_by_doc.values())
    if min_age_hours > 0:
        now = datetime.now(UTC)
        age_filtered: list[AlertAuditRecord] = []
        for rec in pending:
            try:
                ts = datetime.fromisoformat(rec.dispatched_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            age_h = (now - ts).total_seconds() / 3600.0
            if age_h >= min_age_hours:
                age_filtered.append(rec)
        pending = age_filtered

    if not pending:
        console.print("[green]No pending directional alerts to check.[/green]")
        return

    console.print(
        f"[bold]Checking {len(pending)} pending directional alerts "
        f"(threshold={threshold_pct}%, horizon={horizon_hours}h, "
        f"min_age_hours={min_age_hours:g})...[/bold]"
    )

    results = asyncio.run(
        check_alert_price_moves(
            pending,
            threshold_pct=threshold_pct,
            horizon_hours=horizon_hours,
            timeout_seconds=timeout_seconds,
        )
    )

    if not results:
        console.print("[yellow]No price data available for any alerts.[/yellow]")
        return

    # Display results table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Document ID", width=20)
    table.add_column("Asset", width=8)
    table.add_column("Sentiment", width=10)
    table.add_column("Price(T+h)", width=12)
    table.add_column("Move %", width=8)
    table.add_column("Mode", width=20)
    table.add_column("Suggestion", width=14)
    table.add_column("Reason")

    for r in results:
        price_str = f"${r.current_price:,.2f}" if r.current_price else "-"
        change_str = f"{r.change_pct_24h:+.1f}%" if r.change_pct_24h is not None else "-"
        style = {"hit": "green", "miss": "red", "inconclusive": "yellow"}.get(
            r.suggested_outcome, ""
        )
        table.add_row(
            r.document_id[:20],
            r.asset,
            r.sentiment_label,
            price_str,
            change_str,
            r.evaluation_mode,
            f"[{style}]{r.suggested_outcome}[/{style}]",
            r.reason,
        )
    console.print(table)

    # Summary
    hits = sum(1 for r in results if r.suggested_outcome == "hit")
    misses = sum(1 for r in results if r.suggested_outcome == "miss")
    inconc = sum(1 for r in results if r.suggested_outcome == "inconclusive")
    console.print(
        f"\n[bold]Summary:[/bold] {hits} hits, {misses} misses, {inconc} inconclusive"
    )

    if dry_run:
        console.print(
            "[dim](dry-run -- use --apply to write annotations)[/dim]"
        )
        return

    # Apply: deduplicate by document_id, prefer strongest absolute move evidence
    best_by_doc: dict[str, Any] = {}
    for r in results:
        prev = best_by_doc.get(r.document_id)
        if prev is None:
            best_by_doc[r.document_id] = r
            continue
        prev_abs = abs(prev.observed_move_pct or 0.0)
        curr_abs = abs(r.observed_move_pct or 0.0)
        if curr_abs > prev_abs:
            best_by_doc[r.document_id] = r

    applied = 0
    for r in best_by_doc.values():
        annotation = AlertOutcomeAnnotation(
            document_id=r.document_id,
            outcome=r.suggested_outcome,
            asset=r.asset,
            note=f"auto-check ({r.evaluation_mode}, horizon={horizon_hours}h): {r.reason}",
        )
        append_outcome_annotation(annotation, Path(artifacts_dir))
        applied += 1

    console.print(f"[green]{applied} annotations written.[/green]")


@alerts_app.command("auto-annotate")
def alerts_auto_annotate(
    min_age_hours: float = typer.Option(
        6.0, help="Only annotate alerts older than this (hours)",
    ),
    move_threshold: float = typer.Option(
        1.0, help="Price move threshold in percent (1.0 = 1%%)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview without writing annotations",
    ),
) -> None:
    """Auto-annotate directional alerts based on price movement."""
    import asyncio

    from app.alerts.auto_annotator import auto_annotate_pending

    artifacts_dir = Path("artifacts")

    results = asyncio.run(
        auto_annotate_pending(
            audit_dir=artifacts_dir,
            min_age_hours=min_age_hours,
            move_threshold=move_threshold,
            dry_run=dry_run,
        )
    )

    if not results:
        console.print("[yellow]No pending directional alerts to annotate.[/yellow]")
        return

    hits = sum(1 for a in results if a.outcome == "hit")
    misses = sum(1 for a in results if a.outcome == "miss")
    inconclusive = sum(1 for a in results if a.outcome == "inconclusive")

    for a in results:
        color = {"hit": "green", "miss": "red", "inconclusive": "yellow"}[a.outcome]
        console.print(f"[{color}]{a.outcome:>13}[/{color}]  {a.asset}  {a.note}")

    mode = " [dim](dry-run)[/dim]" if dry_run else ""
    console.print(
        f"\n[bold]{len(results)} annotated{mode}:[/bold]"
        f" {hits} hit, {misses} miss, {inconclusive} inconclusive"
    )


@alerts_app.command("daily-briefing")
def alerts_daily_briefing(
    lookback_hours: int = typer.Option(
        24, help="Lookback window in hours",
    ),
    notify: bool = typer.Option(
        False, help="Send briefing via Telegram to operator",
    ),
) -> None:
    """Generate a daily operator briefing from all audit trails."""
    import asyncio

    from app.alerts.daily_briefing import build_daily_briefing

    data = build_daily_briefing(lookback_hours=lookback_hours)
    text = data.to_text()
    console.print(text)

    if notify:
        from app.alerts.notify import send_operator_notification

        ok = asyncio.run(send_operator_notification(text))
        if ok:
            console.print("[green]Telegram notification sent.[/green]")


@alerts_app.command("health-check")
def alerts_health_check(
    lookback_hours: int = typer.Option(
        24, help="Lookback window in hours",
    ),
    notify: bool = typer.Option(
        False, help="Send issues via Telegram to operator",
    ),
) -> None:
    """Run system health checks and report issues."""
    import asyncio

    from app.alerts.health_check import run_health_check

    issues = run_health_check(lookback_hours=lookback_hours)

    if not issues:
        console.print("[bold green]All systems healthy.[/bold green]")
        if notify:
            from app.alerts.notify import send_operator_notification

            ok = asyncio.run(
                send_operator_notification("KAI Health Check: All systems healthy.")
            )
            if ok:
                console.print("[green]Telegram notification sent.[/green]")
        return

    for issue in issues:
        color = "red" if issue.severity == "critical" else "yellow"
        console.print(
            f"[{color}][{issue.severity.upper()}][/{color}] "
            f"{issue.component}: {issue.message}"
        )

    critical = sum(1 for i in issues if i.severity == "critical")
    warnings = sum(1 for i in issues if i.severity == "warning")
    console.print(
        f"\n[bold]{len(issues)} issues:[/bold]"
        f" {critical} critical, {warnings} warnings"
    )

    if notify:
        from app.alerts.notify import send_operator_notification

        lines = ["KAI Health Alert"]
        for issue in issues:
            tag = "CRITICAL" if issue.severity == "critical" else "WARNING"
            lines.append(f"[{tag}] {issue.component}: {issue.message}")
        text = "\n".join(lines)
        ok = asyncio.run(send_operator_notification(text))
        if ok:
            console.print("[green]Telegram notification sent.[/green]")
        else:
            console.print(
                "[yellow]Telegram not configured or send failed.[/yellow]"
            )


@alerts_app.command("ops-status")
def alerts_ops_status(
    lookback_hours: int = typer.Option(
        24, help="Lookback window in hours",
    ),
) -> None:
    """Quick operator dashboard — health, cycles, precision at a glance."""
    from datetime import UTC, datetime
    from pathlib import Path

    from app.alerts.daily_briefing import build_daily_briefing
    from app.alerts.health_check import run_health_check

    artifacts = Path("artifacts")
    now = datetime.now(UTC)

    # Health check
    issues = run_health_check(
        artifacts_dir=artifacts,
        lookback_hours=lookback_hours,
    )
    if issues:
        health_str = ", ".join(
            f"{i.component}({i.severity[0].upper()})"
            for i in issues
        )
    else:
        health_str = "OK"

    # Briefing data
    data = build_daily_briefing(
        artifacts_dir=artifacts,
        lookback_hours=lookback_hours,
    )

    # Last cron run
    log_path = artifacts / "paper_trading_cron.log"
    last_cron = "unknown"
    if log_path.exists():
        with log_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if "cron start" in line:
                    last_cron = line[:19].strip()

    console.print(
        f"[bold]KAI System Status[/bold] "
        f"({now.strftime('%Y-%m-%d %H:%M')} UTC)"
    )
    console.print(f"  Health:       {health_str}")
    console.print(f"  Last Cron:    {last_cron}")
    console.print(
        f"  Cycles {lookback_hours}h:   "
        f"{data.cycles_total} total, "
        f"{data.cycles_completed} completed, "
        f"{data.fills} fills"
    )
    if data.precision_pct is not None:
        console.print(
            f"  Precision:    {data.precision_pct:.1f}% "
            f"({data.hits}h / {data.misses}m)"
        )
    else:
        console.print(
            f"  Precision:    n/a ({data.hits}h / {data.misses}m)"
        )
    console.print(
        f"  Alerts {lookback_hours}h:   "
        f"{data.alerts_dispatched} dispatched, "
        f"{data.alerts_directional} directional"
    )
    console.print(
        f"  Annotations:  {data.total_annotations} total, "
        f"{data.inconclusive} inconclusive"
    )


@alerts_app.command("signal-status")
def alerts_signal_status(
    lookback_hours: int = typer.Option(24, help="Lookback window for rolling counters"),
    handoff_log_path: str | None = typer.Option(
        None, help="Optional override path for Telegram signal handoff log"
    ),
    outbox_log_path: str | None = typer.Option(
        None, help="Optional override path for exchange relay outbox log"
    ),
    sent_log_path: str | None = typer.Option(
        None, help="Optional override path for exchange relay sent log"
    ),
    dead_letter_log_path: str | None = typer.Option(
        None, help="Optional override path for exchange relay dead-letter log"
    ),
) -> None:
    """Show read-only status for Telegram signal handoff and exchange relay pipeline."""
    settings = get_settings()
    op = settings.operator

    payload = build_signal_pipeline_status(
        handoff_log_path=handoff_log_path or op.signal_handoff_log,
        outbox_log_path=outbox_log_path or op.signal_exchange_outbox_log,
        sent_log_path=sent_log_path or op.signal_exchange_sent_log,
        dead_letter_log_path=dead_letter_log_path or op.signal_exchange_dead_letter_log,
        lookback_hours=lookback_hours,
    )
    console.print("[bold]Signal Pipeline Status[/bold]")
    console.print(f"lookback_hours={payload['lookback_hours']}")
    console.print(f"handoff_total={payload['handoff_total']}")
    console.print(f"handoff_lookback={payload['handoff_lookback']}")
    console.print(f"outbox_queued_total={payload['outbox_queued_total']}")
    console.print(f"exchange_sent_total={payload['exchange_sent_total']}")
    console.print(f"exchange_sent_lookback={payload['exchange_sent_lookback']}")
    console.print(f"exchange_dead_letter_total={payload['exchange_dead_letter_total']}")
    console.print(
        f"exchange_dead_letter_lookback={payload['exchange_dead_letter_lookback']}"
    )
    console.print(f"execution_enabled={payload['execution_enabled']}")
    console.print(f"write_back_allowed={payload['write_back_allowed']}")


@alerts_app.command("exchange-relay")
def alerts_exchange_relay(
    endpoint: str | None = typer.Option(
        None, help="Optional override endpoint for signal relay target"
    ),
    batch_size: int = typer.Option(100, help="Max queued rows to process per run"),
    timeout_seconds: int | None = typer.Option(
        None, help="Optional override relay timeout in seconds"
    ),
    max_attempts: int | None = typer.Option(
        None, help="Optional override max retry attempts before dead-letter"
    ),
    outbox_log_path: str | None = typer.Option(
        None, help="Optional override path for exchange relay outbox log"
    ),
    sent_log_path: str | None = typer.Option(
        None, help="Optional override path for exchange relay sent log"
    ),
    dead_letter_log_path: str | None = typer.Option(
        None, help="Optional override path for exchange relay dead-letter log"
    ),
) -> None:
    """Relay queued Telegram signal rows to configured exchange/API endpoint."""
    import asyncio

    settings = get_settings()
    op = settings.operator

    effective_endpoint = (endpoint or op.signal_exchange_relay_endpoint).strip()
    effective_timeout = timeout_seconds or op.signal_exchange_relay_timeout_seconds
    effective_attempts = max_attempts or op.signal_exchange_relay_max_attempts
    effective_outbox = outbox_log_path or op.signal_exchange_outbox_log
    effective_sent = sent_log_path or op.signal_exchange_sent_log
    effective_dead = dead_letter_log_path or op.signal_exchange_dead_letter_log

    if not effective_endpoint:
        console.print(
            "[yellow]Relay endpoint not configured; rows will be retried/dead-lettered "
            "based on max_attempts.[/yellow]"
        )

    stats = asyncio.run(
        relay_exchange_outbox_once(
            outbox_path=effective_outbox,
            sent_log_path=effective_sent,
            dead_letter_log_path=effective_dead,
            endpoint=effective_endpoint,
            api_key=op.signal_exchange_relay_api_key,
            timeout_seconds=effective_timeout,
            max_attempts=effective_attempts,
            batch_size=batch_size,
        )
    )

    payload = stats.to_json_dict()
    console.print("[bold]Exchange Relay Run[/bold]")
    console.print(f"processed={payload['processed']}")
    console.print(f"sent={payload['sent']}")
    console.print(f"requeued={payload['requeued']}")
    console.print(f"dead_lettered={payload['dead_lettered']}")
    console.print(f"skipped={payload['skipped']}")
    console.print(f"execution_enabled={payload['execution_enabled']}")
    console.print(f"write_back_allowed={payload['write_back_allowed']}")


if __name__ == "__main__":
    app()
