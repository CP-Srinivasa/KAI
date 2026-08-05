"""Sprint 5 — Lightning value-layer control surface (POST), max-automation cockpit.

ONE chokepoint for every capital-effective action. Two modes per request:

  * **plan** (no ``confirm``) → returns the dry-run plan + the policy verdict
    (``auto_execute`` / ``needs_confirm`` / ``denied``) + the ``plan_hash`` the
    operator must echo back to execute. No node touch.
  * **execute** (``confirm`` present) → ``denied`` is refused; ``needs_confirm``
    requires a hardened B-005 confirm (matching plan_hash + fresh idempotency key +
    valid HOTP); ``auto_execute`` runs straight through (within the operator's
    envelope). The actual node write stays behind the value-layer send-gate (B-002)
    + ``pay_enabled`` — so this whole surface is INERT until G1.

Auth: served under ``/dashboard/*`` → the app-level email-allowlist middleware
applies (no service-token). The S-001 local-bypass hardening is a separate PR.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.lightning import value_layer as vl
from app.lightning.control_gate import plan_hash, verify_capital_confirm
from app.lightning.demand_evaluator import evaluate_l402_demand
from app.lightning.idempotency_store import PersistentSeenKeys
from app.lightning.ops_ledger import bolt11_amount_sat, spent_today_sat
from app.lightning.policy import PolicyDecision, PolicyStore, evaluate_policy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/api/ln", tags=["ln-control"])

# File-backed idempotency ledger for executed confirms (replay guard). Persisted +
# bounded so a process restart cannot re-open the replay window (a process-local set
# forgot every consumed key on reboot). See app.lightning.idempotency_store.
_seen_idempotency: PersistentSeenKeys = PersistentSeenKeys()


def reset_control_state() -> None:
    """Test seam: clear the idempotency ledger (in memory AND on disk)."""
    _seen_idempotency.clear()


@dataclass(frozen=True)
class _ActionSpec:
    fn: Callable[..., Any]
    amount_key: str | None
    recipient_key: str | None
    irreversible: bool


# action → value-layer fn + how to read its (amount, recipient) for the policy.
_ACTIONS: dict[str, _ActionSpec] = {
    "create_invoice": _ActionSpec(vl.create_invoice, None, None, irreversible=False),
    "pay_invoice": _ActionSpec(vl.pay_invoice, None, None, irreversible=True),
    "keysend": _ActionSpec(vl.keysend, "amt_sat", "dest_pubkey_hex", irreversible=True),
    "send_coins": _ActionSpec(vl.send_coins, "amount_sat", "addr", irreversible=True),
    "open_channel": _ActionSpec(
        vl.open_channel, "local_funding_sat", "node_pubkey_hex", irreversible=True
    ),
    "close_channel": _ActionSpec(vl.close_channel, None, None, irreversible=True),
}


class ConfirmBody(BaseModel):
    hotp: str
    plan_hash: str
    idempotency_key: str


class ActionBody(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    confirm: ConfirmBody | None = None


# Freshness-Gate (ADR 0016, Welle 0): Höchstalter des Kontostands, gegen den der
# Reserve-Floor entscheiden darf. Grosszügig gegen die 30-s-Cache-TTL bemessen —
# gedeckelt werden soll das unbegrenzte Einfrieren, nicht der normale Poll-Jitter.
_BALANCE_MAX_AGE_S = 120.0


async def _available_balance_sat() -> tuple[int, bool]:
    """On-chain+channel balance für die Floor-Prüfung, als ``(sat, known)``.

    ``known=False`` heisst „kein prüfbarer Kontostand", NICHT „Kontostand ist 0" —
    der Unterschied trägt die Begründung im Verdikt.

    Der Node-Cache hält bei degradierten Polls bewusst den älteren, reicheren
    Snapshot fest (Anti-Flicker in ``app.lightning.cache._merge``) und rückt seinen
    Zeitstempel dabei nicht vor. lnd über Tor produziert genau diesen Fall
    regelmässig. Wer das Alter verwirft, lässt den Floor — laut Policy ein harter,
    nicht überschreibbarer Backstop — gegen einen beliebig alten Stand rechnen,
    während der Node weiter ausgibt. Deshalb: Alter prüfen, und ein Snapshot ohne
    frische Balance-Felder zählt nicht, auch wenn er jung ist.
    """
    try:
        from app.lightning.cache import get_cached_node_status

        status, age = await get_cached_node_status()
        if age is None or age > _BALANCE_MAX_AGE_S:
            return 0, False
        if not getattr(status, "balances_available", False):
            return 0, False
        total = int(getattr(status, "wallet_total_sat", 0) or 0) + int(
            getattr(status, "channel_local_sat", 0) or 0
        )
        return total, True
    except Exception:  # noqa: BLE001 — balance is best-effort, never block the endpoint
        return 0, False


def _effective_amount_sat(
    action: str, params: dict[str, Any], spec: _ActionSpec
) -> tuple[int, bool]:
    """Outgoing spend amount for the policy + whether it is KNOWN.

    ``pay_invoice`` carries NO amount param — the sat value is encoded in the BOLT11
    invoice itself. Without parsing it the policy would see 0 and wave the payment
    through as ``auto_execute`` (bypassing per-action/daily cap, the reserve-floor
    backstop AND the HOTP confirm-threshold) — a covert spend hole on the primary
    spend action. We derive it from the invoice HRP; an amountless invoice returns
    ``known=False`` so the caller fails closed to ``needs_confirm``.
    """
    if action == "pay_invoice":
        amt = bolt11_amount_sat(str(params.get("payment_request", "")))
        return amt, amt > 0
    if spec.amount_key:
        return int(params.get(spec.amount_key, 0) or 0), True
    return 0, True  # non-spend actions (create_invoice / close_channel)


def _build_hotp_verifier() -> Any:
    from pathlib import Path

    from app.security.hotp_auth import HotpVerifier

    ln = get_settings().lightning
    return HotpVerifier(seed_path=Path(ln.hotp_seed_path), journal_path=Path(ln.hotp_journal_path))


@router.post("/value-action")
async def value_action(request: Request, body: ActionBody) -> dict[str, Any]:
    """Plan or execute a gated value-layer action (inert until pay_enabled)."""
    spec = _ACTIONS.get(body.action)
    if spec is None:
        raise HTTPException(status_code=422, detail=f"unknown action: {body.action}")

    amount, amount_known = _effective_amount_sat(body.action, body.params, spec)
    recipient = body.params.get(spec.recipient_key) if spec.recipient_key else None
    envelope = PolicyStore().load()
    available, balance_known = await _available_balance_sat()
    decision = evaluate_policy(
        body.action,
        amount_sat=amount,
        recipient=recipient,
        # Gesamtaudit-P0 geschlossen: Tages-Cap zählt jetzt die real executed,
        # wert-abfließenden Sends des UTC-Tages aus dem Ops-Ledger. Für pay_invoice
        # stammt ``amount`` aus dem BOLT11 (nicht aus params) → Caps/Floor greifen jetzt.
        spent_today_sat=spent_today_sat(),
        available_balance_sat=available,
        envelope=envelope,
    )
    # Fail-closed: a spend whose amount we could NOT determine (amountless BOLT11)
    # must never auto-execute — force operator confirm (HOTP) instead of silent pass.
    if not amount_known and decision.decision == "auto_execute":
        decision = PolicyDecision("needs_confirm", "amount unknown (amountless invoice)")
    # Freshness-Gate (ADR 0016, Welle 0): Der Reserve-Floor ist ein harter Backstop.
    # Ohne prüfbaren Kontostand ist er nicht entscheidbar — und ein HOTP-Confirm macht
    # den Stand nicht frisch, also `denied` statt `needs_confirm`.
    #
    # Die Policy verweigert einen solchen Spend zwar ohnehin (unbekannt kommt als 0
    # an, und `0 - amount < floor` greift schon bei floor=0). Sie begründet das aber
    # mit "would breach reserve floor" — einer Aussage über Kapital, das niemand
    # gemessen hat. Der Unterschied ist nicht kosmetisch: Genau diese Verwechslung
    # macht aus einem Messausfall im Audit einen Kapitalbefund.
    if amount > 0 and not balance_known:
        decision = PolicyDecision("denied", "balance stale or unavailable — spend unverifiable")
    ph = plan_hash(body.action, body.params)

    async def _call(**extra: Any) -> Any:
        try:
            return await spec.fn(**body.params, **extra)
        except TypeError as exc:  # bad/typo'd params for this action
            raise HTTPException(status_code=422, detail=f"invalid params: {exc}") from exc

    # ── plan mode: preview only, no execution ────────────────────────────────
    if body.confirm is None:
        # dry_run=True → the value-layer returns the plan (disabled/planned) without
        # touching the node, for every action (irreversible or not).
        plan = await _call(dry_run=True)
        return {
            "mode": "plan",
            "action": body.action,
            "policy": {"decision": decision.decision, "reason": decision.reason},
            "plan_hash": ph,
            "plan": plan.to_dict(),
        }

    # ── execute mode ─────────────────────────────────────────────────────────
    if decision.decision == "denied":
        raise HTTPException(status_code=403, detail=f"policy denied: {decision.reason}")
    if decision.decision == "needs_confirm":
        verdict = verify_capital_confirm(
            hotp_verifier=_build_hotp_verifier(),
            hotp_code=body.confirm.hotp,
            submitted_plan_hash=body.confirm.plan_hash,
            expected_plan_hash=ph,
            idempotency_key=body.confirm.idempotency_key,
            seen_keys=_seen_idempotency,
        )
        if not verdict.ok:
            raise HTTPException(status_code=403, detail=f"confirm rejected: {verdict.reason}")

    # NOTE (satoshi U2/auflage-6): create_invoice mints a real invoice here WITHOUT the
    # public S-002 mint-limiter (that guards the unauthenticated /oracle path). This is
    # deliberate: this cockpit surface is operator-only (the /dashboard/* email-allowlist
    # middleware), so it is not an anonymous mint-flood vector. The public mint path
    # (truth_oracle) carries the rate-limit + the trusted-client-IP key.
    result = (
        await _call(dry_run=False, confirm=True)
        if spec.irreversible
        else await _call(dry_run=False)
    )
    return {"mode": "execute", "action": body.action, "result": result.to_dict()}


@router.get("/demand")
async def demand_verdict() -> dict[str, Any]:
    """G0 demand-probe verdict (U4) — read-only over the demand + earnings ledgers.

    Surfaces the pre-registered G0 metrics (challenges, settled payments, distinct
    fingerprints/days) + the PASS/NO-PASS verdict. No node, no capital."""
    return evaluate_l402_demand()
