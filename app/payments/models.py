"""Domaenenmodell des Payment Control Plane (ADR 0018 §3).

Alle Typen sind ``frozen`` und ``extra="forbid"``. Beides ist im Geldpfad kein
Stilmittel: ein veraenderbarer Betrag ist ein Betrag ohne Zeuge, und ein
stillschweigend akzeptiertes Zusatzfeld ist eine falsche Annahme des Aufrufers,
die erst am Node auffaellt.

**Vier Betraege bleiben getrennt.** ``amount_requested`` (Intent),
``amount_sent`` (Attempt), ``amount_settled`` und ``fee_actual`` (Settlement).
Der Bestand fuehrte nur ``amount_sat`` — damit ist eine Teilzahlung von einer
vollen nicht unterscheidbar und eine Gebuehr nicht von einem Betrag.

**Keine Lightning-Felder.** Rail-Spezifisches lebt in
``Counterparty.ref_hash`` und ``PaymentAttempt.rail_dedup_key``; der einzige
Rohwert im Modell ist ``PaymentIntent.destination`` — er traegt ``repr=False``
und wird vom Journal nie durchgelassen (``journal._redact_payload``).

**Kein float im Geldpfad.** Betraege sind ganzzahlige minor units, Kurse
ganzzahliges ppm. Ein float-Kurs ist ein Rundungsfehler mit Zeitstempel.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.payments.enums import (
    AUDIT_EVENT_TYPES,
    CounterpartyKind,
    PaymentMode,
    PaymentStatus,
    ProofKind,
    SettlementFinality,
    Verdict,
)
from app.payments.money import (
    FROZEN,
    HASH_LENGTH,
    Asset,
    ExchangeRateReference,
    Fee,
    Money,
    require_aware,
    require_hash,
)


class Counterparty(BaseModel):
    """Die Gegenseite — als Hash, nie als Rohwert (Redaktionsgrenze ADR §9)."""

    model_config = FROZEN

    kind: CounterpartyKind
    ref_hash: str = Field(min_length=1, max_length=HASH_LENGTH)
    display: str = Field(default="", max_length=64)


class Proof(BaseModel):
    """Der Beweis einer Wertbewegung — immer als Hash.

    Fuer Lightning ist ``ref_hash`` der ``payment_hash`` (= SHA-256 des
    Preimage). Das Preimage selbst ist ein Zahlungsbeweis mit Geldwert und hat
    im Domaenenmodell keinen Platz; ``extra="forbid"`` macht das mechanisch.
    """

    model_config = FROZEN

    kind: ProofKind
    ref_hash: str

    @field_validator("ref_hash")
    @classmethod
    def _hash(cls, value: str) -> str:
        return require_hash(value, "ref_hash")


class Quote(BaseModel):
    """Vorschau auf Kosten und Route (ADR §3). Nie eine Zusage."""

    model_config = FROZEN

    rail: str = Field(min_length=1, max_length=32)
    amount: Money
    fee_estimate: Money
    route_hint_hash: str = Field(default="", max_length=HASH_LENGTH)
    valid_until: datetime
    #: Woher die Schaetzung stammt (``node_queryroutes`` / ``settings_ppm`` /
    #: ``simulation``). Eine Schaetzung ohne Herkunft ist eine Zahl ohne Gewicht.
    estimate_source: str = Field(min_length=1, max_length=32)

    @field_validator("valid_until")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "valid_until")


class Invoice(BaseModel):
    """Eine Forderung, die KAI ausgestellt hat (Self-Use-Receivable, ADR §1).

    ``payment_request`` ist die kodierte Aufforderung des Rails (BOLT11). Sie
    ist als einzige Groesse hier kein Hash — und das ist kein Bruch der Regel,
    sondern ihr Zweck: eine Forderung, die man nicht weitergeben kann, ist
    keine Forderung. Sie nennt Betrag, Empfaenger und Ablauf, also genau das,
    was der Zahler wissen MUSS; ein Geheimnis waere das Preimage, und das
    kommt hier nie vor.

    Was daraus folgt, gilt trotzdem: ``repr=False``, damit sie nicht durch ein
    beilaeufiges Log laeuft, und **nie** im Journal — dort steht der
    ``ref_hash``. Die Allowlist in :mod:`app.payments.redaction` erzwingt das
    mechanisch, unabhaengig davon, was ein Aufrufer mitgibt.
    """

    model_config = FROZEN

    rail: str = Field(min_length=1, max_length=32)
    ref_hash: str
    amount: Money
    payee_hash: str
    expires_at: datetime
    memo_hash: str = Field(default="", max_length=HASH_LENGTH)
    payment_request: str = Field(default="", max_length=2048, repr=False)

    @field_validator("ref_hash", "payee_hash")
    @classmethod
    def _hash(cls, value: str) -> str:
        return require_hash(value, "hash")

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "expires_at")


class PaymentIntent(BaseModel):
    """Der einzige Einstieg in eine Wertbewegung (ADR §3)."""

    model_config = FROZEN

    intent_id: str = Field(min_length=3, max_length=64)
    #: Journal-eindeutig (ADR §5). Mindestlaenge 16, damit ein Key nicht raetbar
    #: kurz ist und ein Replay nicht durch Zufall gelingt.
    idempotency_key: str = Field(min_length=16, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=64)
    #: ``operator`` oder ``agent:<id>`` — die Policy prueft Agent-Limits daran.
    actor: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=64)
    rail: str = Field(min_length=1, max_length=32)
    #: Der einzige Rohwert im Modell (BOLT11/Adresse). Nie im ``repr``, nie im
    #: Journal — der Adapter leitet daraus Hashes ab.
    destination: str = Field(min_length=1, repr=False)
    amount_requested: Money
    fee_limit: Money
    created_at: datetime
    expires_at: datetime
    mode: PaymentMode
    status: PaymentStatus = PaymentStatus.REQUESTED
    #: IDs der Regeln, die diesen Intent bisher bewertet haben (ADR §3).
    policy_refs: tuple[str, ...] = ()

    @field_validator("created_at", "expires_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "timestamp")

    @model_validator(mode="after")
    def _check_amounts_and_window(self) -> Self:
        if self.amount_requested.minor_units <= 0:
            raise ValueError("amount_requested must be > 0")
        if self.amount_requested.unit != self.fee_limit.unit:
            raise ValueError(
                f"unit mismatch: amount {self.amount_requested.unit} "
                f"vs fee_limit {self.fee_limit.unit}"
            )
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self


class PaymentAttempt(BaseModel):
    """Ein Sendeversuch. Mehrere Attempts, ein Intent (ADR §3)."""

    model_config = FROZEN

    attempt_no: int = Field(ge=1)
    intent_id: str = Field(min_length=3, max_length=64)
    #: Der Schluessel, unter dem der RAIL dedupliziert (Lightning:
    #: ``payment_hash``). Ohne ihn ist ein Retry ein zweiter Send.
    rail_dedup_key: str = Field(min_length=1, max_length=HASH_LENGTH)
    submitted_at: datetime
    amount_sent: Money | None = None
    fee_actual: Money | None = None
    proof: Proof | None = None

    @field_validator("submitted_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "submitted_at")


class Settlement(BaseModel):
    """Was der Rail beweisbar bewegt hat (ADR §3)."""

    model_config = FROZEN

    intent_id: str = Field(min_length=3, max_length=64)
    attempt_no: int = Field(ge=1)
    amount_settled: Money
    fee_actual: Money
    proof: Proof
    finality: SettlementFinality
    settled_at: datetime

    @field_validator("settled_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "settled_at")


class PaymentPolicyDecision(BaseModel):
    """Ergebnis der Regelkette — mit den Regeln, die es getragen haben."""

    model_config = FROZEN

    verdict: Verdict
    reasons: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()
    evaluated_at: datetime

    @field_validator("evaluated_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "evaluated_at")

    @model_validator(mode="after")
    def _blocking_verdict_needs_a_rule(self) -> Self:
        blocking = {Verdict.DENY, Verdict.REQUIRES_APPROVAL, Verdict.RETRY_DENIED}
        if self.verdict in blocking and not self.rule_ids:
            raise ValueError(f"rule_ids required for verdict {self.verdict}")
        return self


class PaymentAuditEvent(BaseModel):
    """Ein Glied der Journal-Kette (ADR §5/§9).

    Der Record traegt seinen eigenen Hash und den des Vorgaengers. Die
    Berechnung liegt in ``journal.py`` — hier steht nur die Form, damit ein
    Leser einen Record validieren kann, ohne den Writer zu importieren.
    """

    model_config = FROZEN

    seq: int = Field(ge=1)
    ts: datetime
    intent_id: str = Field(min_length=1, max_length=64)
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str
    record_hash: str

    @field_validator("ts")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "ts")

    @field_validator("event_type")
    @classmethod
    def _known_event(cls, value: str) -> str:
        if value not in AUDIT_EVENT_TYPES:
            raise ValueError(f"unknown event_type {value!r} (ADR 0018 §9 is exhaustive)")
        return value

    @field_validator("prev_hash", "record_hash")
    @classmethod
    def _hash(cls, value: str) -> str:
        return require_hash(value, "hash")


__all__ = [
    "AUDIT_EVENT_TYPES",
    "HASH_LENGTH",
    "Asset",
    "Counterparty",
    "CounterpartyKind",
    "ExchangeRateReference",
    "Fee",
    "Invoice",
    "Money",
    "PaymentAttempt",
    "PaymentAuditEvent",
    "PaymentIntent",
    "PaymentMode",
    "PaymentPolicyDecision",
    "PaymentStatus",
    "Proof",
    "ProofKind",
    "Quote",
    "Settlement",
    "SettlementFinality",
    "Verdict",
]
