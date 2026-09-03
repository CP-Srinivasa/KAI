"""``pay_invoice`` aus dem Cockpit → Payment Control Plane (ADR 0017 §12).

**Es gibt ab hier genau einen Sendeweg.** Vorher fuehrte ``ln_control`` seine
eigene Kette: eigene Policy (``lightning/policy.py``), eigenes Journal
(``ops_ledger`` v2), eigener Idempotenz-Store, eigener Zustandsbegriff
(``{intent,in_flight,unknown,executed,error}``) — und ``value_layer.pay_invoice``
als zweiter Aufrufer von ``client.pay_invoice`` neben allem, was sonst noch
kam. Zwei Wege zu demselben Node heissen zwei Meinungen ueber denselben
Zahlungsvorgang, und genau daran ist der 25k-Spend vom 07-02 gescheitert.

**Die Cockpit-Zeremonie bleibt.** Plan-Vorschau, ``plan_hash``-Bindung, frischer
Idempotenz-Schluessel und HOTP laufen unveraendert in ``ln_control`` ab, BEVOR
dieser Modul aufgerufen wird. Was sich aendert, ist die Stelle danach: statt
des Value-Layers entscheidet die Regelkette des Control Plane, und der Send
steht in ``PaymentService.execute``.

**Der Idempotenz-Schluessel wird an den Plan gebunden.** Der Cockpit-Schluessel
allein waere zu kurz fuer das Domaenenmodell (>= 16 Zeichen) und — wichtiger —
nicht an die Parameter gebunden, die er freigibt. ``sha256(plan_hash:key)``
ist beides: lang genug und nur fuer GENAU diesen Plan gueltig.

**Ein Purpose ist Pflicht und hat keinen bequemen Default.** Der Aufrufer gibt
ihn in ``params`` an; steht er nicht in ``APP_PAYMENT_PURPOSES_ALLOWED``, lehnt
die Regel ``purpose_allowed`` ab und nennt sich beim Namen. Einen Wert
stillschweigend auf die Allowlist zu setzen haette die Regel zur Attrappe
gemacht.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import HTTPException, Request

from app.payments.models import Money
from app.payments.service import PaymentRequest, PaymentService, PaymentServiceError

#: Verwendungszweck, wenn der Aufrufer keinen nennt. Bewusst NICHT im
#: Default von ``purposes_allowed`` — der Operator muss ihn freischalten.
DEFAULT_PURPOSE = "operator_pay_invoice"


async def legacy_pay_invoice_moved(**_kwargs: Any) -> Any:
    """Stolperdraht im Value-Layer-Register (ADR §12).

    Der Eintrag in ``ln_control._ACTIONS`` bleibt, damit die Taxonomie-
    Invariante haelt und die Kapital-Gates weiter greifen. Wer diese Funktion
    tatsaechlich erreicht, hat die Abzweigung in ``value_action`` entfernt und
    damit einen zweiten Sendeweg wiederhergestellt — lieber ein lauter Fehler
    als eine zweite Wahrheit ueber dieselbe Zahlung.
    """
    raise RuntimeError("pay_invoice is routed through the payment control plane (ADR 0017 §12)")


def bind_idempotency_key(plan_hash: str, cockpit_key: str) -> str:
    """Der Schluessel des Control Plane: an den Plan gebunden, lang genug."""
    return hashlib.sha256(f"{plan_hash}:{cockpit_key}".encode()).hexdigest()


def _sat(amount: int) -> Money:
    return Money(minor_units=amount, currency="SAT", scale=0)


def derive_fee_limit(service: PaymentService, amount_sat: int) -> int:
    """Gebuehrgrenze aus der Konfiguration, nie aus dem Aufruf.

    Ein Fee-Limit im Request waere ein Regler am Geldpfad, den jeder Aufrufer
    hochdrehen kann. Es kommt deshalb aus ``PaymentSettings`` und wird von der
    Regel ``fee_limit_required`` gegen dieselbe Konfiguration geprueft.
    """
    settings = service.settings
    ppm = amount_sat * settings.fee_limit_default_ppm // 1_000_000
    return min(max(ppm, 1), settings.fee_limit_max_sat)


def plan_view(service: PaymentService | None, *, amount_sat: int, purpose: str) -> dict[str, Any]:
    """Die Vorschau. Sie schreibt NICHTS — ein Plan ist kein Vorgang.

    ``service is None`` ist hier ausdruecklich kein Fehler: eine Vorschau ohne
    verdrahteten Control Plane ist unvollstaendig, aber harmlos, und das
    Policy-Verdikt darueber gilt unabhaengig davon. Nur der SEND verlangt den
    Control Plane — und der faellt ohne ihn auf 503.
    """
    return {
        "action": "pay_invoice",
        "route": "payment_control_plane",
        "mode": service.settings.mode if service else "unavailable",
        "amount_sat": amount_sat,
        "fee_limit_sat": derive_fee_limit(service, amount_sat) if service else None,
        "purpose": purpose,
        "status": "planned" if service else "unavailable",
    }


def service_of(request: Request) -> PaymentService:
    service = getattr(request.app.state, "payment_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="payment control plane not available — pay_invoice is routed through it",
        )
    return service  # type: ignore[no-any-return]


async def handle_pay_invoice(
    request: Request,
    params: dict[str, Any],
    *,
    amount_sat: int,
    plan_hash_value: str,
    policy: dict[str, str],
    cockpit_key: str | None,
    hotp_code: str,
) -> dict[str, Any]:
    """Plan oder Ausfuehrung — beides im Control Plane (ADR §12).

    ``cockpit_key is None`` heisst Plan-Modus: nur Vorschau, kein Record. Die
    Cockpit-Zeremonie (plan_hash, frischer Schluessel, HOTP) ist zu diesem
    Zeitpunkt bereits gelaufen; hier faellt nur noch die Frage an, WER sendet.
    """
    purpose = str(params.get("purpose") or DEFAULT_PURPOSE)
    if cockpit_key is None:
        service = getattr(request.app.state, "payment_service", None)
        return {
            "mode": "plan",
            "action": "pay_invoice",
            "policy": policy,
            "plan_hash": plan_hash_value,
            "plan": plan_view(service, amount_sat=amount_sat, purpose=purpose),
        }
    result = await execute_pay_invoice(
        request,
        params=params,
        amount_sat=amount_sat,
        plan_hash=plan_hash_value,
        cockpit_key=cockpit_key,
        hotp_code=hotp_code,
    )
    return {"mode": "execute", "action": "pay_invoice", "result": result}


async def execute_pay_invoice(
    request: Request,
    *,
    params: dict[str, Any],
    amount_sat: int,
    plan_hash: str,
    cockpit_key: str,
    hotp_code: str,
) -> dict[str, Any]:
    """Intent aufnehmen, ggf. freigeben, senden — alles im Control Plane."""
    service = service_of(request)
    purpose = str(params.get("purpose") or DEFAULT_PURPOSE)
    payment_request = str(params.get("payment_request", ""))
    if not payment_request:
        raise HTTPException(status_code=422, detail="payment_request is required")

    try:
        view = await service.create_intent(
            PaymentRequest(
                actor="operator",
                purpose=purpose,
                destination=payment_request,
                amount=_sat(amount_sat),
                fee_limit=_sat(derive_fee_limit(service, amount_sat)),
                correlation_id=plan_hash[:32] or "ln_control",
            ),
            bind_idempotency_key(plan_hash, cockpit_key),
        )
    except PaymentServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if view.status.value == "DENIED":
        # Die Begruendung kommt aus der Regelkette und nennt ihre Regel — anders
        # als der Freitext-``reason``, den der Bestand in einen 403 gab.
        raise HTTPException(
            status_code=403,
            detail={
                "policy": "denied",
                "rule_ids": list(view.decision.rule_ids) if view.decision else [],
                "reasons": list(view.decision.reasons) if view.decision else [],
            },
        )
    if view.status.value == "AWAITING_APPROVAL":
        if not hotp_code:
            raise HTTPException(
                status_code=403, detail="hotp code required: intent is AWAITING_APPROVAL"
            )
        try:
            service.authorize(view.intent_id, hotp_code)
        except PaymentServiceError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    try:
        result = await service.execute(view.intent_id)
    except PaymentServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "action": "pay_invoice",
        "route": "payment_control_plane",
        "intent_id": result.intent_id,
        "status": result.status.value,
        "replayed": result.replayed,
    }


__all__ = [
    "DEFAULT_PURPOSE",
    "bind_idempotency_key",
    "derive_fee_limit",
    "legacy_pay_invoice_moved",
    "execute_pay_invoice",
    "handle_pay_invoice",
    "plan_view",
    "service_of",
]
