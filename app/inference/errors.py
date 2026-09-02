"""Typed operational-inference failures."""

from __future__ import annotations


class InferenceError(RuntimeError):
    """Base error for the operational inference layer."""


class InferenceConfigurationError(InferenceError):
    """The requested logical route has no valid configuration."""


class InferenceBudgetExceededError(InferenceError):
    """A configured hard budget refused the request."""


class InferenceCircuitOpenError(InferenceError):
    """A provider/model circuit is open and cannot accept the request."""


class InferenceExhaustedError(InferenceError):
    """Every bounded retry/fallback attempt failed."""

    def __init__(self, message: str, *, reasons: list[str]) -> None:
        super().__init__(message)
        self.reasons = tuple(reasons)


class GatewayAttemptError(InferenceError):
    """One gateway attempt failed with explicit retry/fallback semantics."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        retryable: bool,
        fallback_allowed: bool = True,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable
        self.fallback_allowed = fallback_allowed
        self.error_type = error_type or type(self).__name__
