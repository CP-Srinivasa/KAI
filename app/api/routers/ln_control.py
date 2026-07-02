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
from app.lightning.policy import PolicyStore, evaluate_policy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/api/ln", tags=["ln-control"])

# Process-local idempotency ledger for executed confirms (replay guard).
_seen_idempotency: set[str] = set()


def reset_control_state() -> None:
    """Test seam: clear the idempotency ledger."""
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


async def _available_balance_sat() -> int:
    """Best-effort on-chain+channel balance for the reserve-floor check (0 if
    unavailable → policy errs conservative: a spend with unknown balance is denied
    if a reserve floor is set)."""
    try:
        from app.lightning.cache import get_cached_node_status

        status, _ = await get_cached_node_status()
        return int(getattr(status, "wallet_total_sat", 0) or 0) + int(
            getattr(status, "channel_local_sat", 0) or 0
        )
    except Exception:  # noqa: BLE001 — balance is best-effort, never block the endpoint
        return 0


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

    amount = int(body.params.get(spec.amount_key, 0) or 0) if spec.amount_key else 0
    recipient = body.params.get(spec.recipient_key) if spec.recipient_key else None
    envelope = PolicyStore().load()
    available = await _available_balance_sat()
    decision = evaluate_policy(
        body.action,
        amount_sat=amount,
        recipient=recipient,
        spent_today_sat=0,  # TODO: sum today's executed sends from the ops ledger
        available_balance_sat=available,
        envelope=envelope,
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
