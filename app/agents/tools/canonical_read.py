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
from collections import Counter
from datetime import UTC, date, datetime, timedelta
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

_DECISION_PACK_TARGET_DATE = date(2026, 5, 30)
_DECISION_PACK_MIN_DIRECTIONAL_7D = 30
_DECISION_PACK_MIN_RESOLVED_7D = 30
_DECISION_PACK_MIN_PAPER_CLOSES = 10
_DECISION_PACK_UNKNOWN_SOURCE_WARN_PCT = 20.0

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


def _normalise_decision_pack_sentiment(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    label = value.strip().lower()
    if label in {"bullish", "bearish", "neutral", "mixed"}:
        return label
    return "unknown"


def _normalise_decision_pack_source(record: object) -> str:
    source = getattr(record, "source_name", None)
    provenance = getattr(record, "provenance", None)
    if not isinstance(source, str) or not source.strip():
        source = getattr(provenance, "source", None)
    if not isinstance(source, str) or not source.strip():
        return "unknown"
    return source.strip().lower()


def _pct(part: int, whole: int) -> float | None:
    if whole <= 0:
        return None
    return round(100.0 * part / whole, 1)


def _read_paper_closed_with_pnl_count(path: Path) -> tuple[int | None, str]:
    if not path.exists():
        return None, "missing"
    count = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    rec = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict) or rec.get("event_type") != "position_closed":
                    continue
                if isinstance(rec.get("trade_pnl_usd"), (int, float)):
                    count += 1
        return count, "ok"
    except OSError:
        return None, "unreadable"


def _build_decision_pack_20260530(
    *,
    audits: list[object] | None,
    outcome_by_doc: dict[str, str],
    now_utc: datetime,
    portfolio_snapshot_ok: bool,
    alert_audit_ok: bool,
    loop_audit_ok: bool,
    paper_execution_audit_path: Path | None,
) -> dict[str, object]:
    cutoff_7d = now_utc - timedelta(days=7)
    latest_recent: dict[str, tuple[datetime, object]] = {}
    for record in audits or []:
        if getattr(record, "is_digest", False):
            continue
        doc_id = getattr(record, "document_id", None)
        if not isinstance(doc_id, str) or not doc_id:
            continue
        dispatched = _parse_iso_utc(getattr(record, "dispatched_at", None))
        if dispatched is None or dispatched < cutoff_7d:
            continue
        prior = latest_recent.get(doc_id)
        if prior is None or dispatched > prior[0]:
            latest_recent[doc_id] = (dispatched, record)

    recent_records = [entry[1] for entry in latest_recent.values()]
    sentiment_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    directional_by_source: Counter[str] = Counter()
    resolved_directional = 0
    inconclusive_directional = 0

    for record in recent_records:
        sentiment = _normalise_decision_pack_sentiment(getattr(record, "sentiment_label", None))
        source = _normalise_decision_pack_source(record)
        sentiment_counts[sentiment] += 1
        source_counts[source] += 1
        if sentiment in {"bullish", "bearish"}:
            directional_by_source[source] += 1
            outcome = outcome_by_doc.get(str(getattr(record, "document_id", "")))
            if outcome in {"hit", "miss"}:
                resolved_directional += 1
            elif outcome == "inconclusive":
                inconclusive_directional += 1

    total_recent = len(recent_records)
    directional_7d = sum(sentiment_counts[s] for s in ("bullish", "bearish"))
    unknown_sources = source_counts.get("unknown", 0)
    unknown_share_pct = _pct(unknown_sources, total_recent)

    paper_closed_count: int | None = None
    paper_audit_status = "not_checked"
    if paper_execution_audit_path is not None:
        paper_closed_count, paper_audit_status = _read_paper_closed_with_pnl_count(
            paper_execution_audit_path
        )

    warnings: list[dict[str, object]] = []
    if not portfolio_snapshot_ok:
        warnings.append(
            {
                "code": "portfolio_snapshot_unavailable",
                "severity": "warn",
                "detail": "Paper portfolio snapshot could not be read.",
            }
        )
    if not alert_audit_ok:
        warnings.append(
            {
                "code": "alert_audit_unavailable",
                "severity": "warn",
                "detail": "Alert audit stream could not be read.",
            }
        )
    if not loop_audit_ok:
        warnings.append(
            {
                "code": "loop_audit_unavailable",
                "severity": "warn",
                "detail": "Trading-loop audit stream could not be read.",
            }
        )
    if directional_7d < _DECISION_PACK_MIN_DIRECTIONAL_7D:
        warnings.append(
            {
                "code": "directional_sample_low",
                "severity": "warn",
                "actual": directional_7d,
                "minimum": _DECISION_PACK_MIN_DIRECTIONAL_7D,
            }
        )
    if resolved_directional < _DECISION_PACK_MIN_RESOLVED_7D:
        warnings.append(
            {
                "code": "resolved_directional_sample_low",
                "severity": "warn",
                "actual": resolved_directional,
                "minimum": _DECISION_PACK_MIN_RESOLVED_7D,
            }
        )
    if paper_closed_count is None or paper_closed_count < _DECISION_PACK_MIN_PAPER_CLOSES:
        warnings.append(
            {
                "code": "paper_pnl_sample_low",
                "severity": "warn",
                "actual": paper_closed_count,
                "minimum": _DECISION_PACK_MIN_PAPER_CLOSES,
            }
        )
    if (
        unknown_share_pct is not None
        and unknown_share_pct > _DECISION_PACK_UNKNOWN_SOURCE_WARN_PCT
    ):
        warnings.append(
            {
                "code": "unknown_source_share_high",
                "severity": "warn",
                "actual_pct": unknown_share_pct,
                "threshold_pct": _DECISION_PACK_UNKNOWN_SOURCE_WARN_PCT,
            }
        )

    source_table = [
        {
            "source": source,
            "alerts": count,
            "directional_alerts": directional_by_source.get(source, 0),
            "share_pct": _pct(count, total_recent),
        }
        for source, count in source_counts.most_common(10)
    ]
    sentiment_order = ("bullish", "bearish", "neutral", "mixed", "unknown")
    sentiment_table = [
        {
            "sentiment": sentiment,
            "alerts": sentiment_counts.get(sentiment, 0),
            "share_pct": _pct(sentiment_counts.get(sentiment, 0), total_recent),
        }
        for sentiment in sentiment_order
        if sentiment_counts.get(sentiment, 0) > 0
    ]

    return {
        "target_date": _DECISION_PACK_TARGET_DATE.isoformat(),
        "days_to_target": (_DECISION_PACK_TARGET_DATE - now_utc.date()).days,
        "window_days": 7,
        "snapshot_robustness": {
            "portfolio_snapshot": "ok" if portfolio_snapshot_ok else "unavailable",
            "alert_audit": "ok" if alert_audit_ok else "unavailable",
            "trading_loop_audit": "ok" if loop_audit_ok else "unavailable",
            "paper_execution_audit": paper_audit_status,
        },
        "sample_sizes": {
            "alerts_7d": total_recent,
            "directional_alerts_7d": directional_7d,
            "resolved_directional_hit_miss_7d": resolved_directional,
            "inconclusive_directional_7d": inconclusive_directional,
            "paper_closed_positions_with_pnl": paper_closed_count,
        },
        "sample_size_warnings": warnings,
        "source_table_7d": source_table,
        "sentiment_table_7d": sentiment_table,
    }


def _summarize_tradingview_webhook_auth_24h(
    audit_path: str, *, cutoff_24h: datetime
) -> dict[str, object] | None:
    # SAT-C-002: Auth-Method-Counter macht silenten hmac→shared_token Downgrade sichtbar.
    # Liest tradingview_webhook_audit.jsonl, zaehlt accepted-Eintraege per auth_method
    # + rejected per reason. Fehlende/leere Datei → None (Endpoint inaktiv).
    path = Path(audit_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        return None
    accepted: dict[str, int] = {}
    rejected: dict[str, int] = {}
    total = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                received = _parse_iso_utc(entry.get("received_at"))
                if received is None or received < cutoff_24h:
                    continue
                total += 1
                outcome = entry.get("outcome")
                if outcome == "accepted":
                    method = str(entry.get("auth_method") or "unknown")
                    accepted[method] = accepted.get(method, 0) + 1
                elif outcome == "rejected":
                    reason = str(entry.get("reason") or "unknown")
                    rejected[reason] = rejected.get(reason, 0) + 1
    except OSError:
        return None
    return {
        "total": total,
        "accepted": accepted,
        "rejected": rejected,
        # Echo current configured mode so consumer can compare expected vs observed.
        # Downgrade-Verdacht: configured_mode='hmac_or_token' UND accepted.shared_token > 0.
    }


def _summarize_warp_status() -> dict[str, object]:
    # Cloudflare WARP detection. WARP routes the laptop's traffic through CF
    # in a way that breaks the kai-trader.org/dashboard CF-Access email-OTP
    # flow (the request arrives "from WARP" and the policy classifies it
    # differently). On 2026-04-19 the operator hit this manually; the daily
    # summary should now surface it automatically so the operator gets a
    # "WARP pausieren" hint without needing memory recall.
    #
    # Detection strategy (ordered):
    #   1. Windows: tasklist for "Cloudflare WARP.exe" — most reliable signal.
    #   2. Cross-platform: WARP installs a virtual interface in the 100.96/12
    #      CGNAT range. If a local NIC has an IP there, WARP is up.
    #   3. Otherwise inactive (or undetectable on this OS).
    #
    # Returns a compact dict consumed by the daily operator summary. The
    # `hint` field is operator-facing copy — keep it terse and actionable.
    import platform
    import subprocess

    is_windows = platform.system() == "Windows"

    # Strategy 1 — Windows process check.
    if is_windows:
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Cloudflare WARP.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            stdout = out.stdout or ""
            if "Cloudflare WARP.exe" in stdout:
                return {
                    "active": True,
                    "detection_method": "process",
                    "hint": (
                        "WARP läuft — falls Dashboard-Login auf "
                        "kai-trader.org hängt: WARP pausieren "
                        "(Taskleisten-Icon)."
                    ),
                }
        except (OSError, subprocess.TimeoutExpired):
            pass

    # Strategy 2 — WARP CGNAT interface (100.96.0.0/12). Cross-platform.
    try:
        import ipaddress
        import socket

        warp_net = ipaddress.ip_network("100.96.0.0/12")
        # Light enumeration: getaddrinfo on the host tends to return WARP's
        # virtual address in the candidate list when the interface is up.
        # Fall back to socket.gethostbyname_ex if needed.
        host_ips: list[str] = []
        try:
            host_ips = socket.gethostbyname_ex(socket.gethostname())[2]
        except OSError:
            host_ips = []
        for ip_str in host_ips:
            try:
                if ipaddress.ip_address(ip_str) in warp_net:
                    return {
                        "active": True,
                        "detection_method": "interface",
                        "hint": (
                            "WARP-Interface aktiv — falls Dashboard-Login hängt: WARP pausieren."
                        ),
                    }
            except ValueError:
                continue
    except Exception:
        pass

    return {
        "active": False,
        "detection_method": "none",
        "hint": None,
    }


def _summarize_telegram_channel_ingest(
    *,
    now: datetime,
    stale_threshold_seconds: int | None = None,
    session_path_override: str | None = None,
    pid_file_override: str | None = None,
    heartbeat_path_override: str | None = None,
    replay_marker_override: str | None = None,
) -> dict[str, object]:
    # Liveness probe for the Telegram premium-channel MTProto listener.
    # Motivation: 2026-04-21 the listener silently died and 6 premium
    # signals (2026-04-23/-24) never entered the pipeline. We now surface
    # `telegram_channel_ingest` in the daily operator summary so /status
    # flags a stale listener within stale_threshold_seconds instead of
    # days.
    #
    # Liveness = max(pid_file.mtime, session_file.mtime). The PID file is
    # written by scripts/telegram_listener_start.sh at process spawn; the
    # Telethon session file gets touched whenever the client processes
    # updates. Either one being fresh is a valid liveness signal.
    try:
        cfg = get_settings().telegram_channel_ingest
    except Exception:
        return {"status": "unknown", "reason": "settings_load_failed"}

    if not cfg.enabled:
        return {
            "status": "disabled",
            "reason": "INGESTION_TELEGRAM_CHANNEL_ENABLED=false",
        }

    # Resolve directly against WORKSPACE_ROOT — .session is not in the
    # ARTIFACT_SUFFIXES allowlist used by resolve_workspace_path().
    from app.agents.tools._helpers import WORKSPACE_ROOT

    # Threshold resolution: caller-provided override wins; otherwise fall
    # back to the configured value, then to a 1800 s safety default.
    # ``getattr`` is defensive — older Mock-based tests construct cfg
    # without the new field; ``isinstance`` filters the resulting Mock.
    if stale_threshold_seconds is None:
        configured = getattr(cfg, "heartbeat_stale_seconds", None)
        stale_threshold_seconds = (
            int(configured) if isinstance(configured, int) and configured > 0 else 1800
        )

    session_rel = session_path_override or cfg.session_path
    pid_rel = pid_file_override or ".telegram_listener.pid"

    session_path = (
        Path(session_rel) if Path(session_rel).is_absolute() else WORKSPACE_ROOT / session_rel
    )
    pid_path = Path(pid_rel) if Path(pid_rel).is_absolute() else WORKSPACE_ROOT / pid_rel

    # Heartbeat path — D-191/S-003. Same defensive resolution as above so
    # MagicMock-based legacy tests don't choke on Path(MagicMock()).
    if heartbeat_path_override is not None:
        hb_rel: str | None = heartbeat_path_override
    else:
        cfg_hb = getattr(cfg, "heartbeat_path", None)
        hb_rel = cfg_hb if isinstance(cfg_hb, str) and cfg_hb else None

    hb_path: Path | None = None
    if hb_rel:
        hb_path = Path(hb_rel) if Path(hb_rel).is_absolute() else WORKSPACE_ROOT / hb_rel

    if not session_path.exists():
        return {
            "status": "missing_session",
            "reason": (
                "session file not found — run: "
                "python -m app.cli.main ingestion telegram-channel setup"
            ),
            "session_path": str(session_path),
        }

    candidates: list[tuple[str, float]] = [("session", session_path.stat().st_mtime)]
    if pid_path.exists():
        candidates.append(("pid_file", pid_path.stat().st_mtime))
    if hb_path is not None and hb_path.exists():
        candidates.append(("heartbeat", hb_path.stat().st_mtime))

    src, last_touch = max(candidates, key=lambda c: c[1])
    last_seen = datetime.fromtimestamp(last_touch, tz=UTC)
    age_seconds = (now - last_seen).total_seconds()

    status = "ok" if age_seconds < stale_threshold_seconds else "stale"

    # F3 (2026-05-05): Heartbeat-Reactivity-Counter. Worker now persists
    # JSON state inside the heartbeat file (boot_iso, last_heartbeat_iso,
    # last_message_iso, messages_since_boot). Parsing it lets us
    # distinguish "process alive but channel silent" (stale_silent —
    # the V19 4-day-silence pattern) from "process alive AND updates
    # flowing" (ok). Pre-F3 heartbeat files are empty-bytes — JSON
    # parse fails gracefully and reactivity_status stays "no_data" so
    # legacy callers keep working unchanged.
    reactivity_status: str = "no_data"
    last_message_age_seconds: int | None = None
    messages_since_boot: int | None = None
    boot_age_seconds: int | None = None
    if hb_path is not None and hb_path.exists():
        try:
            hb_raw = hb_path.read_text(encoding="utf-8")
            if hb_raw.strip():
                hb_data = json.loads(hb_raw)
                if isinstance(hb_data, dict):
                    boot_iso_raw = hb_data.get("boot_iso")
                    last_msg_iso_raw = hb_data.get("last_message_iso")
                    msgs_raw = hb_data.get("messages_since_boot")
                    if isinstance(msgs_raw, int) and msgs_raw >= 0:
                        messages_since_boot = msgs_raw
                    if isinstance(boot_iso_raw, str):
                        try:
                            boot_dt = datetime.fromisoformat(boot_iso_raw)
                            boot_age_seconds = int((now - boot_dt).total_seconds())
                        except ValueError:
                            pass
                    if isinstance(last_msg_iso_raw, str):
                        try:
                            last_msg_dt = datetime.fromisoformat(last_msg_iso_raw)
                            last_message_age_seconds = int((now - last_msg_dt).total_seconds())
                        except ValueError:
                            pass
                    # Classify reactivity (independent of liveness `status`).
                    # cold_boot: just started, no messages yet, < 1 h old
                    # stale_silent: alive but no messages in > 24 h
                    # ok: messages observed within last 24 h
                    cold_boot_threshold = 3600
                    silent_threshold = 24 * 3600
                    if (
                        messages_since_boot == 0
                        and boot_age_seconds is not None
                        and boot_age_seconds < cold_boot_threshold
                    ):
                        reactivity_status = "cold_boot"
                    elif (
                        last_message_age_seconds is None
                        or last_message_age_seconds >= silent_threshold
                    ):
                        reactivity_status = "stale_silent"
                    else:
                        reactivity_status = "ok"
        except (OSError, json.JSONDecodeError):
            # Pre-F3 empty-bytes file or read error — leave reactivity
            # at "no_data". Liveness `status` is unaffected (it uses
            # mtime which write_text() refreshed on the worker side).
            pass

    # Replay marker — written by replay_missed_messages() at worker start.
    # Surfaces whether gap-replay actually ran on the last listener boot, and
    # how many messages it recovered. Without this, a silent
    # last_seen_id<=0 short-circuit (no checkpoint yet) is indistinguishable
    # from a successful empty replay.
    replay_attempted_at: str | None = None
    replay_processed_count: int | None = None
    replay_scanned_count: int | None = None
    if replay_marker_override is not None:
        replay_marker = (
            Path(replay_marker_override)
            if Path(replay_marker_override).is_absolute()
            else WORKSPACE_ROOT / replay_marker_override
        )
    else:
        replay_marker = WORKSPACE_ROOT / "artifacts" / ".telegram_channel_replay.json"
    if replay_marker.exists():
        try:
            data = json.loads(replay_marker.read_text(encoding="utf-8"))
            attempted_raw = data.get("attempted_at")
            replay_attempted_at = str(attempted_raw) if isinstance(attempted_raw, str) else None
            processed_raw = data.get("processed")
            scanned_raw = data.get("scanned")
            if isinstance(processed_raw, int):
                replay_processed_count = processed_raw
            if isinstance(scanned_raw, int):
                replay_scanned_count = scanned_raw
        except (OSError, json.JSONDecodeError):
            pass

    return {
        "status": status,
        "age_seconds": int(age_seconds),
        "last_seen_utc": last_seen.isoformat(),
        "last_seen_source": src,
        "stale_threshold_seconds": stale_threshold_seconds,
        "pid_file_exists": pid_path.exists(),
        "heartbeat_file_exists": bool(hb_path is not None and hb_path.exists()),
        "replay_attempted_at": replay_attempted_at,
        "replay_processed_count": replay_processed_count,
        "replay_scanned_count": replay_scanned_count,
        # F3 (2026-05-05): reactivity layer on top of liveness `status`.
        # Pre-F3 callers ignore these keys; new operator surface uses
        # reactivity_status to surface the V19 stale_silent pattern.
        "reactivity_status": reactivity_status,
        "last_message_age_seconds": last_message_age_seconds,
        "messages_since_boot": messages_since_boot,
        "boot_age_seconds": boot_age_seconds,
    }


def _summarize_operator_envelope_activity(
    *,
    now: datetime,
    envelope_path_override: str | None = None,
    stale_threshold_hours: int = 24,
    dead_threshold_hours: int = 72,
) -> dict[str, object]:
    # Operator-Envelope activity watchdog. Motivation: 2026-04-21 the operator
    # stopped dispatching envelopes; the gap was only noticed on 2026-04-28
    # (7 days). The Re-Entry data flow until 2026-05-16 depends on
    # operator-curated envelopes, so /status now surfaces how long ago the last
    # envelope was received. Status classes:
    #   ok      — last envelope < stale_threshold_hours
    #   stale   — between stale and dead threshold (operator should dispatch)
    #   dead    — >= dead_threshold_hours (Re-Entry data flow is at risk)
    #   empty   — file exists but contains no parseable envelope
    #   missing — file does not exist
    from app.agents.tools._helpers import WORKSPACE_ROOT

    rel_path = envelope_path_override or "artifacts/telegram_message_envelope.jsonl"
    envelope_path = Path(rel_path) if Path(rel_path).is_absolute() else WORKSPACE_ROOT / rel_path

    if not envelope_path.exists():
        return {
            "status": "missing",
            "reason": "envelope file not found",
            "envelope_path": str(envelope_path),
            "stale_threshold_hours": stale_threshold_hours,
            "dead_threshold_hours": dead_threshold_hours,
        }

    cutoff_24h = now - timedelta(hours=24)
    last_ts: datetime | None = None
    total_alltime = 0
    total_24h = 0

    try:
        with envelope_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_iso_utc(entry.get("timestamp_utc"))
                if ts is None:
                    continue
                total_alltime += 1
                if ts >= cutoff_24h:
                    total_24h += 1
                if last_ts is None or ts > last_ts:
                    last_ts = ts
    except OSError:
        return {
            "status": "missing",
            "reason": "envelope file not readable",
            "envelope_path": str(envelope_path),
            "stale_threshold_hours": stale_threshold_hours,
            "dead_threshold_hours": dead_threshold_hours,
        }

    if last_ts is None:
        return {
            "status": "empty",
            "reason": "no parseable envelope with timestamp_utc",
            "envelope_path": str(envelope_path),
            "total_envelopes_alltime": total_alltime,
            "total_envelopes_24h": total_24h,
            "stale_threshold_hours": stale_threshold_hours,
            "dead_threshold_hours": dead_threshold_hours,
        }

    age_hours = (now - last_ts).total_seconds() / 3600.0
    if age_hours >= dead_threshold_hours:
        status = "dead"
        hint = (
            f"Operator-Envelope-Stream tot ({age_hours:.1f}h ohne Signal). "
            "Re-Entry-Datenfluss bis 2026-05-16 ist gefährdet — Envelope dispatchen."
        )
    elif age_hours >= stale_threshold_hours:
        status = "stale"
        hint = (
            f"Operator-Envelope seit {age_hours:.1f}h aus. "
            "Routine-Action: 1-2 Envelopes/Tag bis Re-Entry."
        )
    else:
        status = "ok"
        hint = None

    return {
        "status": status,
        "hours_since_last_envelope": round(age_hours, 2),
        "last_envelope_at_utc": last_ts.isoformat(),
        "total_envelopes_alltime": total_alltime,
        "total_envelopes_24h": total_24h,
        "stale_threshold_hours": stale_threshold_hours,
        "dead_threshold_hours": dead_threshold_hours,
        "hint": hint,
    }


def _summarize_approval_latency_24h(
    *,
    now: datetime,
    send_audit_path_override: str | None = None,
    envelope_path_override: str | None = None,
) -> dict[str, object]:
    """Operator approval-click latency over the last 24h.

    Joins approval-send audit (telegram_approval_send.jsonl, written when the
    bot posts an approval card) with envelope decisions
    (telegram_message_envelope.jsonl) by envelope_id ↔ origin_envelope_id.

    Percentiles cover only *decided* clicks (filled + ignored) — that's the
    operator's actual reaction time. Expired and still_open are reported
    separately so operators see whether the TTL itself is binding (= raise
    TTL) versus the operator missing the click (= add reminder).

    Status classes:
      ok            — at least one send exists in 24h (counts populated)
      no_sends_24h  — nothing was sent in the last 24h (audit healthy, traffic dry)
      missing       — send-audit log absent or unreadable
    """
    from app.agents.tools._helpers import WORKSPACE_ROOT

    send_rel = send_audit_path_override or "artifacts/telegram_approval_send.jsonl"
    env_rel = envelope_path_override or "artifacts/telegram_message_envelope.jsonl"
    send_path = Path(send_rel) if Path(send_rel).is_absolute() else WORKSPACE_ROOT / send_rel
    env_path = Path(env_rel) if Path(env_rel).is_absolute() else WORKSPACE_ROOT / env_rel

    if not send_path.exists():
        return {
            "status": "missing",
            "reason": "send-audit log not found",
            "send_audit_path": str(send_path),
        }

    cutoff_24h = now - timedelta(hours=24)

    # envelope_id -> latest sent_ts (within 24h, status=ok)
    sent_24h: dict[str, datetime] = {}
    try:
        with send_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    rec = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("event") != "telegram_approval_send":
                    continue
                if rec.get("status") != "ok":
                    continue
                env_id = rec.get("envelope_id")
                if not isinstance(env_id, str) or not env_id:
                    continue
                ts = _parse_iso_utc(rec.get("timestamp_utc"))
                if ts is None or ts < cutoff_24h:
                    continue
                prior = sent_24h.get(env_id)
                if prior is None or ts > prior:
                    sent_24h[env_id] = ts
    except OSError:
        return {
            "status": "missing",
            "reason": "send-audit log not readable",
            "send_audit_path": str(send_path),
        }

    if not sent_24h:
        return {
            "status": "no_sends_24h",
            "sent": 0,
            "decided": 0,
            "expired": 0,
            "still_open": 0,
            "p50_seconds": None,
            "p90_seconds": None,
            "p99_seconds": None,
            "decision_rate_pct": None,
        }

    # Earliest decision per origin_envelope_id (filled / ignored / expired).
    # An envelope can have multiple records (e.g. expired + later manual fix);
    # we treat the *first* decision as canonical because TTL/dedup logic does.
    decisions: dict[str, tuple[str, datetime]] = {}
    if env_path.exists():
        try:
            with env_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        rec = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    if rec.get("event") != "telegram_channel_approval":
                        continue
                    origin_id = rec.get("origin_envelope_id")
                    if not isinstance(origin_id, str) or origin_id not in sent_24h:
                        continue
                    stage = str(rec.get("stage") or "")
                    if stage not in {"accepted", "ignored", "expired"}:
                        continue
                    ts = _parse_iso_utc(rec.get("timestamp_utc"))
                    if ts is None:
                        continue
                    prior = decisions.get(origin_id)
                    if prior is None or ts < prior[1]:
                        decisions[origin_id] = (stage, ts)
        except OSError:
            pass

    decided_latencies: list[float] = []
    decided_count = 0
    expired_count = 0
    still_open_count = 0

    for env_id, sent_ts in sent_24h.items():
        decision = decisions.get(env_id)
        if decision is None:
            still_open_count += 1
            continue
        stage, decision_ts = decision
        latency_s = max(0.0, (decision_ts - sent_ts).total_seconds())
        if stage == "expired":
            expired_count += 1
        else:
            decided_count += 1
            decided_latencies.append(latency_s)

    def _pct(p: float) -> float | None:
        if not decided_latencies:
            return None
        ordered = sorted(decided_latencies)
        # Nearest-rank — stable on small n. Index clamped to [0, len-1].
        idx = int(round(p / 100.0 * (len(ordered) - 1)))
        idx = max(0, min(len(ordered) - 1, idx))
        return round(ordered[idx], 1)

    sent_total = len(sent_24h)
    return {
        "status": "ok",
        "sent": sent_total,
        "decided": decided_count,
        "expired": expired_count,
        "still_open": still_open_count,
        "p50_seconds": _pct(50),
        "p90_seconds": _pct(90),
        "p99_seconds": _pct(99),
        "decision_rate_pct": (
            round(100.0 * decided_count / sent_total, 1) if sent_total > 0 else None
        ),
    }


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
    from app.alerts.audit import load_alert_audits, load_outcome_annotations
    from app.orchestrator.trading_loop import load_trading_loop_cycles

    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff_24h = now_utc - timedelta(hours=24)
    today_date = now_utc.date()

    degraded = False
    portfolio_snapshot_ok = False
    alert_audit_ok = False
    loop_audit_ok = False
    audits_for_decision_pack: list[object] | None = None
    outcome_by_doc_for_decision_pack: dict[str, str] = {}
    try:
        paper_execution_resolved_path: Path | None = resolve_workspace_path(
            paper_execution_audit_path,
            label="Paper execution audit",
            allowed_suffixes=frozenset({".jsonl"}),
        )
    except Exception:
        paper_execution_resolved_path = None

    # Portfolio (positions, cash, equity, realized PnL) — single read of the
    # paper execution audit via the canonical snapshot helper. Same source as
    # /operator/portfolio-snapshot, so /status, Telegram /status, and the
    # browser dashboard agree on the truth. Replaces the prior exposure-only
    # read whose 'position_count' key never matched the producer's
    # 'priced_position_count' (silent 0-fallback for 7 surfaces).
    cash_usd: float | None
    total_equity_usd: float | None
    realized_pnl_usd: float | None
    try:
        snapshot = await build_paper_portfolio_snapshot_helper(
            audit_path=paper_execution_audit_path,
        )
        position_count: int | None = int(snapshot.position_count)
        cash_usd = float(snapshot.cash_usd)
        total_equity_usd = float(snapshot.total_equity_usd)
        realized_pnl_usd = float(snapshot.realized_pnl_usd)
        portfolio_snapshot_ok = True
    except Exception:
        position_count = None
        cash_usd = None
        total_equity_usd = None
        realized_pnl_usd = None
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
        outcomes = load_outcome_annotations(audit_dir)
        audits_for_decision_pack = list(audits)
        outcome_by_doc_for_decision_pack = {
            outcome.document_id: outcome.outcome for outcome in outcomes
        }
        alert_audit_ok = True
        recent_alerts = 0
        for record in audits:
            dispatched = _parse_iso_utc(record.dispatched_at)
            if dispatched is not None and dispatched >= cutoff_24h:
                recent_alerts += 1
        alert_rate_24h: float | None = round(recent_alerts / 24.0, 2)
    except Exception:
        alert_rate_24h = None
        degraded = True

    # Cycles today — count loop audit rows whose started_at is on today's UTC
    # date. While we have the audit loaded, also build a 24h status-breakdown
    # so /status surfaces "loop running but blocked by design" vs "loop dead"
    # instead of conflating both as activity. priority_rejected > 50% trips
    # the alert flag — that's the threshold the V1 priority-pipeline fix
    # exposed (Cycles routinely 100% rejected by design under conservative).
    cycle_count_today: int | None
    cycle_status_breakdown_24h: dict[str, int] | None
    priority_rejected_pct_24h: float | None
    priority_rejected_alert: bool
    try:
        loop_path = resolve_workspace_path(
            loop_audit_path,
            label="Loop audit",
            allowed_suffixes=frozenset({".jsonl"}),
        )
        cycles = load_trading_loop_cycles(loop_path)
        cycle_count_today_int = 0
        breakdown: dict[str, int] = {}
        breakdown_total = 0
        for row in cycles:
            started = _parse_iso_utc(row.get("started_at"))
            if started is None:
                continue
            if started.date() == today_date:
                cycle_count_today_int += 1
            if started >= cutoff_24h:
                status_value = str(row.get("status") or "unknown")
                breakdown[status_value] = breakdown.get(status_value, 0) + 1
                breakdown_total += 1
        cycle_count_today = cycle_count_today_int
        cycle_status_breakdown_24h = breakdown
        loop_audit_ok = True
        if breakdown_total > 0:
            rejected = breakdown.get("priority_rejected", 0)
            priority_rejected_pct_24h = round(100.0 * rejected / breakdown_total, 1)
        else:
            priority_rejected_pct_24h = None
        priority_rejected_alert = (
            priority_rejected_pct_24h is not None and priority_rejected_pct_24h > 50.0
        )
    except Exception:
        cycle_count_today = None
        cycle_status_breakdown_24h = None
        priority_rejected_pct_24h = None
        priority_rejected_alert = False
        degraded = True

    # SAT-C-002: TV-Webhook auth-method counter (24h). Silent hmac→shared_token
    # downgrade sichtbar machen. None wenn Audit-Log fehlt/leer.
    tv_auth_summary: dict[str, object] | None
    tv_configured_mode: str = "unknown"
    try:
        tv_settings = get_settings().tradingview
        tv_configured_mode = tv_settings.webhook_auth_mode
        tv_auth_summary = _summarize_tradingview_webhook_auth_24h(
            tv_settings.webhook_audit_log, cutoff_24h=cutoff_24h
        )
    except Exception:
        tv_auth_summary = None

    # Telegram premium-channel listener liveness (D-125-adjacent watchdog).
    # `stale` means the listener process likely died; operators must restart
    # via scripts/telegram_listener_start.sh. Does not mark the overall
    # summary as degraded — ingest going dark is an operational warning,
    # not a data-integrity failure.
    tg_channel_ingest = _summarize_telegram_channel_ingest(now=now_utc)
    warp_status = _summarize_warp_status()
    # Operator-Envelope activity watchdog. Surfaces silent gaps in the
    # operator-curated envelope stream (e.g. 7-day blackout 2026-04-21..04-28).
    operator_envelope = _summarize_operator_envelope_activity(now=now_utc)
    # B-6 approval-click latency over 24h. Replaces the manual JSONL pull that
    # the TTL-decision (2026-05-07) was previously gated on.
    approval_latency_24h = _summarize_approval_latency_24h(now=now_utc)

    def _or_unknown(value: int | float | None) -> object:
        return value if value is not None else "?"

    decision_pack = _build_decision_pack_20260530(
        audits=audits_for_decision_pack,
        outcome_by_doc=outcome_by_doc_for_decision_pack,
        now_utc=now_utc,
        portfolio_snapshot_ok=portfolio_snapshot_ok,
        alert_audit_ok=alert_audit_ok,
        loop_audit_ok=loop_audit_ok,
        paper_execution_audit_path=paper_execution_resolved_path,
    )

    return {
        "report_type": "daily_operator_summary",
        "execution_enabled": False,
        "write_back_allowed": False,
        "status": "degraded" if degraded else "operational",
        "readiness_status": "degraded" if degraded else "operational",
        "position_count": _or_unknown(position_count),
        "cash_usd": _or_unknown(cash_usd),
        "total_equity_usd": _or_unknown(total_equity_usd),
        "realized_pnl_usd": _or_unknown(realized_pnl_usd),
        "ingestion_backlog_documents": _or_unknown(ingestion_backlog),
        "alert_fire_rate_docs_per_hour_24h": _or_unknown(alert_rate_24h),
        "cycle_count_today": _or_unknown(cycle_count_today),
        "cycle_status_breakdown_24h": (
            cycle_status_breakdown_24h if cycle_status_breakdown_24h is not None else "?"
        ),
        "priority_rejected_pct_24h": _or_unknown(priority_rejected_pct_24h),
        "priority_rejected_alert": priority_rejected_alert,
        "tv_webhook_auth_24h": {
            "configured_mode": tv_configured_mode,
            "summary": tv_auth_summary if tv_auth_summary is not None else "no_audit_log",
        },
        "telegram_channel_ingest": tg_channel_ingest,
        "operator_envelope": operator_envelope,
        "approval_latency_24h": approval_latency_24h,
        "warp_status": warp_status,
        "decision_pack_2026_05_30": decision_pack,
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
    Enriches each alert with outcome annotation fields when available
    (``resolved_at``, ``resolved_after_seconds``, ``outcome``) from the
    operator-annotated alert_outcomes.jsonl.
    execution_enabled and write_back_allowed are always False.
    """
    from datetime import datetime

    from app.alerts.audit import load_alert_audits, load_outcome_annotations

    resolved = resolve_workspace_dir(
        audit_dir,
        label="Alert audit directory",
    )
    audits = load_alert_audits(resolved)
    outcomes = load_outcome_annotations(resolved)

    # Latest annotation wins when an operator re-annotates the same document.
    outcome_by_doc: dict[str, dict[str, object]] = {}
    for ann in outcomes:
        prev = outcome_by_doc.get(ann.document_id)
        if prev is None or str(prev.get("annotated_at", "")) <= ann.annotated_at:
            outcome_by_doc[ann.document_id] = {
                "outcome": ann.outcome,
                "resolved_at": ann.annotated_at,
            }

    def _seconds_between(dispatched: str, resolved: str) -> float | None:
        try:
            d = datetime.fromisoformat(dispatched.replace("Z", "+00:00"))
            r = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        delta = (r - d).total_seconds()
        return delta if delta >= 0 else None

    enriched: list[dict[str, object]] = []
    for audit in audits:
        row = audit.to_json_dict()
        outcome_entry = outcome_by_doc.get(audit.document_id)
        if outcome_entry is not None:
            row["outcome"] = outcome_entry["outcome"]
            row["resolved_at"] = outcome_entry["resolved_at"]
            sec = _seconds_between(audit.dispatched_at, str(outcome_entry["resolved_at"]))
            if sec is not None:
                row["resolved_after_seconds"] = sec
        enriched.append(row)

    return {
        "report_type": "alert_audit_summary",
        "execution_enabled": False,
        "write_back_allowed": False,
        "total_alerts": len(audits),
        "total_resolved": len(outcome_by_doc),
        "alerts": enriched,
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
