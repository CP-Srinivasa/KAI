"""One place where an LLM call is measured, classified and audited.

Why this exists (NEO-F-005/-007/-008/-010): the repo had provider *construction*
centralised in ``app.analysis.factory`` but the *call* nowhere. Timeout, error
classification, correlation-id, tokens and audit are call-time concerns, so
they live here.

Two rules that are not negotiable:

* **Telemetry never raises into the caller.** The underlying writer is
  best-effort (``record_llm_call``); this scope adds no failure mode of its own.
* **Exactly one telemetry row per scope** - on success and on failure alike.
  A failure is re-raised unchanged; the scope never swallows.

The error taxonomy is copied (not imported) from ``app.intelligence.core``,
which held the only closed failure vocabulary in the repo. That module stays
quarantined; duplicating ten string constants is cheaper than coupling to it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import ValidationError

from app.observability.llm_telemetry import record_llm_call

if TYPE_CHECKING:
    from app.ai.models import AttemptTrace

ErrorClass = Literal[
    "timeout",
    "rate_limit",
    "auth",
    "quota",
    "schema",
    "refusal",
    "transport",
    "server",
    "cancelled",
    "unknown",
]

Purpose = Literal["analysis", "chat", "intent", "stt", "consensus"]
Outcome = Literal["success", "fallthrough", "exhausted", "skipped"]

# Classes for which a second attempt cannot possibly help. Everything else is
# retryable - deliberately a deny-list, so unclassified errors keep the
# pre-existing retry behaviour instead of silently losing it.
_NON_RETRYABLE: frozenset[str] = frozenset({"auth", "quota", "schema", "cancelled"})

# 4xx codes that DO warrant a retry (the rest of 4xx is a client-side defect).
_RETRYABLE_CLIENT_STATUS: frozenset[int] = frozenset({408, 409, 425, 429})

_TRANSPORT_MARKERS: tuple[str, ...] = (
    "connecterror",
    "connectionerror",
    "connecttimeout",
    "readerror",
    "remoteprotocolerror",
    "apiconnectionerror",
)


# ── correlation propagation ─────────────────────────────────────────────────
# NEO-F-008: request ids existed at the HTTP edge and died there. A ContextVar
# carries one across the await boundaries into providers that cannot take an
# extra argument (BaseAnalysisProvider.analyze is a fixed interface) WITHOUT
# smuggling it through the prompt ``context`` dict, which would change the
# prompt text itself.

_CORRELATION_ID: ContextVar[str | None] = ContextVar("kai_llm_correlation_id", default=None)


def current_correlation_id() -> str | None:
    """Correlation id bound to the current async context, if any."""
    return _CORRELATION_ID.get()


@contextmanager
def correlation_scope(correlation_id: str | None) -> Iterator[str]:
    """Bind *correlation_id* to every LLM call made inside the block.

    Generates one when ``None`` so a chain is never anonymous. Always resets on
    exit, so a pipeline awaited inline cannot leak its id into its caller.
    """
    resolved = correlation_id or f"llm_{uuid4().hex[:12]}"
    token = _CORRELATION_ID.set(resolved)
    try:
        yield resolved
    finally:
        _CORRELATION_ID.reset(token)


def http_status(exc: BaseException) -> int | None:
    """Best-effort HTTP status of *exc*, duck-typed across SDKs. Never raises.

    Covers the OpenAI SDK (``.status_code``), httpx (``.response.status_code``)
    and the Anthropic equivalents without importing any of them.
    """
    try:
        direct = getattr(exc, "status_code", None)
        if isinstance(direct, int) and 100 <= direct < 600:
            return direct
        response = getattr(exc, "response", None)
        if response is not None:
            nested = getattr(response, "status_code", None)
            if isinstance(nested, int) and 100 <= nested < 600:
                return nested
        status = getattr(exc, "status", None)
        if isinstance(status, int) and 100 <= status < 600:
            return status
    except Exception:  # noqa: BLE001 - classification must never raise
        return None
    return None


def classify_error(exc: BaseException) -> ErrorClass:
    """Map *exc* onto the closed taxonomy. Falls back to ``unknown``, never raises."""
    try:
        if isinstance(exc, asyncio.CancelledError):
            return "cancelled"
        # asyncio.TimeoutError is an alias of builtins.TimeoutError since 3.11.
        if isinstance(exc, TimeoutError):
            return "timeout"
        if isinstance(exc, ValidationError):
            return "schema"

        status = http_status(exc)
        if status is not None:
            if status in (401, 403):
                return "auth"
            if status == 402:
                return "quota"
            if status == 429:
                return "rate_limit"
            if status >= 500:
                return "server"

        name = type(exc).__name__.lower()
        if "timeout" in name:
            return "timeout"
        if "ratelimit" in name:
            return "rate_limit"
        if "authentication" in name or "permissiondenied" in name:
            return "auth"
        if any(marker in name for marker in _TRANSPORT_MARKERS):
            return "transport"
    except Exception:  # noqa: BLE001 - classification must never raise
        return "unknown"
    return "unknown"


def is_retryable_error_class(error_class: ErrorClass | None, status: int | None = None) -> bool:
    """Canonical retry decision for both exceptions and recorded attempts.

    LiteLLM transports return :class:`~app.ai.models.AttemptTrace` instead of
    raising.  Keeping this decision here prevents an exception policy and a
    trace policy from drifting apart.
    """
    if error_class is None or error_class in _NON_RETRYABLE:
        return False
    if status is not None and 400 <= status < 500 and status not in _RETRYABLE_CLIENT_STATUS:
        return False
    return True


def is_retryable_error(exc: BaseException) -> bool:
    """Retry predicate for the provider-level ``tenacity`` decorators.

    ``False`` for auth (401/403), quota, schema violations and any other 4xx a
    retry cannot fix - those previously cost three attempts plus up to 15 s of
    backoff for nothing (NEO-F-006).
    """
    return is_retryable_error_class(classify_error(exc), http_status(exc))


def record_attempt_trace(
    attempt_trace: AttemptTrace,
    *,
    correlation_id: str,
    purpose: Purpose,
    logical_route: str,
    mode: str,
    role: str,
    attempt_number: int,
    budget_decision: str,
    circuit_state: str,
    execution_authority: bool,
    schema_status: str | None,
    outcome: Outcome,
    fallback_from: str | None = None,
    fallback_to: str | None = None,
    path: Path | None = None,
) -> None:
    """Append one physical returned attempt to the canonical telemetry stream."""
    raw_status = attempt_trace.detail.get("status_code")
    status = raw_status if isinstance(raw_status, int) else None
    record_llm_call(
        provider=attempt_trace.actual_provider,
        model=attempt_trace.actual_model,
        ok=attempt_trace.ok,
        latency_ms=attempt_trace.latency_ms,
        role=role,
        error_type=str(attempt_trace.detail.get("exception") or "") or None,
        path=path,
        correlation_id=correlation_id,
        call_id=f"llmc_{uuid4().hex[:8]}",
        purpose=purpose,
        attempt=attempt_number,
        error_class=attempt_trace.error_class,
        http_status=status,
        prompt_tokens=attempt_trace.input_tokens or 0,
        completion_tokens=attempt_trace.output_tokens or 0,
        outcome=outcome,
        logical_route=logical_route,
        mode=mode,
        transport=attempt_trace.transport,
        requested_model_alias=attempt_trace.requested_model,
        actual_provider=attempt_trace.actual_provider or None,
        actual_model=attempt_trace.actual_model or None,
        identity_proven=attempt_trace.identity_proven,
        retry_count=max(0, attempt_number - 1),
        fallback_from=fallback_from,
        fallback_to=fallback_to,
        input_tokens=attempt_trace.input_tokens,
        output_tokens=attempt_trace.output_tokens,
        cost_usd=attempt_trace.cost_usd,
        schema_status=schema_status,
        budget_decision=budget_decision,
        circuit_state=circuit_state,
        execution_authority=execution_authority,
        upstream_request_id=attempt_trace.request_id or None,
    )


@dataclass
class CallScope:
    """Mutable handle handed to the caller inside :func:`llm_call_scope`."""

    correlation_id: str
    call_id: str
    provider: str
    model: str
    purpose: str
    role: str
    chain_position: int
    attempt: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failure_outcome: str = "exhausted"
    success_outcome: str = field(default="success", repr=False)

    def set_tokens(self, prompt: int | None, completion: int | None) -> None:
        """Record token usage. Tolerates ``None``/garbage from SDK responses."""
        try:
            self.prompt_tokens = int(prompt or 0)
        except (TypeError, ValueError):
            self.prompt_tokens = 0
        try:
            self.completion_tokens = int(completion or 0)
        except (TypeError, ValueError):
            self.completion_tokens = 0

    def set_model(self, model: str | None) -> None:
        """Override the model once the response reveals which one actually ran."""
        if model:
            self.model = model

    def set_outcome(self, outcome: Outcome) -> None:
        """Override the outcome recorded on *successful* exit."""
        self.success_outcome = outcome


@asynccontextmanager
async def llm_call_scope(
    *,
    purpose: Purpose,
    provider: str,
    model: str | None,
    role: str = "primary",
    correlation_id: str | None = None,
    call_id: str | None = None,
    chain_position: int = 0,
    attempt: int = 1,
    failure_outcome: Outcome = "exhausted",
    path: Path | None = None,
) -> AsyncIterator[CallScope]:
    """Measure exactly one LLM call and append exactly one v2 telemetry row.

    Args:
        purpose: which surface the call serves (analysis/chat/intent/stt/consensus).
        provider: provider name, e.g. ``"openai"``.
        model: model name; ``None`` becomes ``""`` (never invented).
        role: ``primary`` | ``shadow`` | ``validator``.
        correlation_id: request-scoped id; falls back to the ambient
            :func:`correlation_scope` and finally to a generated one, so a row
            is never anonymous.
        call_id: per-attempt id; auto-generated when absent.
        chain_position: index within a fallback chain; ``-1`` marks an outer
            wrapper row (kept for the v1 dashboard reader).
        attempt: 1-based retry counter.
        failure_outcome: what to record when the body raises - ``fallthrough``
            when a further provider will be tried, ``exhausted`` otherwise.
        path: telemetry sink; ``None`` uses the default at call time.

    Yields:
        CallScope - call ``set_tokens`` / ``set_outcome`` / ``set_model`` on it.

    Raises:
        Whatever the body raises, unchanged.
    """
    scope = CallScope(
        correlation_id=correlation_id or current_correlation_id() or f"llm_{uuid4().hex[:12]}",
        call_id=call_id or f"llmc_{uuid4().hex[:8]}",
        provider=provider,
        model=model or "",
        purpose=purpose,
        role=role,
        chain_position=chain_position,
        attempt=attempt,
        failure_outcome=failure_outcome,
    )
    started = monotonic()
    try:
        yield scope
    except BaseException as exc:
        record_llm_call(
            provider=scope.provider,
            model=scope.model,
            ok=False,
            latency_ms=(monotonic() - started) * 1000.0,
            role=scope.role,
            error_type=type(exc).__name__,
            path=path,
            correlation_id=scope.correlation_id,
            call_id=scope.call_id,
            purpose=scope.purpose,
            chain_position=scope.chain_position,
            attempt=scope.attempt,
            error_class=classify_error(exc),
            http_status=http_status(exc),
            prompt_tokens=scope.prompt_tokens,
            completion_tokens=scope.completion_tokens,
            outcome=scope.failure_outcome,
        )
        raise
    record_llm_call(
        provider=scope.provider,
        model=scope.model,
        ok=True,
        latency_ms=(monotonic() - started) * 1000.0,
        role=scope.role,
        path=path,
        correlation_id=scope.correlation_id,
        call_id=scope.call_id,
        purpose=scope.purpose,
        chain_position=scope.chain_position,
        attempt=scope.attempt,
        prompt_tokens=scope.prompt_tokens,
        completion_tokens=scope.completion_tokens,
        outcome=scope.success_outcome,
    )
