"""Paper execution engine typed models."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import ExecutionMode
from app.core.file_lock import append_lock
from app.core.schema_runtime import validate_decision_schema_payload
from app.execution.order_intent import ExecutableOrderIntent

logger = logging.getLogger(__name__)

# Backwards-compatible public name used by bridge/lifecycle tests.
OrderIntent = ExecutableOrderIntent


@dataclass(frozen=True)
class PaperOrder:
    """Immutable order record. Never mutated after creation."""

    order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    quantity: float
    order_type: str  # "market" | "limit"
    limit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    created_at: str
    idempotency_key: str
    status: str = "pending"  # "pending" | "filled" | "cancelled" | "rejected"
    risk_check_id: str = ""
    position_side: str = "long"  # "long" | "short"
    # NEO-P-106 Phase 2: venue-Tag fuer Fee-Lookup. Default "paper" nutzt den
    # worst-case Paper-Fee aus config/venue_fees.yaml; "legacy" bleibt als
    # expliziter Opt-out fuer Tests/historische Konstruktor-fee_pct-Pfade.
    venue: str = "paper"
    # Sprint A Lifecycle: Durchgängige Identität
    correlation_id: str = ""
    # 2026-05-12 Premium-Signal-Sprint A: leverage + source durchreichen damit
    # PaperPosition + Frontend sie ohne audit-jsonl-Crosswalk anzeigen kann.
    leverage: float | None = None
    source: str = ""


@dataclass(frozen=True)
class PaperFill:
    """Immutable fill record."""

    fill_id: str
    order_id: str
    symbol: str
    side: str
    quantity: float
    fill_price: float
    fee_usd: float
    filled_at: str
    slippage_pct: float
    pnl_usd: float = 0.0  # NEO-P-101-r2: per-trade NETTO PnL (Buys=0.0, Sells=netto inkl. fee)
    position_side: str = "long"  # "long" | "short"
    # NEO-P-106 Phase 1: additive Audit-Felder fuer Fee-Provenienz (Backwards-compat
    # weil Defaults; Konsumenten lesen via .get() oder ignorieren unbekannte Keys).
    fee_venue: str = "legacy"
    fee_role: str = "taker"
    fee_bps_applied: float = 0.0
    fee_table_version: str = "unknown"
    correlation_id: str = ""


@dataclass
class PaperPosition:
    """Mutable position (updated on fills)."""

    symbol: str
    quantity: float
    avg_entry_price: float
    stop_loss: float | None
    take_profit: float | None
    opened_at: str
    realized_pnl_usd: float = 0.0
    position_side: str = "long"  # "long" | "short"
    # V25-C (2026-05-04): Multi-target staged exits. List of (price, qty_share)
    # tuples where qty_share is the fraction of the ORIGINAL position to close
    # at that price (sums should equal 1.0 for full coverage). Empty list ==
    # legacy single-TP behaviour via stop_loss + take_profit. The list is
    # consumed left-to-right on each tier-trigger; once empty the residual
    # position is exit only via SL or manual close. Sorted ascending by price
    # at construction so the first tier fires first.
    take_profit_tiers: list[tuple[float, float]] = field(default_factory=list)
    # Original quantity captured at fill time so partial closes know what
    # fraction of "the trade" each tier represents even after prior tiers
    # have already reduced the live quantity.
    initial_quantity: float = 0.0
    correlation_id: str = ""
    # 2026-05-12 Premium-Signal-Sprint A. Channel-stated leverage + source-tag
    # für Dashboard-Anzeige. Optional weil pre-Sprint-A audit-records sie nicht
    # haben — audit_replay setzt None/"" als Fallback.
    leverage: float | None = None
    source: str = ""

    def unrealized_pnl(self, current_price: float) -> float:
        if self.position_side == "short":
            return (self.avg_entry_price - current_price) * self.quantity
        return (current_price - self.avg_entry_price) * self.quantity

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "avg_entry_price": self.avg_entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "opened_at": self.opened_at,
            "realized_pnl_usd": self.realized_pnl_usd,
            "position_side": self.position_side,
            "take_profit_tiers": list(self.take_profit_tiers),
            "initial_quantity": self.initial_quantity,
            "correlation_id": self.correlation_id,
            "leverage": self.leverage,
            "source": self.source,
        }


@dataclass
class PaperPortfolio:
    """Portfolio state for paper trading."""

    initial_equity: float
    cash: float
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    realized_pnl_usd: float = 0.0
    total_fees_usd: float = 0.0
    trade_count: int = 0
    _peak_equity: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self._peak_equity = self.initial_equity

    def total_equity(self, prices: dict[str, float]) -> float:
        position_value = 0.0
        for sym, pos in self.positions.items():
            price = prices.get(sym, pos.avg_entry_price)
            if pos.position_side == "short":
                position_value -= pos.quantity * price
            else:
                position_value += pos.quantity * price
        return self.cash + position_value

    def drawdown_pct(self, prices: dict[str, float]) -> float:
        equity = self.total_equity(prices)
        self._peak_equity = max(self._peak_equity, equity)
        if self._peak_equity <= 0:
            return 0.0
        return ((self._peak_equity - equity) / self._peak_equity) * 100

    def daily_pnl_pct(self, prices: dict[str, float]) -> float:
        equity = self.total_equity(prices)
        return ((equity - self.initial_equity) / self.initial_equity) * 100

    def to_dict(self, prices: dict[str, float] | None = None) -> dict[str, object]:
        p = prices or {}
        return {
            "initial_equity": self.initial_equity,
            "cash": self.cash,
            "realized_pnl_usd": self.realized_pnl_usd,
            "total_fees_usd": self.total_fees_usd,
            "trade_count": self.trade_count,
            "open_positions": len(self.positions),
            "positions": {sym: pos.to_dict() for sym, pos in self.positions.items()},
            "total_equity": self.total_equity(p)
            if p
            else self.cash
            + sum(
                (-pos.quantity if pos.position_side == "short" else pos.quantity)
                * pos.avg_entry_price
                for pos in self.positions.values()
            ),
        }


def _new_order_id() -> str:
    return f"ord_{uuid.uuid4().hex[:12]}"


def _new_fill_id() -> str:
    return f"fill_{uuid.uuid4().hex[:12]}"


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


# ─── Lifecycle Aliases (Reconcile 2026-05-10) ─────────────────────────────────
# Operator-Decision: ``SignalStatus`` (app/execution/normalized_signal.py) ist
# kanonisch für die 16-State-Lifecycle. Codex' ursprüngliche
# ``OrderLifecycleState`` und ``LIFECYCLE_TRANSITIONS`` sind hier als Aliases
# / Re-Exports erhalten, damit der Bridge-Code (``envelope_to_paper_bridge.py``)
# unverändert importieren kann.
#
# Cross-Ref: docs/architecture/signal_to_execution_gap_analysis_20260510.md
from app.execution.normalized_signal import (  # noqa: E402
    LIFECYCLE_TRANSITIONS,  # noqa: F401 — re-export
    IllegalLifecycleTransition,  # noqa: F401 — re-export
)
from app.execution.normalized_signal import (  # noqa: E402
    TERMINAL_STATES as TERMINAL_ORDER_LIFECYCLE_STATES,  # noqa: F401 — re-export
)
from app.execution.normalized_signal import (  # noqa: E402
    SignalStatus as OrderLifecycleState,  # noqa: F401 — alias
)


@dataclass(frozen=True)
class LifecycleTransition:
    correlation_id: str
    from_state: OrderLifecycleState
    to_state: OrderLifecycleState
    reason: str
    timestamp_utc: str = field(default_factory=_now_utc)

    def to_dict(self) -> dict[str, object]:
        return {
            "correlation_id": self.correlation_id,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "reason": self.reason,
            "timestamp_utc": self.timestamp_utc,
        }


def validate_lifecycle_transition(
    from_state: OrderLifecycleState,
    to_state: OrderLifecycleState,
) -> None:
    if to_state not in LIFECYCLE_TRANSITIONS[from_state]:
        raise IllegalLifecycleTransition(
            f"illegal lifecycle transition: {from_state.value} -> {to_state.value}"
        )


def make_lifecycle_transition(
    *,
    correlation_id: str,
    from_state: OrderLifecycleState,
    to_state: OrderLifecycleState,
    reason: str,
) -> LifecycleTransition:
    validate_lifecycle_transition(from_state, to_state)
    return LifecycleTransition(
        correlation_id=correlation_id,
        from_state=from_state,
        to_state=to_state,
        reason=reason,
    )


def _new_decision_id() -> str:
    return f"dec_{uuid.uuid4().hex[:12]}"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NOT_REQUIRED = "not_required"
    AUDIT_ONLY = "audit_only"


class DecisionExecutionState(StrEnum):
    NOT_EXECUTABLE = "not_executable"
    QUEUED = "queued"
    PAPER_ONLY = "paper_only"
    SHADOW_ONLY = "shadow_only"
    READY = "ready"
    BLOCKED = "blocked"
    EXECUTED = "executed"
    FAILED = "failed"


class DecisionRiskAssessment(BaseModel):
    """Typed risk summary for a single operator-visible decision record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1)
    risk_level: str = Field(min_length=1)
    blocked_reasons: tuple[str, ...] = ()
    advisory_notes: tuple[str, ...] = ()
    max_position_pct: float | None = Field(default=None, ge=0.0)
    drawdown_remaining_pct: float | None = Field(default=None, ge=0.0)
    kill_switch_active: bool = False


class DecisionLogicBlock(BaseModel):
    """Typed entry/exit logic block with explicit conditions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1)
    conditions: tuple[str, ...] = Field(min_length=1)
    notes: tuple[str, ...] = ()


class DecisionRecord(BaseModel):
    """Immutable, schema-aligned decision contract for KAI decision instances."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str = Field(default_factory=_new_decision_id, min_length=1)
    timestamp_utc: str = Field(default_factory=_now_utc, min_length=1)
    symbol: str = Field(min_length=1)
    market: str = Field(min_length=1)
    venue: str = Field(min_length=1)
    mode: ExecutionMode
    thesis: str = Field(min_length=1)
    supporting_factors: tuple[str, ...] = Field(min_length=1)
    contradictory_factors: tuple[str, ...] = ()
    confidence_score: float = Field(ge=0.0, le=1.0)
    market_regime: str = Field(min_length=1)
    volatility_state: str = Field(min_length=1)
    liquidity_state: str = Field(min_length=1)
    risk_assessment: DecisionRiskAssessment
    entry_logic: DecisionLogicBlock
    exit_logic: DecisionLogicBlock
    stop_loss: float | None = Field(default=None, gt=0.0)
    take_profit: float | None = Field(default=None, gt=0.0)
    invalidation_condition: str = Field(min_length=1)
    position_size_rationale: str = Field(min_length=1)
    max_loss_estimate: float = Field(ge=0.0)
    data_sources_used: tuple[str, ...] = Field(min_length=1)
    model_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    approval_state: ApprovalState = ApprovalState.AUDIT_ONLY
    execution_state: DecisionExecutionState = DecisionExecutionState.NOT_EXECUTABLE

    @field_validator("timestamp_utc")
    @classmethod
    def _validate_timestamp_utc(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("timestamp_utc must be a valid ISO 8601 timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
            raise ValueError("timestamp_utc must be timezone-aware and normalized to UTC")
        return value

    @model_validator(mode="after")
    def _validate_safe_state(self) -> DecisionRecord:
        if self.mode == ExecutionMode.RESEARCH and self.execution_state in {
            DecisionExecutionState.QUEUED,
            DecisionExecutionState.READY,
            DecisionExecutionState.EXECUTED,
        }:
            raise ValueError("Research decisions must remain non-executable or blocked.")
        if self.mode == ExecutionMode.LIVE and self.approval_state != ApprovalState.APPROVED:
            raise ValueError("Live decisions require explicit approved state.")
        if self.mode == ExecutionMode.LIVE and self.execution_state in {
            DecisionExecutionState.NOT_EXECUTABLE,
            DecisionExecutionState.PAPER_ONLY,
            DecisionExecutionState.SHADOW_ONLY,
        }:
            raise ValueError("Live decisions require a live-compatible execution state.")
        if (
            self.approval_state == ApprovalState.REJECTED
            and self.execution_state == DecisionExecutionState.EXECUTED
        ):
            raise ValueError("Rejected decisions must never be marked as executed.")
        validate_decision_schema_payload(self.to_json_dict())
        return self

    def to_json_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def validate_decision_record_payload(payload: Mapping[str, object]) -> DecisionRecord:
    """Validate an untrusted payload into the canonical immutable decision record."""

    return DecisionRecord.model_validate(dict(payload))


def append_decision_record_jsonl(
    output_path: str | Path,
    record: DecisionRecord,
) -> None:
    """Append a single decision record to an audit JSONL stream."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with append_lock(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_json_dict()) + "\n")


def load_decision_records(input_path: str | Path) -> list[DecisionRecord]:
    """Load a decision record stream fail-closed on malformed rows."""

    path = Path(input_path)
    if not path.exists():
        return []

    records: list[DecisionRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.error(
                    "Decision record stream malformed at %s line %s: %s",
                    path,
                    line_number,
                    exc,
                )
                raise ValueError(f"Invalid decision record JSON at line {line_number}") from exc
            try:
                records.append(validate_decision_record_payload(payload))
            except ValueError as exc:
                logger.error(
                    "Decision record validation failed at %s line %s: %s",
                    path,
                    line_number,
                    exc,
                )
                raise ValueError(f"Invalid decision record payload at line {line_number}") from exc
    return records
