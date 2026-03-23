"""Core research commands: briefs, watchlists, signals, datasets, companion eval, signal handoff."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.core.settings import get_settings

console = Console()
research_core_app = typer.Typer()

@research_core_app.command("brief")
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


@research_core_app.command("watchlists")
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


@research_core_app.command("signals")
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


@research_core_app.command("dataset-export")
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


# ---------------------------------------------------------------------------
# Sprint 16: signal-handoff
# ---------------------------------------------------------------------------


@research_core_app.command("signal-handoff")
def research_signal_handoff(
    output: str = typer.Option(
        "artifacts/signal_handoff.json",
        "--output",
        help="Output path for the signal handoff artifact",
    ),
    limit: int = typer.Option(10, help="Max signals to include in handoff"),
) -> None:
    """Export top signal candidates as a read-only handoff artifact (Sprint 16)."""
    import asyncio
    from pathlib import Path

    async def run() -> None:
        from app.research.execution_handoff import create_signal_handoff, save_signal_handoff
        from app.research.signals import extract_signal_candidates
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        settings = get_settings()
        session_factory = build_session_factory(settings.db)

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=True, limit=limit * 5)

        candidates = extract_signal_candidates(docs)[:limit]
        if not candidates:
            console.print("[yellow]No signal candidates found.[/yellow]")
            return

        handoff = create_signal_handoff(candidates[0])
        out_path = Path(output)
        save_signal_handoff(handoff, out_path)

        console.print(f"[green]Signal handoff saved to {out_path.resolve()}[/green]")
        console.print(f"handoff_id={handoff.handoff_id}")
        console.print(f"target_asset={handoff.target_asset}")
        console.print("execution_enabled=False")

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Sprint 20: handoff-acknowledge / handoff-summary / consumer-ack
# ---------------------------------------------------------------------------


@research_core_app.command("handoff-acknowledge")
def research_handoff_acknowledge(
    handoff_path: str = typer.Argument(..., help="Path to signal handoff JSON artifact"),
    handoff_id: str = typer.Argument(..., help="handoff_id from the artifact to acknowledge"),
    consumer_agent_id: str = typer.Option(
        ..., "--consumer-agent-id", help="Identifier of the acknowledging consumer agent"
    ),
    notes: str = typer.Option("", "--notes", help="Optional audit notes"),
    output: str = typer.Option(
        "artifacts/consumer_acknowledgements.jsonl",
        "--output",
        help="Output path for the acknowledgement audit JSONL",
    ),
) -> None:
    """Audit-only acknowledgement of a consumer-visible signal handoff (Sprint 20)."""
    from pathlib import Path

    from app.research.execution_handoff import (
        append_handoff_acknowledgement_jsonl,
        create_handoff_acknowledgement,
        get_signal_handoff_by_id,
        load_signal_handoffs,
    )

    try:
        handoffs = load_signal_handoffs(Path(handoff_path))
    except FileNotFoundError as exc:
        console.print(f"[red]Signal handoff file not found: {handoff_path}[/red]")
        raise typer.Exit(1) from exc

    try:
        handoff = get_signal_handoff_by_id(handoffs, handoff_id)
    except (KeyError, ValueError) as exc:
        console.print(f"[red]handoff_id not found: {handoff_id}[/red]")
        raise typer.Exit(1) from exc

    if handoff.consumer_visibility != "visible":
        console.print(
            f"[red]Only consumer-visible handoffs can be acknowledged "
            f"(consumer_visibility={handoff.consumer_visibility!r})[/red]"
        )
        raise typer.Exit(1)

    ack = create_handoff_acknowledgement(
        handoff,
        consumer_agent_id=consumer_agent_id,
        notes=notes,
    )
    out_path = Path(output)
    append_handoff_acknowledgement_jsonl(ack, out_path)

    console.print(f"[green]Acknowledgement appended to {out_path.resolve()}[/green]")
    console.print(f"handoff_id={ack.handoff_id}")
    console.print("status=acknowledged_in_audit_only")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_core_app.command("handoff-summary")
@research_core_app.command("handoff-collector-summary")
def research_handoff_summary(
    handoff_path: str = typer.Argument(..., help="Path to signal handoff artifact"),
    acknowledgement_path: str = typer.Option(
        "artifacts/consumer_acknowledgements.jsonl",
        "--ack-path",
        help="Path to consumer acknowledgements JSONL",
    ),
) -> None:
    """Summarize pending and acknowledged handoffs from existing artifacts (Sprint 20)."""
    from pathlib import Path

    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_handoff_acknowledgements, load_signal_handoffs

    try:
        handoffs = load_signal_handoffs(Path(handoff_path))
    except FileNotFoundError as exc:
        console.print(f"[red]Signal handoff file not found: {handoff_path}[/red]")
        raise typer.Exit(1) from exc

    ack_path = Path(acknowledgement_path)
    acknowledgements = load_handoff_acknowledgements(ack_path) if ack_path.exists() else []

    report = build_handoff_collector_summary(handoffs, acknowledgements)
    payload = report.to_json_dict()

    table = Table(title="Handoff Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Total Handoffs", str(payload.get("total_count", 0)))
    table.add_row("Pending", str(payload.get("pending_count", 0)))
    table.add_row("Acknowledged", str(payload.get("acknowledged_count", 0)))
    table.add_row("Execution Enabled", "False")
    console.print(table)


@research_core_app.command("consumer-ack")
def research_consumer_ack(
    handoff_path: str = typer.Argument(..., help="Path to signal handoff JSON artifact"),
    handoff_id: str = typer.Argument(..., help="handoff_id to acknowledge"),
    consumer_agent_id: str = typer.Option(
        ..., "--consumer-agent-id", help="Consumer agent identifier"
    ),
    output: str = typer.Option(
        "artifacts/consumer_acknowledgements.jsonl",
        "--output",
        help="Output path for the acknowledgement JSONL",
    ),
) -> None:
    """Alias for handoff-acknowledge — audit-only consumer acknowledgement (Sprint 20)."""
    from pathlib import Path

    from app.research.execution_handoff import (
        append_handoff_acknowledgement_jsonl,
        create_handoff_acknowledgement,
        get_signal_handoff_by_id,
        load_signal_handoffs,
    )

    try:
        handoffs = load_signal_handoffs(Path(handoff_path))
    except FileNotFoundError as exc:
        console.print(f"[red]Signal handoff file not found: {handoff_path}[/red]")
        raise typer.Exit(1) from exc

    try:
        handoff = get_signal_handoff_by_id(handoffs, handoff_id)
    except (KeyError, ValueError) as exc:
        console.print(f"[red]handoff_id not found: {handoff_id}[/red]")
        raise typer.Exit(1) from exc

    if handoff.consumer_visibility != "visible":
        console.print("[red]Handoff not consumer-visible.[/red]")
        raise typer.Exit(1)

    ack = create_handoff_acknowledgement(handoff, consumer_agent_id=consumer_agent_id)
    out_path = Path(output)
    append_handoff_acknowledgement_jsonl(ack, out_path)

    console.print(f"[green]Consumer ack appended to {out_path.resolve()}[/green]")
    console.print("execution_enabled=False")


# ---------------------------------------------------------------------------
# Sprint 21: readiness-summary
# ---------------------------------------------------------------------------


