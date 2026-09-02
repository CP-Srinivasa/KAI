"""Small in-process closed/open/half-open circuit breaker."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from app.inference.errors import InferenceCircuitOpenError


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


@dataclass
class _Circuit:
    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    opened_at: float | None = None
    half_open_in_flight: bool = False


class CircuitBreaker:
    """Per-key circuit state; process restart intentionally resets it."""

    def __init__(
        self,
        *,
        failure_threshold: int,
        recovery_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._clock = clock
        self._circuits: dict[str, _Circuit] = {}

    def before_call(self, key: str) -> CircuitState:
        circuit = self._circuits.setdefault(key, _Circuit())
        if circuit.state is CircuitState.OPEN:
            opened_at = circuit.opened_at
            elapsed = self._clock() - opened_at if opened_at is not None else 0.0
            if elapsed < self._recovery_seconds:
                raise InferenceCircuitOpenError(f"circuit open for {key}")
            circuit.state = CircuitState.HALF_OPEN
            circuit.half_open_in_flight = False
        if circuit.state is CircuitState.HALF_OPEN:
            if circuit.half_open_in_flight:
                raise InferenceCircuitOpenError(f"half-open probe already in flight for {key}")
            circuit.half_open_in_flight = True
        return circuit.state

    def record_success(self, key: str) -> CircuitState:
        circuit = self._circuits.setdefault(key, _Circuit())
        circuit.state = CircuitState.CLOSED
        circuit.failures = 0
        circuit.opened_at = None
        circuit.half_open_in_flight = False
        return circuit.state

    def record_failure(self, key: str) -> CircuitState:
        circuit = self._circuits.setdefault(key, _Circuit())
        circuit.half_open_in_flight = False
        circuit.failures += 1
        if circuit.state is CircuitState.HALF_OPEN or circuit.failures >= self._failure_threshold:
            circuit.state = CircuitState.OPEN
            circuit.opened_at = self._clock()
        return circuit.state

    def state(self, key: str) -> CircuitState:
        return self._circuits.get(key, _Circuit()).state

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {
            key: {
                "state": circuit.state.value,
                "failures": circuit.failures,
                "opened": circuit.opened_at is not None,
            }
            for key, circuit in sorted(self._circuits.items())
        }
