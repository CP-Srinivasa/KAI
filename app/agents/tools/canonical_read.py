"""Canonical read-only MCP tool implementations.

This module defines both the authoritative tool-name inventory and the
actual async tool functions for all read-only MCP tools.

Tools are registered in app.agents.mcp_server via mcp.add_tool().
No @mcp.tool() decorator is used here -- this module is framework-agnostic.

Design invariants:
- All functions are pure reads: no filesystem writes, no DB mutations.
- No imports from app.agents.mcp_server (circular-import guard).
- execution_enabled is always False.
- write_back_allowed is always False.
- Companion-ML subsystem removed (D-107).

Tool categories:
- watchlist / research: get_watchlists, get_research_brief, get_signal_candidates
- market data: get_market_data_quote
- portfolio: get_paper_portfolio_snapshot, get_paper_positions_summary, get_paper_exposure_summary
- narrative: get_narrative_clusters, get_signals_for_execution
- alerts / journal: get_alert_audit_summary, get_decision_journal_summary
- daily: get_daily_operator_summary
- trading loop: get_trading_loop_status, get_recent_trading_cycles
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.agents.tools._helpers import (
    ALERT_AUDIT_DEFAULT_DIR,
    DECISION_JOURNAL_DEFAULT_PATH,
    LOOP_AUDIT_DEFAULT_PATH,
    PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    build_paper_portfolio_snapshot_helper,
    load_signal_candidates_and_documents,
    resolve_workspace_dir,
    resolve_workspace_path,
)
from app.core.briefs import ResearchBriefBuilder
from app.core.settings import get_settings
from app.core.signals import extract_signal_candidates
from app.core.watchlists import WatchlistRegistry, parse_watchlist_type
from app.storage.db.session import build_session_factory
from app.storage.repositories.document_repo import DocumentRepository

# ---------------------------------------------------------------------------
# Canonical inventory (authoritative list)
# ---------------------------------------------------------------------------

CANONICAL_READ_TOOL_NAMES: tuple[str, ...] = (
    "get_watchlists",
    "get_research_brief",
    "get_signal_candidates",
    "get_market_data_quote",
    "get_paper_portfolio_snapshot",
    "get_paper_positions_summary",
    "get_paper_exposure_summary",
    "get_narrative_clusters",
    "get_signals_for_execution",
    "get_daily_operator_summary",
    "get_alert_audit_summary",
    "get_decision_journal_summary",
    "get_trading_loop_status",
    "get_recent_trading_cycles",
)


def get_canonical_read_tool_names() -> tuple[str, ...]:
    """Return the locked canonical read-only tool name tuple."""
    return CANONICAL_READ_TOOL_NAMES


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def get_watchlists(watchlist_type: str = "assets") -> dict[str, list[str]]:
    """List available research watchlists or show the members of watchlists."""
    settings = get_settings()
    registry = WatchlistRegistry.from_monitor_dir(Path(settings.monitor_dir))
    resolved_type = parse_watchlist_type(watchlist_type)
    all_watchlists = registry.get_all_watchlists(item_type=resolved_type)
    return dict(all_watchlists)


async def get_research_brief(
    watchlist: str, watchlist_type: str = "assets", limit: int = 100
) -> str:
    """Generate a research brief for a specific watchlist."""
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


async def get_signal_candidates(
    watchlist: str | None = None, min_priority: int = 8, limit: int = 50
) -> str:
    """Generate actionable signal candidates from analyzed documents."""
    candidates, _docs = await load_signal_candidates_and_documents(
        watchlist=watchlist,
        min_priority=min_priority,
        limit=limit,
    )
    return json.dumps([c.to_json_dict() for c in candidates], indent=2)


async def get_market_data_quote(
    symbol: str = "BTC/USDT",
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, object]:
    """Return one read-only market data quote snapshot from the canonical adapter path."""
    from app.market_data.service import get_market_data_snapshot

    snapshot = await get_market_data_snapshot(
        symbol=symbol,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )
    return snapshot.to_json_dict()


async def get_paper_portfolio_snapshot(
    audit_path: str = PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Return canonical read-only paper portfolio snapshot from audit replay."""
    snapshot = await build_paper_portfolio_snapshot_helper(
        audit_path=audit_path,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )
    return snapshot.to_json_dict()  # type: ignore[no-any-return]


async def get_paper_positions_summary(
    audit_path: str = PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, object]:
    """Return positions-only slice from canonical paper portfolio snapshot."""
    from app.execution.portfolio_read import build_positions_summary

    snapshot = await build_paper_portfolio_snapshot_helper(
        audit_path=audit_path,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )
    return build_positions_summary(snapshot)


async def get_paper_exposure_summary(
    audit_path: str = PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, object]:
    """Return exposure-only slice from canonical paper portfolio snapshot."""
    from app.execution.portfolio_read import build_exposure_summary

    snapshot = await build_paper_portfolio_snapshot_helper(
        audit_path=audit_path,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )
    return build_exposure_summary(snapshot)


async def get_narrative_clusters(
    min_priority: int = 8,
    limit: int = 200,
    min_cluster_size: int = 2,
    merge_threshold: float = 0.30,
    max_clusters: int = 20,
    merge: bool = False,
) -> dict[str, object]:
    """Group active signal candidates into narrative clusters by asset Jaccard similarity.

    Pure read-only projection -- no DB writes, no routing changes (I-184).
    Returns cluster summaries with velocity, acceleration, and dominant direction.
    """
    from app.analysis.narratives.cluster import ClusterConfig, NarrativeClusterEngine

    settings = get_settings()
    session_factory = build_session_factory(settings.db)
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        docs = await repo.list(is_analyzed=True, limit=limit)

    candidates = extract_signal_candidates(docs, min_priority=min_priority)

    config = ClusterConfig(
        min_cluster_size=min_cluster_size,
        merge_threshold=merge_threshold,
        max_clusters=max_clusters,
    )
    engine = NarrativeClusterEngine(config)
    clusters = engine.cluster(candidates)

    if merge:
        clusters = engine.merge_clusters(clusters)

    return {
        "report_type": "narrative_cluster_report",
        "execution_enabled": False,  # I-180
        "write_back_allowed": False,
        "candidate_count": len(candidates),
        "cluster_count": len(clusters),
        "config": {
            "min_cluster_size": min_cluster_size,
            "merge_threshold": merge_threshold,
            "max_clusters": max_clusters,
            "merge": merge,
        },
        "clusters": [cl.to_json_dict() for cl in clusters],
    }


async def get_signals_for_execution(
    watchlist: str | None = None,
    min_priority: int = 8,
    limit: int = 50,
    provider: str | None = None,
) -> dict[str, object]:
    """Return a read-only external-consumption handoff for qualified signals."""
    candidates, _docs = await load_signal_candidates_and_documents(
        watchlist=watchlist,
        min_priority=min_priority,
        limit=limit,
        provider=provider,
    )
    return {
        "report_type": "execution_handoff_report",
        "execution_enabled": False,
        "write_back_allowed": False,
        "candidate_count": len(candidates),
        "candidates": [c.to_json_dict() for c in candidates],
    }


def _parse_iso_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def get_daily_operator_summary(
    *,
    alert_audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
    loop_audit_path: str = LOOP_AUDIT_DEFAULT_PATH,
    paper_execution_audit_path: str = PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    now: datetime | None = None,
) -> dict[str, object]:
    """Return a daily operator status snapshot for /status and dashboards.

    Reads live state from canonical sources (paper execution audit, alert
    audit, loop audit, document repo). Fields that are not yet measured
    (LLM failure rate, RSS→alert latency) return the literal string
    "not_implemented" so consumers see an explicit gap rather than a
    fabricated zero.

    execution_enabled and write_back_allowed are always False.
    """
    from app.alerts.audit import load_alert_audits
    from app.orchestrator.trading_loop import load_trading_loop_cycles

    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff_24h = now_utc - timedelta(hours=24)
    today_date = now_utc.date()

    degraded = False

    # Positions — read from the paper execution audit (no DB dependency).
    try:
        exposure = await get_paper_exposure_summary(audit_path=paper_execution_audit_path)
        raw_position_count = exposure.get("position_count", 0)
        position_count: int | None = (
            int(raw_position_count) if isinstance(raw_position_count, int) else 0
        )
    except Exception:
        position_count = None
        degraded = True

    # Ingestion backlog — documents persisted but not yet analyzed.
    try:
        settings = get_settings()
        session_factory = build_session_factory(settings.db)
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            ingestion_backlog: int | None = await repo.count_pending_documents()
    except Exception:
        ingestion_backlog = None
        degraded = True

    # Alert fire rate — count audits dispatched in the last 24h, divide by 24.
    try:
        audit_dir = resolve_workspace_dir(alert_audit_dir, label="Alert audit directory")
        audits = load_alert_audits(audit_dir)
        recent_alerts = 0
        for record in audits:
            dispatched = _parse_iso_utc(record.dispatched_at)
            if dispatched is not None and dispatched >= cutoff_24h:
                recent_alerts += 1
        alert_rate_24h: float | None = round(recent_alerts / 24.0, 2)
    except Exception:
        alert_rate_24h = None
        degraded = True

    # Cycles today — count loop audit rows whose started_at is on today's UTC date.
    try:
        loop_path = resolve_workspace_path(
            loop_audit_path,
            label="Loop audit",
            allowed_suffixes=frozenset({".jsonl"}),
        )
        cycles = load_trading_loop_cycles(loop_path)
        cycle_count_today: int | None = 0
        for row in cycles:
            started = _parse_iso_utc(row.get("started_at"))
            if started is not None and started.date() == today_date:
                cycle_count_today += 1
    except Exception:
        cycle_count_today = None
        degraded = True

    def _or_unknown(value: int | float | None) -> object:
        return value if value is not None else "?"

    return {
        "report_type": "daily_operator_summary",
        "execution_enabled": False,
        "write_back_allowed": False,
        "status": "degraded" if degraded else "operational",
        "readiness_status": "degraded" if degraded else "operational",
        "position_count": _or_unknown(position_count),
        "ingestion_backlog_documents": _or_unknown(ingestion_backlog),
        "alert_fire_rate_docs_per_hour_24h": _or_unknown(alert_rate_24h),
        "cycle_count_today": _or_unknown(cycle_count_today),
        # Explicit not-measured markers: /status consumers must NOT treat a
        # missing field as zero. Wire these up once the underlying telemetry
        # exists (LLM call success/failure per provider, per-doc RSS→alert
        # latency join).
        "llm_provider_failure_rate_24h": "not_implemented",
        "rss_to_alert_latency_p95_seconds_24h": "not_implemented",
        "generated_at_utc": now_utc.isoformat(),
    }


async def get_alert_audit_summary(
    audit_dir: str = ALERT_AUDIT_DEFAULT_DIR,
) -> dict[str, object]:
    """Return a read-only summary of dispatched alert audit records.

    Reads from the alert audit JSONL trail and aggregates by channel.
    execution_enabled and write_back_allowed are always False.
    """
    from app.alerts.audit import load_alert_audits

    resolved = resolve_workspace_dir(
        audit_dir,
        label="Alert audit directory",
    )
    audits = load_alert_audits(resolved)
    return {
        "report_type": "alert_audit_summary",
        "execution_enabled": False,
        "write_back_allowed": False,
        "total_alerts": len(audits),
        "alerts": [a.to_json_dict() for a in audits] if audits else [],
    }


async def get_decision_journal_summary(
    journal_path: str = DECISION_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Return a read-only summary of the append-only decision journal.

    execution_enabled and write_back_allowed are always False.
    """
    from app.decisions.journal import (
        build_decision_journal_summary,
        load_decision_journal,
    )

    resolved = resolve_workspace_path(
        journal_path,
        label="Decision journal",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    entries = load_decision_journal(resolved)
    summary = build_decision_journal_summary(entries, journal_path=resolved)
    return summary.to_json_dict()


async def get_trading_loop_status(
    audit_path: str = LOOP_AUDIT_DEFAULT_PATH,
    mode: str = "paper",
) -> dict[str, object]:
    """Return read-only trading-loop status and run-once guard state."""
    from app.orchestrator.trading_loop import build_loop_status_summary

    resolved = resolve_workspace_path(
        audit_path,
        label="Loop audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    summary = build_loop_status_summary(audit_path=resolved, mode=mode)
    return summary.to_json_dict()


async def get_recent_trading_cycles(
    audit_path: str = LOOP_AUDIT_DEFAULT_PATH,
    last_n: int = 20,
) -> dict[str, object]:
    """Return read-only summary of recent trading-loop cycle audits."""
    from app.orchestrator.trading_loop import build_recent_cycles_summary

    resolved = resolve_workspace_path(
        audit_path,
        label="Loop audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    summary = build_recent_cycles_summary(audit_path=resolved, last_n=last_n)
    return summary.to_json_dict()
