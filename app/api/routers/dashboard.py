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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.alerts.hold_metrics import build_hold_metrics_report

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

_ARTIFACTS = Path("artifacts")
_ALERT_AUDIT = _ARTIFACTS / "alert_audit.jsonl"
_ALERT_OUTCOMES = _ARTIFACTS / "alert_outcomes.jsonl"
_TRADING_LOOP_AUDIT = _ARTIFACTS / "trading_loop_audit.jsonl"
_PAPER_EXECUTION_AUDIT = _ARTIFACTS / "paper_execution_audit.jsonl"
_TV_PENDING = _ARTIFACTS / "tradingview_pending_signals.jsonl"
_AUDIT_V1_DISQUALIFIED_FLAG = _ARTIFACTS / "paper_execution_audit_v1_disqualified.flag"

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

# Source-by-doc map (for the active-precision legacy split). Loading it means
# one DB query over directional doc-ids — not hot-path safe without a cache.
# 5 min TTL: docs are rarely re-classified, and the map is additive.
_SOURCE_MAP_TTL_S = 300.0
_source_map_cache: dict[str, Any] = {"at": 0.0, "map": None}


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

        from app.alerts.audit import load_alert_audits
        from app.core.settings import get_settings
        from app.storage.db.session import build_session_factory
        from app.storage.models.document import CanonicalDocumentModel

        audits = load_alert_audits(_ALERT_AUDIT)
        doc_ids: set[str] = set()
        for rec in audits:
            sentiment = (rec.sentiment_label or "").lower()
            if rec.is_digest or sentiment not in {"bullish", "bearish"}:
                continue
            doc_ids.add(rec.document_id)
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
        return cast(dict[str, str], cached) if cached is not None else {}


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


@router.get("/dashboard/api/quality", tags=["dashboard"])
async def dashboard_quality_api() -> JSONResponse:
    """Return quality-bar metrics as JSON for the dashboard SPA."""
    report = await _live_hold_report()

    quality = report.get("signal_quality_validation", {})
    hit_rate = report.get("alert_hit_rate_evidence", {})
    paper = report.get("paper_trading_evidence", {})
    gate = report.get("hold_gate_evaluation", {})

    from app.storage.analytics_db import (
        get_attribution_pnl,
        get_loop_status_counts,
        get_paper_fills_count,
        get_realized_pnl,
        get_recent_alerts,
    )

    realized_pnl_usd, positions_closed = get_realized_pnl()
    attribution_pnl = get_attribution_pnl()
    recent_alerts = get_recent_alerts(20)
    status_counts = get_loop_status_counts()
    paper_fills = get_paper_fills_count()

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

    return JSONResponse(
        content={
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
            "priority_tier_standard_hit_rate_pct": quality.get(
                "priority_tier_standard_hit_rate_pct"
            ),
            "priority_tier_standard_ci_low_pct": quality.get("priority_tier_standard_ci_low_pct"),
            "priority_tier_standard_ci_high_pct": quality.get("priority_tier_standard_ci_high_pct"),
            "forward_precision_pct": fwd.get("precision_pct"),
            "forward_resolved": fwd.get("resolved", 0),
            "forward_hits": fwd.get("hits", 0),
            "forward_miss": fwd.get("miss", 0),
            "paper_fills": paper_fills,
            "paper_fills_with_pnl": positions_closed,
            "paper_realized_pnl_usd": realized_pnl_usd,
            "paper_positions_closed": positions_closed,
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
            # V-DB4a 2026-05-08: Per-source active precision fuer Quality-Tile.
            # Liefert n / hit-rate / Wilson-CI / passes_gate je Source.
            "per_source_active_precision": report.get("per_source_active_precision", {}),
            # V-DB4e 2026-05-08: Per-source rolling 30-day stability windows.
            "per_source_stability": report.get("per_source_stability", {}),
            "attribution_pnl": attribution_pnl,
            "recent_alerts": recent_alerts,
            "generated_at": report.get("generated_at", ""),
        },
        headers={"Cache-Control": "no-store, max-age=0"},
    )


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
    return JSONResponse(
        content=summary.to_json_dict(),
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


# ─── Bayesian Confidence Audit (Schatten-Vergleich-Spalte) ────────────────────

_BAYES_AUDIT = _ARTIFACTS / "bayes_confidence_audit.jsonl"
_BAYES_LIMIT_DEFAULT = 50
_BAYES_LIMIT_MAX = 500
# Outcome-Map für Calibration: pluggable, damit der Endpoint funktioniert,
# *bevor* das Decision_id→Outcome-Linking fertig verdrahtet ist. Default leer.
_BAYES_OUTCOME_MAP: dict[str, int] = {}


@router.get("/dashboard/api/bayes-audit", tags=["dashboard"])
async def dashboard_bayes_audit_api(limit: int = _BAYES_LIMIT_DEFAULT) -> JSONResponse:
    """Letzte N Bayes-Confidence-Reports aus dem Audit-Sidecar.

    Liefert eine kompakte Tabelle pro Decision für die Dashboard-Spalte —
    Prior, Posterior, Confidence, Uncertainty und Counts der Beitragsklassen.
    Die ausführlichen Beitragslisten + Erklär-Strings sind im Original-Report
    abrufbar (``include_full=true``).

    Schema-Versionierung über ``BayesAuditEntry.schema_version``.  Bei
    fehlender Datei → leeres ``entries``-Array, kein 5xx (Schatten-Modus
    darf das Dashboard nicht brechen).
    """
    from app.signals.bayes_journal import load_bayes_reports

    capped = max(1, min(int(limit), _BAYES_LIMIT_MAX))
    try:
        all_entries = load_bayes_reports(_BAYES_AUDIT)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bayes_audit_load_failed: %s", exc)
        return JSONResponse(
            content={"error": "bayes_audit_unavailable", "detail": str(exc)},
            status_code=503,
        )

    tail = all_entries[-capped:]
    entries: list[dict[str, Any]] = []
    for e in reversed(tail):  # newest first for the UI
        report = e.report
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
                "decision_id": e.decision_id,
                "timestamp_utc": e.timestamp_utc,
                "symbol": e.symbol,
                "direction": e.direction,
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
            "total_count": len(all_entries),
            "returned_count": len(entries),
            "limit": capped,
            "entries": entries,
        },
        headers={"Cache-Control": "no-store, max-age=0"},
    )


# ─── Calibration-Report (Quality-Bar-Erweiterung) ─────────────────────────────


@router.get("/dashboard/api/calibration", tags=["dashboard"])
async def dashboard_calibration_api(n_bins: int = 10) -> JSONResponse:
    """Brier / Log-Loss / ECE / Reliability-Diagramm aus Bayes-Audit + Outcomes.

    KAI-Leitsatz-Pflicht: bewerte *Calibration*, nicht nur Trefferquote.
    Solange noch keine Outcome-Verkettung verdrahtet ist, antwortet der
    Endpoint mit einem ehrlichen leeren Report (``n_pairs=0`` + Note).
    """
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

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "n_pairs": report.n_pairs,
        "total_weight": report.total_weight,
        "brier_score": report.brier_score,
        "log_loss": report.log_loss,
        "expected_calibration_error": report.expected_calibration_error,
        "mean_predicted": report.mean_predicted,
        "mean_observed": report.mean_observed,
        "sample_sufficient": report.sample_sufficient,
        "bins": [b.model_dump() for b in report.bins],
        "notes": list(report.notes),
        "outcome_map_size": len(_BAYES_OUTCOME_MAP),
    }
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "no-store, max-age=0"},
    )
