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

    # Premium-Fastlane states (Goal 2026-06-05 §16). Immediate-routing lane for
    # authentic premium signals in paper/testnet/demo.
    FASTLANE_RECEIVED = "fastlane_received"
    FASTLANE_VALIDATED = "fastlane_validated"
    FASTLANE_AUTO_APPROVED = "fastlane_auto_approved"
    FASTLANE_BYPASSED_APPROVAL = "fastlane_bypassed_approval"
    FASTLANE_BYPASSED_ALLOWLIST = "fastlane_bypassed_allowlist"
    FASTLANE_BYPASSED_ENTRY_MODE = "fastlane_bypassed_entry_mode"
    FASTLANE_ORDER_INTENT_CREATED = "fastlane_order_intent_created"
    FASTLANE_ORDER_SUBMITTED = "fastlane_order_submitted"
    FASTLANE_BRACKET_ATTACHED = "fastlane_bracket_attached"
    FASTLANE_PENDING_ENTRY = "fastlane_pending_entry"
    FASTLANE_POSITION_OPEN = "fastlane_position_open"
    FASTLANE_PARTIALLY_CLOSED = "fastlane_partially_closed"
    FASTLANE_CLOSED_TP = "fastlane_closed_tp"
    FASTLANE_CLOSED_SL = "fastlane_closed_sl"
    FASTLANE_CLOSED_MANUAL = "fastlane_closed_manual"
    FASTLANE_ORPHAN_COMPLETION = "fastlane_orphan_completion"
    FASTLANE_REQUIRES_SCALE_REVIEW = "fastlane_requires_scale_review"
    FASTLANE_REJECTED_SCHEMA = "fastlane_rejected_schema"
    FASTLANE_REJECTED_DUPLICATE = "fastlane_rejected_duplicate"
    FASTLANE_REJECTED_ROUTING = "fastlane_rejected_routing"


GREEN_STATES = frozenset(
    {
        PremiumSignalState.POSITION_OPEN,
        PremiumSignalState.PARTIALLY_CLOSED,
        PremiumSignalState.CLOSED_TP,
        PremiumSignalState.RECONCILED_COMPLETION,
        PremiumSignalState.FASTLANE_POSITION_OPEN,
        PremiumSignalState.FASTLANE_PARTIALLY_CLOSED,
        PremiumSignalState.FASTLANE_CLOSED_TP,
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
        PremiumSignalState.FASTLANE_RECEIVED,
        PremiumSignalState.FASTLANE_VALIDATED,
        PremiumSignalState.FASTLANE_AUTO_APPROVED,
        PremiumSignalState.FASTLANE_BYPASSED_APPROVAL,
        PremiumSignalState.FASTLANE_BYPASSED_ALLOWLIST,
        PremiumSignalState.FASTLANE_BYPASSED_ENTRY_MODE,
        PremiumSignalState.FASTLANE_ORDER_INTENT_CREATED,
        PremiumSignalState.FASTLANE_ORDER_SUBMITTED,
        PremiumSignalState.FASTLANE_BRACKET_ATTACHED,
        PremiumSignalState.FASTLANE_PENDING_ENTRY,
        PremiumSignalState.FASTLANE_ORPHAN_COMPLETION,
        PremiumSignalState.FASTLANE_REQUIRES_SCALE_REVIEW,
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
        PremiumSignalState.FASTLANE_CLOSED_SL,
        PremiumSignalState.FASTLANE_REJECTED_SCHEMA,
        PremiumSignalState.FASTLANE_REJECTED_DUPLICATE,
        PremiumSignalState.FASTLANE_REJECTED_ROUTING,
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
    if stage == "fastlane_allowlist_bypassed":
        return PremiumSignalState.FASTLANE_BYPASSED_ALLOWLIST
    if stage == "fastlane_entry_mode_bypassed_for_paper":
        return PremiumSignalState.FASTLANE_BYPASSED_ENTRY_MODE
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
        PremiumSignalState.FASTLANE_RECEIVED: "Fastlane empfangen",
        PremiumSignalState.FASTLANE_VALIDATED: "Fastlane validiert",
        PremiumSignalState.FASTLANE_AUTO_APPROVED: "Fastlane auto-freigegeben",
        PremiumSignalState.FASTLANE_BYPASSED_APPROVAL: "Fastlane: Approval übersprungen",
        PremiumSignalState.FASTLANE_BYPASSED_ALLOWLIST: "Fastlane: Allowlist übersprungen",
        PremiumSignalState.FASTLANE_BYPASSED_ENTRY_MODE: "Fastlane: entry_mode übersprungen",
        PremiumSignalState.FASTLANE_ORDER_INTENT_CREATED: "Fastlane Order-Intent erzeugt",
        PremiumSignalState.FASTLANE_ORDER_SUBMITTED: "Fastlane Order gesendet",
        PremiumSignalState.FASTLANE_BRACKET_ATTACHED: "Fastlane SL/TP-Bracket gesetzt",
        PremiumSignalState.FASTLANE_PENDING_ENTRY: "Fastlane wartet auf Entry",
        PremiumSignalState.FASTLANE_POSITION_OPEN: "Fastlane Position eröffnet",
        PremiumSignalState.FASTLANE_PARTIALLY_CLOSED: "Fastlane Teilziel erreicht",
        PremiumSignalState.FASTLANE_CLOSED_TP: "Fastlane Trade abgeschlossen",
        PremiumSignalState.FASTLANE_CLOSED_SL: "Fastlane Stop Loss",
        PremiumSignalState.FASTLANE_CLOSED_MANUAL: "Fastlane manuell geschlossen",
        PremiumSignalState.FASTLANE_ORPHAN_COMPLETION: "Fastlane Completion ohne Match",
        PremiumSignalState.FASTLANE_REQUIRES_SCALE_REVIEW: "Fastlane Skalenprüfung nötig",
        PremiumSignalState.FASTLANE_REJECTED_SCHEMA: "Fastlane Schema abgelehnt",
        PremiumSignalState.FASTLANE_REJECTED_DUPLICATE: "Fastlane Duplikat abgelehnt",
        PremiumSignalState.FASTLANE_REJECTED_ROUTING: "Fastlane Routing abgelehnt",
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
