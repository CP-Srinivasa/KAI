"""KAI Persona API Router.

Endpoints:
    GET  /api/kai/persona  — full snapshot (states, phrases, templates) for SPA bootstrap
    GET  /api/kai/state    — current resolved runtime state for live polling
    POST /api/kai/audit    — append a KAI audit event (frontend-emitted)
    GET  /api/kai/audit    — tail recent audit events for /audit drilldown

All endpoints are read-only or append-only; nothing in this router can mutate
trading state, persona config or system runtime. Auth is delegated to the
existing app-level auth dependency (Cloudflare Access). Read-endpoints stay
public-on-LAN; write-endpoint requires the same auth as /operator/.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.audit.kai_audit_service import (
    KaiAuditValidationError,
    get_default_kai_audit_service,
)
from app.messaging.kai_persona import (
    KaiPersonaConfigError,
    load_kai_persona,
)
from app.messaging.kai_phrase_engine import get_kai_phrase
from app.messaging.kai_state_resolver import (
    create_fallback_state,
    fail_closed_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kai", tags=["kai"])


@router.get("/persona")
async def get_kai_persona_snapshot() -> JSONResponse:
    """Return the validated persona snapshot for SPA bootstrap.

    Fail-closed: invalid YAML returns HTTP 503 with KaiPersonaConfigError detail.
    The frontend then falls back to a hard-coded ERROR-state.
    """
    try:
        persona = load_kai_persona()
    except KaiPersonaConfigError as exc:
        logger.error("[kai-api] persona load failed: %s", exc)
        # Audit the failure for forensic replay (fail-closed).
        try:
            get_default_kai_audit_service().append(
                "KAI_CONFIG_VALIDATION_FAILED",
                state="ERROR",
                severity="critical",
                source="api/kai/persona",
                message=f"persona config invalid: {exc}",
                payload={"error": str(exc)},
            )
        except KaiAuditValidationError:
            pass
        raise HTTPException(status_code=503, detail=f"KAI persona config invalid: {exc}") from exc

    return JSONResponse(content=persona.to_snapshot_dict())


class KaiStateInput(BaseModel):
    """Optional client-side trigger for the resolver. Empty list -> OFFLINE fallback."""

    states: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/state")
async def get_current_kai_state() -> JSONResponse:
    """Return the current resolved runtime state.

    Phase 1 implementation: resolves to IDLE with a fresh phrase. Later phases
    will aggregate inputs from agent-worker, exposure summary and hold metrics
    to compute the actual state. The endpoint contract stays stable.
    """
    try:
        persona = load_kai_persona()
        comment = get_kai_phrase("IDLE", persona.language_default, persona=persona)
        rt = create_fallback_state("IDLE", comment)
    except KaiPersonaConfigError as exc:
        rt = fail_closed_state(str(exc))
    return JSONResponse(content=rt.to_dict())


class KaiAuditEventInput(BaseModel):
    type: str
    state: str
    severity: str
    source: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    correlationId: str | None = None
    id: str | None = None
    timestamp: str | None = None


@router.post("/audit")
async def append_kai_audit_event(
    event: KaiAuditEventInput = Body(...),
) -> JSONResponse:
    """Append a KAI audit event sent by the frontend.

    The backend re-validates the event (cannot trust client-side schema) and
    rejects with 400 if it does not conform.
    """
    payload = event.model_dump(exclude_none=True)
    # FastAPI/Pydantic camelCase -> internal snake_case mapping.
    if "correlationId" in payload:
        payload["correlationId"] = payload["correlationId"]
    try:
        record = get_default_kai_audit_service().append_event(payload)
    except KaiAuditValidationError as exc:
        raise HTTPException(status_code=400, detail=f"invalid kai audit event: {exc}") from exc
    return JSONResponse(content=record, status_code=201)


@router.get("/audit")
async def get_kai_audit_tail(limit: int = Query(default=100, ge=1, le=1000)) -> JSONResponse:
    """Return the last N KAI audit events. Read-only forensic surface."""
    events = get_default_kai_audit_service().tail(limit=limit)
    return JSONResponse(
        content={
            "count": len(events),
            "limit": limit,
            "fetched_at": datetime.now(UTC).isoformat(),
            "events": events,
        },
    )
