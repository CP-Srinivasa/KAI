"""Core types of the intelligence layer (ADR 0015 §2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

# The complete, closed set of failure modes. Every stage of the fail-closed
# chain maps to exactly one reason; providers never raise into callers.
FALLBACK_DISABLED = "disabled"
FALLBACK_UNAVAILABLE = "unavailable"
FALLBACK_TIMEOUT = "timeout"
FALLBACK_MALFORMED_JSON = "malformed_json"
FALLBACK_SCHEMA_VIOLATION = "schema_violation"
FALLBACK_NO_MODEL = "no_model_configured"


@dataclass(frozen=True)
class LLMRequest:
    """One task-scoped completion request (prompt already context-built + redacted)."""

    task_type: str
    prompt: str
    schema: dict[str, Any]
    input_refs: tuple[str, ...] = ()
    max_tokens: int = 2048
    timeout_s: float = 120.0


@dataclass(frozen=True)
class LLMResult:
    """Outcome of a completion. ``ok=False`` always carries a fallback_reason."""

    ok: bool
    data: dict[str, Any] | None
    provider: str
    model: str
    latency_ms: float
    fallback_reason: str | None = None
    confidence: float | None = None
    evidence: tuple[str, ...] = ()


class LLMProvider(Protocol):
    """Uniform provider seam. Implementations must never substitute another
    provider on failure (ADR 0015: no silent cloud fallback)."""

    name: str

    def complete(self, request: LLMRequest) -> LLMResult: ...

    def available(self) -> bool: ...
