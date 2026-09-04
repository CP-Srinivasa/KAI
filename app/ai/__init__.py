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
from app.ai.budget import (
    BudgetDecision,
    BudgetEntry,
    BudgetPolicy,
    BudgetState,
    accumulate,
    decide,
    headroom_usd,
)
from app.ai.circuit import (
    CircuitBook,
    CircuitKey,
    CircuitPolicy,
    CircuitState,
    circuit_key,
)
from app.ai.gateway import GatewayOutcome, TransportCall, execute
from app.ai.models import (
    AttemptTrace,
    InferenceResult,
    cost_known_rate,
    total_cost_usd,
)
from app.ai.modes import (
    DEFAULT_MODE,
    MODES,
    Mode,
    graduated_routes,
    has_execution_authority,
    parse_mode,
    resolve_mode,
    unknown_route_keys,
)
from app.ai.retry import (
    MAX_ATTEMPTS_CEILING,
    RetryPolicy,
    delay_before_attempt,
    is_retryable_class,
    should_retry,
    total_backoff_s,
)
from app.ai.routes import ROUTES, Route, route_for

__all__ = [
    "DEFAULT_MODE",
    "MODES",
    "ROUTES",
    "AttemptTrace",
    "BudgetDecision",
    "BudgetEntry",
    "BudgetPolicy",
    "BudgetState",
    "CircuitBook",
    "CircuitKey",
    "CircuitPolicy",
    "CircuitState",
    "GatewayOutcome",
    "TransportCall",
    "CallScope",
    "InferenceResult",
    "Mode",
    "MAX_ATTEMPTS_CEILING",
    "Route",
    "RetryPolicy",
    "ErrorClass",
    "classify_error",
    "correlation_scope",
    "current_correlation_id",
    "http_status",
    "is_retryable_class",
    "is_retryable_error",
    "accumulate",
    "circuit_key",
    "cost_known_rate",
    "decide",
    "delay_before_attempt",
    "execute",
    "headroom_usd",
    "graduated_routes",
    "has_execution_authority",
    "llm_call_scope",
    "parse_mode",
    "resolve_mode",
    "route_for",
    "should_retry",
    "total_backoff_s",
    "total_cost_usd",
    "unknown_route_keys",
]
