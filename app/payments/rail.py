"""Das Rail-Interface (ADR 0018 §7).

Ein Rail ist ein Adapter, kein Teilhaber: er kennt ``app.payments`` nicht, er
wird von hier aus benutzt. Was er ueber sich SAGEN muss, steht in
:class:`RailCapabilities` — und zwar so, dass die Policy daraus entscheiden
kann, ohne Lightning zu kennen.

**Warum Capabilities und nicht `if rail == "lightning"`.** Die vier Annahmen,
die Lightning stillschweigend erfuellt, gelten fuer keinen zweiten Rail:
Settlement ist sofort und endgueltig, es gibt keine Rueckbuchung, der Node
dedupliziert ueber den ``payment_hash``, und eine Zahlung ist entweder
gescheitert oder durch. SEPA (kein Modul, nur als Beleg, dass das Modell
traegt) waere: ``BUSINESS_DAYS`` · ``reversal_supported=True`` · 8 Wochen
Rueckbuchungsfenster · ``BY_RAIL_KEY``. Wer diese Annahmen im Control Plane
verdrahtet, kann sie spaeter nicht mehr finden.

**Was ein Rail NIE tut:** einen Timeout als Fehlschlag melden. Eine
ausbleibende Antwort ist keine Aussage; sie ist :attr:`RailOutcome.UNKNOWN`
und fuehrt zu ``RECONCILIATION_REQUIRED``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator

from app.payments.enums import CounterpartyKind, RailOutcome, SettlementFinality
from app.payments.models import Invoice, Money, PaymentAttempt, PaymentIntent, Proof, Quote
from app.payments.money import FROZEN, HASH_LENGTH, require_aware
from app.payments.rail_invoice import (
    MAX_MEMO_LENGTH,
    MEMO_PREFIX,
    InvoiceRequest,
    InvoiceStatus,
)


class RailAction(StrEnum):
    """Was ein Rail koennen KANN. v0.1 erlaubt zwei davon (ADR §1)."""

    PAY_INVOICE = "pay_invoice"
    CREATE_INVOICE = "create_invoice"
    KEYSEND = "keysend"
    SEND_COINS = "send_coins"
    OPEN_CHANNEL = "open_channel"
    CLOSE_CHANNEL = "close_channel"


class DedupGuarantee(StrEnum):
    """Wie ein Rail einen doppelten Send verhindert — oder eben nicht.

    ``NONE`` ist der gefaehrlichste Wert und deshalb der Default in jeder
    Zweifelsfrage: keysend erzeugt bei jedem Aufruf ein frisches Preimage, also
    einen neuen ``payment_hash``, und on-chain gibt es ueberhaupt keine
    Wiederholungssperre.
    """

    NONE = "NONE"
    BY_RAIL_KEY = "BY_RAIL_KEY"
    BY_PAYMENT_HASH = "BY_PAYMENT_HASH"


class CaptureModel(StrEnum):
    IMMEDIATE = "IMMEDIATE"
    AUTH_CAPTURE = "AUTH_CAPTURE"


class RailCapabilities(BaseModel):
    """Die Selbstauskunft eines Rails — Entscheidungsgrundlage der Policy."""

    model_config = FROZEN

    name: str = Field(min_length=1, max_length=32)
    settlement_finality: SettlementFinality
    confirmation_depth_required: int = Field(default=0, ge=0)
    reversal_supported: bool = False
    reversal_window: timedelta | None = None
    dedup_guarantee: DedupGuarantee = DedupGuarantee.NONE
    #: Wie lange eine Zahlung unterwegs sein kann, bevor sie sicher tot ist.
    #: Fuer Lightning aus der CLTV-Obergrenze, nicht aus dem Invoice-Ablauf:
    #: ein steckengebliebener HTLC haengt Tage, die Invoice ist nach einer
    #: Stunde nur noch unbezahlbar (Red-Team D-05).
    max_inflight_window: timedelta = timedelta(days=1)
    capture_model: CaptureModel = CaptureModel.IMMEDIATE
    batch_semantics: str = Field(default="none", max_length=32)
    fee_model: str = Field(default="unknown", max_length=32)
    supported_actions: frozenset[RailAction] = frozenset()

    def supports(self, action: RailAction) -> bool:
        return action in self.supported_actions


class RailHealth(BaseModel):
    """Zustand des Rails. ``healthy`` ist eine Konjunktion, keine Meinung."""

    model_config = FROZEN

    rail: str = Field(min_length=1, max_length=32)
    reachable: bool = False
    synced_to_chain: bool = False
    synced_to_graph: bool = False
    wallet_locked: bool = True
    observed_at: datetime
    reason: str = Field(default="", max_length=200)

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "observed_at")

    @property
    def healthy(self) -> bool:
        """Nur ein erreichbarer, synchroner, entsperrter Node ist gesund.

        Ein nicht synchroner Node kann eine Route falsch bewerten, ein
        gesperrtes Wallet kann nicht signieren — beides sieht von aussen aus
        wie "der Node antwortet".
        """
        return (
            self.reachable
            and self.synced_to_chain
            and self.synced_to_graph
            and not self.wallet_locked
        )


class DecodedDestination(BaseModel):
    """Was der Rail ueber das Ziel sagt — die Bindung fuer die Allowlist.

    ``payee_hash`` ist nie ``None``: eine Allowlist, die gegen einen
    unbekannten Empfaenger prueft, ist keine Allowlist. Kann der Rail das Ziel
    nicht aufloesen, gibt es kein ``DecodedDestination``, sondern einen Fehler.
    """

    model_config = FROZEN

    rail: str = Field(min_length=1, max_length=32)
    kind: CounterpartyKind
    payee_hash: str = Field(min_length=HASH_LENGTH, max_length=HASH_LENGTH)
    rail_dedup_key: str = Field(min_length=1, max_length=HASH_LENGTH)
    amount: Money | None = None
    expires_at: datetime | None = None
    memo_hash: str = Field(default="", max_length=HASH_LENGTH)


class RailResult(BaseModel):
    """Die Antwort auf einen Send — oder ihr ausdrueckliches Ausbleiben."""

    model_config = FROZEN

    rail: str = Field(min_length=1, max_length=32)
    outcome: RailOutcome
    rail_dedup_key: str = Field(min_length=1, max_length=HASH_LENGTH)
    observed_at: datetime
    amount_sent: Money | None = None
    fee_actual: Money | None = None
    proof: Proof | None = None
    failure_reason: str = Field(default="", max_length=64)
    #: Der rohe Status des Rails, nur zur Diagnose — nie Entscheidungsgrundlage.
    raw_status: str = Field(default="", max_length=32)

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "observed_at")


class RailLookup(BaseModel):
    """Was der Rail SPAETER ueber einen Send sagt (Reconciliation, ADR §8)."""

    model_config = FROZEN

    rail: str = Field(min_length=1, max_length=32)
    found: bool
    outcome: RailOutcome
    rail_dedup_key: str = Field(min_length=1, max_length=HASH_LENGTH)
    observed_at: datetime
    amount_sent: Money | None = None
    fee_actual: Money | None = None
    proof: Proof | None = None
    failure_reason: str = Field(default="", max_length=64)

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "observed_at")


class RailPayment(BaseModel):
    """Eine Zahlung, die der RAIL kennt — Grundlage der Rueckwaerts-Richtung (ADR §8).

    Bewusst schmaler als :class:`RailLookup`: hier wird nicht nach einem
    bekannten Schluessel gefragt, sondern aufgezaehlt, was der Rail bewegt hat.
    Was davon KAI nie beauftragt hat, ist ein Waisen-Settlement.
    """

    model_config = FROZEN

    rail: str = Field(min_length=1, max_length=32)
    rail_dedup_key: str = Field(min_length=1, max_length=HASH_LENGTH)
    outcome: RailOutcome
    observed_at: datetime
    amount_sent: Money | None = None
    fee_actual: Money | None = None

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "observed_at")


class RailPaymentList(BaseModel):
    """Was der Rail im Fenster aufzaehlen konnte — und wie ehrlich das ist.

    ``window_enforced=False`` heisst: der Rail konnte ``since`` NICHT anwenden
    (lnd liefert in ``ListPayments`` keinen Zeitstempel, den
    ``app/lightning/client.py`` durchreicht). Der Reconciler muss dann damit
    rechnen, dass auch Historie von VOR der Inbetriebnahme des Journals
    auftaucht — er meldet jede Waise genau einmal, statt sie zu unterschlagen
    oder bei jedem Lauf neu zu melden.

    ``complete=False`` heisst: die Aufzaehlung wurde durch eine Seitengrenze
    abgeschnitten. Ein "keine Waisen" auf einer abgeschnittenen Liste ist keine
    Zusage, und der Report sagt das.
    """

    model_config = FROZEN

    rail: str = Field(min_length=1, max_length=32)
    payments: tuple[RailPayment, ...] = ()
    window_enforced: bool = False
    complete: bool = True


@runtime_checkable
class PaymentRail(Protocol):
    """Was jeder Rail koennen muss (ADR §7).

    Alle Methoden sind ``async``: ein Rail spricht ueber Netz, und ein
    synchroner Aufruf im Geldpfad blockiert genau den Prozess, der als
    naechstes ein Timeout beurteilen muesste.
    """

    name: str

    def capabilities(self) -> RailCapabilities: ...

    async def health(self) -> RailHealth: ...

    async def decode(self, destination: str) -> DecodedDestination: ...

    async def quote(self, intent: PaymentIntent) -> Quote: ...

    async def pay(self, intent: PaymentIntent, attempt: PaymentAttempt) -> RailResult: ...

    async def lookup(self, rail_dedup_key: str) -> RailLookup: ...

    async def list_payments(self, since: datetime) -> RailPaymentList: ...

    async def create_invoice(self, request: InvoiceRequest) -> Invoice: ...

    async def invoice_status(self, ref_hash: str) -> InvoiceStatus: ...


class RailError(RuntimeError):
    """Der Rail konnte eine Frage nicht beantworten.

    Ausdruecklich NICHT fuer "der Send ist gescheitert" — dafuer gibt es
    :class:`RailResult` mit :attr:`RailOutcome.FAILED`. Eine Exception im
    Sendepfad heisst immer "wir wissen es nicht".
    """


__all__ = [
    "MAX_MEMO_LENGTH",
    "MEMO_PREFIX",
    "CaptureModel",
    "DecodedDestination",
    "DedupGuarantee",
    "InvoiceRequest",
    "InvoiceStatus",
    "PaymentRail",
    "RailAction",
    "RailCapabilities",
    "RailError",
    "RailHealth",
    "RailLookup",
    "RailPayment",
    "RailPaymentList",
    "RailResult",
]
