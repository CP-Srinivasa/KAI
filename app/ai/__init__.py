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
    is_retryable_error_class,
    llm_call_scope,
    record_attempt_trace,
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
from app.ai.config import InferenceSettings
from app.ai.gateway import (
    AsyncGatewayOutcome,
    AsyncTransportCall,
    GatewayOutcome,
    TransportCall,
    execute,
    execute_async,
)
from app.ai.models import (
    AttemptResult,
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
    DEFAULT_MAX_ATTEMPTS,
    MAX_ATTEMPTS_CEILING,
    RetryPolicy,
    retry_delay_s,
    should_retry,
    worst_case_backoff_s,
)
from app.ai.routes import ROUTES, Route, route_for
from app.ai.runtime import (
    LiteLLMCallError,
    LiteLLMRequest,
    RoutedValue,
    environment_settings,
    invoke,
    reset_environment_settings,
)

__all__ = [
    "DEFAULT_MODE",
    "MODES",
    "ROUTES",
    "AttemptTrace",
    "AttemptResult",
    "BudgetDecision",
    "BudgetEntry",
    "BudgetPolicy",
    "BudgetState",
    "CircuitBook",
    "CircuitKey",
    "CircuitPolicy",
    "CircuitState",
    "GatewayOutcome",
    "AsyncGatewayOutcome",
    "AsyncTransportCall",
    "TransportCall",
    "CallScope",
    "InferenceResult",
    "InferenceSettings",
    "Mode",
    "Route",
    "ErrorClass",
    "classify_error",
    "correlation_scope",
    "current_correlation_id",
    "http_status",
    "is_retryable_error",
    "is_retryable_error_class",
    "accumulate",
    "circuit_key",
    "cost_known_rate",
    "decide",
    "execute",
    "execute_async",
    "headroom_usd",
    "graduated_routes",
    "has_execution_authority",
    "llm_call_scope",
    "record_attempt_trace",
    "parse_mode",
    "resolve_mode",
    "route_for",
    "total_cost_usd",
    "unknown_route_keys",
    "RetryPolicy",
    "DEFAULT_MAX_ATTEMPTS",
    "MAX_ATTEMPTS_CEILING",
    "worst_case_backoff_s",
    "LiteLLMCallError",
    "LiteLLMRequest",
    "RoutedValue",
    "invoke",
    "environment_settings",
    "reset_environment_settings",
    "retry_delay_s",
    "should_retry",
]
