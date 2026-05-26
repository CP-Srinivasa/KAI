"""Loop idle-signal — detect a silently-rejecting trading loop.

The 2026-05-26 daily-strategy review surfaced 66/66 cycles since the
previous afternoon as ``priority_rejected`` — formally healthy
("cron is running, file is fresh") but operationally inert. Without a
positive idle signal the operator has no automated way to catch the
case where the loop runs every 10 minutes and rejects every cycle on
the same gate.

This module reads the canonical trading-loop audit JSONL and returns a
single, fixture-friendly summary. It is read-only — no side effects, no
notifications. The CLI wrapper in ``app/cli/commands/trading.py``
turns the signal into an exit code and (optionally) a Telegram ping.

Idle semantics:
    A run window of length ``window_hours`` is idle when
      total_cycles >= min_cycles  AND
      priority_rejected / total_cycles >= idle_fraction  AND
      completed == 0
    The third clause is the load-bearing one — a window that contains
    any completed cycle is by definition not idle, even when the
    priority-gate is rejecting most candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from app.orchestrator.models import CycleStatus
from app.orchestrator.trading_loop import _AUDIT_LOG, load_trading_loop_cycles


@dataclass(frozen=True)
class LoopIdleSignal:
    """Result of a loop-idle-check over a rolling window.

    Status:
        healthy           — window has data AND at least one completed cycle
                            (or rejection rate below ``idle_fraction``).
        idle              — every threshold tripped, see ``reason`` for the
                            specific combination.
        insufficient_data — total_cycles < min_cycles; cannot decide yet.
    """

    status: Literal["healthy", "idle", "insufficient_data"]
    reason: str
    window_hours: int
    window_start_utc: str
    window_end_utc: str
    total_cycles: int
    completed: int
    priority_rejected: int
    other_rejected: int
    rejection_fraction: float | None
    min_cycles: int
    idle_fraction: float
    audit_path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "window_hours": self.window_hours,
            "window_start_utc": self.window_start_utc,
            "window_end_utc": self.window_end_utc,
            "total_cycles": self.total_cycles,
            "completed": self.completed,
            "priority_rejected": self.priority_rejected,
            "other_rejected": self.other_rejected,
            "rejection_fraction": self.rejection_fraction,
            "min_cycles": self.min_cycles,
            "idle_fraction": self.idle_fraction,
            "audit_path": self.audit_path,
        }


def compute_loop_idle_signal(
    *,
    audit_path: str | Path = _AUDIT_LOG,
    window_hours: int = 24,
    idle_fraction: float = 0.95,
    min_cycles: int = 6,
    now_utc: datetime | None = None,
) -> LoopIdleSignal:
    """Build the idle signal from the canonical trading-loop audit.

    ``now_utc`` is exposed for tests; in production the wall clock is
    used. ``min_cycles=6`` matches one hour at the 10-min cron cadence;
    ``window_hours=24`` matches the cron-control horizon. ``idle_fraction``
    is conservative on purpose — we only fire when essentially every
    cycle in the window was a priority-gate reject. A noisier signal
    would alert on normal quiet markets.
    """
    if not 0.0 < idle_fraction <= 1.0:
        raise ValueError("idle_fraction must be in (0.0, 1.0]")
    if min_cycles < 1:
        raise ValueError("min_cycles must be >= 1")
    if window_hours < 1:
        raise ValueError("window_hours must be >= 1")

    window_end = now_utc if now_utc is not None else datetime.now(UTC)
    window_start = window_end - timedelta(hours=window_hours)
    window_start_iso = window_start.isoformat()
    window_end_iso = window_end.isoformat()

    audit_path_resolved = Path(audit_path)
    records = load_trading_loop_cycles(audit_path_resolved)

    total = 0
    completed = 0
    priority_rejected = 0
    other_rejected = 0
    for record in records:
        started_raw = record.get("started_at")
        if not isinstance(started_raw, str) or started_raw < window_start_iso:
            continue
        if started_raw > window_end_iso:
            continue
        total += 1
        status = str(record.get("status", "unknown"))
        if status == CycleStatus.COMPLETED.value:
            completed += 1
        elif status == CycleStatus.PRIORITY_REJECTED.value:
            priority_rejected += 1
        elif status.endswith("_rejected") or status in {
            CycleStatus.ORDER_FAILED.value,
            CycleStatus.ERROR.value,
        }:
            other_rejected += 1

    rejection_fraction: float | None = None
    if total > 0:
        rejection_fraction = priority_rejected / total

    if total < min_cycles:
        return LoopIdleSignal(
            status="insufficient_data",
            reason=f"total_cycles<{min_cycles}",
            window_hours=window_hours,
            window_start_utc=window_start_iso,
            window_end_utc=window_end_iso,
            total_cycles=total,
            completed=completed,
            priority_rejected=priority_rejected,
            other_rejected=other_rejected,
            rejection_fraction=rejection_fraction,
            min_cycles=min_cycles,
            idle_fraction=idle_fraction,
            audit_path=str(audit_path_resolved),
        )

    if completed > 0:
        return LoopIdleSignal(
            status="healthy",
            reason=f"completed_cycles_present:{completed}",
            window_hours=window_hours,
            window_start_utc=window_start_iso,
            window_end_utc=window_end_iso,
            total_cycles=total,
            completed=completed,
            priority_rejected=priority_rejected,
            other_rejected=other_rejected,
            rejection_fraction=rejection_fraction,
            min_cycles=min_cycles,
            idle_fraction=idle_fraction,
            audit_path=str(audit_path_resolved),
        )

    # completed == 0 from here. Decide on rejection fraction.
    assert rejection_fraction is not None  # total >= min_cycles >= 1
    if rejection_fraction >= idle_fraction:
        return LoopIdleSignal(
            status="idle",
            reason=(
                f"priority_rejected_share:{rejection_fraction:.2f}"
                f">=idle_fraction:{idle_fraction:.2f}"
            ),
            window_hours=window_hours,
            window_start_utc=window_start_iso,
            window_end_utc=window_end_iso,
            total_cycles=total,
            completed=0,
            priority_rejected=priority_rejected,
            other_rejected=other_rejected,
            rejection_fraction=rejection_fraction,
            min_cycles=min_cycles,
            idle_fraction=idle_fraction,
            audit_path=str(audit_path_resolved),
        )

    return LoopIdleSignal(
        status="healthy",
        reason=(
            f"priority_rejected_share:{rejection_fraction:.2f}<idle_fraction:{idle_fraction:.2f}"
        ),
        window_hours=window_hours,
        window_start_utc=window_start_iso,
        window_end_utc=window_end_iso,
        total_cycles=total,
        completed=0,
        priority_rejected=priority_rejected,
        other_rejected=other_rejected,
        rejection_fraction=rejection_fraction,
        min_cycles=min_cycles,
        idle_fraction=idle_fraction,
        audit_path=str(audit_path_resolved),
    )


__all__ = ["LoopIdleSignal", "compute_loop_idle_signal"]
