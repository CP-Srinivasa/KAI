"""Core research CLI commands: briefs, watchlists, signals."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console

from app.core.briefs import ResearchBriefBuilder
from app.core.settings import get_settings
from app.core.signals import extract_signal_candidates
from app.core.watchlists import WatchlistRegistry, parse_watchlist_type
from app.storage.db.session import build_session_factory
from app.storage.repositories.document_repo import DocumentRepository

console = Console()
research_core_app = typer.Typer(
    help="Core research commands: briefs, watchlists, signals",
    no_args_is_help=True,
)


@research_core_app.command("brief")
def research_brief(
    watchlist: str = typer.Argument(..., help="Watchlist name"),
    watchlist_type: str = typer.Option("assets", "--type", help="Watchlist type"),
    limit: int = typer.Option(100, "--limit", help="Max documents"),
) -> None:
    """Generate a research brief for a watchlist."""

    async def _run() -> str:
        settings = get_settings()
        registry = WatchlistRegistry.from_monitor_dir(Path(settings.monitor_dir))
        resolved_type = parse_watchlist_type(watchlist_type)
        watchlist_items = registry.get_watchlist(watchlist, item_type=resolved_type)
        session_factory = build_session_factory(settings.db)
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=True, limit=limit * 5)
        if watchlist_items:
            docs = registry.filter_documents(docs, watchlist, item_type=resolved_type)
        docs = docs[:limit]
        builder = ResearchBriefBuilder(cluster_name=watchlist)
        brief = builder.build(docs)
        return brief.to_markdown()

    console.print(asyncio.run(_run()))


@research_core_app.command("watchlists")
def research_watchlists(
    watchlist_type: str = typer.Option("assets", "--type", help="Watchlist type"),
) -> None:
    """List available watchlists."""
    settings = get_settings()
    registry = WatchlistRegistry.from_monitor_dir(Path(settings.monitor_dir))
    resolved_type = parse_watchlist_type(watchlist_type)
    all_watchlists = registry.get_all_watchlists(item_type=resolved_type)
    for name, items in all_watchlists.items():
        console.print(f"[bold]{name}[/bold]: {', '.join(items)}")


@research_core_app.command("signals")
def research_signals(
    watchlist: str | None = typer.Option(None, "--watchlist", help="Filter by watchlist"),
    min_priority: int = typer.Option(8, "--min-priority", help="Minimum priority"),
    limit: int = typer.Option(50, "--limit", help="Max results"),
) -> None:
    """Generate actionable signal candidates."""

    async def _run() -> list[dict]:
        settings = get_settings()
        registry = WatchlistRegistry.from_monitor_dir(Path(settings.monitor_dir))
        watchlist_boosts = None
        if watchlist:
            items = registry.get_watchlist(watchlist, item_type="assets")
            if items:
                watchlist_boosts = dict.fromkeys(items, 1)
        session_factory = build_session_factory(settings.db)
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=True, limit=limit * 5)
        candidates = extract_signal_candidates(
            docs, min_priority=min_priority, watchlist_boosts=watchlist_boosts
        )
        return [c.to_json_dict() for c in candidates[:limit]]

    results = asyncio.run(_run())
    console.print(json.dumps(results, indent=2))
