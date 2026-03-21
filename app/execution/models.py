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
from app.schemas.runtime_validator import validate_decision_schema_payload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaperOrder:
    """Immutable order record. Never mutated after creation."""
    order_id: str
    symbol: str
    side: str           # "buy" | "sell"
    quantity: float
    order_type: str     # "market" | "limit"
    limit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    created_at: str
    idempotency_key: str
    status: str = "pending"  # "pending" | "filled" | "cancelled" | "rejected"
    risk_check_id: str = ""


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

    def unrealized_pnl(self, current_price: float) -> float:
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
        position_value = sum(
            p.quantity * prices.get(sym, p.avg_entry_price)
            for sym, p in self.positions.items()
        )
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
            "total_equity": self.total_equity(p) if p else self.cash + sum(
                pos.quantity * pos.avg_entry_price for pos in self.positions.values()
            ),
        }


def _new_order_id() -> str:
    return f"ord_{uuid.uuid4().hex[:12]}"


def _new_fill_id() -> str:
    return f"fill_{uuid.uuid4().hex[:12]}"


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


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
            raise ValueError(
                "Research decisions must remain non-executable or blocked."
            )
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
                raise ValueError(
                    f"Invalid decision record JSON at line {line_number}"
                ) from exc
            try:
                records.append(validate_decision_record_payload(payload))
            except ValueError as exc:
                logger.error(
                    "Decision record validation failed at %s line %s: %s",
                    path,
                    line_number,
                    exc,
                )
                raise ValueError(
                    f"Invalid decision record payload at line {line_number}"
                ) from exc
    return records
