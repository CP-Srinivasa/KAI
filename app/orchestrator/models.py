"""Trading loop cycle models — immutable audit records. (Security First)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class CycleStatus(StrEnum):
    COMPLETED = "completed"
    NO_MARKET_DATA = "no_market_data"
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
