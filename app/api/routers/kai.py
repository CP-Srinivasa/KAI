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

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.audit.kai_audit_service import (
    KaiAuditValidationError,
    get_default_kai_audit_service,
)
from app.messaging.kai_chat_engine import ChatReply, transcribe_audio_via_whisper
from app.messaging.kai_chat_engine import chat as kai_chat_dispatch
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

    Phase 1 implementation: resolves to a FIXED IDLE state — it is not derived
    from live system inputs yet. The response is therefore flagged
    ``is_stub: true`` / ``phase: 1`` so the UI renders it honestly as a
    placeholder instead of pretending a live status. Later phases will aggregate
    inputs from agent-worker, exposure summary and hold metrics; the contract
    stays stable and ``is_stub`` flips to false once the resolver is wired.
    """
    is_stub = True
    try:
        persona = load_kai_persona()
        comment = get_kai_phrase("IDLE", persona.language_default, persona=persona)
        rt = create_fallback_state("IDLE", comment)
    except KaiPersonaConfigError as exc:
        rt = fail_closed_state(str(exc))
        # A real fail-closed error is truthful, not a placeholder.
        is_stub = False
    content = rt.to_dict()
    content["is_stub"] = is_stub
    content["phase"] = 1
    return JSONResponse(content=content)


class KaiAuditEventInput(BaseModel):
    type: str
    state: str
    severity: str
    source: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    correlationId: str | None = None  # noqa: N815 — JSON-API-compat with frontend camelCase
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


class KaiChatInput(BaseModel):
    """Phase-2 chat-input. language defaults to de (operator language)."""

    message: str = Field(..., min_length=1, max_length=2000)
    language: str = Field(default="de")


@router.post("/chat")
async def post_kai_chat(payload: KaiChatInput = Body(...)) -> JSONResponse:
    """Operator chat with KAI. Hybrid: trading-intent vs smalltalk (GPT-4o).

    Returns: {reply, intent, source, timestamp}.
    No state mutation. No write to trading audit. KAI-audit-event for forensic
    replay only on dispatch errors (handler itself never raises).
    """
    reply: ChatReply = await kai_chat_dispatch(
        message=payload.message,
        language=payload.language,
    )
    return JSONResponse(
        content={
            "reply": reply.reply,
            "intent": reply.intent,
            "source": reply.source,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )


@router.post("/transcribe")
async def post_kai_transcribe(
    audio: UploadFile = File(...),
    language: str = Form(default="de"),
) -> JSONResponse:
    """Transcribe a voice audio blob via OpenAI Whisper.

    Frontend uses MediaRecorder API and POSTs the resulting blob (webm/mp4/m4a/ogg).
    Returns: {text, filename}. On Whisper failure: text is empty string, frontend
    shows a friendly fallback message.
    """
    audio_data = await audio.read()
    if not audio_data:
        raise HTTPException(status_code=400, detail="empty_audio")
    if len(audio_data) > 25 * 1024 * 1024:  # OpenAI Whisper hard limit ~25MB
        raise HTTPException(status_code=413, detail="audio_too_large_max_25mb")

    text = await transcribe_audio_via_whisper(
        audio_data,
        filename=audio.filename or "voice.webm",
        language=language,
    )
    return JSONResponse(
        content={
            "text": text or "",
            "filename": audio.filename,
            "size_bytes": len(audio_data),
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )
