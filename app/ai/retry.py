"""Bounded retry mechanics for transports governed by :mod:`app.ai`.

The decision whether a failure is retryable remains in ``app.ai.audit``.
This module only owns the finite attempt count and bounded delay calculation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from app.ai.audit import is_retryable_error_class
from app.ai.models import AttemptTrace

DEFAULT_MAX_ATTEMPTS: Final = 3


@dataclass(frozen=True)
class RetryPolicy:
    """Finite exponential backoff; defaults to one call plus two retries."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_backoff_s: float = 0.25
    max_backoff_s: float = 2.0
    max_jitter_s: float = 0.1

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= DEFAULT_MAX_ATTEMPTS:
            raise ValueError("max_attempts must be between 1 and 3")
        if self.base_backoff_s < 0 or self.max_backoff_s < 0 or self.max_jitter_s < 0:
            raise ValueError("retry delays must be non-negative")


def should_retry(attempt: AttemptTrace) -> bool:
    """Use the one audit taxonomy for a returned transport attempt."""
    raw_status = attempt.detail.get("status_code")
    status = raw_status if isinstance(raw_status, int) else None
    return is_retryable_error_class(attempt.error_class, status)


def retry_delay_s(
    failed_attempt_number: int,
    policy: RetryPolicy,
    *,
    jitter: Callable[[], float] = lambda: 0.0,
) -> float:
    """Delay before the next attempt, bounded even for a hostile jitter source."""
    exponential = float(policy.base_backoff_s * (2 ** max(0, failed_attempt_number - 1)))
    raw_jitter = jitter()
    jitter_value = float(raw_jitter) if isinstance(raw_jitter, (int, float)) else 0.0
    bounded_jitter = min(policy.max_jitter_s, max(0.0, jitter_value))
    return float(min(policy.max_backoff_s, exponential + bounded_jitter))


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "RetryPolicy",
    "retry_delay_s",
    "should_retry",
]
