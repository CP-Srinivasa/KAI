"""KYT domain models — enums, transaction context, assessment result.

Privacy by design: ``TransactionContext`` carries only what is needed to assess
transaction risk. Any raw wallet address is pseudonymised (hashed) before it
ever reaches an audit record (see ``audit.py``); the context keeps the raw value
only in-memory for screening and never persists it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class KytRiskLevel(StrEnum):
    """Ordered risk levels. ``UNKNOWN`` means not assessable from available data."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        return {
            KytRiskLevel.LOW: 1,
            KytRiskLevel.MEDIUM: 2,
            KytRiskLevel.HIGH: 3,
            KytRiskLevel.CRITICAL: 4,
            KytRiskLevel.UNKNOWN: 0,
        }[self]


class KytDecision(StrEnum):
    """What the system should do with the transaction."""

    ALLOW = "allow"
    WARN = "warn"
    HOLD = "hold"
    BLOCK = "block"
    MANUAL_REVIEW = "manual_review"

    @property
    def blocks_execution(self) -> bool:
        """True when the transaction must NOT be auto-executed."""
        return self in (KytDecision.HOLD, KytDecision.BLOCK, KytDecision.MANUAL_REVIEW)


class KytCheckPhase(StrEnum):
    PRE_TRANSACTION = "pre_transaction"
    POST_TRANSACTION = "post_transaction"
    HISTORICAL_LOOKBACK = "historical_lookback"


class KytReasonCode(StrEnum):
    """Auditable reason codes attached to every flag and assessment."""

    # Screening (address/entity/symbol/venue)
    SANCTIONED_ENTITY = "sanctioned_entity"
    BLACKLISTED_ADDRESS = "blacklisted_address"
    BLOCKLISTED_SYMBOL = "blocklisted_symbol"
    PRIVACY_COIN = "privacy_coin"
    DELISTED_SYMBOL = "delisted_symbol"
    MIXER_EXPOSURE = "mixer_exposure"
    BRIDGE_ABUSE = "bridge_abuse"
    CHAIN_HOPPING = "chain_hopping"
    DARKNET_LINK = "darknet_link"
    RANSOMWARE_LINK = "ransomware_link"
    SCAM_PHISHING = "scam_phishing"
    STOLEN_FUNDS = "stolen_funds"
    RISKY_JURISDICTION = "risky_jurisdiction"
    VENUE_RISK = "venue_risk"
    NEW_COUNTERPARTY = "new_counterparty"
    # Behavioural (derived from order/fill history)
    STRUCTURING = "structuring"
    ROUND_TRIPPING = "round_tripping"
    FREQUENCY_SPIKE = "frequency_spike"
    AMOUNT_ANOMALY = "amount_anomaly"
    PROFILE_DEVIATION = "profile_deviation"
    # Data / operational
    MISSING_TRAVEL_RULE_DATA = "missing_travel_rule_data"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INSUFFICIENT_DATA = "insufficient_data"
    OK = "ok"


@dataclass(frozen=True)
class KytFlag:
    """A single risk signal raised by a provider or behavioural analyzer."""

    code: KytReasonCode
    level: KytRiskLevel
    detail: str
    source: str  # provider/analyzer name that raised it
    data_available: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "level": self.level.value,
            "detail": self.detail,
            "source": self.source,
            "data_available": self.data_available,
        }


@dataclass(frozen=True)
class TransactionContext:
    """Normalized transaction input for KYT.

    For KAI's exchange-order flow only ``symbol``/``venue``/``side``/``quantity``/
    ``notional_usd`` are reliably present. Address/counterparty/chain are
    optional and default to None → assessed as ``unknown`` rather than fabricated.
    """

    tx_id: str
    phase: KytCheckPhase
    symbol: str | None = None
    venue: str | None = None
    side: str | None = None
    quantity: float | None = None
    notional_usd: float | None = None
    entry_price: float | None = None
    source: str = ""
    correlation_id: str = ""
    counterparty: str | None = None
    wallet_address: str | None = None
    chain: str | None = None
    jurisdiction: str | None = None
    timestamp_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class KytAssessment:
    """Result of a KYT evaluation. Fully serialisable + auditable."""

    tx_id: str
    phase: KytCheckPhase
    risk_level: KytRiskLevel
    decision: KytDecision
    score: int  # 0..100, higher = riskier; UNKNOWN-dominated → 0
    flags: tuple[KytFlag, ...]
    reason_codes: tuple[KytReasonCode, ...]
    provider_sources: tuple[str, ...]
    data_completeness: float  # fraction 0..1 of applicable datapoints present
    recommended_next_step: str
    assessed_at_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, object]:
        return {
            "tx_id": self.tx_id,
            "phase": self.phase.value,
            "risk_level": self.risk_level.value,
            "decision": self.decision.value,
            "score": self.score,
            "flags": [f.to_dict() for f in self.flags],
            "reason_codes": [c.value for c in self.reason_codes],
            "provider_sources": list(self.provider_sources),
            "data_completeness": round(self.data_completeness, 3),
            "recommended_next_step": self.recommended_next_step,
            "assessed_at_utc": self.assessed_at_utc,
        }
