"""Consumer collection and audit-only acknowledgement orchestration.

Sprint 20 — defines the controlled consumer acknowledgement surface.
Contract: docs/sprint20_consumer_collection_contract.md
Invariants: I-116–I-122

Core principle (I-116):
  Consumer acknowledgement is AUDIT ONLY.
  Acknowledgement ≠ execution. Acknowledgement ≠ approval.
  Consumer state ≠ routing decision (I-117, I-121, I-122).
  No reverse channel into KAI core analysis (I-118, I-120).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Mandatory disclaimer on every acknowledgement artifact (I-116, I-117)
_ACK_AUDIT_NOTE: str = (
    "Acknowledgement is audit only (I-116). "
    "Receipt does not confirm trade intent (I-117). "
    "Consumer state is not a routing decision (I-121). "
    "KAI does not execute trades."
)

# Default JSONL filename for consumer acknowledgements (I-120: append-only)
CONSUMER_ACK_JSONL_FILENAME = "consumer_acknowledgements.jsonl"


@dataclass(frozen=True)
class ConsumerAcknowledgement:
    """Immutable audit record for a consumer's receipt of a SignalHandoff (I-116).

    frozen=True — acknowledgements are append-only and never mutated after creation.
    is_acknowledged is always True — the record only exists when a consumer has
    acknowledged receipt.  This is a receipt, NOT an approval or execution trigger.
    """

    ack_id: str  # UUID generated at creation — unique per acknowledgement (I-118)
    handoff_id: str  # refs SignalHandoff.handoff_id
    signal_id: str  # refs SignalHandoff.signal_id
    consumer_agent_id: str  # opaque identifier of the acknowledging consumer
    visibility_class: str  # from SignalHandoff.delivery_class (I-110)
    acknowledged_at: str  # ISO 8601 timestamp
    is_acknowledged: bool = True  # always True — presence means receipt (I-119)
    audit_note: str = field(default=_ACK_AUDIT_NOTE)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "ack_id": self.ack_id,
            "handoff_id": self.handoff_id,
            "signal_id": self.signal_id,
            "consumer_agent_id": self.consumer_agent_id,
            "visibility_class": self.visibility_class,
            "acknowledged_at": self.acknowledged_at,
            "is_acknowledged": self.is_acknowledged,
            "audit_note": self.audit_note,
        }


def create_consumer_acknowledgement(
    handoff_id: str,
    signal_id: str,
    consumer_agent_id: str,
    *,
    visibility_class: str = "unknown",
) -> ConsumerAcknowledgement:
    """Create an immutable audit-only consumer acknowledgement (I-116, I-118).

    Generates a fresh UUID ack_id. Does NOT modify any existing handoff or signal.
    """
    return ConsumerAcknowledgement(
        ack_id=str(uuid.uuid4()),
        handoff_id=handoff_id,
        signal_id=signal_id,
        consumer_agent_id=consumer_agent_id,
        visibility_class=visibility_class,
        acknowledged_at=datetime.now(UTC).isoformat(),
    )


def append_consumer_acknowledgement(
    ack: ConsumerAcknowledgement,
    output_path: str | Path,
) -> None:
    """Append a ConsumerAcknowledgement to the designated JSONL audit file (I-120)."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ack.to_json_dict()) + "\n")


def load_consumer_acknowledgements(
    input_path: str | Path,
) -> list[ConsumerAcknowledgement]:
    """Load existing consumer acknowledgements from the JSONL audit file."""
    p = Path(input_path)
    if not p.exists():
        return []

    acks: list[ConsumerAcknowledgement] = []
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            ack = ConsumerAcknowledgement(
                ack_id=data["ack_id"],
                handoff_id=data["handoff_id"],
                signal_id=data["signal_id"],
                consumer_agent_id=data["consumer_agent_id"],
                visibility_class=data["visibility_class"],
                acknowledged_at=data["acknowledged_at"],
                is_acknowledged=data.get("is_acknowledged", True),
                audit_note=data.get("audit_note", _ACK_AUDIT_NOTE),
            )
            acks.append(ack)
        except (json.JSONDecodeError, KeyError):
            continue
    return acks


@dataclass(frozen=True)
class ConsumerAuditSummary:
    """Aggregated representation of consumer acknowledgements (I-122).

    This summary ONLY aggregates the audit log. It explicitly enforces
    interface_mode="read_only" to guarantee no structural implications of
    approval, execution, or routing.
    """

    total_handoffs: int
    acknowledged_count: int
    pending_count: int
    acknowledgements_by_consumer: dict[str, int]
    acknowledgements_by_signal: dict[str, int]
    acknowledged_handoffs: list[dict[str, object]]
    interface_mode: str = "read_only"

    def to_json_dict(self) -> dict[str, object]:
        return {
            "total_handoffs": self.total_handoffs,
            "acknowledged_count": self.acknowledged_count,
            "pending_count": self.pending_count,
            "consumers": self.acknowledgements_by_consumer,
            "signals": self.acknowledgements_by_signal,
            "interface_mode": self.interface_mode,
            "acknowledged_handoffs": self.acknowledged_handoffs,
        }


def build_consumer_audit_summary(
    handoffs: list[object],  # expects list[SignalHandoff] but we duck-type for simplicity here
    acknowledgements: list[ConsumerAcknowledgement],
) -> ConsumerAuditSummary:
    """Build the ConsumerAuditSummary from raw handoffs and the acknowledgement audit log."""
    total = len(handoffs)

    counts_by_consumer: dict[str, int] = {}
    counts_by_signal: dict[str, int] = {}
    acknowledged_list: list[dict[str, object]] = []

    for ack in acknowledgements:
        agent = ack.consumer_agent_id
        counts_by_consumer[agent] = counts_by_consumer.get(agent, 0) + 1

        signal = ack.signal_id
        counts_by_signal[signal] = counts_by_signal.get(signal, 0) + 1

        acknowledged_list.append(
            {
                "handoff_id": ack.handoff_id,
                "signal_id": ack.signal_id,
                "consumer_agent_id": ack.consumer_agent_id,
                "acknowledged_at": ack.acknowledged_at,
            }
        )

    recognized_count = len(acknowledgements)
    # Acknowledgements are append-only audit receipts and do not close handoffs.
    pending_count = total

    return ConsumerAuditSummary(
        total_handoffs=total,
        acknowledged_count=recognized_count,
        pending_count=pending_count,
        acknowledgements_by_consumer=counts_by_consumer,
        acknowledgements_by_signal=counts_by_signal,
        acknowledged_handoffs=acknowledged_list,
    )


def build_handoff_collector_summary(
    handoffs: list[object],
    acknowledgements: list[ConsumerAcknowledgement],
) -> ConsumerAuditSummary:
    """Alias for backwards compatibility with tests and CLI."""
    return build_consumer_audit_summary(handoffs, acknowledgements)
