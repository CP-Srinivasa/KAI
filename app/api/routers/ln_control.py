"""Lightning-Cockpit (POST) — was nach dem Rueckbau davon uebrig ist.

**Es gibt genau einen Sendeweg, und er liegt nicht mehr hier.** Bis ADR 0018
§12 fuehrte dieses Modul eine zweite, vollstaendige Geldkette: eigene Policy
(``lightning/policy.py``), eigenes Journal (``ops_ledger`` v2), eigener
Idempotenz-Store, eigenes HOTP-Gate. ``pay_invoice`` ist seit §12 an
``ln_control_delegate`` delegiert; PR 1 entfernt die Kette, die daneben
weiterlief, statt sie als Attrappe stehen zu lassen.

Zwei Aktionen bleiben:

  * ``pay_invoice`` → Payment Control Plane. Die Zeremonie (Regelkette,
    Freigabeschwelle, HOTP, Idempotenz am Journal) laeuft dort, nicht hier;
    dieses Modul liefert nur ``plan_hash`` und den aus dem BOLT11 abgeleiteten
    Betrag und reicht weiter.
  * ``create_invoice`` → Empfangs-Gate (``lightning/receive_gate``). Kapitalfrei,
    hinter ``receive_enabled`` und der Operator-Auth des ``/dashboard/*``-Pfads.

``keysend``, ``send_coins``, ``open_channel`` und ``close_channel`` sind
ENTFERNT. ADR §1 fuehrt sie als DEFERRED; die Regelkette des Control Plane
haette sie mit ``unsupported_action`` abgelehnt. Ein Menue-Eintrag, der nur
existiert, um abgelehnt zu werden, sieht aus wie eine Faehigkeit.

**Was mit ``lightning/policy.py`` verschwindet — ausdruecklich benannt.** Der
Operator-Envelope (``artifacts/ln_policy.json``, ``allowed_actions``) faellt
mit dem Modul. Fuer ``pay_invoice`` war er schon vor PR 1 wirkungslos (die
Delegations-Abzweigung stand vor dem 403). Fuer ``create_invoice`` war er ein
zweiter Schalter neben ``receive_enabled``; uebrig bleiben ``receive_enabled``,
die Operator-Auth und die Plan-Bindung unten. Der Reserve-Boden des Envelopes
ist NICHT verschwunden — er steht als Regel ``reserve_floor`` in
``app/payments/policy.py`` und als ``APP_PAYMENT_RESERVE_FLOOR_SAT``.

Auth: unter ``/dashboard/*``. Die S-001-Haertung
(``app/security/auth.py::_requires_strong_auth``) verlangt fuer jeden
``/dashboard/api/ln/*``-Pfad echte Auth (CF-Access ODER Bearer) auch von
127.0.0.1 — ``/value-action`` ist in keiner Read-Allowlist.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routers import ln_control_delegate as delegate
from app.lightning import receive_gate
from app.lightning.demand_evaluator import evaluate_l402_demand
from app.payments.rails.lightning_mapping import bolt11_amount_sat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/api/ln", tags=["ln-control"])

#: Die zwei Aktionen, die dieses Cockpit noch kennt. Ein Register statt einer
#: Kette von ``if``: eine unbekannte Aktion ist ein 422, kein stiller Durchlauf.
ACTIONS = frozenset({"pay_invoice", "create_invoice"})

# Das Gate besitzt diese kwargs; ein Aufrufer darf sie nie ueber ``params``
# einschmuggeln. Ein injiziertes ``authorization`` schriebe eine LUEGE in den
# Audit-Trail ("per HOTP bestaetigt" auf einer nicht bestaetigten Aktion).
_RESERVED_PARAMS = frozenset({"cfg", "dry_run", "confirm", "intent_id", "authorization"})


class ConfirmBody(BaseModel):
    hotp: str = ""
    plan_hash: str
    idempotency_key: str


class ActionBody(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    confirm: ConfirmBody | None = None


@router.post("/value-action")
async def value_action(request: Request, body: ActionBody) -> dict[str, Any]:
    """Plane oder fuehre eine gegatete Wert-Schicht-Aktion aus."""
    if body.action not in ACTIONS:
        raise HTTPException(status_code=422, detail=f"unknown action: {body.action}")
    reserved = _RESERVED_PARAMS.intersection(body.params)
    if reserved:
        raise HTTPException(
            status_code=422,
            detail=f"reserved params are controlled by the value gate: {sorted(reserved)}",
        )
    ph = delegate.plan_hash(body.action, body.params)

    if body.action == "pay_invoice":
        # Der Betrag steht NICHT in den Params — er ist im BOLT11 kodiert. Ohne
        # ihn saehe die Regelkette 0 und jede Betrags-Regel liefe ins Leere.
        amount_sat = bolt11_amount_sat(str(body.params.get("payment_request", "")))
        return await delegate.handle_pay_invoice(
            request,
            body.params,
            amount_sat=amount_sat,
            plan_hash_value=ph,
            policy={"decision": "payment_control_plane", "reason": "ADR 0018 §12"},
            cockpit_key=body.confirm.idempotency_key if body.confirm else None,
            hotp_code=body.confirm.hotp if body.confirm else "",
        )

    return await _mint(body, plan_hash_value=ph)


async def _mint(body: ActionBody, *, plan_hash_value: str) -> dict[str, Any]:
    """``create_invoice`` — kapitalfrei, aber an den vorgeschauten Plan gebunden.

    Die Bindung bleibt, das HOTP nicht: sie verhindert, dass zwischen Vorschau
    und Ausfuehrung andere Parameter untergeschoben werden, und das gilt auch
    fuer einen Betrag, der nur hereinkommt. Eine zweites Mal gepraegte Rechnung
    dagegen kostet nichts — niemand zahlt sie doppelt —, weshalb hier kein
    persistenter Replay-Speicher steht. Der, den es gab, existierte fuer den
    Sendepfad; er ist mit ihm gegangen.
    """
    try:
        if body.confirm is None:
            plan = await receive_gate.create_invoice(**body.params, dry_run=True)
            return {
                "mode": "plan",
                "action": body.action,
                "policy": {"decision": "receive_gate", "reason": "capital-free mint"},
                "plan_hash": plan_hash_value,
                "plan": plan.to_dict(),
            }
        if body.confirm.plan_hash != plan_hash_value:
            raise HTTPException(
                status_code=403,
                detail="confirm rejected: plan hash mismatch (plan changed since preview)",
            )
        if not body.confirm.idempotency_key:
            raise HTTPException(
                status_code=403, detail="confirm rejected: idempotency key required"
            )
        result = await receive_gate.create_invoice(
            **body.params,
            dry_run=False,
            intent_id=body.confirm.idempotency_key,
            authorization={"policy_decision": "receive_gate", "plan_hash": plan_hash_value},
        )
    except TypeError as exc:  # falsche/vertippte Params fuer diese Aktion
        raise HTTPException(status_code=422, detail=f"invalid params: {exc}") from exc
    return {"mode": "execute", "action": body.action, "result": result.to_dict()}


@router.get("/demand")
async def demand_verdict() -> dict[str, Any]:
    """G0 demand-probe verdict (U4) — read-only over the demand + earnings ledgers.

    Surfaces the pre-registered G0 metrics (challenges, settled payments, distinct
    fingerprints/days) + the PASS/NO-PASS verdict. No node, no capital."""
    return evaluate_l402_demand()
