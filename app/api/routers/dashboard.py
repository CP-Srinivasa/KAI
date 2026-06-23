"""Operator dashboard JSON API.

Liefert Quality-Bar-Metriken (`GET /dashboard/api/quality`) für das React-SPA
unter `/dashboard`. Das SPA selbst wird in `app/api/main.py` als StaticFiles-
Mount (`web/dist/`) eingehängt — dieser Router kümmert sich nur noch um die
JSON-Daten.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.alerts.hold_metrics import build_hold_metrics_report
from app.api.deps import get_document_repo
from app.audit.stream_validation import (
    AuditStreamName,
    AuditStreamReadResult,
    load_audit_stream,
    summarize_audit_stream_result,
)
from app.learning.bayes_quarantine import is_corrupt_close
from app.observability.dashboard_metric_registry import (
    build_dashboard_metric_registry,
    reconcile_dashboard_snapshot,
)
from app.storage.repositories.document_repo import DocumentRepository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

_ARTIFACTS = Path("artifacts")
_ALERT_AUDIT = _ARTIFACTS / "alert_audit.jsonl"
_ALERT_OUTCOMES = _ARTIFACTS / "alert_outcomes.jsonl"
_TRADING_LOOP_AUDIT = _ARTIFACTS / "trading_loop_audit.jsonl"
_PAPER_EXECUTION_AUDIT = _ARTIFACTS / "paper_execution_audit.jsonl"
_BRIDGE_PENDING_ORDERS = _ARTIFACTS / "bridge_pending_orders.jsonl"
_ENTRY_WATCHER_AUDIT = _ARTIFACTS / "entry_watcher_audit.jsonl"
_TV_PENDING = _ARTIFACTS / "tradingview_pending_signals.jsonl"
_AUDIT_V1_DISQUALIFIED_FLAG = _ARTIFACTS / "paper_execution_audit_v1_disqualified.flag"
_SOURCE_RELIABILITY_REPORT = Path("monitor/source_reliability.json")

# Frankfurter: ECB reference rates, no API key, daily refresh (~16:00 CET).
# 1-hour TTL is generous — the underlying rate updates once per business day.
# .app domain redirects to .dev/v1 since the 2026 migration; we hit /v1 directly.
_FX_URL = "https://api.frankfurter.dev/v1/latest"
_FX_CACHE_TTL_S = 3600.0
_FX_FALLBACK_EUR_PER_USD = 0.921  # mirrors web/src/state/CurrencyProvider.tsx
_fx_cache: dict[str, Any] = {"at": 0.0, "payload": None}

# In-process TTL cache for the hold report. build_hold_metrics_report touches
# four jsonl files (~2 MB total) and runs in ~400 ms. With the dashboard
# polling every 60 s and other consumers (telegram, agent_worker) reading the
# same data, a 30 s cache absorbs bursts without hiding fresh ticks.
_HOLD_CACHE_TTL_S = 30.0
_hold_cache: dict[str, Any] = {"at": 0.0, "report": None}

# Same rationale as _hold_cache: the provenance report reads the same
# alert-audit + outcomes files plus the TV pending log. 30 s TTL.
_PROVENANCE_CACHE_TTL_S = 30.0
_provenance_cache: dict[str, Any] = {"at": 0.0, "payload": None}

# NEO-P-201/202: the quality endpoint full-scans paper/alert/loop audit JSONL
# (trading_loop_audit ~27 MB) and had no top-level cache -> 2-4s on the Pi on
# EVERY request, even repeats, and it is polled by ~6 components in parallel.
# Cache the assembled response so repeat polls are served instantly; TTL matches
# the inner _hold/_provenance caches it depends on.
_QUALITY_CACHE_TTL_S = 20.0
_quality_cache: dict[str, Any] = {"at": 0.0, "payload": None}

# Edge-window build parses both audit streams (trading_loop_audit ~27 MB), so the
# Edge-Truth panel's 60 s poll must not re-parse every tick. Keyed by mode.
_EDGE_WINDOW_CACHE_TTL_S = 60.0
_edge_window_cache: dict[str, dict[str, Any]] = {}

# Source-by-doc map (for the active-precision legacy split). Loading it means
# one DB query over directional doc-ids — not hot-path safe without a cache.
# 5 min TTL: docs are rarely re-classified, and the map is additive.
_SOURCE_MAP_TTL_S = 300.0
_source_map_cache: dict[str, Any] = {"at": 0.0, "map": None}

_REENTRY_TARGET_DATE = "2026-05-16"
_ARTIFACT_STALE_WARNING_HOURS = 3.0
_ARTIFACT_STALE_CRITICAL_HOURS = 24.0


def _warn_on_audit_stream_issues(result: AuditStreamReadResult) -> None:
    if not result.issues:
        return
    summary = summarize_audit_stream_result(result)
    logger.warning(
        "audit_stream_schema_issues: stream=%s issues=%s first=%s",
        result.stream,
        result.issue_count,
        summary["sample_issues"][0] if summary["sample_issues"] else "n/a",
    )


def _validate_dashboard_stream(path: Path, stream: AuditStreamName) -> AuditStreamReadResult:
    result = load_audit_stream(path, stream)
    _warn_on_audit_stream_issues(result)
    return result


def _load_jsonl(path: Path, tail: int = 0) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except json.JSONDecodeError:
            continue
    return rows[-tail:] if tail else rows


def _parse_iso_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _artifact_updated_at(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    except OSError:
        return None


def _artifact_stale_status(path: Path, *, now: datetime | None = None) -> str:
    updated_at = _artifact_updated_at(path)
    if updated_at is None:
        return "unverified"
    updated_dt = _parse_iso_utc(updated_at)
    if updated_dt is None:
        return "unverified"
    age_hours = ((now or datetime.now(UTC)) - updated_dt).total_seconds() / 3600.0
    if age_hours >= _ARTIFACT_STALE_CRITICAL_HOURS:
        return "stale"
    if age_hours >= _ARTIFACT_STALE_WARNING_HOURS:
        return "warning"
    return "ok"


def _first_present_ts(row: dict[str, Any], keys: tuple[str, ...]) -> datetime | None:
    for key in keys:
        parsed = _parse_iso_utc(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _reentry_target_from_settings() -> tuple[str, str]:
    """Resolve the Re-Entry target date, preferring operator config.

    Returns ``(target_date, source)`` where source is ``"config"`` when the
    value comes from ``ALERT_REENTRY_TARGET_DATE`` and ``"default_historical"``
    when it falls back to the module constant. Never invents a new target.
    """
    try:
        from app.core.settings import get_settings

        configured = (get_settings().alerts.reentry_target_date or "").strip()
        if configured:
            return configured, "config"
    except Exception as exc:  # noqa: BLE001 — never break the dashboard on config load
        logger.warning("reentry_target_settings_load_failed: %s", exc)
    return _REENTRY_TARGET_DATE, "default_historical"


def _reentry_status(*, target_date: str | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    if target_date is None:
        target_date, target_source = _reentry_target_from_settings()
    else:
        target_source = "explicit"
    target = _parse_iso_utc(f"{target_date}T00:00:00+00:00")
    if target is None:
        # Fail safe: an empty/invalid configured date must not crash and must
        # not look current — it needs an operator re-evaluation.
        return {
            "target_date": target_date,
            "target_source": target_source,
            "today": now.date().isoformat(),
            "status": "requires_re_evaluation",
            "days_delta": None,
            "warning": (
                "Re-Entry target date is missing or not parseable; operator must set a new target."
            ),
        }
    delta_days = (target.date() - now.date()).days
    if delta_days < 0:
        # A target in the past means there is no CURRENTLY ACTIVE re-entry target.
        # Present it neutrally (config pending) rather than as an alarming
        # "expired/error": the operator simply has not set a new target yet. The
        # lapsed date is still surfaced for context; a genuinely future target
        # below reads as "active".
        return {
            "target_date": target_date,
            "target_source": target_source,
            "today": now.date().isoformat(),
            "status": "no_active_target",
            "days_delta": delta_days,
            "warning": (
                f"Kein aktives Re-Entry-Target — das letzte Ziel ({target_date}) liegt "
                "in der Vergangenheit, Konfiguration ausstehend (kein Fehler). Operator "
                "setzt ALERT_REENTRY_TARGET_DATE, sobald ein neues Ziel feststeht."
            ),
        }
    return {
        "target_date": target_date,
        "target_source": target_source,
        "today": now.date().isoformat(),
        "status": "active",
        "days_delta": delta_days,
        "warning": None,
    }


def _metric_contract(
    *,
    value: object,
    unit: str,
    semantic_type: str,
    scope: str,
    source_artifact: Path,
    generated_at: str,
    window_hours: int | None = None,
    since: str | None = None,
    until: str | None = None,
    sample_size: int | None = None,
    is_decision_relevant: bool = False,
    is_read_only: bool = True,
    quality_status: str = "ok",
    warning: str | None = None,
    explanation: str | None = None,
    confidence_interval: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "semantic_type": semantic_type,
        "scope": scope,
        "window_hours": window_hours,
        "since": since,
        "until": until,
        "generated_at": generated_at,
        "source_artifact": str(source_artifact),
        "source_artifact_updated_at": _artifact_updated_at(source_artifact),
        "stale_status": _artifact_stale_status(source_artifact),
        "sample_size": sample_size,
        "confidence_interval": confidence_interval,
        "is_decision_relevant": is_decision_relevant,
        "is_read_only": is_read_only,
        "quality_status": quality_status,
        "warning": warning,
        "explanation": explanation,
    }


def _pct_fraction(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(float(value) * 100.0, 1)


def _empty_source_reliability(status: str) -> dict[str, Any]:
    # FS-3 (#199): a missing/unreadable/invalid report is fail-CLOSED — explicitly
    # NOT a neutral-good state. reliability_status carries the fail-closed vocab so
    # the UI never reads absence as "all fine".
    _status_map = {"missing": "unavailable", "unreadable": "corrupt", "invalid": "corrupt"}
    return {
        "status": status,
        "reliability_status": _status_map.get(status, "unavailable"),
        "generated_at": None,
        "window_days": None,
        "quality_status": "unverified",
        "health_warning": (
            "source_reliability.json is not available — no trust boosts applied (fail-closed)."
        ),
        "trusted_count": 0,
        "source_count": 0,
        "active_sources_count": 0,
        "legacy_sources_count": 0,
        "unknown_sources_count": 0,
        "tier_counts": {},
        "top_sources": [],
        "unknown_bucket": None,
    }


def _load_source_reliability_summary() -> dict[str, Any]:
    if not _SOURCE_RELIABILITY_REPORT.exists():
        return _empty_source_reliability("missing")
    try:
        payload = json.loads(_SOURCE_RELIABILITY_REPORT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("source_reliability_unreadable: %s", exc)
        return _empty_source_reliability("unreadable")
    if not isinstance(payload, dict):
        return _empty_source_reliability("invalid")

    raw_scores = payload.get("scores")
    raw_thresholds = payload.get("thresholds")
    thresholds = raw_thresholds if isinstance(raw_thresholds, dict) else {}
    # Minimum resolved-signal count below which a source's hit-rate is not
    # statistically load-bearing — 100% at n=1 must never read as "trusted".
    # The producer (source_reliability.build_source_reliability_report) serializes
    # this as `min_n_for_promote`; the legacy `min_n`/`min_resolved` keys are kept
    # for backward compatibility. Fallback default mirrors the promote gate (30),
    # NOT an unrelated 50 (which silently over-flagged sources as provisional).
    min_n = int(
        thresholds.get("min_n_for_promote")
        or thresholds.get("min_n")
        or thresholds.get("min_resolved")
        or 30
    )
    scores: list[dict[str, Any]] = []
    tier_counts: dict[str, int] = {}
    provisional_count = 0
    if isinstance(raw_scores, dict):
        for key, raw in raw_scores.items():
            if not isinstance(raw, dict):
                continue
            source = str(raw.get("source_name") or key)
            tier = str(raw.get("tier") or "unknown")
            n = int(raw.get("n") or 0)
            is_provisional = n < min_n
            score = {
                "source_name": source,
                "hits": int(raw.get("hits") or 0),
                "miss": int(raw.get("miss") or 0),
                "n": n,
                "point_estimate_pct": _pct_fraction(raw.get("point_estimate")),
                "wilson_lower_95_pct": _pct_fraction(raw.get("wilson_lower_95")),
                "tier": tier,
                "priority_modifier": int(raw.get("priority_modifier") or 0),
                # small-n deemphasis: UI must render provisional sources muted
                # and never as load-bearing quality (D-truth-layer).
                "is_provisional": is_provisional,
                "sample_warning": (
                    f"Only {n} resolved signals (< {min_n}); hit-rate is provisional."
                    if is_provisional
                    else None
                ),
            }
            scores.append(score)
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            if is_provisional:
                provisional_count += 1

    scores.sort(key=lambda score: int(score.get("n") or 0), reverse=True)
    unknown_bucket = next(
        (score for score in scores if str(score.get("source_name", "")).lower() == "unknown"),
        None,
    )
    trusted_count = tier_counts.get("trusted", 0)
    low_count = tier_counts.get("low", 0)
    generated_at = payload.get("generated_at")
    generated_dt = _parse_iso_utc(generated_at)
    stale = False
    if generated_dt is not None:
        stale = (datetime.now(UTC) - generated_dt).total_seconds() > 36 * 3600
    unknown_low = bool(unknown_bucket and unknown_bucket.get("tier") == "low")
    quality_status = "ok"
    health_warning = None
    if stale:
        quality_status = "stale"
        health_warning = "Source-Reliability report is older than 36h."
    elif trusted_count == 0 and low_count > 0:
        quality_status = "warning"
        health_warning = (
            "No trusted source currently exists; source reliability is a quality constraint, "
            "not a positive readiness signal."
        )
    if unknown_low:
        quality_status = "critical" if trusted_count == 0 else "warning"
        health_warning = (
            "Unknown/legacy bucket is low quality; active and legacy source evidence must stay "
            "separated."
        )
    # FS-3 (#199): explicit active/legacy/unknown separation so legacy never
    # inflates trusted and 0-trusted-with-evidence never reads as healthy.
    _legacy_tokens = {"unknown", ""}
    legacy_sources_count = sum(
        1 for s in scores if str(s.get("source_name", "")).strip().lower() in _legacy_tokens
    )
    unknown_sources_count = sum(
        1 for s in scores if str(s.get("source_name", "")).strip().lower() == "unknown"
    )
    active_sources_count = len(scores) - legacy_sources_count
    # reliability_status: fail-closed vocab. stale wins; otherwise ok.
    reliability_status = "stale" if stale else "ok"
    return {
        "status": "ok",
        "reliability_status": reliability_status,
        "generated_at": generated_at,
        "window_days": payload.get("window_days"),
        "thresholds": payload.get("thresholds", {}),
        "quality_status": quality_status,
        "health_warning": health_warning,
        "trusted_count": trusted_count,
        "source_count": len(scores),
        "active_sources_count": active_sources_count,
        "legacy_sources_count": legacy_sources_count,
        "unknown_sources_count": unknown_sources_count,
        "provisional_count": provisional_count,
        "min_n": min_n,
        "tier_counts": tier_counts,
        "top_sources": scores[:8],
        "unknown_bucket": unknown_bucket,
    }


async def _load_source_by_doc() -> dict[str, str]:
    """Resolve directional doc-ids → source_name from the DB.

    Used so the hold-report's active-precision metric can filter the
    legacy-unknown bucket (docs that have no CanonicalDocument row, a
    pre-D-139 artefact). On any failure returns an empty map — hold_metrics
    falls back to its date-based cutoff.
    """
    now = time.monotonic()
    cached = _source_map_cache.get("map")
    if cached is not None and (now - _source_map_cache["at"]) < _SOURCE_MAP_TTL_S:
        return cast(dict[str, str], cached)

    try:
        from sqlalchemy import select

        from app.core.settings import get_settings
        from app.storage.db.session import build_session_factory
        from app.storage.models.document import CanonicalDocumentModel

        audits = _validate_dashboard_stream(_ALERT_AUDIT, "alert_audit").rows
        doc_ids: set[str] = set()
        for rec in audits:
            sentiment = str(rec.get("sentiment_label") or "").lower()
            if bool(rec.get("is_digest")) or sentiment not in {"bullish", "bearish"}:
                continue
            doc_ids.add(str(rec["document_id"]))
        if not doc_ids:
            _source_map_cache["map"] = {}
            _source_map_cache["at"] = now
            return {}

        session_factory = build_session_factory(get_settings().db)
        async with session_factory.begin() as session:
            stmt = select(
                CanonicalDocumentModel.id,
                CanonicalDocumentModel.source_name,
                CanonicalDocumentModel.provider,
            ).where(CanonicalDocumentModel.id.in_(doc_ids))
            rows = (await session.execute(stmt)).all()
        source_map = {
            str(row[0]): ((row[1] or row[2] or "unknown").strip().lower()) for row in rows
        }
        # doc_ids present in audits but absent from DB → legacy-unknown
        for doc_id in doc_ids:
            source_map.setdefault(doc_id, "unknown")
        _source_map_cache["map"] = source_map
        _source_map_cache["at"] = now
        return source_map
    except Exception as exc:
        logger.warning("source_map_load_failed: %s", exc)
        return cached or {}


async def _live_hold_report() -> dict[str, Any]:
    """Build the hold-metrics report on demand from the live audit files.

    Replaces the previous behaviour of reading a pre-computed snapshot file
    that was only refreshed by an out-of-band script run — that snapshot was
    sometimes 24 h+ stale, making the dashboard quality-bar feel frozen.
    """
    now = time.monotonic()
    if _hold_cache["report"] is not None and (now - _hold_cache["at"]) < _HOLD_CACHE_TTL_S:
        return cast(dict[str, Any], _hold_cache["report"])
    source_map = await _load_source_by_doc()
    _validate_dashboard_stream(_ALERT_AUDIT, "alert_audit")
    _validate_dashboard_stream(_PAPER_EXECUTION_AUDIT, "paper_execution_audit")
    report = build_hold_metrics_report(
        alert_audit_path=_ALERT_AUDIT,
        alert_outcomes_path=_ALERT_OUTCOMES,
        trading_loop_audit_path=_TRADING_LOOP_AUDIT,
        paper_execution_audit_path=_PAPER_EXECUTION_AUDIT,
        source_by_doc=source_map or None,
    )
    _hold_cache["report"] = report
    _hold_cache["at"] = now
    return report


async def _fetch_fx_live() -> dict[str, Any] | None:
    """Fetch USD->EUR from Frankfurter (ECB ref). Returns None on any failure."""
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            r = await client.get(_FX_URL, params={"base": "USD", "symbols": "EUR"})
            r.raise_for_status()
            data = r.json()
            eur = float(data["rates"]["EUR"])
            return {
                "base": "USD",
                "rates": {"USD": 1.0, "EUR": eur},
                "source": "frankfurter.app (ECB ref)",
                "as_of": data.get("date", ""),
                "fetched_at": datetime.now(UTC).isoformat(),
                "live": True,
            }
    except Exception as exc:
        logger.warning("fx_fetch_failed: %s", exc)
        return None


@router.get("/dashboard/api/fx", tags=["dashboard"])
async def dashboard_fx_api() -> JSONResponse:
    """USD-base FX rates for dashboard display formatting.

    Live-source: Frankfurter.app (ECB reference rates, daily). Cached 1 h.
    On any failure (network, parse, schema) returns the static fallback rate
    so the UI never breaks — flagged with ``live=false`` so the client can
    show a hint if desired.
    """
    now = time.monotonic()
    if _fx_cache["payload"] is not None and (now - _fx_cache["at"]) < _FX_CACHE_TTL_S:
        return JSONResponse(
            content=_fx_cache["payload"],
            headers={"Cache-Control": "public, max-age=1800"},
        )

    payload = await _fetch_fx_live()
    if payload is None:
        payload = {
            "base": "USD",
            "rates": {"USD": 1.0, "EUR": _FX_FALLBACK_EUR_PER_USD},
            "source": "fallback (static)",
            "as_of": "",
            "fetched_at": datetime.now(UTC).isoformat(),
            "live": False,
        }

    _fx_cache["payload"] = payload
    _fx_cache["at"] = now
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "public, max-age=1800"},
    )


_SHADOW_LEDGER = _ARTIFACTS / "shadow_candidate_ledger.jsonl"

# Operator-readable labels for every EXECUTION_ENTRY_MODE (incl. the D-233
# limited paper modes). The badge must never show a raw enum the operator has
# to decode.
_ENTRY_MODE_LABELS: dict[str, str] = {
    "disabled": "disabled — Kill-Switch",
    "paper_premium_limited": "paper (nur Premium-Route)",
    "paper_learning": "paper-learning (Premium + Real-Analysis)",
    "paper": "paper (voll)",
    "probe": "probe (gedrosselt)",
    "live_limited": "LIVE limited",
    "live_normal": "LIVE",
}


def _entry_runtime_block() -> dict[str, Any]:
    """Live entry-mode truth for the dashboard badge (S6, D-233 modes incl.).

    Uses the same ``resolve_entry_policy`` SSOT the bridge/loop/feeder enforce
    — including the legacy three-arm migration aliases, so a Pi on
    ``disabled``+Acks shows its routes as open via alias instead of pretending
    the kill-switch closed everything.
    """
    try:
        from app.core.settings import get_settings
        from app.execution.entry_policy import resolve_entry_policy

        policy = resolve_entry_policy(get_settings())
        open_routes = [
            {
                "route": route.value,
                "alias_used": verdict.alias_used,
            }
            for route, verdict in policy.verdicts.items()
            if verdict.allowed
        ]
        return {
            "entry_mode": policy.mode.value,
            "entry_mode_label": _ENTRY_MODE_LABELS.get(policy.mode.value, policy.mode.value),
            "autonomous_loop_open": policy.mode.allows_autonomous_loop_entry,
            "open_routes": open_routes,
            "contradictions": list(policy.contradictions),
        }
    except Exception as exc:  # noqa: BLE001 — badge must degrade, never 500 the endpoint
        logger.warning("entry runtime block failed: %s", exc)
        return {"entry_mode": None, "entry_mode_label": "unbekannt", "error": str(exc)}


def _shadow_attribution_24h() -> dict[str, Any]:
    """Real-vs-canary attribution of shadow candidates in the last 24h (S6).

    REAL == ``source=autonomous_generator`` (the fail-closed REAL_SOURCES set);
    every other source (autonomous_loop, canary_probe, …) counts as probe. A
    missing/empty ledger returns zeros — honest absence, no fabrication.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    real = 0
    probe = 0
    for record in _load_jsonl(_SHADOW_LEDGER, tail=8000):
        ts_raw = record.get("ts_utc")
        if not isinstance(ts_raw, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts < cutoff:
            continue
        if record.get("source") == "autonomous_generator":
            real += 1
        else:
            probe += 1
    return {"real_candidates_24h": real, "probe_candidates_24h": probe}


@router.get("/dashboard/api/quality", tags=["dashboard"])
async def dashboard_quality_api() -> JSONResponse:
    """Return quality-bar metrics as JSON for the dashboard SPA."""
    cache_now = time.monotonic()
    cached_quality = _quality_cache.get("payload")
    if cached_quality is not None and (cache_now - _quality_cache["at"]) < _QUALITY_CACHE_TTL_S:
        return JSONResponse(
            content=cached_quality, headers={"Cache-Control": "no-store, max-age=0"}
        )
    report = await _live_hold_report()

    quality = report.get("signal_quality_validation", {})
    hit_rate = report.get("alert_hit_rate_evidence", {})
    paper = report.get("paper_trading_evidence", {})
    gate = report.get("hold_gate_evaluation", {})

    exec_rows = _load_jsonl(_PAPER_EXECUTION_AUDIT)
    fills = [r for r in exec_rows if r.get("event_type") == "order_filled"]
    # NEO-P-101-r2: position_closed-Events haben ab schema_version=v2 das
    # per-Trade NETTO-Feld trade_pnl_usd (inkl. Fee). Legacy v1-Zeilen (vor
    # NEO-P-101-r2) tragen nur entry_price/exit_price/quantity — dafür
    # rekonstruieren wir per Brutto-Formel (ohne Fee). Fees werden mit
    # NEO-P-106 (Maker/Taker-Modell) per-trade nachgereicht.
    #
    # 2026-05-25 Forensik-Fix: position_partial_closed Events tragen ebenfalls
    # trade_pnl_usd (siehe paper_engine.py:842). Vorher wurden sie ignoriert,
    # was zu einer systematischen PnL-Untererfassung führte (Codex-Beleg: Pi
    # hatte 24 partials vs 15 fulls; Quality-Endpoint zeigte $759 statt $2486).
    # Exclude corrupt closes so the quality card shows real PnL. Unified verdict
    # (bayes_quarantine.is_corrupt_close) = exact forensic signatures (DS-20260529-V1
    # MATIC stale-exit, DS-20260601 ETH off-market) OVER the generic phantom-return
    # guard — same set as the realized-by-asset path (2026-06-23 leak fix).
    closes: list[dict[str, Any]] = []
    quarantined_closes_list: list[dict[str, Any]] = []
    for r in exec_rows:
        if r.get("event_type") not in ("position_closed", "position_partial_closed"):
            continue
        if is_corrupt_close(r):
            quarantined_closes_list.append(r)
        else:
            closes.append(r)

    def _close_pnl(r: dict[str, Any]) -> float:
        if r.get("schema_version") == "v2":
            return float(r.get("trade_pnl_usd", 0.0))
        return (float(r.get("exit_price", 0.0)) - float(r.get("entry_price", 0.0))) * float(
            r.get("quantity", 0.0)
        )

    generated_at = str(report.get("generated_at", datetime.now(UTC).isoformat()))
    now_utc = datetime.now(UTC)
    rolling_window_hours = 24
    rolling_start = now_utc - timedelta(hours=rolling_window_hours)
    close_ts_keys = (
        "closed_at",
        "timestamp_utc",
        "filled_at",
        "executed_at",
        "created_at",
        "updated_at",
    )
    fill_ts_keys = ("filled_at", "timestamp_utc", "created_at", "executed_at")
    recent_closes = [
        r
        for r in closes
        if (ts := _first_present_ts(r, close_ts_keys)) is not None and ts >= rolling_start
    ]
    recent_fills = [
        r
        for r in fills
        if (ts := _first_present_ts(r, fill_ts_keys)) is not None and ts >= rolling_start
    ]
    recent_pnl_usd = round(sum(_close_pnl(r) for r in recent_closes), 2)
    pnl_values = [_close_pnl(r) for r in closes]
    wins = [pnl for pnl in pnl_values if pnl > 0]
    losses = [pnl for pnl in pnl_values if pnl < 0]
    decided_closes = len(wins) + len(losses)
    expectancy = round(sum(pnl_values) / len(pnl_values), 2) if pnl_values else None
    win_rate = round(len(wins) / decided_closes * 100.0, 2) if decided_closes else None
    avg_win = round(sum(wins) / len(wins), 2) if wins else None
    avg_loss = round(sum(losses) / len(losses), 2) if losses else None

    realized_pnl_usd = round(sum(_close_pnl(r) for r in closes), 2)
    quarantined_pnl_usd = round(sum(_close_pnl(r) for r in quarantined_closes_list), 2)
    positions_closed = sum(1 for r in closes if r.get("event_type") == "position_closed")
    positions_partial_closed = sum(
        1 for r in closes if r.get("event_type") == "position_partial_closed"
    )

    audit_rows = _load_jsonl(_ALERT_AUDIT)
    non_digest = [r for r in audit_rows if not r.get("is_digest")]
    recent_alerts = non_digest[-20:]

    outcome_rows = _load_jsonl(_ALERT_OUTCOMES)
    outcomes_by_doc: dict[str, str] = {}
    for o in outcome_rows:
        outcomes_by_doc[o.get("document_id", "")] = o.get("outcome", "")

    loop_rows = _load_jsonl(_TRADING_LOOP_AUDIT)
    status_counts: dict[str, int] = {}
    for r in loop_rows:
        s = r.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    from app.execution.signal_execution_status import build_signal_execution_status

    signal_execution = build_signal_execution_status(
        bridge_log_path=_BRIDGE_PENDING_ORDERS,
        paper_audit_log_path=_PAPER_EXECUTION_AUDIT,
        entry_watcher_log_path=_ENTRY_WATCHER_AUDIT,
    )

    fwd = report.get("forward_simulation", {})

    audit_v1_disqualified = False
    audit_provenance: dict[str, Any] | None = None
    if _AUDIT_V1_DISQUALIFIED_FLAG.exists():
        audit_v1_disqualified = True
        try:
            audit_provenance = json.loads(_AUDIT_V1_DISQUALIFIED_FLAG.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("audit_v1_disqualified_flag_unreadable: %s", exc)
            audit_provenance = {"error": "flag_unreadable"}

    reentry = _reentry_status()
    source_reliability = _load_source_reliability_summary()
    # P2-DECISION (truth-layer): ``metric_contract`` is intentionally kept as
    # API-side metadata only. The UI renders truth via the parallel fields
    # (``paper_evidence``, ``reentry``, regime ``is_read_only``,
    # ``source_reliability.quality_status``) — the contract must NOT contradict
    # them (guarded by test_api_dashboard::*contract*). Option for P2: either
    # promote the contract to the single UI source of truth OR remove it. It is
    # deliberately NOT expanded into a second, divergent truth layer here.
    metric_contract = {
        "paper_fills_with_pnl": _metric_contract(
            value=positions_closed + positions_partial_closed,
            unit="count",
            semantic_type="paper_closed_trade_activity",
            scope="cutoff_since" if audit_provenance else "lifetime",
            since=(
                str(audit_provenance.get("cut_off_ts_utc"))
                if isinstance(audit_provenance, dict) and audit_provenance.get("cut_off_ts_utc")
                else None
            ),
            until=generated_at,
            generated_at=generated_at,
            source_artifact=_PAPER_EXECUTION_AUDIT,
            sample_size=positions_closed + positions_partial_closed,
            quality_status="historical_only",
            warning=(
                "Historical/cutoff paper close count; not a rolling-24h execution-success metric."
            ),
            explanation=(
                "This proves historical paper activity only. Current execution state comes from "
                "/dashboard/api/priority-gate."
            ),
        ),
        "paper_fills_recent_24h": _metric_contract(
            value=len(recent_fills),
            unit="count",
            semantic_type="paper_fill_activity",
            scope="rolling_24h",
            window_hours=rolling_window_hours,
            since=rolling_start.isoformat(),
            until=now_utc.isoformat(),
            generated_at=generated_at,
            source_artifact=_PAPER_EXECUTION_AUDIT,
            sample_size=len(recent_fills),
            quality_status="warning" if len(recent_fills) == 0 else "ok",
            warning=(
                "No paper fills were observed in the rolling 24h window."
                if len(recent_fills) == 0
                else None
            ),
        ),
        "priority_tier_lift_pct": _metric_contract(
            value=quality.get("priority_tier_lift_pct"),
            unit="percentage_points",
            semantic_type="priority_quality_lift",
            scope="cutoff_since",
            generated_at=generated_at,
            source_artifact=_ALERT_AUDIT,
            sample_size=(
                int(quality.get("priority_tier_high_conviction_resolved") or 0)
                + int(quality.get("priority_tier_standard_resolved") or 0)
            ),
            confidence_interval=None,
            is_decision_relevant=True,
            quality_status=(
                "critical"
                if isinstance(quality.get("priority_tier_lift_pct"), (int, float))
                and float(quality.get("priority_tier_lift_pct")) < 0
                else "warning"
            ),
            warning=(
                "High-priority is not outperforming standard priority; do not present it as a "
                "validated quality label."
            ),
            explanation="P10 hit-rate minus P7-P9 hit-rate.",
        ),
        "market_regime": _metric_contract(
            value="read_only",
            unit="state",
            semantic_type="market_context",
            scope="snapshot",
            generated_at=generated_at,
            source_artifact=Path("artifacts/regime_state"),
            is_decision_relevant=False,
            is_read_only=True,
            quality_status="read_only",
            warning="Market regime is diagnostic only and does not gate trades or risk yet.",
        ),
        "source_reliability": _metric_contract(
            value=source_reliability.get("trusted_count", 0),
            unit="trusted_sources",
            semantic_type="source_quality_health",
            scope="rolling_90d",
            generated_at=generated_at,
            source_artifact=_SOURCE_RELIABILITY_REPORT,
            sample_size=source_reliability.get("source_count", 0),
            is_decision_relevant=True,
            quality_status=str(source_reliability.get("quality_status", "unverified")),
            warning=source_reliability.get("health_warning"),
            explanation="Wilson-based source tiers consumed by eligibility priority modifiers.",
        ),
    }

    # Truth-Layer v2 wiring (Issue #170 Part A): serve the canonical scalar
    # dashboard metrics through the formal MetricRegistry — ONE calculation
    # source. The registry is built from the SAME values the contract above
    # computed (no second, divergent path). Unsourced risk scalars serve
    # ``degraded`` (value withheld), never a fabricated number. The frontend is
    # expected to render ``metric_registry`` verbatim; ``frontend_calculation_allowed``
    # is False on every definition.
    _priority_lift = quality.get("priority_tier_lift_pct")
    registry_values: dict[str, float | None] = {
        "paper_fills_with_pnl": float(positions_closed + positions_partial_closed),
        "paper_fills_recent_24h": float(len(recent_fills)),
        "priority_tier_lift_pct": (
            float(_priority_lift) if isinstance(_priority_lift, (int, float)) else None
        ),
        "source_reliability_trusted_count": float(source_reliability.get("trusted_count", 0) or 0),
    }
    _registry = build_dashboard_metric_registry(registry_values)
    _now_ms = int(now_utc.timestamp() * 1000)
    metric_registry = {
        mid: _registry.serve(mid, now_ms=_now_ms, timestamp_utc=generated_at).model_dump()
        for mid in _registry.metric_ids()
    }
    # Inline reconciliation: the contract's numeric values must equal the SSOT
    # within declared tolerance. Drift → warning (logged), never a hard fail.
    _reconcile_snapshot = {
        mid: v for mid, v in registry_values.items() if isinstance(v, (int, float))
    }
    metric_registry_reconciliation = [
        r.model_dump()
        for r in reconcile_dashboard_snapshot(_registry, _reconcile_snapshot, now_ms=_now_ms)
    ]
    for _r in metric_registry_reconciliation:
        if not _r["within_tolerance"]:
            logger.warning(
                "dashboard_metric_registry_drift: %s ssot=%s external=%s reason=%s",
                _r["metric_id"],
                _r["ssot_value"],
                _r["external_value"],
                _r["reason"],
            )

    # Sprint S6 (#157 scope gap): entry-mode badge + canary attribution.
    # ``runtime`` mirrors the LIVE entry policy (D-233 SSOT — same resolver the
    # bridge/loop/feeder enforce, so the badge can never drift from runtime
    # truth). ``shadow_attribution`` separates REAL generator candidates from
    # canary/loop probes in the last 24h so a healthy-looking shadow stream
    # can never silently be 100% canary again (the 2026-06-03 incident class).
    runtime_block = _entry_runtime_block()
    shadow_attribution = _shadow_attribution_24h()

    quality_payload: dict[str, Any] = {
        # v2: the metric_registry block is now the single source of truth for
        # the scalar metrics; metric_contract stays for its richer per-metric
        # provenance/quality metadata and is reconciled against the registry.
        "dashboard_truth_contract_version": 2,
        "metric_contract": metric_contract,
        "metric_registry": metric_registry,
        "metric_registry_reconciliation": metric_registry_reconciliation,
        "runtime": runtime_block,
        "shadow_attribution": shadow_attribution,
        "reentry": reentry,
        "precision_pct": quality.get("resolved_precision_pct"),
        "false_positive_pct": quality.get("resolved_false_positive_rate_pct"),
        "resolved_count": hit_rate.get("resolved_directional_documents", 0),
        "directional_count": hit_rate.get("directional_alert_documents", 0),
        "hits": hit_rate.get("alert_hits", 0),
        "misses": hit_rate.get("alert_misses", 0),
        "active_precision_pct": quality.get("active_precision_pct"),
        "active_resolved_count": hit_rate.get("active_resolved_directional_documents", 0),
        "active_hits": hit_rate.get("active_alert_hits", 0),
        "active_misses": hit_rate.get("active_alert_misses", 0),
        "legacy_resolved_count": hit_rate.get("legacy_resolved_documents", 0),
        "legacy_unknown_cutoff": hit_rate.get("legacy_unknown_cutoff"),
        # priority_corr ist als deprecated markiert (D-149) — Pearson auf
        # P7-P10-Band misst nichts Sinnvolles. Bleibt fuer Backwards-Compat
        # exposed, das Dashboard nutzt jetzt priority_tier_lift_pct.
        "priority_corr": quality.get("priority_hit_correlation"),
        "priority_tier_lift_pct": quality.get("priority_tier_lift_pct"),
        "priority_tier_high_conviction_threshold": quality.get(
            "priority_tier_high_conviction_threshold"
        ),
        "priority_tier_high_conviction_resolved": quality.get(
            "priority_tier_high_conviction_resolved"
        ),
        "priority_tier_high_conviction_hit_rate_pct": quality.get(
            "priority_tier_high_conviction_hit_rate_pct"
        ),
        "priority_tier_high_conviction_ci_low_pct": quality.get(
            "priority_tier_high_conviction_ci_low_pct"
        ),
        "priority_tier_high_conviction_ci_high_pct": quality.get(
            "priority_tier_high_conviction_ci_high_pct"
        ),
        "priority_tier_standard_resolved": quality.get("priority_tier_standard_resolved"),
        "priority_tier_standard_hit_rate_pct": quality.get("priority_tier_standard_hit_rate_pct"),
        "priority_tier_standard_ci_low_pct": quality.get("priority_tier_standard_ci_low_pct"),
        "priority_tier_standard_ci_high_pct": quality.get("priority_tier_standard_ci_high_pct"),
        "forward_precision_pct": fwd.get("precision_pct"),
        "forward_resolved": fwd.get("resolved", 0),
        "forward_hits": fwd.get("hits", 0),
        "forward_miss": fwd.get("miss", 0),
        "paper_fills": len(fills),
        "paper_fills_with_pnl": positions_closed + positions_partial_closed,
        "paper_realized_pnl_usd": realized_pnl_usd,
        "paper_quarantined_pnl_usd": quarantined_pnl_usd,
        "paper_quarantined_closes": len(quarantined_closes_list),
        "paper_positions_closed": positions_closed,
        "paper_positions_partial_closed": positions_partial_closed,
        "paper_evidence": {
            "scope": "cutoff_since" if audit_provenance else "lifetime",
            "since": metric_contract["paper_fills_with_pnl"]["since"],
            "until": generated_at,
            "window_hours": rolling_window_hours,
            "fills_total": len(fills),
            "fills_recent_24h": len(recent_fills),
            "closed_total": positions_closed + positions_partial_closed,
            "closed_recent_24h": len(recent_closes),
            "realized_pnl_total_usd": realized_pnl_usd,
            "realized_pnl_recent_24h_usd": recent_pnl_usd,
            "expectancy_usd": expectancy,
            "win_rate_pct": win_rate,
            "avg_win_usd": avg_win,
            "avg_loss_usd": avg_loss,
            "fees_slippage_included": "partial",
            "source_artifact": str(_PAPER_EXECUTION_AUDIT),
            "source_artifact_updated_at": _artifact_updated_at(_PAPER_EXECUTION_AUDIT),
            "stale_status": _artifact_stale_status(_PAPER_EXECUTION_AUDIT),
            "quality_status": (
                "warning"
                if not pnl_values or expectancy is None or expectancy <= 0
                else "historical_only"
            ),
            "warning": (
                "Paper fill count is historical/cutoff evidence; rolling execution is reported "
                "separately by priority-gate."
            ),
        },
        "audit_v1_disqualified": audit_v1_disqualified,
        "audit_provenance": audit_provenance,
        "paper_cycles": paper.get("loop_metrics", {}).get("total_cycles", 0),
        "real_price_cycles": quality.get("paper_real_price_cycle_count", 0),
        "gate_status": gate.get("overall_status"),
        "blocking_reasons": gate.get("blocking_reasons", []),
        "actionable_rate_pct": quality.get("directional_actionable_rate_pct"),
        "high_priority_hit_rate_pct": quality.get("high_priority_hit_rate_pct"),
        "low_priority_hit_rate_pct": quality.get("low_priority_hit_rate_pct"),
        "loop_status_counts": status_counts,
        "signal_execution": signal_execution,
        # V-DB4a 2026-05-08: Per-source active precision fuer Quality-Tile.
        # Liefert n / hit-rate / Wilson-CI / passes_gate je Source.
        "per_source_active_precision": report.get("per_source_active_precision", {}),
        # V-DB4e 2026-05-08: Per-source rolling 30-day stability windows.
        "per_source_stability": report.get("per_source_stability", {}),
        "source_reliability": source_reliability,
        "recent_alerts": [
            {
                "doc_id": r.get("document_id", "")[:12],
                "sentiment": r.get("sentiment_label", ""),
                "priority": r.get("priority"),
                "assets": r.get("affected_assets", []),
                "dispatched_at": r.get("dispatched_at", "")[:16],
                "outcome": outcomes_by_doc.get(r.get("document_id", ""), ""),
            }
            for r in reversed(recent_alerts)
        ],
        "generated_at": generated_at,
    }
    _quality_cache["payload"] = quality_payload
    _quality_cache["at"] = cache_now
    return JSONResponse(
        content=quality_payload,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/dashboard/api/edge-window", tags=["dashboard"])
async def dashboard_edge_window_api(canonical: bool = True) -> JSONResponse:
    """Edge-Truth panel data — the cost-adjusted edge verdict + its honesty context.

    ``canonical=true`` (default) restricts the edge to the REAL generator's
    attributed sources (``CANONICAL_EDGE_SOURCES`` = autonomous_generator /
    real_analysis) — the contamination-proof answer (forensic 2026-06-23,
    memory kai_edge_epoch_contamination_20260623). ``canonical=false`` shows the
    FULL stream and is flagged ``contaminated=true`` so the UI can warn that the
    unattributed May-canary closes are mixed in. READ-ONLY, fail-closed.
    """
    mode = "canonical" if canonical else "full"
    cache_now = time.monotonic()
    cached = _edge_window_cache.get(mode)
    if cached is not None and (cache_now - cached["at"]) < _EDGE_WINDOW_CACHE_TTL_S:
        return JSONResponse(
            content=cached["payload"], headers={"Cache-Control": "no-store, max-age=0"}
        )

    try:
        from app.observability.evidence_window import (
            CANONICAL_EDGE_SOURCES,
            build_window_from_audit,
        )

        report = build_window_from_audit(
            loop_audit_path=_TRADING_LOOP_AUDIT,
            exec_audit_path=_PAPER_EXECUTION_AUDIT,
            source_allowlist=CANONICAL_EDGE_SOURCES if canonical else None,
        )
        w, e = report.window, report.edge
        payload: dict[str, Any] = {
            "available": True,
            "canonical": canonical,
            # The full stream is honestly flagged contaminated — its edge mixes the
            # unattributed May-canary epoch; canonical is the defensible answer.
            "contaminated": not canonical,
            "source_allowlist": list(w.source_allowlist) if w.source_allowlist else None,
            "closes_excluded_by_source": w.closes_excluded_by_source,
            "trade_count": e.trade_count,
            "p_mu_net_positive": e.p_mu_net_positive,
            "median_net_bps": round(e.median_net_bps, 1),
            "mean_net_bps": round(e.mean_net_bps, 1),
            "realized_pnl_usd_sum": round(e.realized_pnl_usd_sum, 2),
            "quarantine_excluded_count": e.quarantine_excluded.excluded_count,
            "live_orders_attempted": report.safety.live_orders_attempted,
            "window_started_at": w.started_at,
            "window_ended_at": w.ended_at,
            "error": None,
        }
        _edge_window_cache[mode] = {"at": cache_now, "payload": payload}
        return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})
    except Exception as exc:  # noqa: BLE001 — endpoint must never 500 the dashboard
        logger.warning("dashboard_edge_window_failed", exc_info=True)
        return JSONResponse(
            content={"available": False, "canonical": canonical, "error": str(exc)},
            headers={"Cache-Control": "no-store, max-age=0"},
        )


@router.get("/dashboard/api/n-overview", tags=["dashboard"])
async def dashboard_n_overview_api() -> JSONResponse:
    """Die fünf „n" an EINER Stelle (Dali 2026-06-13).

    Es gibt fünf verschiedene „resolved/n", die unterschiedliche Pipelines
    zählen und alle ähnlich heißen — genau die UX-Falle, über die der Edge-
    Re-Run gestolpert ist. Dieser Endpoint liest jede Quelle einzeln und reicht
    die Rohwerte an den reinen Assembler ``build_n_overview`` (SSOT für Labels/
    Zuordnung). Jede Quelle degradiert auf ``None`` statt eine Zahl zu erfinden.
    """
    from app.observability.generator_edge_collector import collect_edge_inputs_from_resolved
    from app.observability.n_overview import build_n_overview

    # 1) Gate-n (#167): resolved_real + Gesamt-Zeilen des resolved-Ledgers.
    resolved_real: int | None = None
    resolved_ledger_lines: int | None = None
    collected = None
    try:
        collected = collect_edge_inputs_from_resolved()
        resolved_real = collected.resolved_real
        resolved_ledger_lines = (
            collected.resolved_real
            + collected.skipped_non_real
            + collected.skipped_canary
            + collected.skipped_no_score
        )
    except Exception as exc:  # noqa: BLE001 — Panel degradiert, kein 500
        logger.warning("n_overview_gate_read_failed: %s", exc)

    # 2) total_resolved + EV-Gate — der VOLLE `trading generator-edge`-Report
    # (eine Berechnungsquelle, identisch zur CLI): liefert total_resolved (closed
    # trades nach Implausibilitäts-Filter |exit/entry-1|≤0.40) UND die Per-Cohort-
    # Profile. Daraus das autonomous_generator-Profil → AUSGEFÜHRTE Generator-
    # Trades + EV-Verdict (der bindende Engpass, nicht die all-sources-Zahl).
    total_resolved: int | None = None
    generator_executed: int | None = None
    generator_threshold: int | None = None
    generator_verdict: str | None = None
    generator_ev_bps: float | None = None
    try:
        from app.observability.edge_report import (
            load_audit_events,
            parse_closed_trades_with_exclusions,
        )
        from app.observability.generator_edge import (
            EdgeGateConfig,
            build_generator_edge_report,
        )

        events = load_audit_events(str(_PAPER_EXECUTION_AUDIT))
        trades, _exclusions = parse_closed_trades_with_exclusions(
            events, implausible_move_threshold=0.40
        )
        edge_report = build_generator_edge_report(
            trades,
            cohort_type="generator",
            venue="paper",
            ic_aligned_by_cohort=(collected.ic_aligned_by_cohort or None) if collected else None,
            outcome_pairs_by_cohort=(collected.outcome_pairs_by_cohort or None)
            if collected
            else None,
            config=EdgeGateConfig(),
        ).to_dict()
        total_resolved = edge_report.get("total_resolved")
        generator_threshold = edge_report.get("gate_config", {}).get("min_resolved")
        gen_profile = next(
            (
                p
                for p in edge_report.get("profiles", [])
                if p.get("cohort_key") == "autonomous_generator"
            ),
            None,
        )
        if gen_profile is not None:
            generator_executed = gen_profile.get("resolved_count")
            generator_verdict = gen_profile.get("verdict")
            generator_ev_bps = gen_profile.get("expected_value_after_costs_bps")
    except Exception as exc:  # noqa: BLE001
        logger.warning("n_overview_edge_report_read_failed: %s", exc)

    # 5) paper_trades_all_time — EXAKT der Daily-Digest-Zähler `_paper_fills_count`
    # (position_closed + position_partial_closed, ungefiltert; ein Event pro Trade).
    paper_trades_all_time: int | None = None
    try:
        from app.cli.commands.daily_strategy import _paper_fills_count

        paper_trades_all_time = _paper_fills_count()
    except Exception as exc:  # noqa: BLE001
        logger.warning("n_overview_paper_fills_read_failed: %s", exc)

    # 4) resolved directional alerts (D-227) — EXAKT der Daily-Digest-Zähler
    # `_resolved_directional_count` über die volle alert_outcomes.jsonl:
    # resolved == entschieden == hit + miss (ohne Legacy-Cutoff des Hold-Reports).
    resolved_directional_alerts: int | None = None
    try:
        from app.cli.commands.daily_strategy import _resolved_directional_count

        d227 = _resolved_directional_count()
        resolved_directional_alerts = int(d227.get("hit", 0)) + int(d227.get("miss", 0))
    except Exception as exc:  # noqa: BLE001
        logger.warning("n_overview_d227_read_failed: %s", exc)

    payload = build_n_overview(
        resolved_real=resolved_real,
        resolved_ledger_lines=resolved_ledger_lines,
        total_resolved=total_resolved,
        paper_trades_all_time=paper_trades_all_time,
        resolved_directional_alerts=resolved_directional_alerts,
        generator_executed=generator_executed,
        generator_threshold=generator_threshold,
        generator_verdict=generator_verdict,
        generator_ev_bps=generator_ev_bps,
    )
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/priority-gate", tags=["dashboard"])
async def dashboard_priority_gate_api() -> JSONResponse:
    """D-184: operator-visibility for the D-182 priority-tier paper-fill gate.

    Returns the active threshold + rolling 24h bucket counts so the dashboard
    can distinguish "quiet because gate is raised" from "quiet because no
    signals". Fails soft to zeros when the audit file is absent.
    """
    from app.orchestrator.trading_loop import build_priority_gate_summary

    try:
        summary = build_priority_gate_summary(audit_path=_TRADING_LOOP_AUDIT, window_hours=24)
    except Exception as exc:  # noqa: BLE001
        logger.warning("priority_gate_summary_failed: %s", exc)
        return JSONResponse(
            content={"error": "priority_gate_unavailable", "detail": str(exc)},
            status_code=503,
        )
    payload = summary.to_json_dict()
    rejected_total = summary.priority_rejected + summary.other_rejected
    payload["rejected_total"] = rejected_total
    payload["rejected_pct"] = (
        round(rejected_total / summary.total_cycles * 100.0, 2) if summary.total_cycles else 0.0
    )
    payload["filled_total"] = summary.completed
    payload["top_reject_reason"] = (
        "below_priority_threshold"
        if summary.priority_rejected >= summary.other_rejected and summary.priority_rejected > 0
        else ("other_rejected" if summary.other_rejected > 0 else None)
    )
    payload["threshold_effect"] = (
        "active_blocking"
        if summary.gate_active and summary.priority_rejected > 0
        else "no_recent_fill"
    )
    # Loop heartbeat: distinguish "gate blocking actively" / "loop ran but
    # rejected" from "no cycles at all" and "worker possibly down". 0 filled
    # must NEVER read as healthy when we cannot prove the loop is alive.
    audit_present = _TRADING_LOOP_AUDIT.exists()
    audit_freshness = _artifact_stale_status(_TRADING_LOOP_AUDIT) if audit_present else "unverified"
    if not audit_present or summary.total_cycles == 0:
        heartbeat_status = "unknown"
        heartbeat_warning = (
            "Trading-loop activity is not verified (no cycles / no audit) — "
            "0 fills is NOT a health signal."
        )
    elif audit_freshness in {"stale", "critical"}:
        heartbeat_status = "stale"
        heartbeat_warning = "Trading-loop audit is stale; recent activity is not verified."
    elif summary.gate_active and summary.priority_rejected > 0:
        heartbeat_status = "active_blocking"
        heartbeat_warning = None
    else:
        heartbeat_status = "active"
        heartbeat_warning = None
    payload["heartbeat_status"] = heartbeat_status
    payload["heartbeat_warning"] = heartbeat_warning
    payload["loop_audit_present"] = audit_present
    payload["loop_audit_freshness"] = audit_freshness
    try:
        report = await _live_hold_report()
        quality = report.get("signal_quality_validation", {})
        lift = quality.get("priority_tier_lift_pct")
        high_n = quality.get("priority_tier_high_conviction_resolved")
        standard_n = quality.get("priority_tier_standard_resolved")
        if not isinstance(lift, (int, float)):
            verdict = "insufficient_data"
        elif float(lift) < 0:
            verdict = "priority_underperforming"
        elif high_n and standard_n:
            verdict = "priority_validated"
        else:
            verdict = "priority_unproven"
        payload["priority_quality"] = {
            "high_priority_lift_pct": lift,
            "high_priority_resolved": high_n,
            "standard_resolved": standard_n,
            "current_quality_verdict": verdict,
            "warning": (
                "Priority gate is blocking conservatively, but High-P is not a validated "
                "quality label in the current evidence window."
                if verdict in {"priority_underperforming", "priority_unproven", "insufficient_data"}
                else None
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("priority_quality_load_failed: %s", exc)
        payload["priority_quality"] = {
            "high_priority_lift_pct": None,
            "current_quality_verdict": "unverified",
            "warning": "Priority quality evidence could not be loaded.",
        }
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/dashboard/api/provenance", tags=["dashboard"])
async def dashboard_provenance_api() -> JSONResponse:
    """Per-source active-precision split (Wilson 95% CI) for the dashboard SPA.

    Joins alert_audit × alert_outcomes × CanonicalDocument.source_name and
    returns hit-rate + CI per source. Drives the re-entry verdict at the
    2026-05-16 gate: only sources with sample_sufficient (≥30 resolved) are
    judgment-ready.
    """
    from dataclasses import asdict

    from app.alerts.provenance_metrics import build_provenance_split_report

    now = time.monotonic()
    cached = _provenance_cache.get("payload")
    if cached is not None and (now - _provenance_cache["at"]) < _PROVENANCE_CACHE_TTL_S:
        return JSONResponse(
            content=cached,
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    source_map = await _load_source_by_doc()
    try:
        report = build_provenance_split_report(
            alert_audit_path=_ALERT_AUDIT,
            alert_outcomes_path=_ALERT_OUTCOMES,
            tradingview_pending_signals_path=_TV_PENDING,
            source_by_doc=source_map or None,
        )
    except Exception as exc:
        logger.warning("provenance_report_failed: %s", exc)
        return JSONResponse(
            content={"error": "provenance_unavailable", "detail": str(exc)},
            status_code=503,
        )

    payload = {
        "generated_at": report.generated_at,
        "overall": asdict(report.overall),
        "overall_active": asdict(report.overall_active),
        "by_source": [asdict(m) for m in report.by_source],
        "tradingview_pipeline": asdict(report.tradingview_pipeline),
        "verdict": report.verdict,
        "notes": list(report.notes),
        "min_sample_for_judgment": 30,
    }
    _provenance_cache["payload"] = payload
    _provenance_cache["at"] = now
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/dashboard/api/integrations", tags=["dashboard"])
async def dashboard_integrations_api() -> JSONResponse:
    """Echter Konfigurations-/Aktiv-Zustand der externen Integrationen.

    Ersetzt die früher hartkodierten Status-Badges im Settings-Tab
    „Integrationen". Jeder Status wird ausschließlich aus den (fail-closed)
    Settings-Flags abgeleitet — keine erfundenen „aktiv"/„vorbereitet"-Labels
    mehr (No-Fake-Doktrin). Für TradingView werden die Live-Pipeline-Zähler
    aus dem bereits gecachten Provenance-Report angereichert, sofern frisch;
    dieser Endpoint baut den schweren Report bewusst NICHT selbst und ist damit
    von dessen Fehlerpfaden entkoppelt.

    status ∈ {"active", "disabled"}.
    """
    from app.core.settings import get_settings

    settings = get_settings()

    # Telegram: aktiv, sobald irgendein Bot-Token konfiguriert ist (Alert- oder
    # Operator-Kanal).
    telegram_configured = bool(
        settings.alerts.telegram_token or settings.operator.telegram_bot_token
    )

    # LLM-Consensus-Gate: aktiv, sobald mindestens ein Provider-Key gesetzt ist.
    llm_providers = [
        name
        for name, key in (
            ("openai", settings.providers.openai_api_key),
            ("anthropic", settings.providers.anthropic_api_key),
            ("gemini", settings.providers.gemini_api_key),
        )
        if key
    ]

    # TradingView-Webhook: „live/aktiv" == der POST-Endpoint ist erreichbar.
    # Das ist EXAKT die Mount-Bedingung aus app/api/routers/tradingview.py
    # (_settings_gate): webhook_enabled UND — je nach auth_mode — das passende
    # Credential (webhook_secret für hmac, webhook_shared_token für die
    # token-basierten Modi inkl. hmac_strict_event_id/hmac_or_token). Wir rufen
    # den Gate direkt auf, statt die Bedingung hier zu duplizieren — sonst driftet
    # der Status vom echten 404-/202-Verhalten ab (auf der Pi läuft
    # hmac_strict_event_id ohne webhook_secret; die alte `enabled AND secret`-
    # Heuristik meldete fälschlich „disabled").
    from fastapi import HTTPException

    from app.api.routers.tradingview import _settings_gate as _tv_settings_gate

    tv = settings.tradingview
    try:
        _tv_settings_gate(settings)
        tv_mounted = True
    except HTTPException:
        tv_mounted = False
    tv_pipeline: dict[str, Any] | None = None
    cached = _provenance_cache.get("payload")
    if (
        tv_mounted
        and cached is not None
        and (time.monotonic() - _provenance_cache["at"]) < _PROVENANCE_CACHE_TTL_S
    ):
        # Read-only Anreicherung aus dem bereits gebauten Provenance-Payload —
        # kein zusätzlicher Report-Build.
        tv_pipeline = cached.get("tradingview_pipeline")

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "integrations": {
            "telegram": {
                "status": "active" if telegram_configured else "disabled",
                "configured": telegram_configured,
            },
            "llm": {
                "status": "active" if llm_providers else "disabled",
                "providers": llm_providers,
            },
            "tradingview": {
                "status": "active" if tv_mounted else "disabled",
                "webhook_enabled": bool(tv.webhook_enabled),
                "secret_configured": bool(tv.webhook_secret),
                "shared_token_configured": bool(tv.webhook_shared_token),
                "mounted": tv_mounted,
                "auth_mode": tv.webhook_auth_mode,
                "signal_routing_enabled": bool(tv.webhook_signal_routing_enabled),
                "auto_promote_enabled": bool(tv.webhook_auto_promote_enabled),
                "pipeline": tv_pipeline,
            },
            "email": {
                # Kein SMTP-Setting im Backend → ehrlich „nicht konfiguriert".
                "status": "disabled",
                "configured": False,
            },
        },
    }
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


# ─── Bayesian Confidence Audit (Schatten-Vergleich-Spalte) ────────────────────

_BAYES_AUDIT = _ARTIFACTS / "bayes_confidence_audit.jsonl"
_BAYES_LIMIT_DEFAULT = 50
_BAYES_LIMIT_MAX = 500
# Outcome-Map für Calibration: pluggable, damit der Endpoint funktioniert,
# *bevor* das Decision_id→Outcome-Linking fertig verdrahtet ist. Default leer.
_BAYES_OUTCOME_MAP: dict[str, int] = {}


@router.get("/dashboard/api/bayes-audit", tags=["dashboard"])
async def dashboard_bayes_audit_api(limit: int = _BAYES_LIMIT_DEFAULT) -> JSONResponse:
    """Letzte N Bayes-Confidence-Reports aus dem Audit-Sidecar."""
    capped = max(1, min(int(limit), _BAYES_LIMIT_MAX))
    try:
        validation = _validate_dashboard_stream(_BAYES_AUDIT, "bayes_confidence_audit")
    except Exception as exc:  # noqa: BLE001
        logger.warning("bayes_audit_load_failed: %s", exc)
        return JSONResponse(
            content={"error": "bayes_audit_unavailable", "detail": str(exc)},
            status_code=503,
        )

    tail = validation.rows[-capped:]
    entries: list[dict[str, Any]] = []
    for e in reversed(tail):
        raw_report = e.get("report")
        report = raw_report if isinstance(raw_report, dict) else {}
        increased = report.get("increased")
        decreased = report.get("decreased")
        neutral = report.get("neutral")
        discarded = report.get("discarded")
        residual = report.get("residual_uncertainty_drivers")
        increased_items = increased if isinstance(increased, list) else []
        decreased_items = decreased if isinstance(decreased, list) else []
        neutral_items = neutral if isinstance(neutral, list) else []
        discarded_items = discarded if isinstance(discarded, list) else []
        residual_items = residual if isinstance(residual, list) else []
        entries.append(
            {
                "decision_id": e.get("decision_id"),
                "timestamp_utc": e.get("timestamp_utc"),
                "symbol": e.get("symbol"),
                "direction": e.get("direction"),
                "prior_probability": report.get("prior_probability"),
                "posterior_probability": report.get("posterior_probability"),
                "confidence_score": report.get("confidence_score"),
                "uncertainty_score": report.get("uncertainty_score"),
                "evidence_weight": report.get("evidence_weight"),
                "agreement": report.get("agreement"),
                "increased_count": len(increased_items),
                "decreased_count": len(decreased_items),
                "neutral_count": len(neutral_items),
                "discarded_count": len(discarded_items),
                "residual_uncertainty_drivers": list(residual_items),
            }
        )

    return JSONResponse(
        content={
            "generated_at": datetime.now(UTC).isoformat(),
            "total_count": validation.valid_count,
            "returned_count": len(entries),
            "limit": capped,
            "audit_stream_validation": summarize_audit_stream_result(validation),
            "entries": entries,
        },
        headers={"Cache-Control": "no-store, max-age=0"},
    )


# ─── Calibration-Report (Quality-Bar-Erweiterung) ─────────────────────────────


@router.get("/dashboard/api/calibration", tags=["dashboard"])
async def dashboard_calibration_api(n_bins: int = 10) -> JSONResponse:
    """Brier / Log-Loss / ECE / Reliability-Diagramm aus Bayes-Audit + Outcomes."""
    from app.learning.calibration import compute_calibration
    from app.learning.calibration_loader import pairs_from_bayes_audit

    capped_bins = max(2, min(int(n_bins), 50))
    try:
        pairs = pairs_from_bayes_audit(
            bayes_audit_path=_BAYES_AUDIT,
            outcomes=_BAYES_OUTCOME_MAP,
        )
        report = compute_calibration(pairs, n_bins=capped_bins)
    except Exception as exc:  # noqa: BLE001
        logger.warning("calibration_report_failed: %s", exc)
        return JSONResponse(
            content={"error": "calibration_unavailable", "detail": str(exc)},
            status_code=503,
        )

    # Honesty: the outcome-map that joins bayes predictions to realised outcomes
    # is not wired in production yet (_BAYES_OUTCOME_MAP is empty), so n_pairs is
    # always 0 live. Mark that explicitly as a wiring gap (state "disabled") so a
    # null/empty report is not misread as "perfectly calibrated on 0 samples".
    wired = bool(_BAYES_OUTCOME_MAP)
    notes = list(report.notes)
    if not wired:
        notes.insert(
            0,
            "Outcome-Map nicht verdrahtet (wiring_status=not_connected): n_pairs "
            "bleibt 0, bis der Bayes→Outcome-Join existiert — das ist KEIN "
            "'perfekt kalibriert', sondern ein noch nicht angeschlossener Pfad.",
        )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "state": "ok" if wired else "disabled",
        "wiring_status": "connected" if wired else "not_connected",
        "n_pairs": report.n_pairs,
        "total_weight": report.total_weight,
        "brier_score": report.brier_score,
        "log_loss": report.log_loss,
        "expected_calibration_error": report.expected_calibration_error,
        "mean_predicted": report.mean_predicted,
        "mean_observed": report.mean_observed,
        "sample_sufficient": report.sample_sufficient,
        "bins": [b.model_dump() for b in report.bins],
        "notes": notes,
        "outcome_map_size": len(_BAYES_OUTCOME_MAP),
    }
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


# Default assets exposed by the regime endpoint. R1 covers BTC + ETH; later
# sprints extend the list as the classifier handles more universes.
_REGIME_DASHBOARD_ASSETS: tuple[str, ...] = ("BTC", "ETH")


@router.get("/dashboard/api/regime", tags=["dashboard"])
async def dashboard_regime_api() -> JSONResponse:
    """Latest regime snapshot per asset for the dashboard tile.

    R1 (2026-05-09): hourly classification persisted to
    ``artifacts/regime_state/<asset>_regime.jsonl`` by
    ``kai-regime-classify.timer``. This endpoint is read-only — it loads
    the latest committed snapshot per asset and returns it as-is. Missing
    JSONL → asset is omitted from ``by_asset`` (the tile shows a "noch keine
    Klassifikation"-empty-state).
    """
    from app.regime.storage import latest_regime_snapshot, resolve_regime_path

    by_asset: dict[str, dict[str, Any]] = {}
    by_asset_meta: dict[str, dict[str, Any]] = {}
    now = datetime.now(UTC)
    for asset in _REGIME_DASHBOARD_ASSETS:
        artifact_path = resolve_regime_path(asset)
        try:
            snap = latest_regime_snapshot(asset)
        except Exception as exc:  # noqa: BLE001 — never break the dashboard
            logger.warning("regime_snapshot_load_failed: asset=%s err=%s", asset, exc)
            continue
        if snap is not None:
            by_asset[asset] = snap.to_json_dict()
            snap_dt = _parse_iso_utc(snap.timestamp)
            age_hours = (
                round((now - snap_dt).total_seconds() / 3600.0, 2) if snap_dt is not None else None
            )
            by_asset_meta[asset] = {
                "source_artifact": str(artifact_path),
                "source_artifact_updated_at": _artifact_updated_at(artifact_path),
                "snapshot_timestamp": snap.timestamp,
                "snapshot_age_hours": age_hours,
                "stale_status": (
                    "unverified"
                    if age_hours is None
                    else (
                        "stale"
                        if age_hours >= _ARTIFACT_STALE_CRITICAL_HOURS
                        else ("warning" if age_hours >= _ARTIFACT_STALE_WARNING_HOURS else "ok")
                    )
                ),
                "is_read_only": True,
                "is_decision_relevant": False,
                "quality_status": "read_only",
                "warning": "Read-only diagnosis; does not influence trades, gates, or risk.",
            }

    # Separate RESPONSE freshness (this endpoint just answered) from DATA
    # freshness (how old the underlying snapshots are). A fresh response must
    # never imply fresh regime data. data_freshness_status = worst per-asset.
    asset_states = [meta.get("stale_status") for meta in by_asset_meta.values()]
    if not asset_states:
        data_freshness_status = "no_data"
    elif "stale" in asset_states:
        data_freshness_status = "stale"
    elif "warning" in asset_states:
        data_freshness_status = "warning"
    elif "unverified" in asset_states:
        data_freshness_status = "unverified"
    else:
        data_freshness_status = "ok"
    response_generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return JSONResponse(
        content={
            # ``generated_at`` kept for backwards-compat == response time.
            "generated_at": response_generated_at,
            "response_generated_at": response_generated_at,
            "data_freshness_status": data_freshness_status,
            "semantic_status": "read_only",
            "is_read_only": True,
            "is_decision_relevant": False,
            "warning": (
                "Market regime is diagnostic only; no gate/risk integration is active. "
                "Response time is not data freshness — see per-asset snapshot_age_hours."
            ),
            "by_asset": by_asset,
            "by_asset_metadata": by_asset_meta,
        },
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/dashboard/api/lightning", tags=["dashboard"])
async def dashboard_lightning_api() -> JSONResponse:
    """Read-only Lightning-Node-Status + souveräne Chain-Wahrheit (L1, default-off).

    Liest den lnd-Node-Status über den Hintergrund-Cache
    (``app.lightning.cache.get_cached_node_status()``) und reichert Block-Höhe/
    Sync aus KAIs EIGENER bitcoind (``app.chain.cache``, L1) an — so bleiben
    Höhe/Sync truthful, selbst wenn lnds ``getinfo`` (Tor) hängt, und das
    Node-Panel flackert nicht (Anti-Flacker-Merge hält den letzten vollen
    ``getinfo``-Snapshot). Der Request blockiert NIE auf lnd/bitcoind;
    ``node_age_seconds``/``chain_age_seconds`` zeigen das Snapshot-Alter. ``state``
    ist ``disabled`` / ``pending`` (Cache kalt) / ``unavailable`` / ``ok``
    (``info_available`` zeigt, ob die ``getinfo``-Detailfelder Peers/Channels/
    Alias gefüllt sind). Beide Quellen fail-closed/default-off; kein
    schreibender/kapitalrelevanter Pfad.
    """
    from dataclasses import asdict

    from app.chain.cache import get_cached_chain_status
    from app.lightning.cache import get_cached_node_status

    # lnd-Node-Status über den Hintergrund-Cache — lnds getinfo (Tor) ist langsam/
    # intermittierend; der Request darf dafür NIE blockieren und das Panel soll
    # nicht zwischen gefüllt/leer flackern. ``node_age_seconds`` zeigt das Alter
    # des Snapshots (None solange Cache kalt / ``pending``).
    status, node_age = await get_cached_node_status()
    payload = asdict(status)
    payload["node_age_seconds"] = node_age

    # Souveräne Chain-Wahrheit: Höhe/Sync bevorzugt aus der eigenen bitcoind.
    # Über den Hintergrund-Cache gelesen — bitcoind-RPC kann auf dem Pi minutenlang
    # cs_main halten; der Request darf dafür NIE blockieren. ``chain_age_seconds``
    # zeigt das Alter des Snapshots (None solange Cache kalt / ``pending``).
    chain, chain_age = await get_cached_chain_status()
    payload["chain"] = asdict(chain)
    payload["chain_age_seconds"] = chain_age
    if chain.state == "ok":
        payload["block_height"] = chain.blocks
        payload["synced_to_chain"] = chain.synced

    payload["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/ln/channels", tags=["dashboard"])
async def dashboard_ln_channels_api() -> JSONResponse:
    """Read-only Per-Channel-Aufschlüsselung (lnd ``listchannels``, default-off).

    Spiegelt ``app.lightning.adapter.get_channels()`` — pro Channel Kapazität,
    Outbound (``local_sat``) / Inbound (``remote_sat``) und Aktiv-Status, plus
    aggregierte Summen. Liefert ``disabled`` (Feature aus, kein Netzcall),
    ``unavailable`` (an, aber Node nicht erreichbar) oder ``ok``. Fail-closed,
    KEIN schreibender/kapitalrelevanter Pfad (read-only ``listchannels``).
    """
    from dataclasses import asdict

    from app.lightning.adapter import get_channels

    status = await get_channels()
    payload = asdict(status)
    payload["num_channels"] = len(status.channels)
    payload["total_local_sat"] = sum(c.local_sat for c in status.channels)
    payload["total_remote_sat"] = sum(c.remote_sat for c in status.channels)
    payload["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/chain", tags=["dashboard"])
async def dashboard_chain_api() -> JSONResponse:
    """Read-only Chain-Status aus KAIs EIGENER bitcoind (L1, default-off).

    Spiegelt ``app.chain.adapter.get_chain_status()`` über den Hintergrund-Cache
    (``app.chain.cache``) — souveräne On-Chain-Wahrheit (Tip-Höhe, Sync,
    Fee-Estimate, Mempool) aus der eigenen Node statt aus einer Dritt-API. Der
    Request blockiert NIE auf bitcoind; ``age_seconds`` zeigt das Snapshot-Alter.
    ``state`` ist ``disabled`` (Feature aus, kein Netzcall), ``pending`` (an, aber
    noch kein erfolgreicher Fetch — Cache kalt), ``unavailable`` (an, aber Node
    nicht erreichbar) oder ``ok``. Fail-closed, kein schreibender/
    kapitalrelevanter Pfad.
    """
    from dataclasses import asdict

    from app.chain.cache import get_cached_chain_status

    status, age = await get_cached_chain_status()
    payload = asdict(status)
    payload["age_seconds"] = age
    payload["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/markets/derivatives", tags=["dashboard"])
async def dashboard_markets_derivatives_api() -> JSONResponse:
    """Read-only Perp-Derivate-Snapshot (Funding + Open Interest) aus KAIs EIGENER Ingestion.

    Liest die entkoppelt aktualisierten Snapshot-Caches (``funding_cache.json`` /
    ``oi_cache.json``, geschrieben vom Funding/OI-Refresh-Service; Quelle z. B.
    bybit) — KEINE Live-Dritt-API im Request-Pfad und KEINE erfundenen Werte.
    Fehlt ein Cache, bleibt die Liste leer (fail-closed/ehrlich). ``funding_rate``
    ist der 8h-Satz als Anteil (0.0001 = 1bp). Read-only, kein schreibender/
    kapitalrelevanter Pfad.
    """
    from dataclasses import asdict

    from app.core.evidence_settings import (
        FundingEvidenceSettings,
        OpenInterestEvidenceSettings,
    )
    from app.signals.funding_snapshot_store import FundingSnapshotStore
    from app.signals.oi_snapshot_store import OpenInterestSnapshotStore

    funding_map: dict[str, Any] = {}
    oi_map: dict[str, Any] = {}
    try:
        funding_map = {
            sym: asdict(snap)
            for sym, snap in FundingSnapshotStore(FundingEvidenceSettings().snapshot_path)
            .read_all()
            .items()
        }
    except Exception:  # noqa: BLE001 — read-only surface must never raise into the request
        funding_map = {}
    try:
        oi_map = {
            sym: asdict(snap)
            for sym, snap in OpenInterestSnapshotStore(OpenInterestEvidenceSettings().snapshot_path)
            .read_all()
            .items()
        }
    except Exception:  # noqa: BLE001
        oi_map = {}

    rows: list[dict[str, Any]] = []
    for sym in sorted(set(funding_map) | set(oi_map)):
        f = funding_map.get(sym) or {}
        o = oi_map.get(sym) or {}
        rows.append(
            {
                "symbol": sym,
                "funding_rate": f.get("rate"),
                "mark_price": f.get("mark_price"),
                "funding_source": f.get("source"),
                "funding_ts": f.get("timestamp_utc"),
                "open_interest": o.get("open_interest"),
                "oi_change_zscore": o.get("oi_change_zscore"),
                "oi_source": o.get("source"),
                "oi_ts": o.get("timestamp_utc"),
            }
        )

    payload = {
        "available": bool(rows),
        "rows": rows,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/markets/sentiment", tags=["dashboard"])
async def dashboard_markets_sentiment_api() -> JSONResponse:
    """Read-only Krypto-Markt-Sentiment (Fear & Greed Index, alternative.me).

    Liest einen TTL-gecachten Snapshot (``app.market_data.sentiment``) der freien,
    öffentlichen Fear-&-Greed-API (fixe Provider-URL, kein Key, kein Scraping,
    SSRF-safe). Der Request blockiert NIE auf dem Provider; ``age_seconds`` zeigt
    das Snapshot-Alter. ``available`` ist False, solange der Cache kalt ist oder
    der Fetch fehlschlägt — dann KEIN erfundener Wert (No-Fake). ``value`` 0..100.
    Read-only, kein schreibender/kapitalrelevanter Pfad.
    """
    from dataclasses import asdict

    from app.market_data.sentiment import get_cached_sentiment

    snap, age = await get_cached_sentiment()
    payload = asdict(snap)
    payload["age_seconds"] = age
    payload["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/markets/liquidations", tags=["dashboard"])
async def dashboard_markets_liquidations_api() -> JSONResponse:
    """Read-only Perp-Liquidationen (OKX public liquidation-orders, kein Key).

    Liest einen TTL-gecachten Snapshot (``app.market_data.liquidations``) der
    freien, öffentlichen OKX-API (fixe Provider-URL → SSRF-safe, kein Scraping).
    Je Symbol: liquidierte Long- vs Short-Größe (``sz`` in OKX-Kontrakten,
    einheitenfreie Richtungs-Pressure), Event-Anzahl, letzter Zeitstempel. Der
    Request blockiert NIE auf dem Provider; ``available`` ist False solange der
    Cache kalt ist oder der Fetch fehlschlägt → KEIN erfundener Wert (No-Fake).
    Read-only, kein schreibender/kapitalrelevanter Pfad.
    """
    from dataclasses import asdict

    from app.market_data.liquidations import get_cached_liquidations

    snap, age = await get_cached_liquidations()
    payload = asdict(snap)
    payload["age_seconds"] = age
    payload["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


# Binance all-market liquidation canary (#316). Local-file read → small TTL cache
# so rapid dashboard polls don't re-parse the ledger every request.
_LIQ_STREAM_TTL_S = 15.0
_liq_stream_cache: dict[str, Any] = {"at": 0.0, "payload": None}


@router.get("/dashboard/api/markets/liquidations-stream", tags=["dashboard"])
async def dashboard_markets_liquidations_stream_api() -> JSONResponse:
    """Read-only Binance all-market Liquidations-Canary (#316, ``!forceOrder@arr``).

    Liest den lokalen Event-Ledger (vom ``kai-liquidation-stream``-Consumer
    geschrieben) + einen Heartbeat und liefert windowed Metriken. KEIN Live-
    Provider-Call im Request, KEIN schreibender/kapitalrelevanter Pfad. Das
    Heartbeat trennt ehrlich „verbunden, aber ruhiger Markt" (idle) von „Feed
    down". ``is_snapshot_limited`` ist immer True: der All-Market-Stream pusht nur
    die größte Liquidation pro Symbol/1000 ms → unterzählt, nie als Markt-Total.
    """
    from datetime import timedelta

    from app.ingestion.liquidations.binance_stream import HEARTBEAT_PATH
    from app.market_data.liquidation_ledger import DEFAULT_PATH as _LIQ_LEDGER
    from app.market_data.liquidation_ledger import load_events
    from app.market_data.liquidation_metrics import compute_liquidation_metrics

    mono = time.monotonic()
    cached = _liq_stream_cache.get("payload")
    if cached is not None and (mono - _liq_stream_cache["at"]) < _LIQ_STREAM_TTL_S:
        return JSONResponse(content=cached, headers={"Cache-Control": "no-store, max-age=0"})

    now = datetime.now(UTC)
    events = load_events(_LIQ_LEDGER, since=now - timedelta(hours=1), max_lines=20_000)
    metrics = compute_liquidation_metrics(events, now)

    heartbeat_age: float | None = None
    try:
        ts = datetime.fromisoformat(HEARTBEAT_PATH.read_text(encoding="utf-8").strip())
        heartbeat_age = round((now - ts).total_seconds(), 1)
    except (OSError, ValueError):
        heartbeat_age = None
    stream_connected = heartbeat_age is not None and heartbeat_age <= 120.0

    payload = {
        "available": stream_connected,
        "source": "binance_forceorder",
        "is_snapshot_limited": True,
        "stream_connected": stream_connected,
        "heartbeat_age_seconds": heartbeat_age,
        "metrics": metrics.to_dict(),
        "generated_at": now.isoformat(),
    }
    _liq_stream_cache["payload"] = payload
    _liq_stream_cache["at"] = mono
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/markets/momentum", tags=["dashboard"])
async def dashboard_markets_momentum_api() -> JSONResponse:
    """Read-only Preis-Momentum (Binance 24h-Ticker, kein Key).

    Liest einen TTL-gecachten Snapshot (``app.market_data.momentum``) des freien,
    öffentlichen Binance-Endpoints (fixe Provider-URL → SSRF-safe, kein Scraping).
    Je Symbol: letzter Preis + echte 24h-Änderung in %. Der Request blockiert NIE
    auf dem Provider; ``available`` ist False solange der Cache kalt ist oder der
    Fetch fehlschlägt → KEIN erfundener Wert. Read-only, kein schreibender/
    kapitalrelevanter Pfad.
    """
    from dataclasses import asdict

    from app.market_data.momentum import get_cached_momentum

    snap, age = await get_cached_momentum()
    payload = asdict(snap)
    payload["age_seconds"] = age
    payload["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/integrity", tags=["dashboard"])
async def dashboard_integrity_api() -> JSONResponse:
    """Read-only L3 Audit-Integritäts-Status (OpenTimestamps-Anchoring, default-off).

    Spiegelt ``app.integrity.get_integrity_status()`` — reiner Datei-Read der
    bereits geschriebenen Anchor-Records (kein Digest-Compute, kein Stamping, kein
    Netzcall → blockiert nie). ``state`` ist ``disabled`` (Feature aus),
    ``no_anchor`` (an, aber noch nichts verankert), ``ok`` (letzter Anchor
    gefunden; ``proof_available`` zeigt, ob ein OTS-Proof on-chain-verankerbar
    vorliegt) oder ``unavailable`` (Proofs-Dir unlesbar). Fail-soft, kein
    schreibender Pfad.
    """
    from dataclasses import asdict

    from app.integrity import check_l3_integrity_freshness, get_integrity_status

    status = get_integrity_status()
    payload = asdict(status)
    # Freshness/replay watchdog: a green KPI must not hide a stale timer or a
    # tampered audit log. stamper=null / proof_available=false stay non-errors.
    payload["freshness"] = asdict(check_l3_integrity_freshness())
    payload["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/audit-chain", tags=["dashboard"])
async def dashboard_audit_chain_api() -> JSONResponse:
    """Decision-Journal Tamper-Evidence (#314): Integrität der Hash-Chain.

    Dritte Truth-Layer-KPI neben Replay-Status (Portfolio-Rekonstruierbarkeit) und
    OTS-Integrity (On-Chain-Anchoring). Verifiziert ``decision_journal_chain.jsonl``
    (Genesis, lückenlose Verkettung, Chain-/Record-Hash-Konsistenz) gegen die
    Journal-Payloads. State: ``ok`` (tamper-frei) / ``empty`` (noch nichts verkettet)
    / ``broken`` (Manipulation erkannt) / ``unavailable`` (Datei unlesbar). Eine
    Journal-Rotation ist ``journal_gaps`` (informativ), KEIN Tamper. Reiner Datei-
    Read via ``to_thread`` (off the event loop, blockiert nie); fail-soft, nie 500.
    """
    import asyncio

    from app.observability.audit_chain_status import load_audit_chain_status

    payload: dict[str, Any] = {
        "state": "unavailable",
        "available": False,
        "entries": 0,
        "errors": 0,
        "first_error": None,
        "journal_gaps": 0,
        "cross_checked": False,
        "reason": "",
    }
    try:
        status = await asyncio.to_thread(load_audit_chain_status)
        payload = status.to_dict()
    except Exception as exc:  # noqa: BLE001 — Panel degradiert, kein 500
        logger.warning("audit_chain_status_read_failed: %s", exc)
        payload["reason"] = f"Status-Fehler: {exc}"
    payload["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/edge-timeseries", tags=["dashboard"])
async def dashboard_edge_timeseries_api() -> JSONResponse:
    """Edge-Verlauf (#319): Precision/Brier/IC je Zeitfenster aus dem resolved-Ledger.

    Reicht den hintergrund-gecachten Serien-Stand durch — exakt dieselbe Outcome-/
    Real-Source-Logik wie der Edge-Collector. Fenster unter ``min_resolved`` liefern
    ``None`` (kein Chart-Punkt auf dünner Stichprobe — keine irreführenden Trendlinien).
    Der Ledger-Read (>5s auf dem Pi) läuft im Hintergrund (``edge_timeseries_cache``),
    nie auf dem Request-Pfad. Cold-Start: ``warming=true`` mit leerer Serie statt
    Blockieren. Fail-soft: leere Serie statt 500.
    """
    from app.observability.edge_timeseries import (
        DEFAULT_BUCKET_DAYS,
        DEFAULT_MIN_RESOLVED,
    )
    from app.observability.edge_timeseries_cache import get_cached_edge_timeseries

    windows: list[dict[str, Any]] = []
    age: float | None = None
    try:
        series, age = await get_cached_edge_timeseries()
        windows = [w.to_dict() for w in series]
    except Exception as exc:  # noqa: BLE001 — Panel degradiert, kein 500
        logger.warning("edge_timeseries_read_failed: %s", exc)
    payload: dict[str, Any] = {
        "windows": windows,
        "bucket_days": DEFAULT_BUCKET_DAYS,
        "min_resolved": DEFAULT_MIN_RESOLVED,
        "cache_age_seconds": round(age, 1) if age is not None else None,
        "warming": age is None and not windows,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/replay-status", tags=["dashboard"])
async def dashboard_replay_status_api() -> JSONResponse:
    """Replay-SSOT-Status (#314): Integrität des Paper-Execution-Audit-Replays.

    ``artifacts/paper_execution_audit.jsonl`` ist die Replay-SSOT. Der Endpoint
    reicht den hintergrund-gecachten Status durch (``replay_status_cache`` — der
    Replay-Read läuft via ``to_thread`` off the event loop, nie blockierend).
    State: ``ok`` (sauber) / ``degraded`` (Skips oder Lifecycle-Fehler) /
    ``unavailable`` (Replay fehlgeschlagen/Datei fehlt). Cold-Start: ``warming``.
    Fail-soft: nie 500.
    """
    from app.observability.replay_status_cache import get_cached_replay_status

    payload: dict[str, Any] = {
        "state": "warming",
        "available": False,
        "positions": 0,
        "fills_replayed": 0,
        "skipped_events": 0,
        "lifecycle_errors": 0,
        "reason": "",
        "cache_age_seconds": None,
        "warming": True,
    }
    try:
        status, age = await get_cached_replay_status()
        if status is not None:
            payload = {
                **status.to_dict(),
                "cache_age_seconds": round(age, 1) if age is not None else None,
                "warming": False,
            }
    except Exception as exc:  # noqa: BLE001 — Panel degradiert, kein 500
        logger.warning("replay_status_read_failed: %s", exc)
    payload["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/source-activity", tags=["dashboard"])
async def dashboard_source_activity_api(
    window_hours: int = 24,
    silent_after_hours: int = 168,
    repo: DocumentRepository = Depends(get_document_repo),  # noqa: B008
) -> JSONResponse:
    """Read-only per-source ingestion activity (Quellen-Live-Zyklus).

    Aggregiert den kanonischen Dokumenten-Store nach Quelle: Lifetime-Count,
    Count im ``window_hours``-Fenster, letzter ``fetched_at`` und ein ``silent``-
    Flag (letzter Fetch älter als ``silent_after_hours``, default 7 Tage) je
    Quelle — so sieht der Operator, welche Quelle liefert und welche verstummt/
    tot ist. ``silent_count`` zählt die verstummten. Reiner Read über die DB;
    kein Eingriff in den Ingestion-Schreibpfad.
    """
    from dataclasses import asdict

    rows = await repo.source_activity(
        window_hours=window_hours, silent_after_hours=silent_after_hours
    )
    payload = {
        "window_hours": window_hours,
        "silent_after_hours": silent_after_hours,
        "silent_count": sum(1 for r in rows if r.silent),
        "sources": [asdict(r) for r in rows],
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/source-lifecycle", tags=["dashboard"])
async def dashboard_source_lifecycle_api(events_limit: int = 15) -> JSONResponse:
    """Read-only Source-Lifecycle-Ranking + jüngste Statuswechsel (Phase 4).

    Liest das vom ``source_lifecycle_recalc``-Job geschriebene
    ``monitor/source_ranking.json`` (deterministisches Top-N-Ranking mit
    ``provisional``/``silent``/``pinned``/``rotation_flagged``-Flags + Tier) plus
    die letzten ``events_limit`` Lifecycle-Audit-Events (newest-first). Anders als
    die Gate-gefilterte Top-5/Flop-5-Liste zeigt dieses Ranking AUCH provisorische
    Quellen (n unter Validierungs-Schwelle) — ehrlich markiert, nie als belastbar.
    Fail-closed: fehlt/kaputt → ``available: false``, nie ein 500.
    """
    import json
    from pathlib import Path

    from app.learning.source_lifecycle_audit import read_lifecycle_events

    payload: dict[str, Any] = {
        "available": False,
        "generated_at": None,
        "counts": {},
        "ranked": [],
        "recent_events": [],
        "silent_after_days": None,
        "error": None,
    }
    try:
        ranking_path = Path("monitor/source_ranking.json")
        if ranking_path.exists():
            data = json.loads(ranking_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                payload["available"] = True
                payload["generated_at"] = data.get("generated_at")
                payload["counts"] = data.get("counts") or {}
                payload["ranked"] = data.get("ranked") or []
                payload["silent_after_days"] = data.get("silent_after_days")
        events = read_lifecycle_events(Path("artifacts"))
        limit = max(0, events_limit)
        recent = events[-limit:] if (limit and events) else []
        payload["recent_events"] = [
            {
                "source": e.source,
                "from_status": e.from_status,
                "to_status": e.to_status,
                "reason": e.reason,
                "recorded_at_utc": e.recorded_at_utc,
            }
            for e in reversed(recent)
        ]
    except Exception as exc:  # noqa: BLE001 — endpoint must never 500 the dashboard
        logger.warning("dashboard_source_lifecycle_failed", exc_info=True)
        payload["error"] = str(exc)
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/dashboard/api/operator-board", tags=["dashboard"])
async def dashboard_operator_board_api() -> JSONResponse:
    """Kuratiertes Operator-Board (#315): Todos / Phasen / Verbesserungen aus der
    gepflegten SSOT ``docs/operator_board.json`` (read-only, deklarativ — NICHT
    live-berechnet). Fehlt/kaputt → ehrlich leer (kein erfundener Inhalt). Die
    blockierenden Gates + akuten Probleme kommen separat LIVE aus den Truth-Chips
    (AcutePointsBoard); diese Datei liefert nur die kuratierten Listen.
    """
    import json
    from pathlib import Path

    payload: dict[str, Any] = {
        "stand": "",
        "note": "",
        "todos": [],
        "phases": [],
        "improvements": [],
    }
    path = Path("docs/operator_board.json")
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                payload["stand"] = str(data.get("stand", ""))
                payload["note"] = str(data.get("note", ""))
                payload["todos"] = data.get("todos") or []
                payload["phases"] = data.get("phases") or []
                payload["improvements"] = data.get("improvements") or []
    except Exception as exc:  # noqa: BLE001 — Panel degradiert, kein 500
        logger.warning("operator_board_read_failed: %s", exc)
    payload["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Honesty: this board is hand-curated (not live-computed), so expose how old
    # the snapshot is instead of letting a 5-day-old "stand" read as current.
    age_days: int | None = None
    stand_raw = payload["stand"]
    if isinstance(stand_raw, str) and stand_raw.strip():
        try:
            stand_date = datetime.strptime(stand_raw.strip()[:10], "%Y-%m-%d").date()
            age_days = (datetime.now(UTC).date() - stand_date).days
        except ValueError:
            age_days = None
    payload["age_days"] = age_days
    payload["is_stale"] = bool(age_days is not None and age_days > 7)
    payload["content_type"] = "curated_static"
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store, max-age=0"})
