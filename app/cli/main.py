import logging
from pathlib import Path
from typing import cast

import typer
from rich.console import Console
from rich.table import Table

from app.alerts.audit import (
    AlertOutcomeAnnotation,
    OutcomeLabel,
    append_outcome_annotation,
    load_alert_audits,
    load_outcome_annotations,
)
from app.alerts.hold_metrics import build_hold_metrics_report, write_hold_metrics_report
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.ingestion.base.interfaces import FetchResult
from app.ingestion.rss.service import RSSCollectedFeed, collect_rss_feed
from app.storage.db.session import build_session_factory
from app.storage.document_ingest import IngestPersistStats, persist_fetch_result

__all__ = ["app"]

logger = logging.getLogger(__name__)

app = typer.Typer(name="trading-bot", help="AI Analyst Trading Bot CLI", no_args_is_help=True)
console = Console()

ingest_app = typer.Typer(help="Ingestion commands", no_args_is_help=True)
pipeline_app = typer.Typer(help="End-to-end pipeline commands", no_args_is_help=True)
query_app = typer.Typer(help="Query commands", no_args_is_help=True)
alerts_app = typer.Typer(help="Alert commands", no_args_is_help=True)

app.add_typer(ingest_app, name="ingest")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(query_app, name="query")
app.add_typer(alerts_app, name="alerts")


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


# ── query analyze-pending ─────────────────────────────────────────────────────


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
            status = "[green]✓ sent[/green]" if r.success else f"[red]✗ failed: {r.error}[/red]"
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
    paper = report["paper_trading_evidence"]
    console.print(f"[green]Report written:[/green] {json_out}")
    console.print(f"[green]Summary written:[/green] {md_out}")
    console.print(
        f"[bold]Gate:[/bold] {gate['overall_status']} | "
        f"resolved directional {hit['resolved_directional_documents']}/"
        f"{hit['minimum_resolved_directional_alerts_for_gate']} | "
        f"paper cycles {paper['loop_metrics']['total_cycles']}"
    )


@alerts_app.command("pending-annotations")
def alerts_pending_annotations(
    limit: int = typer.Option(20, help="Max rows to print"),
    artifacts_dir: str = typer.Option("artifacts", help="Artifacts directory"),
) -> None:
    """List directional alerts without outcome annotation (deduped by document_id)."""
    records = load_alert_audits(Path(artifacts_dir))
    annotations = load_outcome_annotations(Path(artifacts_dir))
    latest_ann_by_doc = {a.document_id: a.outcome for a in annotations}

    latest_directional_by_doc = {}
    for rec in records:
        sentiment = (rec.sentiment_label or "").lower()
        if rec.is_digest or sentiment not in {"bullish", "bearish"}:
            continue
        prev = latest_directional_by_doc.get(rec.document_id)
        if prev is None or rec.dispatched_at > prev.dispatched_at:
            latest_directional_by_doc[rec.document_id] = rec

    pending = [
        rec
        for rec in latest_directional_by_doc.values()
        if rec.document_id not in latest_ann_by_doc
    ]
    pending.sort(key=lambda r: r.dispatched_at, reverse=True)

    console.print(
        f"[bold]{len(pending)} pending directional alerts[/bold] "
        f"(limit={limit}, total directional={len(latest_directional_by_doc)})"
    )
    if not pending:
        console.print("[green]No pending annotations.[/green]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Document ID")
    table.add_column("Dispatched At")
    table.add_column("Sentiment", width=10)
    table.add_column("Priority", width=8)
    table.add_column("Assets")
    for rec in pending[:limit]:
        table.add_row(
            rec.document_id,
            rec.dispatched_at,
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


if __name__ == "__main__":
    app()
