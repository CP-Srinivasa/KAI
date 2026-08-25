from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import TypedDict, overload

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 1.0
DEFAULT_RING_SIZE = 3600
DEFAULT_FLUSH_INTERVAL_S = 60.0
DEFAULT_ARTIFACT_PATH = Path("artifacts/observability/event_loop_lag.jsonl")
MAX_LOG_LINES = 2000
KEEP_LOG_LINES = 1440
SNAPSHOT_WINDOWS_S = (60, 3600)


class LagSnapshot(TypedDict):
    n: int
    p50_s: float
    p95_s: float
    max_s: float
    window_s: int
    last_sample_at_monotonic: float | None


ClockFn = Callable[[], float]
SleepFn = Callable[[float], Awaitable[None]]
UtcNowFn = Callable[[], datetime]
LagSample = tuple[float, float]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _nearest_rank(sorted_values: list[float], quantile: float) -> float:
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, math.ceil(quantile * len(sorted_values)) - 1))
    return sorted_values[index]


class EventLoopLagSampler:
    """In-process event-loop lag sampler with an in-memory ring and JSONL rollup."""

    def __init__(
        self,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        ring_size: int = DEFAULT_RING_SIZE,
        flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
        artifact_path: Path = DEFAULT_ARTIFACT_PATH,
        clock: ClockFn = time.monotonic,
        sleep: SleepFn = asyncio.sleep,
        utc_now: UtcNowFn = _utc_now,
        max_log_lines: int = MAX_LOG_LINES,
        keep_log_lines: int = KEEP_LOG_LINES,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")
        if ring_size <= 0:
            raise ValueError("ring_size must be > 0")
        if flush_interval_s <= 0:
            raise ValueError("flush_interval_s must be > 0")
        if keep_log_lines <= 0 or max_log_lines < keep_log_lines:
            raise ValueError("log rotation bounds are invalid")

        self.interval_s = interval_s
        self.flush_interval_s = flush_interval_s
        self.artifact_path = artifact_path
        self._clock = clock
        self._sleep = sleep
        self._utc_now = utc_now
        self._max_log_lines = max_log_lines
        self._keep_log_lines = keep_log_lines
        self._samples: deque[LagSample] = deque(maxlen=ring_size)
        self._lock = Lock()
        self._task: asyncio.Task[None] | None = None
        self._last_flush_at: float | None = None

    def start(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._last_flush_at = self._clock()
        self._task = asyncio.create_task(self.run(), name="kai-event-loop-lag-sampler")
        return self._task

    async def run(self) -> None:
        if self._last_flush_at is None:
            self._last_flush_at = self._clock()
        while True:
            before = self._clock()
            await self._sleep(self.interval_s)
            after = self._clock()
            lag_s = max(0.0, after - before - self.interval_s)
            self._record_sample(after, lag_s)
            if after - self._last_flush_at >= self.flush_interval_s:
                self._flush_window_60()
                self._last_flush_at = after

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()

    @overload
    def snapshot(self, *, window_s: int) -> LagSnapshot: ...

    @overload
    def snapshot(self, *, window_s: None = None) -> dict[str, LagSnapshot]: ...

    def snapshot(self, *, window_s: int | None = None) -> LagSnapshot | dict[str, LagSnapshot]:
        if window_s is not None:
            return self._snapshot_for_window(window_s)
        return {f"{window}s": self._snapshot_for_window(window) for window in SNAPSHOT_WINDOWS_S}

    def _record_sample(self, sample_at_monotonic: float, lag_s: float) -> None:
        with self._lock:
            self._samples.append((sample_at_monotonic, lag_s))

    def _snapshot_for_window(self, window_s: int) -> LagSnapshot:
        now = self._clock()
        cutoff = now - window_s
        with self._lock:
            window_samples = [
                (sample_at, lag_s) for sample_at, lag_s in self._samples if sample_at >= cutoff
            ]
        values = sorted(lag_s for _, lag_s in window_samples)
        return {
            "n": len(values),
            "p50_s": _nearest_rank(values, 0.50),
            "p95_s": _nearest_rank(values, 0.95),
            "max_s": values[-1] if values else 0.0,
            "window_s": window_s,
            "last_sample_at_monotonic": window_samples[-1][0] if window_samples else None,
        }

    def _flush_window_60(self) -> None:
        snapshot = self._snapshot_for_window(60)
        ts = self._utc_now()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        payload: dict[str, str | int | float] = {
            "ts_utc": ts.astimezone(UTC).isoformat(),
            "window_s": 60,
            "n": snapshot["n"],
            "p50_s": snapshot["p50_s"],
            "p95_s": snapshot["p95_s"],
            "max_s": snapshot["max_s"],
        }
        try:
            self._append_jsonl(payload)
        except Exception:
            logger.warning(
                "event_loop_lag_write_failed",
                exc_info=True,
                extra={"path": str(self.artifact_path)},
            )

    def _append_jsonl(self, payload: dict[str, str | int | float]) -> None:
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        with self.artifact_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        lines = self.artifact_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) <= self._max_log_lines:
            return
        kept = lines[-self._keep_log_lines :]
        tmp_path = self.artifact_path.with_name(f"{self.artifact_path.name}.tmp")
        tmp_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        tmp_path.replace(self.artifact_path)


DEFAULT_EVENT_LOOP_LAG_SAMPLER = EventLoopLagSampler()
