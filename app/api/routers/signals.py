"""Dashboard signal paste endpoint — parity with Telegram structured messages.

POST /api/signals/paste accepts a structured block ([SIGNAL] / [NEWS] /
[EXCHANGE_RESPONSE]) and routes it through the same v2 envelope pipeline
used by the Telegram bot:

- parse via `parse_structured_message`
- schema-validate via `validate_message_model`
- wrap via `MessageEnvelope.wrap(source_channel=dashboard)`
- de-duplicate via idempotency_key over the envelope audit JSONL
- audit append to `artifacts/telegram_message_envelope.jsonl` (shared log)

No order execution is performed here — the dashboard surface is read-only
for execution; a SIGNAL accepted here is available for downstream routing
just like a Telegram-accepted one.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.routers.operator import require_operator_api_token
from app.messaging.message_models import (
    ExchangeResponse,
    MessageEnvelope,
    NewsMessage,
    SourceChannel,
    TradingSignal,
)
from app.messaging.message_schema import (
    MessageSchemaValidationError,
    validate_message_model,
)
from app.messaging.signal_parser import (
    SignalParseError,
    parse_structured_message,
)

logger = logging.getLogger(__name__)

_ENVELOPE_AUDIT_PATH = Path("artifacts/telegram_message_envelope.jsonl")
_LOOKBACK = 500

router = APIRouter(prefix="/signals", tags=["signals"])


class SignalPasteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    operator_user_id: str | None = Field(default=None, max_length=128)
    trace_id: str | None = Field(default=None, max_length=128)


class SignalPasteResponse(BaseModel):
    status: str  # accepted | duplicate | rejected
    stage: str   # accepted | idempotency_gate | parse | schema_validation | execution_gate
    message_type: str | None = None
    envelope_id: str | None = None
    idempotency_key: str | None = None
    errors: list[str] = Field(default_factory=list)


def _audit_path() -> Path:
    path = _ENVELOPE_AUDIT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _append_audit(record: dict[str, object]) -> None:
    path = _audit_path()
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.error("[signals.paste] Audit write failed: %s", exc)


def _is_duplicate(idempotency_key: str) -> bool:
    path = _audit_path()
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.warning("[signals.paste] Audit read failed: %s", exc)
        return False
    for line in reversed(lines[-_LOOKBACK:]):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("idempotency_key") != idempotency_key:
            continue
        if rec.get("stage") in {"accepted", "idempotency_gate"}:
            return True
    return False


def _record_base(
    stage: str,
    status: str,
    message_type: str,
    operator_user_id: str | None,
    trace_id: str | None,
) -> dict[str, object]:
    rec: dict[str, object] = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "event": "dashboard_message_envelope",
        "source": "dashboard",
        "stage": stage,
        "status": status,
        "message_type": message_type,
        "execution_enabled": False,
        "write_back_allowed": False,
    }
    if operator_user_id:
        rec["operator_user_id"] = operator_user_id
    if trace_id:
        rec["trace_id"] = trace_id
    return rec


@router.post("/paste", response_model=SignalPasteResponse)
async def paste_signal(
    payload: SignalPasteRequest,
    request: Request,
    _auth: Annotated[None, Depends(require_operator_api_token)] = None,
) -> SignalPasteResponse:
    """Accept a structured NEWS/SIGNAL/EXCHANGE_RESPONSE block from the dashboard."""
    text = payload.text.strip()

    try:
        parsed = parse_structured_message(text)
    except SignalParseError as exc:
        rec = _record_base(
            "parse", "rejected", "unknown",
            payload.operator_user_id, payload.trace_id,
        )
        rec["errors"] = [str(exc)]
        rec["payload"] = {"text_preview": text[:300]}
        _append_audit(rec)
        return SignalPasteResponse(
            status="rejected", stage="parse",
            message_type=None, errors=[str(exc)],
        )

    try:
        schema_payload = validate_message_model(parsed)
    except MessageSchemaValidationError as exc:
        errors = exc.errors or [str(exc)]
        rec = _record_base(
            "schema_validation", "rejected",
            parsed.message_type.value,
            payload.operator_user_id, payload.trace_id,
        )
        rec["errors"] = list(errors)
        rec["payload"] = {"text_preview": text[:300]}
        _append_audit(rec)
        return SignalPasteResponse(
            status="rejected", stage="schema_validation",
            message_type=parsed.message_type.value, errors=list(errors),
        )

    if isinstance(parsed, TradingSignal):
        validation_errors = parsed.validation_errors
        if validation_errors:
            envelope = MessageEnvelope.wrap(
                parsed,
                source_channel=SourceChannel.DASHBOARD,
                operator_user_id=payload.operator_user_id,
                trace_id=payload.trace_id,
            )
            rec = _record_base(
                "execution_gate", "blocked", "signal",
                payload.operator_user_id, payload.trace_id,
            )
            rec["envelope_id"] = envelope.envelope_id
            rec["idempotency_key"] = envelope.idempotency_key
            rec["payload"] = dict(envelope.payload)
            rec["errors"] = list(validation_errors)
            _append_audit(rec)
            return SignalPasteResponse(
                status="rejected", stage="execution_gate",
                message_type="signal",
                envelope_id=envelope.envelope_id,
                idempotency_key=envelope.idempotency_key,
                errors=list(validation_errors),
            )

    envelope = MessageEnvelope.wrap(
        parsed,
        source_channel=SourceChannel.DASHBOARD,
        operator_user_id=payload.operator_user_id,
        trace_id=payload.trace_id,
    )
    message_type = envelope.payload_type.value

    if _is_duplicate(envelope.idempotency_key):
        rec = _record_base(
            "idempotency_gate", "duplicate", message_type,
            payload.operator_user_id, payload.trace_id,
        )
        rec["envelope_id"] = envelope.envelope_id
        rec["idempotency_key"] = envelope.idempotency_key
        rec["payload"] = dict(envelope.payload)
        _append_audit(rec)
        return SignalPasteResponse(
            status="duplicate", stage="idempotency_gate",
            message_type=message_type,
            envelope_id=envelope.envelope_id,
            idempotency_key=envelope.idempotency_key,
        )

    rec = _record_base(
        "accepted", "ok", message_type,
        payload.operator_user_id, payload.trace_id,
    )
    rec["envelope_id"] = envelope.envelope_id
    rec["idempotency_key"] = envelope.idempotency_key
    rec["payload"] = dict(envelope.payload)
    rec["schema_payload_keys"] = sorted(schema_payload.keys())
    _append_audit(rec)

    # Mirror shape used by telegram bot's structured NEWS/EXCHANGE_RESPONSE paths:
    # no order execution happens here. For TradingSignal downstream routing
    # (paper handoff) a follow-up worker can consume the envelope JSONL.
    _ = (NewsMessage, ExchangeResponse)  # keep imports referenced; isinstance for typing only

    return SignalPasteResponse(
        status="accepted", stage="accepted",
        message_type=message_type,
        envelope_id=envelope.envelope_id,
        idempotency_key=envelope.idempotency_key,
    )


class EnvelopeRecord(BaseModel):
    timestamp_utc: str | None = None
    event: str | None = None
    source: str | None = None
    stage: str | None = None
    status: str | None = None
    message_type: str | None = None
    envelope_id: str | None = None
    idempotency_key: str | None = None
    errors: list[str] = Field(default_factory=list)


class EnvelopeRecentResponse(BaseModel):
    count: int
    records: list[EnvelopeRecord]


def _project_record(raw: dict[str, object]) -> EnvelopeRecord:
    errors = raw.get("errors")
    errors_list = [str(e) for e in errors] if isinstance(errors, list) else []
    return EnvelopeRecord(
        timestamp_utc=_as_str(raw.get("timestamp_utc")),
        event=_as_str(raw.get("event")),
        source=_as_str(raw.get("source")),
        stage=_as_str(raw.get("stage")),
        status=_as_str(raw.get("status")),
        message_type=_as_str(raw.get("message_type")),
        envelope_id=_as_str(raw.get("envelope_id")),
        idempotency_key=_as_str(raw.get("idempotency_key")),
        errors=errors_list,
    )


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


@router.get("/envelope/recent", response_model=EnvelopeRecentResponse)
async def recent_envelopes(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    _auth: Annotated[None, Depends(require_operator_api_token)] = None,
) -> EnvelopeRecentResponse:
    """Return the newest N envelope audit records (parse/accepted/duplicate/…)."""
    path = _ENVELOPE_AUDIT_PATH
    if not path.exists():
        return EnvelopeRecentResponse(count=0, records=[])
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.warning("[signals.recent] Audit read failed: %s", exc)
        return EnvelopeRecentResponse(count=0, records=[])

    records: list[EnvelopeRecord] = []
    # Walk from newest (end of file) backward, stop at limit
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        records.append(_project_record(raw))
        if len(records) >= limit:
            break
    return EnvelopeRecentResponse(count=len(records), records=records)
