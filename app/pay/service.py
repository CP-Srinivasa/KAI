"""Die Produktschicht ueber der versiegelten Payment Fabric (KAI PAY v0.1).

Ein Mensch oder ein Dienst fordert eine Zahlung an, bezahlt per Lightning, und
KAI bestaetigt den Eingang. Mehr ist es nicht — und alles daran, was mit Geld
zu tun hat, passiert im Kern:

============================  ===============================================
Frage                         Wer antwortet
============================  ===============================================
Wie lautet die Forderung?     ``PaymentService.create_invoice`` (ADR 0018 §1)
Wurde bezahlt?                ``payments.receivables.settle_receivable``
Wie viel, wann, nachweisbar?  ``payments.receivables.settlement_of`` (Journal)
Wann laeuft sie ab?           ``payments.receivables.expiry_of`` (Journal)
============================  ===============================================

**Diese Datei schreibt NIE ins Geld-Journal.** Sie kennt ``PaymentJournal``
nicht einmal als Namen; der einzige Weg zu einem ``receivable_settled`` fuehrt
durch den Kern-Durchgang, den auch der Reconcile-Timer faehrt. Ein zweiter
Schreiber waere ein zweiter Reconciler mit eigener Meinung — und zwei Meinungen
ueber denselben Geldeingang sind teurer als gar keine.

**Der Store ist ein Cache, keine Wahrheit.** Er verknuepft ``payment_id`` mit
``ref_hash``, haelt Beschreibung, Referenz, Callback-Ziel und den QR-Code und
merkt sich, welcher Webhook schon raus ist. Sein Statuswort dient dazu, den
Poller nicht auf erledigte Forderungen zu schicken — jede AUSKUNFT leitet den
Status frisch aus dem Kern ab.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.pay_settings import PaySettings
from app.pay.status import PayStatus, RailView, next_status
from app.pay.store import (
    MAX_DESCRIPTION_LENGTH,
    MAX_REFERENCE_LENGTH,
    PayRequest,
    PayStore,
)
from app.pay.webhook import deliver
from app.payments.models import Money
from app.payments.rail import InvoiceRequest, RailError
from app.payments.receivables import (
    ReceivableSettlement,
    expiry_of,
    settle_receivable,
    settlement_of,
)
from app.payments.service import PaymentService, PaymentServiceError

logger = logging.getLogger(__name__)

#: Kuerzeste vertretbare Frist: eine Minute reicht fuer einen automatischen
#: Zahler, nicht fuer einen Menschen — deshalb ist der Default 15 Minuten.
MIN_EXPIRY_SECONDS = 60
MAX_EXPIRY_SECONDS = 86_400


class PayError(Exception):
    """Die Anfrage passt nicht zu den Grenzen dieser Schicht (kein Serverfehler)."""


@dataclass(frozen=True)
class Receipt:
    """Der Beleg einer bezahlten Forderung — jede Geldangabe aus dem Journal."""

    receipt_id: str
    payment_id: str
    amount_sat: int
    paid_amount_sat: int
    paid_at: datetime
    reference: str
    description: str
    rail: str
    journal_seq: int
    record_hash: str
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "payment_id": self.payment_id,
            "amount_sat": self.amount_sat,
            "paid_amount_sat": self.paid_amount_sat,
            "paid_at": self.paid_at.isoformat(),
            "reference": self.reference,
            "description": self.description,
            "rail": self.rail,
            "audit": {"journal_seq": self.journal_seq, "record_hash": self.record_hash},
            "created_at": self.created_at.isoformat(),
        }

    def to_text(self) -> str:
        """Die Textfassung — was ein Mensch weiterreicht, ohne JSON zu lesen."""
        return "\n".join(
            [
                f"KAI PAY receipt {self.receipt_id}",
                f"payment_id     {self.payment_id}",
                f"reference      {self.reference or '-'}",
                f"description    {self.description}",
                f"requested      {self.amount_sat} sat",
                f"paid           {self.paid_amount_sat} sat",
                f"paid at        {self.paid_at.isoformat()}",
                f"rail           {self.rail or 'unknown'}",
                f"journal        seq={self.journal_seq} record_hash={self.record_hash}",
                "",
                "Die Geldangaben stammen aus dem hash-verketteten Geld-Journal",
                "(artifacts/payments/payment_journal.jsonl, ADR 0018 §5).",
            ]
        )


class PayService:
    """Anfordern, nachfragen, belegen. Ohne eigene Meinung ueber Geld."""

    def __init__(
        self,
        *,
        payments: PaymentService,
        store: PayStore,
        settings: PaySettings,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._payments = payments
        self._store = store
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def store(self) -> PayStore:
        return self._store

    @property
    def settings(self) -> PaySettings:
        return self._settings

    # -- Anfordern ---------------------------------------------------------- #

    async def create_request(
        self,
        *,
        amount_sat: int,
        description: str,
        reference: str = "",
        expiry_seconds: int | None = None,
        webhook_url: str = "",
        idempotency_key: str = "",
    ) -> PayRequest:
        """Stelle eine Forderung aus und verknuepfe sie mit dieser Anfrage.

        Der Kern stellt die Invoice aus UND journalisiert sie in einem Zug; erst
        danach entsteht hier eine Zeile. Die Reihenfolge ist die Zusage: eine
        Forderung, die diese Schicht kennt, steht auch im Geld-Journal — nie
        umgekehrt.

        Raises:
            PayError: Betrag, Text oder Frist verletzen die Grenzen dieser
                Schicht; oder der Kern hat die Forderung abgelehnt.
        """
        amount = self._checked_amount(amount_sat)
        text = self._checked_description(description)
        ref = self._checked_reference(reference)
        url = self._checked_webhook(webhook_url)
        expiry = self._checked_expiry(expiry_seconds)

        key_hash = _key_hash(idempotency_key)
        if key_hash:
            replayed = self._store.by_key(key_hash)
            if replayed is not None:
                return replayed

        payment_id = f"pay_{uuid.uuid4().hex[:12]}"
        try:
            invoice = await self._payments.create_invoice(
                InvoiceRequest(
                    amount=Money(minor_units=amount, currency="SAT", scale=0),
                    purpose=self._settings.purpose,
                    expiry_seconds=expiry,
                    memo=text,
                ),
                # Die Bestellreferenz des Aufrufers, sonst der eigene Schluessel:
                # ein leerer ``order_ref`` waere ein Geldeingang ohne Zuordnung.
                order_ref=ref or payment_id,
            )
        except (RailError, PaymentServiceError, ValueError) as exc:
            raise PayError(f"the payment control plane refused the invoice: {exc}") from exc

        return self._store.create(
            PayRequest(
                payment_id=payment_id,
                ref_hash=invoice.ref_hash,
                # Betrag und Ablauf so, wie der Kern sie AUSGESTELLT hat — nicht
                # so, wie sie angefragt wurden. Eine Anzeige, die von der
                # Invoice abweicht, ist eine falsche Zusage.
                amount_sat=invoice.amount.minor_units,
                description=text,
                reference=ref,
                webhook_url=url,
                created_at=self._clock(),
                expires_at=invoice.expires_at,
                bolt11=invoice.payment_request,
                idempotency_key_hash=key_hash,
            )
        )

    # -- Nachfragen --------------------------------------------------------- #

    async def refresh(self, payment_id: str) -> PayRequest:
        """Frage den Kern nach dem Stand — und buche dort, wenn bezahlt wurde.

        Der einzige Weg zu einer Zustandsaenderung fuehrt durch
        :func:`app.payments.receivables.settle_receivable`, denselben Durchgang,
        den der Reconcile-Timer faehrt. Zweimaliges Aufrufen nach einer Zahlung
        erzeugt deshalb genau EINEN ``receivable_settled``-Record.

        Raises:
            PayError: unbekannte ``payment_id``.
        """
        request = self._require(payment_id)
        journal = self._payments.journal
        now = self._clock()

        settlement = settlement_of(journal, request.ref_hash)
        if settlement is not None:
            return await self._settled(request, settlement)
        if request.status in (PayStatus.EXPIRED, PayStatus.FAILED):
            # Terminal und im Kern belegt (kein Settlement-Record, Frist um).
            # Ein Rail-Aufruf auf eine tote Invoice kostet nur Zeit.
            return request

        try:
            outcome = await settle_receivable(
                journal, self._payments.rail, request.ref_hash, now=now
            )
        except PaymentServiceError as exc:
            return self._store.note_error(payment_id, f"no rail: {exc}")

        if outcome.settlement is not None:
            return await self._settled(request, outcome.settlement)
        if outcome.error:
            # Fail-soft: ohne Aussage des Rails aendert sich nichts. Der Grund
            # steht in der Antwort, nicht im Zustand.
            return self._store.note_error(payment_id, outcome.error)

        expires_at = expiry_of(journal, request.ref_hash) or request.expires_at
        target = next_status(
            PayStatus.WAITING,
            RailView(settled=outcome.settled),
            now=now,
            expires_at=expires_at,
        )
        if target is PayStatus.WAITING:
            return request
        return self._store.mark(payment_id, target)

    def lookup_by_reference(self, reference: str) -> list[PayRequest]:
        """Alle Forderungen zu einer Bestellreferenz — aelteste zuerst."""
        return self._store.by_reference(reference)

    async def receipt(self, payment_id: str) -> Receipt:
        """Der Beleg. Jede Geldangabe kommt aus dem Journal-Record.

        Raises:
            PayError: unbekannte ``payment_id`` oder noch kein Eingang gebucht.
        """
        request = self._require(payment_id)
        settlement = settlement_of(self._payments.journal, request.ref_hash)
        if settlement is None:
            raise PayError(
                f"{payment_id} has no settlement record in the money journal — "
                "a receipt is issued for money that arrived, not for money that was asked for"
            )
        return Receipt(
            # Aus dem Record-Hash abgeleitet: derselbe Eingang ergibt immer
            # denselben Beleg, und zwei Belege mit einer Nummer sind derselbe.
            receipt_id=f"rcpt_{settlement.record_hash[:12]}",
            payment_id=payment_id,
            amount_sat=request.amount_sat,
            paid_amount_sat=settlement.amount_settled_minor_units,
            paid_at=settlement.settled_at,
            reference=request.reference,
            description=request.description,
            rail=settlement.rail,
            journal_seq=settlement.journal_seq,
            record_hash=settlement.record_hash,
            created_at=request.created_at,
        )

    def view(self, request: PayRequest) -> dict[str, Any]:
        """Die Aussenansicht einer Forderung — Geldangaben aus dem Journal.

        ``bolt11``/``lightning_uri`` stehen NUR bei ``WAITING`` darin. Der
        Grund ist die Seite selbst: nach einem Reload muss sie denselben
        QR-Code zeigen koennen, ohne eine zweite Forderung auszustellen — zwei
        gueltige QR-Codes fuer eine Bestellung sind eine Einladung, zweimal zu
        zahlen. Ist die Forderung erledigt oder abgelaufen, ist die
        Aufforderung wertlos und verschwindet aus der Antwort; sie stehen zu
        lassen hiesse, zum Bezahlen von etwas einzuladen, das keiner mehr
        annimmt.

        **Die Aufforderung ist kein Geheimnis** (dieselbe Begruendung wie in
        ``payments.py::create_invoice``: ohne sie kann der Zahler nicht
        zahlen). Sie kommt aus dem Store und nicht aus dem Kern, weil der Kern
        sie NICHT aufbewahrt: die Redaktions-Allowlist des Geld-Journals laesst
        ausschliesslich den ``ref_hash`` durch (``app/payments/redaction.py``),
        und ``Invoice.payment_request`` gibt es genau einmal — in der Antwort
        auf ``create_invoice``. Sie ist damit ein Praesentationsdatum der
        Verknuepfung, keine Geldwahrheit.
        """
        settlement = (
            settlement_of(self._payments.journal, request.ref_hash)
            if request.status is PayStatus.SETTLED
            else None
        )
        payable = request.status is PayStatus.WAITING and bool(request.bolt11)
        return {
            "payment_id": request.payment_id,
            "status": request.status.value,
            "amount_sat": request.amount_sat,
            "paid_amount_sat": settlement.amount_settled_minor_units if settlement else 0,
            "paid_at": settlement.settled_at.isoformat() if settlement else None,
            "reference": request.reference,
            "description": request.description,
            "created_at": request.created_at.isoformat(),
            "expires_at": request.expires_at.isoformat(),
            "bolt11": request.bolt11 if payable else None,
            "lightning_uri": f"lightning:{request.bolt11}" if payable else None,
            "last_error": request.last_error,
        }

    def health(self) -> dict[str, Any]:
        """Betriebszustand der Produktschicht — keine Geldkennzahl."""
        open_now = self._store.open_requests(self._settings.max_open_requests)
        newest = self._store.newest_settled()
        last_at: str | None = None
        if newest is not None:
            # Auch dieser Zeitpunkt kommt aus dem Journal, nicht aus dem Cache.
            settlement = settlement_of(self._payments.journal, newest.ref_hash)
            last_at = settlement.settled_at.isoformat() if settlement else None
        return {
            "enabled": self._settings.enabled,
            "open_requests": len(open_now),
            "settled_total": self._store.settled_count(),
            "last_settled_at": last_at,
        }

    # -- Intern ------------------------------------------------------------- #

    async def _settled(self, request: PayRequest, settlement: ReceivableSettlement) -> PayRequest:
        """Zustand nachfuehren und — genau einmal — den Callback ausloesen."""
        already = request.status is PayStatus.SETTLED
        updated = self._store.mark(
            request.payment_id,
            PayStatus.SETTLED,
            journal_seq=settlement.journal_seq,
            record_hash=settlement.record_hash,
        )
        if already or not updated.webhook_url:
            return updated
        result = await deliver(
            updated.webhook_url,
            {
                "payment_id": updated.payment_id,
                "status": PayStatus.SETTLED.value,
                "amount_sat": updated.amount_sat,
                "paid_amount_sat": settlement.amount_settled_minor_units,
                "paid_at": settlement.settled_at.isoformat(),
                "reference": updated.reference,
                "description": updated.description,
                "ts": self._clock().isoformat(),
            },
            secret=self._settings.webhook_secret,
        )
        self._store.webhook_result(updated.payment_id, ok=result.ok, detail=result.detail)
        return updated

    def _require(self, payment_id: str) -> PayRequest:
        request = self._store.get(payment_id)
        if request is None:
            raise PayError(f"unknown payment request: {payment_id}")
        return request

    def _checked_amount(self, amount_sat: int) -> int:
        if amount_sat < 1 or amount_sat > self._settings.max_amount_sat:
            raise PayError(
                f"amount_sat must be between 1 and {self._settings.max_amount_sat} "
                f"(APP_PAY_MAX_AMOUNT_SAT), got {amount_sat}"
            )
        return amount_sat

    @staticmethod
    def _checked_description(description: str) -> str:
        text = description.strip()
        if not text or len(text) > MAX_DESCRIPTION_LENGTH:
            raise PayError(f"description must be 1..{MAX_DESCRIPTION_LENGTH} characters")
        return text

    @staticmethod
    def _checked_reference(reference: str) -> str:
        ref = reference.strip()
        if len(ref) > MAX_REFERENCE_LENGTH:
            raise PayError(f"reference must be at most {MAX_REFERENCE_LENGTH} characters")
        return ref

    @staticmethod
    def _checked_webhook(webhook_url: str) -> str:
        url = webhook_url.strip()
        if url and not url.lower().startswith("https://"):
            raise PayError(
                "webhook_url must be https — a settlement notice over plain http can be "
                "read and rewritten on the way, and the receiver would act on it"
            )
        return url

    def _checked_expiry(self, expiry_seconds: int | None) -> int:
        expiry = expiry_seconds or self._settings.default_expiry_seconds
        if expiry < MIN_EXPIRY_SECONDS or expiry > MAX_EXPIRY_SECONDS:
            raise PayError(
                f"expiry_seconds must be between {MIN_EXPIRY_SECONDS} and {MAX_EXPIRY_SECONDS}"
            )
        return expiry


def _key_hash(idempotency_key: str) -> str:
    key = idempotency_key.strip()
    return hashlib.sha256(key.encode("utf-8")).hexdigest() if key else ""


__all__ = ["MAX_EXPIRY_SECONDS", "MIN_EXPIRY_SECONDS", "PayError", "PayService", "Receipt"]
