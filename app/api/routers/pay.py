"""Die HTTP-Grenze von KAI PAY v0.1 (D-CORE-006).

Fuenf Endpunkte, ein Dienst. Der Router entscheidet **nichts** — er uebersetzt
HTTP in einen Aufruf an :class:`~app.pay.service.PayService` und dessen Antwort
zurueck. Ueber Geld entscheidet auch der nicht: er fragt den Payment Control
Plane (ADR 0018), und der ist versiegelt.

**Drei Dinge, die hier trotzdem stehen muessen:**

1. **Aus heisst 404, nicht "leer".** Ohne ``APP_PAY_ENABLED=true`` existiert
   dieser Pfad fuer den Aufrufer nicht. Ein abgeschalteter Endpunkt, der 200
   mit leerer Liste antwortet, sieht aus wie ein funktionierender ohne Kunden.
2. **Auth ist NICHT hier.** ``/pay/*`` faellt in ``app/security/auth.py`` durch
   alle Bypass-Zweige (die oeffentliche Liste ist exakt und kennt nur ``/paper``,
   ``/health*`` und den TV-Webhook) und landet bei CF-Access/Bearer — genau wie
   ``/payments/*``. Ein Test haelt das fest, statt es zu behaupten.
3. **Rate-Limit auf der Mutation.** Dieselbe Bauart wie in ``payments.py``: der
   ``FailureTracker`` zaehlt hier VERSUCHE. Eine Forderung auszustellen kostet
   einen Node-Aufruf und einen Journal-Append; ein Client in einer Schleife
   darf den Serialisierungspunkt nicht belegen.

**Was NIE ueber diese Grenze geht:** der ``ref_hash`` der Forderung und der
Webhook-Schluessel. Der ``payment_id`` genuegt fuer jede Auskunft; der
``ref_hash`` ist der Schluessel, unter dem der Kern bucht, und gehoert in den
Audit-Pfad (``GET /payments/audit``), nicht in eine Produktantwort.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.pay.service import (
    MAX_EXPIRY_SECONDS,
    MIN_EXPIRY_SECONDS,
    PayError,
    PayService,
)
from app.pay.store import MAX_DESCRIPTION_LENGTH, MAX_REFERENCE_LENGTH, PayRequest
from app.security.rate_limit import FailureTracker, client_ip

router = APIRouter(prefix="/pay", tags=["pay"])

#: 30 Anfragen je Minute und Client-IP — dieselbe Zahl wie in ``payments.py``,
#: aus demselben Grund und bewusst nicht konfigurierbar: eine Env-Variable
#: waere ein Regler, den im Ernstfall niemand findet.
_MUTATION_LIMIT = FailureTracker(window_seconds=60.0, threshold=30)

_DISABLED = "kai pay disabled"


def _reset_rate_limiter_for_tests() -> None:
    """Test-Naht — dieselbe Form wie ``payments._reset_rate_limiter_for_tests``."""
    _MUTATION_LIMIT.clear_all()


def _guard_rate(request: Request) -> None:
    locked, retry_after = _MUTATION_LIMIT.is_limited(client_ip(request))
    if locked:
        raise HTTPException(
            status_code=429,
            detail="too many pay requests",
            headers={"Retry-After": str(retry_after)},
        )
    _MUTATION_LIMIT.record_failure(client_ip(request))


class PayRequestCreate(BaseModel):
    """Was ein Aufrufer angibt. Alles andere vergeben Dienst und Kern."""

    amount_sat: int = Field(gt=0)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)
    reference: str = Field(default="", max_length=MAX_REFERENCE_LENGTH)
    expiry_seconds: int | None = Field(default=None, ge=MIN_EXPIRY_SECONDS, le=MAX_EXPIRY_SECONDS)
    webhook_url: str = Field(default="", max_length=512)


def _service(request: Request) -> PayService:
    """Der Dienst — oder 404, wenn diese Schicht abgeschaltet ist."""
    service = getattr(request.app.state, "pay_service", None)
    if service is None:
        raise HTTPException(status_code=404, detail=_DISABLED)
    return service  # type: ignore[no-any-return]


def _created(entry: PayRequest) -> dict[str, Any]:
    """Die Antwort auf eine neue Forderung — mit allem zum Bezahlen, sonst nichts."""
    return {
        "payment_id": entry.payment_id,
        "status": entry.status.value,
        "amount_sat": entry.amount_sat,
        "description": entry.description,
        "reference": entry.reference,
        "bolt11": entry.bolt11,
        # Der Wallet-Link. Er steht neben dem BOLT11 und nicht statt ihm: ein
        # Desktop-Zahler kopiert die Zeichenkette, ein Handy folgt dem Schema.
        "lightning_uri": f"lightning:{entry.bolt11}" if entry.bolt11 else "",
        "created_at": entry.created_at.isoformat(),
        "expires_at": entry.expires_at.isoformat(),
    }


@router.post("/requests", status_code=201)
async def create_request(
    request: Request,
    body: PayRequestCreate,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Eine Zahlung anfordern. Der Kern stellt die Forderung aus und bucht sie.

    ``Idempotency-Key`` ist optional — anders als bei ``/payments/intents``, wo
    er Pflicht ist. Der Unterschied ist die Richtung: dort wuerde ein Retry zu
    einem zweiten SEND, hier zu einer zweiten Forderung. Die kostet kein Geld,
    nur einen zweiten QR-Code; wer das vermeiden will, schickt den Header.
    """
    _guard_rate(request)
    service = _service(request)
    try:
        entry = await service.create_request(
            amount_sat=body.amount_sat,
            description=body.description,
            reference=body.reference,
            expiry_seconds=body.expiry_seconds,
            webhook_url=body.webhook_url,
            idempotency_key=idempotency_key,
        )
    except PayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _created(entry)


@router.get("/requests")
async def list_requests(
    request: Request,
    reference: str = Query(default="", max_length=MAX_REFERENCE_LENGTH),
    limit: int = Query(default=10, ge=1, le=100),
) -> dict[str, Any]:
    """Nach Bestellreferenz suchen — oder die letzten Forderungen ansehen.

    Die Referenz-Abfrage ist der Weg, auf dem ein fremder Dienst *seine* eigene
    Bestellnummer wiederfindet, ohne sich eine ``payment_id`` zu merken. Sie
    antwortet mit ``paid`` als einer Frage, nicht als einer Liste: das ist die
    Auskunft, auf die ein Shop-System wartet.
    """
    service = _service(request)
    if reference.strip():
        entries = service.lookup_by_reference(reference)
        views = [service.view(entry) for entry in entries]
        return {
            "reference": reference.strip(),
            "paid": any(view["status"] == "SETTLED" for view in views),
            "payment_ids": [entry.payment_id for entry in entries],
            "latest": views[-1] if views else None,
        }
    return {"requests": [service.view(entry) for entry in service.store.recent(limit)]}


@router.get("/requests/{payment_id}")
async def get_request(request: Request, payment_id: str) -> dict[str, Any]:
    """Der Stand einer Forderung — frisch beim Kern nachgefragt."""
    service = _service(request)
    try:
        entry = await service.refresh(payment_id)
    except PayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return service.view(entry)


@router.get("/requests/{payment_id}/receipt")
async def get_receipt(
    request: Request, payment_id: str, response_format: str = Query(default="json", alias="format")
) -> Any:
    """Der Beleg. Existiert erst, wenn das Geld-Journal den Eingang traegt."""
    service = _service(request)
    try:
        await service.refresh(payment_id)
        receipt = await service.receipt(payment_id)
    except PayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if response_format == "text":
        return PlainTextResponse(receipt.to_text())
    return receipt.to_dict()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Betriebszustand — ohne Geldkennzahl und ohne Nachfrage beim Rail."""
    service = _service(request)
    task = getattr(request.app.state, "pay_poller_task", None)
    return {**service.health(), "poller_alive": bool(task is not None and not task.done())}


__all__ = ["router"]
