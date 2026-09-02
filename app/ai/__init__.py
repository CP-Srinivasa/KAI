"""AI control plane — the call layer for every LLM interaction in KAI.

Scope boundary (NEO, 2026-09-02): this package owns *observability and chain
policy* for LLM calls. It does NOT own provider construction (that stays in
``app.analysis.factory``), it does NOT own transport (that stays in the
``app.integrations.*`` SDK clients), and it introduces no process, no venv and
no external dependency of its own.

Deliberately NOT part of this package: ``app.intelligence`` (ADR 0015). That
layer's contract is the opposite one — fail-closed, no silent cloud fallback,
``influences_execution=false``. It stays quarantined and untouched.
"""

from app.ai.audit import (
    CallScope,
    ErrorClass,
    classify_error,
    correlation_scope,
    current_correlation_id,
    http_status,
    is_retryable_error,
    llm_call_scope,
)

__all__ = [
    "CallScope",
    "ErrorClass",
    "classify_error",
    "correlation_scope",
    "current_correlation_id",
    "http_status",
    "is_retryable_error",
    "llm_call_scope",
]
