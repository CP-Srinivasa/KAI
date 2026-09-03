"""Deterministischer Rail fuer SIMULATION und Tests (ADR 0018 §1).

Kein Node, kein Netz, keine Uhr-Abhaengigkeit — und trotzdem ein Rail, der die
unangenehmen Faelle liefert. Das ist der Punkt: ein Simulationsrail, der immer
``SETTLED`` sagt, prueft nur den Happy Path, und der Happy Path ist nicht der,
an dem Geld verloren geht.

**Was das Ziel steuert.** Das Praefix der Destination waehlt den Ausgang,
reproduzierbar und ohne Zufall:

===========================  =================================================
``sim:settle:<x>``           SETTLED sofort, mit Proof
``sim:fail:<x>``             FAILED mit Evidenz (der Node SAGT: nichts geflossen)
``sim:unknown:<x>``          UNKNOWN — Timeout/Transport, keine Aussage
``sim:inflight:<x>``         IN_FLIGHT, wird beim naechsten ``lookup`` SETTLED
alles andere                 SETTLED (der langweilige Normalfall)
===========================  =================================================

``sim:unknown:`` ist der wichtigste Fall der ganzen Datei: er erzeugt genau die
Lage des 25k-Spends vom 07-02 — der Aufruf endet ohne Antwort, und niemand
weiss, ob Geld geflossen ist.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from app.payments.enums import ProofKind, RailOutcome, SettlementFinality
from app.payments.models import Invoice, Money, PaymentAttempt, PaymentIntent, Proof, Quote
from app.payments.rail import (
    DecodedDestination,
    DedupGuarantee,
    InvoiceRequest,
    InvoiceStatus,
    RailAction,
    RailCapabilities,
    RailError,
    RailHealth,
    RailLookup,
    RailPayment,
    RailPaymentList,
    RailResult,
)

_PREFIX_SETTLE = "sim:settle:"
_PREFIX_FAIL = "sim:fail:"
_PREFIX_UNKNOWN = "sim:unknown:"
_PREFIX_INFLIGHT = "sim:inflight:"

#: Fee-Schaetzung: 1000 ppm des Betrags, mindestens 1. Bewusst simpel und
#: sichtbar — eine erfundene Genauigkeit waere schlimmer als eine grobe Zahl.
_SIM_FEE_PPM = 1000


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class SimulationRail:
    """Ein Rail ohne Node. Deterministisch bis auf die Uhr."""

    name = "lightning"

    def __init__(self, *, now: datetime | None = None) -> None:
        #: Feste Zeit fuer reproduzierbare Tests; ``None`` = echte Uhr.
        self._fixed_now = now
        self._inflight: set[str] = set()
        self._payments: list[RailPayment] = []
        self._invoices: dict[str, Invoice] = {}
        self._settled_invoices: dict[str, datetime] = {}

    # -- Testhaken ---------------------------------------------------------- #

    def settle(self, ref_hash: str) -> None:
        """Simuliere, dass jemand eine ausgestellte Invoice bezahlt hat.

        Der Empfangspfad hat sonst keinen Ausloeser: eine Invoice wird von
        AUSSEN beglichen, und ohne diesen Haken koennte kein Test den einzigen
        Zustandswechsel pruefen, der beim Self-Use-Receivable zaehlt.
        """
        if ref_hash not in self._invoices:
            raise RailError(f"cannot settle an unknown invoice: {ref_hash[:12]}…")
        self._settled_invoices[ref_hash] = self._now()

    # -- Rail --------------------------------------------------------------- #

    def _now(self) -> datetime:
        return self._fixed_now or datetime.now(UTC)

    def capabilities(self) -> RailCapabilities:
        """Wie Lightning — damit ein Wechsel auf den echten Rail nichts
        umschaltet, was die Policy vorher anders bewertet hat."""
        return RailCapabilities(
            name=self.name,
            settlement_finality=SettlementFinality.INSTANT,
            confirmation_depth_required=0,
            reversal_supported=False,
            dedup_guarantee=DedupGuarantee.BY_PAYMENT_HASH,
            max_inflight_window=timedelta(days=1),
            fee_model="routing_fee",
            supported_actions=frozenset({RailAction.PAY_INVOICE, RailAction.CREATE_INVOICE}),
        )

    async def health(self) -> RailHealth:
        return RailHealth(
            rail=self.name,
            reachable=True,
            synced_to_chain=True,
            synced_to_graph=True,
            wallet_locked=False,
            observed_at=self._now(),
            reason="simulation",
        )

    async def decode(self, destination: str) -> DecodedDestination:
        target = destination.strip()
        if not target:
            raise RailError("empty destination cannot be decoded")
        return DecodedDestination(
            rail=self.name,
            kind="ln_invoice",
            payee_hash=_sha(f"payee:{target}"),
            rail_dedup_key=_sha(f"dedup:{target}"),
            expires_at=self._now() + timedelta(hours=1),
        )

    async def quote(self, intent: PaymentIntent) -> Quote:
        fee = max(1, intent.amount_requested.minor_units * _SIM_FEE_PPM // 1_000_000)
        return Quote(
            rail=self.name,
            amount=intent.amount_requested,
            fee_estimate=Money(
                minor_units=fee,
                currency=intent.amount_requested.currency,
                scale=intent.amount_requested.scale,
            ),
            valid_until=self._now() + timedelta(minutes=5),
            estimate_source="simulation",
        )

    async def pay(self, intent: PaymentIntent, attempt: PaymentAttempt) -> RailResult:
        destination = intent.destination.strip()
        key = attempt.rail_dedup_key
        moment = self._now()
        fee = Money(
            minor_units=max(1, intent.amount_requested.minor_units * _SIM_FEE_PPM // 1_000_000),
            currency=intent.amount_requested.currency,
            scale=intent.amount_requested.scale,
        )

        if destination.startswith(_PREFIX_UNKNOWN):
            # Kein Proof, kein Betrag, kein failure_reason: wir wissen NICHTS.
            return RailResult(
                rail=self.name,
                outcome=RailOutcome.UNKNOWN,
                rail_dedup_key=key,
                observed_at=moment,
                raw_status="timeout",
            )
        if destination.startswith(_PREFIX_FAIL):
            return RailResult(
                rail=self.name,
                outcome=RailOutcome.FAILED,
                rail_dedup_key=key,
                observed_at=moment,
                failure_reason="NO_ROUTE",
                raw_status="FAILED",
            )
        if destination.startswith(_PREFIX_INFLIGHT):
            self._inflight.add(key)
            return RailResult(
                rail=self.name,
                outcome=RailOutcome.IN_FLIGHT,
                rail_dedup_key=key,
                observed_at=moment,
                raw_status="IN_FLIGHT",
            )
        self.inject_payment(key, amount=intent.amount_requested)
        return RailResult(
            rail=self.name,
            outcome=RailOutcome.SETTLED,
            rail_dedup_key=key,
            observed_at=moment,
            amount_sent=intent.amount_requested,
            fee_actual=fee,
            proof=Proof(kind=ProofKind.PREIMAGE, ref_hash=_sha(f"proof:{key}")),
            raw_status="SUCCEEDED",
        )

    async def lookup(self, rail_dedup_key: str) -> RailLookup:
        """Ein zuvor in-flight gegangener Send ist beim Nachfragen angekommen."""
        moment = self._now()
        if rail_dedup_key in self._inflight:
            self._inflight.discard(rail_dedup_key)
            return RailLookup(
                rail=self.name,
                found=True,
                outcome=RailOutcome.SETTLED,
                rail_dedup_key=rail_dedup_key,
                observed_at=moment,
                proof=Proof(kind=ProofKind.PREIMAGE, ref_hash=_sha(f"proof:{rail_dedup_key}")),
            )
        # Nicht gefunden heisst UNBEKANNT, nicht "gescheitert": die Simulation
        # haelt keine Historie, und ein erfundenes FAILED wuerde einen Retry
        # freigeben, den kein Node gedeckt hat.
        return RailLookup(
            rail=self.name,
            found=False,
            outcome=RailOutcome.UNKNOWN,
            rail_dedup_key=rail_dedup_key,
            observed_at=moment,
        )

    async def list_payments(self, since: datetime) -> RailPaymentList:
        """Was dieser Rail bewegt hat — inklusive dem, was KAI nie beauftragt hat.

        Der Simulationsrail fuehrt eine eigene Liste, in die ``pay`` seine
        Settlements eintraegt und in die ein Test ueber :meth:`inject_payment`
        eine FREMDE Zahlung legen kann. Ohne diesen Haken haette die
        Rueckwaerts-Richtung des Reconcilers keinen Ausloeser, und ein
        Waisen-Settlement waere nur behauptet statt gepruefet.
        """
        return RailPaymentList(
            rail=self.name,
            payments=tuple(p for p in self._payments if p.observed_at >= since),
            window_enforced=True,
            complete=True,
        )

    def inject_payment(self, rail_dedup_key: str, *, amount: Money | None = None) -> None:
        """Testhaken: eine Zahlung am Rail, die KEIN Intent begruendet hat."""
        self._payments.append(
            RailPayment(
                rail=self.name,
                rail_dedup_key=rail_dedup_key,
                outcome=RailOutcome.SETTLED,
                observed_at=self._now(),
                amount_sent=amount,
            )
        )

    async def create_invoice(self, request: InvoiceRequest) -> Invoice:
        moment = self._now()
        ref_hash = _sha(
            f"invoice:{request.purpose}:{request.amount.minor_units}:{len(self._invoices)}"
        )
        invoice = Invoice(
            rail=self.name,
            ref_hash=ref_hash,
            amount=request.amount,
            payee_hash=_sha("payee:self"),
            expires_at=moment + timedelta(seconds=request.expiry_seconds),
            memo_hash=request.memo_hash,
        )
        self._invoices[ref_hash] = invoice
        return invoice

    async def invoice_status(self, ref_hash: str) -> InvoiceStatus:
        invoice = self._invoices.get(ref_hash)
        settled_at = self._settled_invoices.get(ref_hash)
        return InvoiceStatus(
            rail=self.name,
            ref_hash=ref_hash,
            settled=settled_at is not None,
            observed_at=self._now(),
            amount_paid=invoice.amount if (invoice and settled_at) else None,
            settled_at=settled_at,
        )


__all__ = ["SimulationRail"]
