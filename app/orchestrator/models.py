"""Trading loop models and read-only control-plane summaries."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class CycleStatus(StrEnum):
    COMPLETED = "completed"
    NO_MARKET_DATA = "no_market_data"
    STALE_DATA = "stale_data"  # market data received but freshness threshold exceeded
    NO_SIGNAL = "no_signal"
    RISK_REJECTED = "risk_rejected"
    SIZE_REJECTED = "size_rejected"
    ORDER_FAILED = "order_failed"
    ERROR = "error"


def _new_cycle_id() -> str:
    return f"cyc_{uuid.uuid4().hex[:12]}"


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class LoopCycle:
    """
    Immutable audit record for one trading loop cycle.

    Represents the complete outcome of a single run_cycle() call.
    Written to JSONL audit log on every cycle (including non-trades).

    Design invariants:
    - Immutable after creation
    - Every field has a defined default (no required fields beyond identity)
    - status is the authoritative outcome descriptor
    """

    cycle_id: str
    started_at: str
    completed_at: str
    symbol: str
    status: CycleStatus

    # Step outcomes
    market_data_fetched: bool = False
    signal_generated: bool = False
    risk_approved: bool = False
    order_created: bool = False
    fill_simulated: bool = False

    # Traceability IDs (None = step not reached)
    decision_id: str | None = None
    risk_check_id: str | None = None
    order_id: str | None = None

    # Notes and violations
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LoopStatusSummary:
    """Read-only status surface for explicit run-once loop control."""

    mode: str
    run_once_allowed: bool
    run_once_block_reason: str | None
    total_cycles: int
    last_cycle_id: str | None
    last_cycle_status: str | None
    last_cycle_symbol: str | None
    last_cycle_completed_at: str | None
    audit_path: str
    auto_loop_enabled: bool = False
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "trading_loop_status_summary",
            "mode": self.mode,
            "run_once_allowed": self.run_once_allowed,
            "run_once_block_reason": self.run_once_block_reason,
            "total_cycles": self.total_cycles,
            "last_cycle_id": self.last_cycle_id,
            "last_cycle_status": self.last_cycle_status,
            "last_cycle_symbol": self.last_cycle_symbol,
            "last_cycle_completed_at": self.last_cycle_completed_at,
            "audit_path": self.audit_path,
            "auto_loop_enabled": self.auto_loop_enabled,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


@dataclass(frozen=True)
class RecentCyclesSummary:
    """Read-only recent-cycle audit projection for operator visibility."""

    total_cycles: int
    status_counts: dict[str, int]
    recent_cycles: tuple[dict[str, object], ...]
    last_n: int
    audit_path: str
    auto_loop_enabled: bool = False
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "recent_trading_cycles_summary",
            "total_cycles": self.total_cycles,
            "status_counts": dict(self.status_counts),
            "recent_cycles": [dict(row) for row in self.recent_cycles],
            "last_n": self.last_n,
            "audit_path": self.audit_path,
            "auto_loop_enabled": self.auto_loop_enabled,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }
