"""Die HTTP-Grenze des Payment Control Plane (ADR 0018 §10/§11).

Sieben Endpunkte, ein Dienst. Der Router entscheidet **nichts** ueber Geld: er
uebersetzt HTTP in einen Aufruf an :class:`~app.payments.service.PaymentService`
und dessen Antwort zurueck. Jede Regel — Idempotenz, Policy, Freigabe,
Zustandsuebergang — liegt hinter dieser Grenze. Ein Router, der eine
Vorpruefung macht, ist eine zweite Vergabestelle mit eigener Meinung.

**Drei Dinge, die hier trotzdem stehen muessen:**

1. **``Idempotency-Key`` ist Pflicht, nicht optional.** Ohne Header 400 —
   nicht "dann generieren wir einen". Ein serverseitig erzeugter Key macht
   jeden Retry des Clients zu einer neuen Zahlung, und genau dafuer gibt es
   den Header.
2. **Auth ist NICHT hier.** ``/payments/*`` faellt in ``app/security/auth.py``
   durch alle Bypass-Zweige (weder ``/health``-Liste noch ``/dashboard``-Praefix
   noch ``/oracle``) und landet bei CF-Access/Bearer. Ein Test haelt das fest,
   statt es zu behaupten.
3. **Rate-Limit auf der Mutation.** Der bestehende
   :class:`~app.security.rate_limit.FailureTracker` zaehlt hier VERSUCHE, nicht
   Fehlversuche: eine Zahlungsaufnahme ist teuer (Journal-Lock, Node-Decode),
   und ein Client in einer Schleife darf den Serialisierungspunkt nicht
   belegen.

**Was NIE ueber diese Grenze geht:** die Destination im Klartext zurueck, der
Idempotency-Key, ein Preimage. Die Antworten bestehen aus Status, Verdikt und
Hashes — man soll sie ohne Nachdenken loggen koennen.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.payments.models import Money
from app.payments.rail import MAX_MEMO_LENGTH, InvoiceRequest, RailError
from app.payments.service import PaymentRequest, PaymentService, PaymentServiceError
from app.payments.service_types import IntentView
from app.security.rate_limit import FailureTracker, client_ip

router = APIRouter(prefix="/payments", tags=["payments"])

#: Aufnahme-Versuche je Client-IP und Fenster. 30/Minute ist grosszuegig fuer
#: einen Operator und eng genug, dass eine Schleife den Journal-Lock nicht
#: belegt. Bewusst NICHT konfigurierbar: eine Env-Variable waere ein Regler,
#: den im Ernstfall niemand findet.
_MUTATION_LIMIT = FailureTracker(window_seconds=60.0, threshold=30)


def _reset_rate_limiter_for_tests() -> None:
    """Test-Naht — dieselbe Form wie ``tradingview._reset_rate_limiter_for_tests``."""
    _MUTATION_LIMIT.clear_all()


def _guard_rate(request: Request) -> None:
    locked, retry_after = _MUTATION_LIMIT.is_limited(client_ip(request))
    if locked:
        raise HTTPException(
            status_code=429,
            detail="too many payment requests",
            headers={"Retry-After": str(retry_after)},
        )
    _MUTATION_LIMIT.record_failure(client_ip(request))


# --------------------------------------------------------------------------- #
# Formen
# --------------------------------------------------------------------------- #


class PaymentIntentRequest(BaseModel):
    """Was ein Aufrufer angibt. IDs, Modus und Rail vergibt der Dienst."""

    actor: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=64)
    destination: str = Field(min_length=1)
    amount_sat: int = Field(gt=0)
    fee_limit_sat: int = Field(ge=0)
    correlation_id: str = Field(default="unset", min_length=1, max_length=64)
    ttl_seconds: int = Field(default=3600, gt=0)


class ExecuteRequest(BaseModel):
    """Der Freigabecode. Leer heisst "keiner", nicht "keiner noetig"."""

    hotp_code: str = Field(default="", max_length=16)


class InvoiceCreateRequest(BaseModel):
    amount_sat: int = Field(gt=0)
    purpose: str = Field(min_length=1, max_length=64)
    #: Eine Stunde, weil ein MENSCH zahlt: Wallet oeffnen, scannen, bestaetigen.
    #: Die frueheren 300 s waren aus der L402-Mint-Logik uebernommen, wo ein
    #: Client sofort und automatisch zahlt — dort ist kurz richtig, hier war es
    #: ein abgelaufener QR-Code (Rueckweg-Test 2026-09-04). Obergrenze 24 h:
    #: eine unbezahlte Invoice belegt eine Zeile am Node, "unbegrenzt" ist
    #: keine Frist.
    expiry_seconds: int = Field(default=3600, gt=0, le=86_400)
    #: KAIs eigene Bestellreferenz (Self-Use, ADR 0016). Der Rail sieht sie nie.
    order_ref: str = Field(default="", max_length=64)
    #: Der Text auf der Forderung. Leer heisst ``kai-pay: <purpose>`` — nicht
    #: "kein Memo": eine Invoice ohne Praefix bucht die Einnahmenerkennung nie.
    #: Frueher stand hier ein ``memo_hash``; ein Hash, den der Aufrufer selbst
    #: waehlt, belegt nichts und erreicht den Node nicht.
    memo: str = Field(default="", max_length=MAX_MEMO_LENGTH)


def _sat(amount: int) -> Money:
    return Money(minor_units=amount, currency="SAT", scale=0)


def _view(view: IntentView) -> dict[str, Any]:
    decision = view.decision
    return {
        "intent_id": view.intent_id,
        "status": view.status.value,
        "replayed": view.replayed,
        "detail": view.detail,
        "verdict": decision.verdict.value if decision else None,
        "rule_ids": list(decision.rule_ids) if decision else [],
        "reasons": list(decision.reasons) if decision else [],
    }


def _service(request: Request) -> PaymentService:
    service = getattr(request.app.state, "payment_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="payment control plane not available in this process",
        )
    return service  # type: ignore[no-any-return]


def _refuse(exc: PaymentServiceError) -> HTTPException:
    """Eine Verweigerung des Control Plane ist ein 409, kein 500.

    Der Aufrufer hat gegen den ZUSTAND verstossen (falscher Status, kein
    Verifier, Modus verbietet den Send) — das ist eine Aussage ueber seinen
    Vorgang, kein Serverfehler.
    """
    return HTTPException(status_code=409, detail=str(exc))


# --------------------------------------------------------------------------- #
# Endpunkte
# --------------------------------------------------------------------------- #


@router.post("/intents")
async def create_intent(
    request: Request,
    body: PaymentIntentRequest,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Einen Intent aufnehmen. ``replayed=true`` ist eine ANTWORT, kein Fehler."""
    _guard_rate(request)
    key = idempotency_key.strip()
    if len(key) < 16:
        raise HTTPException(
            status_code=400,
            detail=(
                "Idempotency-Key header is required (>= 16 chars). Without it a "
                "client retry would become a second payment."
            ),
        )
    try:
        view = await _service(request).create_intent(
            PaymentRequest(
                actor=body.actor,
                purpose=body.purpose,
                destination=body.destination,
                amount=_sat(body.amount_sat),
                fee_limit=_sat(body.fee_limit_sat),
                correlation_id=body.correlation_id,
                ttl_seconds=body.ttl_seconds,
            ),
            key,
        )
    except PaymentServiceError as exc:
        raise _refuse(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _view(view)


@router.get("/intents/{intent_id}")
async def get_intent(request: Request, intent_id: str) -> dict[str, Any]:
    try:
        return _view(_service(request).get(intent_id))
    except PaymentServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/intents/{intent_id}/simulate")
async def simulate_intent(request: Request, intent_id: str) -> dict[str, Any]:
    """Vorschau ohne Send (ADR §1). Auch in SHADOW und LIVE erlaubt."""
    _guard_rate(request)
    try:
        preview = await _service(request).simulate(intent_id)
    except PaymentServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    quote = preview.quote
    return {
        "intent_id": preview.intent_id,
        "status": preview.status.value,
        "quote": (
            {
                "rail": quote.rail,
                "amount_minor_units": quote.amount.minor_units,
                "fee_estimate_minor_units": quote.fee_estimate.minor_units,
                "estimate_source": quote.estimate_source,
                "valid_until": quote.valid_until.isoformat(),
            }
            if quote
            else None
        ),
        "verdict": preview.decision.verdict.value if preview.decision else None,
    }


@router.post("/intents/{intent_id}/execute")
async def execute_intent(
    request: Request, intent_id: str, body: ExecuteRequest | None = None
) -> dict[str, Any]:
    """Freigeben (falls noetig) und senden. Ein zweiter Aufruf sendet NICHT.

    Der HOTP-Code wird nur verlangt, wenn die Policy ihn verlangt hat
    (``AWAITING_APPROVAL``). Ihn immer zu fordern waere bequemer zu erklaeren,
    haette aber den Operator daran gewoehnt, fuer jede Kleinstzahlung einen
    Code zu tippen — und eine Gewohnheit ist keine Kontrolle.
    """
    _guard_rate(request)
    service = _service(request)
    code = (body.hotp_code if body else "").strip()
    try:
        current = service.get(intent_id)
    except PaymentServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if current.status.value == "AWAITING_APPROVAL":
        if not code:
            raise HTTPException(
                status_code=400,
                detail="hotp_code is required while the intent is AWAITING_APPROVAL",
            )
        try:
            service.authorize(intent_id, code)
        except PaymentServiceError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        return _view(await service.execute(intent_id))
    except PaymentServiceError as exc:
        raise _refuse(exc) from exc
    except RailError as exc:
        raise HTTPException(status_code=502, detail=f"rail refused: {exc}") from exc


@router.post("/invoices")
async def create_invoice(request: Request, body: InvoiceCreateRequest) -> dict[str, Any]:
    """Eine eigene Forderung ausstellen (Self-Use-Receivable, ADR §1)."""
    _guard_rate(request)
    try:
        invoice = await _service(request).create_invoice(
            InvoiceRequest(
                amount=_sat(body.amount_sat),
                purpose=body.purpose,
                expiry_seconds=body.expiry_seconds,
                memo=body.memo,
            ),
            order_ref=body.order_ref,
        )
    except RailError as exc:
        raise HTTPException(status_code=502, detail=f"rail refused: {exc}") from exc
    return {
        "ref_hash": invoice.ref_hash,
        "rail": invoice.rail,
        "amount_minor_units": invoice.amount.minor_units,
        "expires_at": invoice.expires_at.isoformat(),
        "order_ref": body.order_ref,
        # Die Aufforderung selbst. Sie gehoert in die Antwort und nirgendwo
        # sonst hin: das Journal traegt nur ``invoice_ref_hash`` (Allowlist in
        # app/payments/redaction.py). Kein Geheimnis — ohne sie kann der
        # Zahler schlicht nicht zahlen.
        "payment_request": invoice.payment_request,
    }


@router.get("/invoices/{ref_hash}")
async def invoice_status(request: Request, ref_hash: str) -> dict[str, Any]:
    try:
        status = await _service(request).invoice_status(ref_hash)
    except RailError as exc:
        raise HTTPException(status_code=502, detail=f"rail refused: {exc}") from exc
    return {
        "ref_hash": status.ref_hash,
        "settled": status.settled,
        "amount_paid_minor_units": (status.amount_paid.minor_units if status.amount_paid else 0),
        "settled_at": status.settled_at.isoformat() if status.settled_at else None,
        "observed_at": status.observed_at.isoformat(),
    }


@router.get("/audit")
async def audit(request: Request, intent_id: str = "") -> dict[str, Any]:
    """Die Records EINES Vorgangs — genau so, wie sie im Journal stehen.

    ``intent_id`` ist Pflicht. Ein leerer Filter waere ein Dump des gesamten
    Geld-Journals ueber HTTP: Betraege, Gegenparteien-Hashes und Zeitpunkte
    jeder Wertbewegung in einer einzigen Antwort. Der Audit-Pfad beantwortet
    eine Frage nach einem Vorgang, er ist kein Export.
    """
    service = _service(request)
    if not intent_id.strip():
        raise HTTPException(status_code=400, detail="intent_id query parameter is required")
    events = service.audit(intent_id)
    return {
        "intent_id": intent_id,
        "events": [
            {
                "seq": event.seq,
                "ts": event.ts.isoformat(),
                "event_type": event.event_type,
                "payload": event.payload,
                "record_hash": event.record_hash,
            }
            for event in events
        ],
    }


__all__ = ["router"]
