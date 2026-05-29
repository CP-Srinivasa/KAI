"""KYT (Know Your Transaction) read API.

Backward-compatible, read-only surface for the dashboard KYT panel + operator
reporting. No execution, no fabricated data — everything is sourced from the
KYT audit (``artifacts/kyt/assessments.jsonl``) and the live config.

Endpoints
---------
- GET /api/kyt/status                  — config (enabled/mode/provider) + counts
- GET /api/kyt/assessments?limit=N     — recent per-transaction assessments
- GET /api/kyt/transaction/{tx_id}     — latest assessment for one transaction
- GET /api/kyt/open-reviews            — hold/manual_review/block awaiting action

Auth: same CF-Access path as the other read routers (middleware in main.py).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kyt", tags=["kyt"])

_KYT_AUDIT = Path("artifacts/kyt/assessments.jsonl")
_BLOCKING_DECISIONS = {"hold", "manual_review", "block"}


def _load_assessments(limit: int = 200) -> list[dict[str, Any]]:
    if not _KYT_AUDIT.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = _KYT_AUDIT.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("[API] kyt audit read failed: %s", exc)
        return []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


@router.get("/status")
async def kyt_status() -> dict[str, Any]:
    """Live KYT configuration + a rolling decision-count summary."""
    enabled, mode, provider = False, "shadow", "local_lists"
    try:
        from app.core.settings import get_settings

        s = getattr(get_settings(), "kyt", None)
        if s is not None:
            enabled = bool(s.enabled)
            mode = str(s.mode)
            provider = str(s.provider)
    except Exception as exc:  # noqa: BLE001 — read surface must not 500
        logger.warning("[API] kyt status settings read failed: %s", exc)

    rows = _load_assessments(limit=500)
    by_decision: dict[str, int] = {}
    for r in rows:
        d = str(r.get("decision", "?"))
        by_decision[d] = by_decision.get(d, 0) + 1

    return {
        "report_type": "kyt_status",
        "enabled": enabled,
        "mode": mode,
        "provider": provider,
        "assessments_seen": len(rows),
        "by_decision": by_decision,
        "audit_present": _KYT_AUDIT.exists(),
    }


@router.get("/assessments")
async def kyt_assessments(limit: int = 100) -> dict[str, Any]:
    """Recent per-transaction assessments, newest first."""
    limit = max(1, min(limit, 1000))
    rows = _load_assessments(limit=limit)
    rows.reverse()
    return {
        "report_type": "kyt_assessments",
        "count": len(rows),
        "assessments": rows,
    }


@router.get("/transaction/{tx_id}")
async def kyt_transaction(tx_id: str) -> dict[str, Any]:
    """Latest assessment for a single transaction id (incl. historical re-checks)."""
    rows = [r for r in _load_assessments(limit=1000) if str(r.get("tx_id")) == tx_id]
    return {
        "report_type": "kyt_transaction",
        "tx_id": tx_id,
        "found": bool(rows),
        "latest": rows[-1] if rows else None,
        "history": rows,
    }


@router.get("/open-reviews")
async def kyt_open_reviews(limit: int = 200) -> dict[str, Any]:
    """Assessments whose decision blocks execution and awaits operator/SENTR action."""
    rows = _load_assessments(limit=max(1, min(limit, 1000)))
    open_reviews = [r for r in rows if str(r.get("decision")) in _BLOCKING_DECISIONS]
    open_reviews.reverse()
    return {
        "report_type": "kyt_open_reviews",
        "count": len(open_reviews),
        "open_reviews": open_reviews,
    }
