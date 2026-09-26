"""``/pay`` — Operator bezahlt eine BOLT11-Rechnung ueber den Payment Control Plane (D-277).

Der einzige Sendepfad, den eine Handy-Wallet nicht bietet: maschinell,
journaliert, gecappt, mit Operator-Freigabe im bestehenden Kanal. Zwei Schritte,
kein Send ohne den zweiten:

    /pay <bolt11>          dekodieren, Policy, Quote  -> Vorschau + Intent
    /pay ok [<hotp>]       freigeben (HOTP nur, wenn die Policy ihn verlangt) + senden
    /pay cancel            Vorschau verwerfen (der Intent laeuft im Journal aus)
    /pay status            Zustand des letzten Intents

Alles laeuft durch :class:`app.payments.service.PaymentService` — derselbe
Weg wie das Cockpit (``/ln/value-action``), dieselben Regeln (Caps, Reserve,
Allowlist, HOTP-Schwelle), dasselbe Journal. Der Bot bleibt Transport: kein
Node-Aufruf, kein Fee-Regler im Kommando (Fee-Limit kommt aus den Settings).

Antworten nennen NIE die Rechnung oder das Ziel — nur Betrag, Gebuehren, Zustand
und die Intent-ID.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.bolt11 import bolt11_amount_sat
from app.core.payment_settings import fee_limit_for_amount
from app.payments.models import Money
from app.payments.service_types import PaymentRequest, PaymentServiceError

logger = logging.getLogger(__name__)

#: Muss mit ``app.api.routers.ln_control_delegate.DEFAULT_PURPOSE`` uebereinstimmen —
#: ein Test bindet beide. Der Purpose steht in ``APP_PAYMENT_PURPOSES_ALLOWED``.
PAY_PURPOSE = "operator_pay_invoice"
PENDING_TTL = timedelta(minutes=10)
#: Nur aus diesen Zustaenden fuehrt ``/pay ok`` noch zu einem Send. Alles danach
#: (gesendet, gescheitert, abgelaufen) weist der Service als Replay ab — die
#: Vorschau darf dann keine Freigabe anbieten.
_CONFIRMABLE = frozenset({"AWAITING_APPROVAL", "AUTHORIZED"})
USAGE = (
    "⚡ */pay* — Rechnung bezahlen (D-277)\n"
    "/pay <bolt11> — Vorschau (Betrag, Gebuehr, Policy)\n"
    "/pay ok [<hotp>] — freigeben und senden\n"
    "/pay cancel — Vorschau verwerfen\n"
    "/pay status — Zustand des letzten Vorgangs"
)


@dataclass(frozen=True)
class PendingPay:
    intent_id: str
    amount_sat: int
    fee_limit_sat: int
    needs_hotp: bool
    created_at: datetime


@dataclass
class PayFlow:
    """Pro Chat hoechstens EINE offene Vorschau; nach ``PENDING_TTL`` verfaellt sie."""

    pending: dict[int, PendingPay] = field(default_factory=dict)
    last_intent: dict[int, str] = field(default_factory=dict)

    def get(self, chat_id: int, *, now: datetime) -> PendingPay | None:
        item = self.pending.get(chat_id)
        if item is None:
            return None
        if now - item.created_at > PENDING_TTL:
            self.pending.pop(chat_id, None)
            return None
        return item


def _sat(amount: int) -> Money:
    return Money(minor_units=int(amount), currency="SAT", scale=0)


def _fee_limit_sat(service: Any, amount_sat: int) -> int:
    """Wie ``ln_control_delegate.derive_fee_limit``: aus den Settings, nie aus dem Kommando."""
    settings = service.settings
    return fee_limit_for_amount(settings, amount_sat)


def _idempotency_key(chat_id: int, payment_request: str) -> str:
    # Dieselbe Rechnung aus demselben Chat ist derselbe Vorgang — ein Retry
    # wird zum Replay (HTTP-200-Semantik des Control Plane), nie zur zweiten Zahlung.
    digest = hashlib.sha256(f"tgpay:{chat_id}:{payment_request.strip()}".encode()).hexdigest()
    return f"tgpay_{digest[:32]}"


def _reasons(view: Any) -> str:
    decision = getattr(view, "decision", None)
    if decision is None:
        return ""
    reasons = list(getattr(decision, "reasons", ()) or ())
    rules = list(getattr(decision, "rule_ids", ()) or ())
    parts = [str(r) for r in reasons[:3]] or [str(r) for r in rules[:3]]
    return "; ".join(parts)


def _fee_line(quote: Any, fee_limit: int) -> str:
    """Gebuehrenzeile der Vorschau. Eine Node-Probe darf warnen, eine Settings-Zahl nicht."""
    source = quote.estimate_source
    if source == "node_probe_no_route":
        return (
            f"Gebuehr: ⚠️ Node findet per Probe keine Route (Limit {fee_limit} sat) — "
            "der Send wird voraussichtlich scheitern"
        )
    estimate = quote.fee_estimate.minor_units
    line = f"Gebuehr: ~{estimate} sat ({source}), Limit {fee_limit} sat"
    if source.startswith("node_") and estimate > fee_limit:
        line += " ⚠️ Schaetzung liegt ueber dem Limit — der Send wird voraussichtlich scheitern"
    return line


async def _preview(flow: PayFlow, service: Any, chat_id: int, bolt11: str, now: datetime) -> str:
    amount = bolt11_amount_sat(bolt11)
    if amount <= 0:
        return (
            "❌ Rechnung ohne Betrag wird nicht bezahlt — der Betrag muss in der "
            "Rechnung stehen, nicht im Kommando."
        )
    fee_limit = _fee_limit_sat(service, amount)
    try:
        view = await service.create_intent(
            PaymentRequest(
                actor="operator",
                purpose=PAY_PURPOSE,
                destination=bolt11,
                amount=_sat(amount),
                fee_limit=_sat(fee_limit),
                correlation_id=f"tg:{chat_id}",
            ),
            _idempotency_key(chat_id, bolt11),
        )
    except PaymentServiceError as exc:
        return f"❌ Vorgang abgelehnt: {exc}"
    except ValueError as exc:
        return f"❌ Ungueltige Eingabe: {exc}"

    status = view.status.value
    if status == "DENIED":
        return f"⛔ Policy lehnt ab ({_reasons(view) or 'ohne Begruendung'}). Nichts gesendet."
    if status not in _CONFIRMABLE:
        flow.pending.pop(chat_id, None)
        flow.last_intent[chat_id] = view.intent_id
        return (
            f"ℹ️ Diese Rechnung ist bereits bekannt: Intent `{view.intent_id}` → {status}. "
            "Kein erneuter Send moeglich — fuer einen neuen Versuch eine neue Rechnung."
        )

    fee_line = f"Gebuehr: Schaetzung n/a, Limit {fee_limit} sat"
    try:
        sim = await service.simulate(view.intent_id)
        if sim.quote is not None:
            fee_line = _fee_line(sim.quote, fee_limit)
    except PaymentServiceError as exc:
        fee_line = f"Gebuehr: Vorschau fehlgeschlagen ({exc}), Limit {fee_limit} sat"

    needs_hotp = status == "AWAITING_APPROVAL"
    flow.pending[chat_id] = PendingPay(
        intent_id=view.intent_id,
        amount_sat=amount,
        fee_limit_sat=fee_limit,
        needs_hotp=needs_hotp,
        created_at=now,
    )
    flow.last_intent[chat_id] = view.intent_id
    replay = " (bereits bekannt — Replay)" if view.replayed else ""
    confirm = "/pay ok <hotp>" if needs_hotp else "/pay ok"
    return (
        f"⚡ *Zahlung vorbereitet*{replay}\n"
        f"Betrag: {amount} sat\n"
        f"{fee_line}\n"
        f"Modus: {service.settings.mode}\n"
        f"Status: {status}\n"
        f"Intent: `{view.intent_id}`\n"
        f"Bestaetigen mit {confirm} (10 min), verwerfen mit /pay cancel"
    )


async def _confirm(flow: PayFlow, service: Any, chat_id: int, hotp: str, now: datetime) -> str:
    item = flow.get(chat_id, now=now)
    if item is None:
        return "❌ Keine offene Vorschau. Erst /pay <bolt11>."
    try:
        current = service.get(item.intent_id)
    except PaymentServiceError as exc:
        flow.pending.pop(chat_id, None)
        return f"❌ Vorgang nicht mehr bekannt: {exc}"

    if current.status.value == "AWAITING_APPROVAL":
        if not hotp:
            return "❌ Die Policy verlangt eine Freigabe: /pay ok <hotp> (6 Ziffern)."
        try:
            service.authorize(item.intent_id, hotp)
        except PaymentServiceError as exc:
            # Kein Detail zum Fehlgrund nach aussen (Side-Channel), Vorschau bleibt.
            logger.warning("[PAY] authorize refused intent=%s: %s", item.intent_id, exc)
            return "❌ Freigabe abgelehnt. Vorschau bleibt offen — Code pruefen."

    try:
        result = await service.execute(item.intent_id)
    except PaymentServiceError as exc:
        flow.pending.pop(chat_id, None)
        return f"❌ Nicht gesendet: {exc}"
    except Exception as exc:  # noqa: BLE001 - Rail-Fehler duerfen den Bot nicht reissen
        flow.pending.pop(chat_id, None)
        logger.error("[PAY] execute failed intent=%s: %s", item.intent_id, type(exc).__name__)
        return f"⚠️ Send ohne Aussage ({type(exc).__name__}) — der Reconciler klaert den Vorgang."

    flow.pending.pop(chat_id, None)
    status = result.status.value
    if status in {"SETTLED", "SETTLED_REVERSIBLE"}:
        return f"✅ Bezahlt: {item.amount_sat} sat. Intent `{item.intent_id}` → {status}."
    if status in {"FAILED_FINAL", "FAILED_RETRYABLE"}:
        return f"❌ Zahlung gescheitert ({status}) — nichts bewegt. Intent `{item.intent_id}`."
    if status == "RECONCILIATION_REQUIRED":
        return (
            "⚠️ Ergebnis unbekannt (RECONCILIATION_REQUIRED) — der Send kann draussen "
            f"sein. Der Reconciler fragt den Node. Intent `{item.intent_id}`."
        )
    replay = " (Replay, nicht erneut gesendet)" if result.replayed else ""
    return f"ℹ️ Intent `{item.intent_id}` → {status}{replay}."


def _status(flow: PayFlow, service: Any, chat_id: int) -> str:
    intent_id = flow.last_intent.get(chat_id)
    if not intent_id:
        return "Kein /pay-Vorgang in dieser Sitzung."
    try:
        view = service.get(intent_id)
    except PaymentServiceError as exc:
        return f"Intent `{intent_id}`: {exc}"
    return f"Intent `{intent_id}` → {view.status.value}"


async def handle_pay(
    args: str,
    service: Any,
    chat_id: int,
    *,
    flow: PayFlow,
    now: datetime | None = None,
) -> str:
    """Telegram-Reply fuer ``/pay …``. Gibt IMMER einen String zurueck, wirft nie."""
    moment = now or datetime.now(UTC)
    parts = args.strip().split()
    if not parts or parts[0].lower() == "help":
        return USAGE
    verb = parts[0].lower()
    if verb in {"ok", "bestaetigen", "confirm"}:
        return await _confirm(flow, service, chat_id, parts[1] if len(parts) > 1 else "", moment)
    if verb in {"cancel", "abbrechen"}:
        dropped = flow.pending.pop(chat_id, None)
        return (
            f"Vorschau `{dropped.intent_id}` verworfen (nichts gesendet)."
            if dropped
            else "Keine offene Vorschau."
        )
    if verb == "status":
        return _status(flow, service, chat_id)
    if not verb.startswith("ln"):
        return "❌ Das sieht nicht nach einer BOLT11-Rechnung aus.\n" + USAGE
    if len(parts) > 1:
        return "❌ Nur die Rechnung angeben — Betrag und Gebuehr kommen aus Rechnung und Settings."
    return await _preview(flow, service, chat_id, parts[0], moment)


__all__ = ["PAY_PURPOSE", "PENDING_TTL", "USAGE", "PayFlow", "PendingPay", "handle_pay"]
