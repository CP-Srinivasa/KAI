"""Operator dashboard JSON API.

Liefert Quality-Bar-Metriken (`GET /dashboard/api/quality`) für das React-SPA
unter `/dashboard`. Das SPA selbst wird in `app/api/main.py` als StaticFiles-
Mount (`web/dist/`) eingehängt — dieser Router kümmert sich nur noch um die
JSON-Daten.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["dashboard"])

_ARTIFACTS = Path("artifacts")
_HOLD_REPORT = _ARTIFACTS / "ph5_hold" / "ph5_hold_metrics_report.json"
_ALERT_AUDIT = _ARTIFACTS / "alert_audit.jsonl"
_ALERT_OUTCOMES = _ARTIFACTS / "alert_outcomes.jsonl"
_TRADING_LOOP_AUDIT = _ARTIFACTS / "trading_loop_audit.jsonl"
_PAPER_EXECUTION_AUDIT = _ARTIFACTS / "paper_execution_audit.jsonl"


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


def _load_hold_report() -> dict[str, Any] | None:
    if not _HOLD_REPORT.exists():
        return None
    try:
        return json.loads(_HOLD_REPORT.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


@router.get("/dashboard/api/quality", tags=["dashboard"])
async def dashboard_quality_api() -> JSONResponse:
    """Return quality-bar metrics as JSON for the dashboard SPA."""
    report = _load_hold_report()
    if report is None:
        return JSONResponse({"error": "hold_report_not_found"}, status_code=404)

    quality = report.get("signal_quality_validation", {})
    hit_rate = report.get("alert_hit_rate_evidence", {})
    paper = report.get("paper_trading_evidence", {})
    gate = report.get("hold_gate_evaluation", {})

    exec_rows = _load_jsonl(_PAPER_EXECUTION_AUDIT)
    fills = [r for r in exec_rows if r.get("event_type") == "order_filled"]

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

    fwd = report.get("forward_simulation", {})

    return JSONResponse({
        "precision_pct": quality.get("resolved_precision_pct"),
        "false_positive_pct": quality.get("resolved_false_positive_rate_pct"),
        "resolved_count": hit_rate.get("resolved_directional_documents", 0),
        "directional_count": hit_rate.get("directional_alert_documents", 0),
        "hits": hit_rate.get("alert_hits", 0),
        "misses": hit_rate.get("alert_misses", 0),
        "priority_corr": quality.get("priority_hit_correlation"),
        "forward_precision_pct": fwd.get("precision_pct"),
        "forward_resolved": fwd.get("resolved", 0),
        "forward_hits": fwd.get("hits", 0),
        "forward_miss": fwd.get("miss", 0),
        "paper_fills": len(fills),
        "paper_cycles": paper.get("loop_metrics", {}).get("total_cycles", 0),
        "real_price_cycles": quality.get("paper_real_price_cycle_count", 0),
        "gate_status": gate.get("overall_status"),
        "blocking_reasons": gate.get("blocking_reasons", []),
        "actionable_rate_pct": quality.get("directional_actionable_rate_pct"),
        "high_priority_hit_rate_pct": quality.get("high_priority_hit_rate_pct"),
        "low_priority_hit_rate_pct": quality.get("low_priority_hit_rate_pct"),
        "loop_status_counts": status_counts,
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
        "generated_at": report.get("generated_at", ""),
    })
