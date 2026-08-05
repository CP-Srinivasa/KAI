"""Sprint 5 — Lightning value-layer control surface (POST), max-automation cockpit.

ONE chokepoint for every capital-effective action. Two modes per request:

  * **plan** (no ``confirm``) → returns the dry-run plan + the policy verdict
    (``auto_execute`` / ``needs_confirm`` / ``denied``) + the ``plan_hash`` the
    operator must echo back to execute. No node touch.
  * **execute** (``confirm`` present) → ``denied`` is refused; ``needs_confirm``
    requires a hardened B-005 confirm (matching plan_hash + fresh idempotency key +
    valid HOTP); ``auto_execute`` needs NO HOTP but still binds to the previewed
    plan_hash and burns a fresh idempotency key (W0-P4 — the auto path used to
    skip both, leaving replay + plan substitution open). The actual node write
    stays behind the value-layer send-gate (B-002) + ``pay_enabled``.

Capital actions (every ``irreversible`` spec) additionally require a FRESH,
balance-bearing node snapshot (W0-P1): stale/unavailable state ⇒ hard deny —
never evaluated against a cached balance.

Auth: served under ``/dashboard/*`` → the app-level email-allowlist middleware
applies (no service-token). The S-001 local-bypass hardening is a separate PR.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.lightning import value_layer as vl
from app.lightning.adapter import LightningBalanceSnapshot, get_fresh_available_balance
from app.lightning.control_gate import (
    plan_hash,
    verify_auto_execute_confirm,
    verify_capital_confirm,
)
from app.lightning.demand_evaluator import evaluate_l402_demand
from app.lightning.idempotency_store import PersistentSeenKeys
from app.lightning.ops_ledger import bolt11_amount_sat, spent_today_sat
from app.lightning.payment_reconciliation import reconcile_spent_today
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
    risk_class: Literal[
        "receive", "offchain_spend", "onchain_spend", "channel_open", "channel_close"
    ]


# action → value-layer fn + how to read its (amount, recipient) for the policy.
_ACTIONS: dict[str, _ActionSpec] = {
    "create_invoice": _ActionSpec(
        vl.create_invoice, None, None, irreversible=False, risk_class="receive"
    ),
    "pay_invoice": _ActionSpec(
        vl.pay_invoice, None, None, irreversible=True, risk_class="offchain_spend"
    ),
    "keysend": _ActionSpec(
        vl.keysend,
        "amt_sat",
        "dest_pubkey_hex",
        irreversible=True,
        risk_class="offchain_spend",
    ),
    "send_coins": _ActionSpec(
        vl.send_coins,
        "amount_sat",
        "addr",
        irreversible=True,
        risk_class="onchain_spend",
    ),
    "open_channel": _ActionSpec(
        vl.open_channel,
        "local_funding_sat",
        "node_pubkey_hex",
        irreversible=True,
        risk_class="channel_open",
    ),
    "close_channel": _ActionSpec(
        vl.close_channel, None, None, irreversible=True, risk_class="channel_close"
    ),
}


class ConfirmBody(BaseModel):
    hotp: str
    plan_hash: str
    idempotency_key: str


class ActionBody(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    confirm: ConfirmBody | None = None


def _classify_action_risk(
    decision: PolicyDecision,
    *,
    action: str,
    spec: _ActionSpec,
    params: dict[str, Any],
    fresh_balance: LightningBalanceSnapshot | None,
) -> PolicyDecision:
    """Apply non-amount risk rules after the operator envelope.

    The envelope remains the configurable budget layer.  This function supplies
    invariant action semantics that an amount of zero cannot bypass: capital
    actions require a synchronous balance observation; on-chain/channel actions
    are never automatic; and force-close is not executable through this API.
    """
    if spec.risk_class != "receive":
        if fresh_balance is None or fresh_balance.state != "ok":
            reason = fresh_balance.reason if fresh_balance is not None else "not observed"
            return PolicyDecision("denied", f"fresh node balance unavailable: {reason}")
    if decision.decision == "denied":
        return decision
    if action == "close_channel" and bool(params.get("force", False)):
        return PolicyDecision("denied", "force close is prohibited on the API control surface")
    if spec.risk_class in {"onchain_spend", "channel_open", "channel_close"}:
        return PolicyDecision("needs_confirm", f"{spec.risk_class} is manual-only")
    return decision


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
    reserved = {"cfg", "dry_run", "confirm", "intent_id", "authorization"}.intersection(
        body.params
    )
    if reserved:
        raise HTTPException(
            status_code=422,
            detail=f"reserved params are controlled by the value gate: {sorted(reserved)}",
        )

    amount, amount_known = _effective_amount_sat(body.action, body.params, spec)
    recipient = body.params.get(spec.recipient_key) if spec.recipient_key else None
    envelope = PolicyStore().load()
    fresh_balance = (
        None if spec.risk_class == "receive" else await get_fresh_available_balance()
    )
    available = fresh_balance.available_balance_sat if fresh_balance is not None else 0
    ledger_spent = spent_today_sat()
    spend_reconciliation: dict[str, Any] = (
        {
            "effective_spent_sat": ledger_spent,
            "gap_sat": None,
            "available": False,
        }
        if spec.risk_class == "receive"
        else await reconcile_spent_today(
            cfg=get_settings().lightning,
            ledger_spent_sat=ledger_spent,
        )
    )
    if spend_reconciliation.get("gap_sat") not in (None, 0):
        logger.warning("LN daily-spend ledger/LND drift: %s", spend_reconciliation)
    decision = evaluate_policy(
        body.action,
        amount_sat=amount,
        recipient=recipient,
        # Gesamtaudit-P0 geschlossen: Tages-Cap zählt jetzt die real executed,
        # wert-abfließenden Sends des UTC-Tages aus dem Ops-Ledger. Für pay_invoice
        # stammt ``amount`` aus dem BOLT11 (nicht aus params) → Caps/Floor greifen jetzt.
        spent_today_sat=int(spend_reconciliation["effective_spent_sat"]),
        available_balance_sat=available,
        envelope=envelope,
    )
    # Fail-closed: a spend whose amount we could NOT determine (amountless BOLT11)
    # must never auto-execute — force operator confirm (HOTP) instead of silent pass.
    if not amount_known and decision.decision == "auto_execute":
        decision = PolicyDecision("needs_confirm", "amount unknown (amountless invoice)")
    decision = _classify_action_risk(
        decision,
        action=body.action,
        spec=spec,
        params=body.params,
        fresh_balance=fresh_balance,
    )
    if spec.risk_class == "offchain_spend" and not spend_reconciliation.get("available"):
        decision = PolicyDecision(
            "denied", "LND ListPayments daily-spend reconciliation unavailable"
        )
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
    else:
        # W0-P4: auto_execute keeps max automation (no HOTP) but is no longer a
        # free pass — the request must echo the previewed plan_hash (no param
        # substitution) and burn a fresh idempotency key (no replay).
        verdict = verify_auto_execute_confirm(
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
        await _call(
            dry_run=False,
            confirm=True,
            intent_id=body.confirm.idempotency_key,
            authorization={
                "policy_decision": decision.decision,
                "confirmation": "hotp" if decision.decision == "needs_confirm" else "auto",
                "plan_hash": ph,
            },
        )
        if spec.irreversible
        else await _call(
            dry_run=False,
            intent_id=body.confirm.idempotency_key,
            authorization={
                "policy_decision": decision.decision,
                "confirmation": "auto",
                "plan_hash": ph,
            },
        )
    )
    return {"mode": "execute", "action": body.action, "result": result.to_dict()}


@router.get("/demand")
async def demand_verdict() -> dict[str, Any]:
    """G0 demand-probe verdict (U4) — read-only over the demand + earnings ledgers.

    Surfaces the pre-registered G0 metrics (challenges, settled payments, distinct
    fingerprints/days) + the PASS/NO-PASS verdict. No node, no capital."""
    return evaluate_l402_demand()
