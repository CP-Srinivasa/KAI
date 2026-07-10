"""Append-only audit trail for every LLM call (ADR 0015 §2.6).

Canonical write pattern: frozen Pydantic record (extra=forbid) -> append_lock
-> single-line JSON append. Read back only via ``iter_jsonl_tolerant``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.core.file_lock import append_lock
from app.intelligence.core import LLMRequest, LLMResult

DEFAULT_AUDIT_PATH = Path("artifacts/intelligence_audit.jsonl")


class LlmAuditRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "v1"
    request_id: str
    ts: str
    task_type: str
    provider: str
    model: str
    prompt_hash: str
    input_refs: tuple[str, ...]
    latency_ms: float
    ok: bool
    fallback_reason: str | None
    confidence: float | None
    evidence: tuple[str, ...]
    redaction_count: int
    # Layer constant (ADR 0015): recorded on every row, never configurable.
    influences_execution: bool = False


def build_audit_record(
    request: LLMRequest, result: LLMResult, *, redaction_count: int
) -> LlmAuditRecord:
    return LlmAuditRecord(
        request_id=uuid.uuid4().hex[:16],
        ts=datetime.now(UTC).isoformat(),
        task_type=request.task_type,
        provider=result.provider,
        model=result.model,
        prompt_hash=hashlib.sha256(request.prompt.encode("utf-8")).hexdigest(),
        input_refs=request.input_refs,
        latency_ms=round(result.latency_ms, 3),
        ok=result.ok,
        fallback_reason=result.fallback_reason,
        confidence=result.confidence,
        evidence=result.evidence,
        redaction_count=redaction_count,
    )


def append_audit_record(record: LlmAuditRecord, path: Path = DEFAULT_AUDIT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record.model_dump(), sort_keys=True, separators=(",", ":"))
    with append_lock(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
