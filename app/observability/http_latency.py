from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Callable
from threading import Lock
from typing import Protocol, TypedDict

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_S = 600.0
DEFAULT_MAX_SAMPLES_PER_ROUTE = 5000
UNMATCHED_ROUTE = "<unmatched>"


class RouteLatencySnapshot(TypedDict):
    count: int
    p50_s: float
    p95_s: float
    max_s: float


class LatencyRecorder(Protocol):
    def record(self, route: str, duration_s: float) -> None: ...


ClockFn = Callable[[], float]
LatencySample = tuple[float, float]


def _nearest_rank(sorted_values: list[float], quantile: float) -> float:
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, math.ceil(quantile * len(sorted_values)) - 1))
    return sorted_values[index]


def _stable_seconds(value: float) -> float:
    return round(value, 12)


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return UNMATCHED_ROUTE


class SlidingWindowHTTPRouteLatencyRecorder:
    def __init__(
        self,
        *,
        window_s: float = DEFAULT_WINDOW_S,
        max_samples_per_route: int = DEFAULT_MAX_SAMPLES_PER_ROUTE,
        clock: ClockFn = time.monotonic,
    ) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be > 0")
        if max_samples_per_route <= 0:
            raise ValueError("max_samples_per_route must be > 0")
        self.window_s = window_s
        self.max_samples_per_route = max_samples_per_route
        self.clock = clock
        self._samples: dict[str, deque[LatencySample]] = {}
        self._lock = Lock()

    def record(self, route: str, duration_s: float) -> None:
        self.record_at(route, duration_s, self.clock())

    def record_at(self, route: str, duration_s: float, observed_at_s: float) -> None:
        clean_duration = max(0.0, duration_s)
        with self._lock:
            route_samples = self._samples.get(route)
            if route_samples is None:
                route_samples = deque(maxlen=self.max_samples_per_route)
                self._samples[route] = route_samples
            route_samples.append((observed_at_s, clean_duration))
            self._prune_locked(route, observed_at_s)

    def snapshot(self) -> dict[str, RouteLatencySnapshot]:
        now = self.clock()
        result: dict[str, RouteLatencySnapshot] = {}
        with self._lock:
            for route in sorted(self._samples):
                self._prune_locked(route, now)
                values = sorted(duration_s for _, duration_s in self._samples[route])
                if not values:
                    continue
                result[route] = {
                    "count": len(values),
                    "p50_s": _stable_seconds(_nearest_rank(values, 0.50)),
                    "p95_s": _stable_seconds(_nearest_rank(values, 0.95)),
                    "max_s": _stable_seconds(values[-1]),
                }
        return result

    def _prune_locked(self, route: str, now: float) -> None:
        cutoff = now - self.window_s
        route_samples = self._samples[route]
        while route_samples and route_samples[0][0] < cutoff:
            route_samples.popleft()


class HTTPLatencyMiddleware:
    """Pure ASGI middleware; does not wrap or consume response streaming."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        recorder: LatencyRecorder | None = None,
        clock: ClockFn | None = None,
    ) -> None:
        self.app = app
        self._recorder = recorder or HTTP_LATENCY_RECORDER
        recorder_clock = getattr(self._recorder, "clock", None)
        self._clock = clock or (recorder_clock if callable(recorder_clock) else time.monotonic)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = self._clock()
        try:
            await self.app(scope, receive, send)
        finally:
            finished_at = self._clock()
            duration_s = max(0.0, finished_at - started_at)
            try:
                record_at = getattr(self._recorder, "record_at", None)
                if callable(record_at):
                    record_at(_route_template(scope), duration_s, finished_at)
                else:
                    self._recorder.record(_route_template(scope), duration_s)
            except Exception:
                logger.warning("http_latency_record_failed", exc_info=True)


HTTP_LATENCY_RECORDER = SlidingWindowHTTPRouteLatencyRecorder()


def install_http_latency_middleware(
    app: FastAPI,
    *,
    recorder: SlidingWindowHTTPRouteLatencyRecorder = HTTP_LATENCY_RECORDER,
) -> None:
    app.state.http_latency_recorder = recorder
    app.add_middleware(HTTPLatencyMiddleware, recorder=recorder)
