"""Signal candidate models — immutable, fully typed. (Security First)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

logger = logging.getLogger(__name__)


class SignalDirection(StrEnum):
    LONG = "long"
    SHORT = "short"


class SignalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    CLOSED = "closed"


class IllegalStateTransitionError(ValueError):
    """Raised when a signal state transition violates the FSM rules."""


@dataclass(frozen=True)
class SignalStateTransition:
    """Audit record for a state machine transition."""

    decision_id: str
    from_state: SignalState
    to_state: SignalState
    source: str
    timestamp_utc: str
    reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "decision_id": self.decision_id,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "source": self.source,
            "timestamp_utc": self.timestamp_utc,
            "reason": self.reason,
        }


class SignalStateMachine:
    """FSM rules for signal execution and approval states."""

    # Key: from_state, Value: allowed to_states
    VALID_TRANSITIONS: dict[SignalState, frozenset[SignalState]] = {
        SignalState.PENDING: frozenset(
            {
                SignalState.APPROVED,
                SignalState.REJECTED,
                SignalState.CANCELLED,
            }
        ),
        SignalState.APPROVED: frozenset(
            {
                SignalState.EXECUTED,
                SignalState.REJECTED,
                SignalState.CANCELLED,
            }
        ),
        SignalState.EXECUTED: frozenset(
            {
                SignalState.CLOSED,
            }
        ),
        SignalState.REJECTED: frozenset(),
        SignalState.CANCELLED: frozenset(),
        SignalState.CLOSED: frozenset(),
    }

    @classmethod
    def validate_transition(cls, from_state: SignalState, to_state: SignalState) -> None:
        """Raise IllegalStateTransitionError if the transition is not allowed."""
        if to_state not in cls.VALID_TRANSITIONS.get(from_state, frozenset()):
            raise IllegalStateTransitionError(
                f"Illegal state transition from {from_state.value} to {to_state.value}"
            )


@dataclass(frozen=True)
class SignalProvenance:
    """Origin tag for any signal feeding downstream pipelines.

    D-125 invariant: every TradingView-pivot signal must carry provenance so
    the later quality-bar phase can attribute precision deltas to the right
    signal path (RSS pipeline vs. tradingview_webhook vs. binance_ohlcv_rsi).
    `signal_path_id` is None for audit-only stages (e.g. TV-1 webhook ingest).

    Extended 2026-04-22 (SAT-C-PROV-20260422-001) with attribution-integrity
    fields so the outcome-layer carries beglaubigte Zuordnung, not just
    analysis-time DB joins: ``auth_method`` records how the ingress was
    authenticated, ``ingest_event_id`` pins the concrete upstream event, and
    ``provenance_hash`` is an HMAC-SHA256 seal over the other five fields
    using ``AlertSettings.provenance_secret``.
    """

    source: str  # e.g. "rss", "tradingview_webhook", "binance_ohlcv_rsi"
    version: str  # pivot stage tag, e.g. "tv-1", "tv-2", "tv-3"
    signal_path_id: str | None = None  # set when routed to a concrete pipeline
    auth_method: str | None = None  # "hmac" | "shared_token" | "n/a"
    ingest_event_id: str | None = None  # upstream event identifier
    provenance_hash: str | None = None  # HMAC-SHA256 hex over the other fields

    def to_dict(self) -> dict[str, str]:
        """Serialize to the minimal dict embedded in JSONL rows (non-None only)."""
        d: dict[str, str] = {"source": self.source, "version": self.version}
        if self.signal_path_id is not None:
            d["signal_path_id"] = self.signal_path_id
        if self.auth_method is not None:
            d["auth_method"] = self.auth_method
        if self.ingest_event_id is not None:
            d["ingest_event_id"] = self.ingest_event_id
        if self.provenance_hash is not None:
            d["provenance_hash"] = self.provenance_hash
        return d

    @classmethod
    def from_dict(cls, data: dict[str, object] | None) -> SignalProvenance | None:
        """Parse a nested provenance dict; returns None if missing/invalid."""
        if not isinstance(data, dict):
            return None
        source = data.get("source")
        version = data.get("version")
        if not isinstance(source, str) or not isinstance(version, str):
            return None

        def _opt_str(key: str) -> str | None:
            value = data.get(key)
            return value if isinstance(value, str) else None

        return cls(
            source=source,
            version=version,
            signal_path_id=_opt_str("signal_path_id"),
            auth_method=_opt_str("auth_method"),
            ingest_event_id=_opt_str("ingest_event_id"),
            provenance_hash=_opt_str("provenance_hash"),
        )

    def _hash_payload(self) -> bytes:
        """Canonical HMAC payload — order-stable, None → empty string."""
        parts = [
            self.source,
            self.version,
            self.signal_path_id or "",
            self.auth_method or "",
            self.ingest_event_id or "",
        ]
        return "|".join(parts).encode("utf-8")

    def compute_hash(self, secret: str) -> str:
        """Return HMAC-SHA256 hex of the canonical payload under ``secret``."""
        if not secret:
            return ""
        return hmac.new(secret.encode("utf-8"), self._hash_payload(), hashlib.sha256).hexdigest()

    def with_hash(self, secret: str) -> SignalProvenance:
        """Return a copy with ``provenance_hash`` filled.

        Fail-open: when ``secret`` is empty, returns self unchanged — the hash
        stays ``None`` so downstream consumers can distinguish unbound rows.
        Callers that require a sealed record must check ``provenance_hash is
        not None`` explicitly.
        """
        if not secret:
            return self
        return replace(self, provenance_hash=self.compute_hash(secret))

    def verify_hash(self, secret: str, *, secret_next: str = "") -> bool:
        """Constant-time verification of the stored hash.

        V8.3 rotation: when ``secret_next`` is non-empty, both secrets are
        accepted (primary first, then next). This preserves verifiability of
        historical rows signed under the old secret during a rollover window.
        Both compare operations run unconditionally when the hash + both
        secrets are present — no short-circuit — so a passing verification
        does not leak which key matched via timing.
        """
        if not self.provenance_hash or not secret:
            return False
        primary_match = hmac.compare_digest(self.compute_hash(secret), self.provenance_hash)
        if not secret_next:
            return primary_match
        next_match = hmac.compare_digest(self.compute_hash(secret_next), self.provenance_hash)
        return primary_match or next_match


def _new_decision_id() -> str:
    return f"dec_{uuid.uuid4().hex[:12]}"


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class SignalCandidate:
    """
    Candidate trading signal derived from AnalysisResult + MarketDataPoint.

    Contains all mandatory KAI decision fields (see DECISION_SCHEMA.json).
    Immutable — never mutate after creation.

    Design invariants:
    - All required fields must be populated at construction
    - confidence_score must be in [0.0, 1.0]
    - entry_price must be > 0
    - stop_loss_price is required by default (require_stop_loss=True)
    """

    decision_id: str
    timestamp_utc: str
    symbol: str
    market: str  # "crypto" | "equities" | "forex" | "unknown"
    venue: str  # "paper" | exchange name
    mode: str  # "paper" | "live"
    direction: SignalDirection

    # Mandatory decision fields (KAI Decision Schema)
    thesis: str
    supporting_factors: tuple[str, ...]
    contradictory_factors: tuple[str, ...]
    confidence_score: float  # [0.0, 1.0]
    confluence_count: int

    # Market context
    market_regime: str  # "trending" | "ranging" | "volatile" | "unknown"
    volatility_state: str  # "low" | "normal" | "high" | "extreme"
    liquidity_state: str  # "adequate" | "low" | "critical"

    # Entry / exit
    entry_price: float
    stop_loss_price: float | None
    take_profit_price: float | None
    invalidation_condition: str

    # Risk
    risk_assessment: str
    position_size_rationale: str
    max_loss_estimate_pct: float

    # Traceability
    data_sources_used: tuple[str, ...]
    source_document_id: str
    model_version: str
    prompt_version: str

    # State (default: pending)
    approval_state: SignalState = SignalState.PENDING
    execution_state: SignalState = SignalState.PENDING

    # D-125 provenance: optional, fail-open (None = legacy/RSS path).
    provenance: SignalProvenance | None = None

    def with_execution_state(
        self, new_state: SignalState, source: str, reason: str = ""
    ) -> tuple[SignalCandidate, SignalStateTransition]:
        """Transition execution_state according to FSM rules.

        Returns:
            A tuple of (the updated SignalCandidate, the SignalStateTransition audit record).
        Raises:
            IllegalStateTransitionError: if the transition violates FSM rules.
        """
        SignalStateMachine.validate_transition(self.execution_state, new_state)
        transition = SignalStateTransition(
            decision_id=self.decision_id,
            from_state=self.execution_state,
            to_state=new_state,
            source=source,
            timestamp_utc=_now_utc(),
            reason=reason,
        )
        updated = replace(self, execution_state=new_state)
        return updated, transition
