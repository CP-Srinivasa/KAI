"""Canonical premium-signal state semantics.

This module is intentionally small and pure: audit readers and UI endpoints can
derive the same operator-facing state without re-encoding bridge/paper wording.
It does not enable execution and it never bypasses safety gates.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class PremiumSignalState(StrEnum):
    PARSED = "parsed"
    PARSED_OK = "parsed"
    ENVELOPE_ACCEPTED = "envelope_accepted"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    BRIDGE_PENDING = "bridge_pending"
    SOURCE_SKIPPED = "source_skipped"
    BRIDGE_REJECTED = "bridge_rejected"
    ENTRY_DISABLED = "entry_disabled"
    PENDING_ENTRY = "pending_entry"
    PAPER_ORDER_CREATED = "paper_order_created"
    POSITION_OPEN = "position_open"
    PARTIALLY_CLOSED = "partially_closed"
    CLOSED_TP = "closed_tp"
    CLOSED_SL = "closed_sl"
    CLOSED_MANUAL = "closed_manual"
    CLOSED_UNKNOWN = "closed_unknown"
    ORPHAN_COMPLETION = "orphan_completion"
    RECONCILED_COMPLETION = "reconciled_completion"
    INVALID = "invalid"
    REQUIRES_REVIEW = "requires_review"
    REQUIRES_SCALE_REVIEW = "requires_scale_review"
    PAPER_EXECUTION_FAILED = "paper_execution_failed"
    MARKET_DATA_FAILED = "market_data_failed"
    SCALE_REJECTED = "scale_rejected"
    RISK_REJECTED = "risk_rejected"


GREEN_STATES = frozenset(
    {
        PremiumSignalState.POSITION_OPEN,
        PremiumSignalState.PARTIALLY_CLOSED,
        PremiumSignalState.CLOSED_TP,
        PremiumSignalState.RECONCILED_COMPLETION,
    }
)
WARN_STATES = frozenset(
    {
        PremiumSignalState.PARSED_OK,
        PremiumSignalState.ENVELOPE_ACCEPTED,
        PremiumSignalState.AWAITING_APPROVAL,
        PremiumSignalState.APPROVED,
        PremiumSignalState.BRIDGE_PENDING,
        PremiumSignalState.PENDING_ENTRY,
        PremiumSignalState.ORPHAN_COMPLETION,
        PremiumSignalState.REQUIRES_REVIEW,
        PremiumSignalState.REQUIRES_SCALE_REVIEW,
    }
)
RED_STATES = frozenset(
    {
        PremiumSignalState.SOURCE_SKIPPED,
        PremiumSignalState.BRIDGE_REJECTED,
        PremiumSignalState.ENTRY_DISABLED,
        PremiumSignalState.CLOSED_SL,
        PremiumSignalState.CLOSED_UNKNOWN,
        PremiumSignalState.INVALID,
        PremiumSignalState.PAPER_EXECUTION_FAILED,
        PremiumSignalState.MARKET_DATA_FAILED,
        PremiumSignalState.SCALE_REJECTED,
        PremiumSignalState.RISK_REJECTED,
    }
)


def _coerce_state(state: PremiumSignalState | str | None) -> PremiumSignalState | None:
    if isinstance(state, PremiumSignalState):
        return state
    raw = str(state or "").strip()
    if not raw:
        return None
    try:
        return PremiumSignalState(raw)
    except ValueError:
        return PremiumSignalState.__members__.get(raw.upper())


def state_tone(state: PremiumSignalState | str | None) -> str:
    s = _coerce_state(state)
    if s is None:
        return "neutral"
    if s in GREEN_STATES:
        return "pos"
    if s in WARN_STATES:
        return "warn"
    if s in RED_STATES:
        return "neg"
    return "neutral"


def normalized_source(source: str | None) -> str | None:
    if not source:
        return None
    return source.removesuffix("_approved")


def origin_signal_id(record: dict[str, Any]) -> str | None:
    payload = record.get("payload")
    p = payload if isinstance(payload, dict) else {}
    for value in (
        p.get("origin_signal_id"),
        record.get("origin_signal_id"),
        p.get("source_uid"),
        record.get("source_uid"),
        p.get("signal_id"),
        record.get("origin_envelope_id"),
        record.get("envelope_id"),
    ):
        if isinstance(value, str) and value:
            return value
    return None


def approval_state(record: dict[str, Any]) -> str:
    src = record.get("source")
    if isinstance(src, str) and src.endswith("_approved"):
        return "approved"
    if record.get("origin_envelope_id"):
        return "approved"
    if record.get("message_type") == "signal" and record.get("stage") == "accepted":
        return "awaiting_approval"
    return "none"


def bridge_stage_to_state(stage: str | None, reason: str | None = None) -> PremiumSignalState:
    if stage in {"filled", "filled_duplicate_suppressed"}:
        return PremiumSignalState.POSITION_OPEN
    if stage == "pending":
        return PremiumSignalState.PENDING_ENTRY
    if stage == "rejected_entry_mode" or reason in {
        "entry_mode_disabled",
        "premium_paper_execution_disabled",
    }:
        return PremiumSignalState.ENTRY_DISABLED
    if stage == "rejected_risk":
        return PremiumSignalState.RISK_REJECTED
    if stage == "rejected_scale_review":
        return PremiumSignalState.REQUIRES_SCALE_REVIEW
    if stage == "rejected_fill":
        return PremiumSignalState.PAPER_EXECUTION_FAILED
    if stage in {"no_market_data"} or reason == "no_market_data":
        return PremiumSignalState.MARKET_DATA_FAILED
    if stage and stage.startswith("rejected_"):
        return PremiumSignalState.BRIDGE_REJECTED
    if stage == "skipped_source":
        return PremiumSignalState.SOURCE_SKIPPED
    if stage == "expired":
        return PremiumSignalState.REQUIRES_REVIEW
    return PremiumSignalState.APPROVED


def close_reason_to_state(
    reason: str | None, *, realized_pnl_usd: float | None
) -> PremiumSignalState:
    r = (reason or "").lower()
    if "stop" in r:
        return PremiumSignalState.CLOSED_SL
    if "manual" in r:
        return PremiumSignalState.CLOSED_MANUAL
    if realized_pnl_usd is None:
        return PremiumSignalState.CLOSED_UNKNOWN
    return PremiumSignalState.CLOSED_TP


def state_label(state: PremiumSignalState | str | None) -> str:
    s = _coerce_state(state)
    if s is None:
        return "Unbekannt"
    return {
        PremiumSignalState.PARSED: "Geparst",
        PremiumSignalState.PARSED_OK: "Geparst",
        PremiumSignalState.ENVELOPE_ACCEPTED: "Geparst & gespeichert",
        PremiumSignalState.AWAITING_APPROVAL: "Wartet auf Freigabe",
        PremiumSignalState.APPROVED: "Freigegeben",
        PremiumSignalState.BRIDGE_PENDING: "Bridge ausstehend",
        PremiumSignalState.SOURCE_SKIPPED: "Quelle nicht allowlisted",
        PremiumSignalState.BRIDGE_REJECTED: "Bridge abgelehnt",
        PremiumSignalState.ENTRY_DISABLED: "Execution gestoppt",
        PremiumSignalState.PENDING_ENTRY: "Wartet auf Entry",
        PremiumSignalState.PAPER_ORDER_CREATED: "Paper Order erzeugt",
        PremiumSignalState.POSITION_OPEN: "Paper Position eröffnet",
        PremiumSignalState.PARTIALLY_CLOSED: "Teilziel erreicht",
        PremiumSignalState.CLOSED_TP: "Trade abgeschlossen",
        PremiumSignalState.CLOSED_SL: "Stop Loss",
        PremiumSignalState.CLOSED_MANUAL: "Manuell geschlossen",
        PremiumSignalState.CLOSED_UNKNOWN: "Geschlossen, PnL ungeklärt",
        PremiumSignalState.ORPHAN_COMPLETION: "Completion ohne Match",
        PremiumSignalState.RECONCILED_COMPLETION: "Completion reconciled",
        PremiumSignalState.INVALID: "Ungültig",
        PremiumSignalState.REQUIRES_REVIEW: "Prüfung nötig",
        PremiumSignalState.REQUIRES_SCALE_REVIEW: "Skalenprüfung nötig",
        PremiumSignalState.PAPER_EXECUTION_FAILED: "Paper-Ausführung fehlgeschlagen",
        PremiumSignalState.MARKET_DATA_FAILED: "Marktdaten fehlen",
        PremiumSignalState.SCALE_REJECTED: "Skalierung abgelehnt",
        PremiumSignalState.RISK_REJECTED: "Risk-Gate abgelehnt",
    }[s]


__all__ = [
    "GREEN_STATES",
    "PremiumSignalState",
    "RED_STATES",
    "WARN_STATES",
    "approval_state",
    "bridge_stage_to_state",
    "close_reason_to_state",
    "normalized_source",
    "origin_signal_id",
    "state_label",
    "state_tone",
]
