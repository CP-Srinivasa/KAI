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

Capital actions additionally require TWO fail-closed preconditions (W0-P1 / PR-C):
a FRESH, balance-bearing node snapshot — stale/unavailable state ⇒ hard deny, never
evaluated against a cached balance — and a VERIFIABLE money journal, because a spend
that cannot be journalled ahead of time must not happen. Whether an action is
"capital" is not decided here: ``policy.ACTION_RISK_CLASSES`` is the single taxonomy
(M-8) and this module derives from it via ``is_capital_action``.

Auth: served under ``/dashboard/*``. The S-001 local-bypass hardening IS in place
(``app/security/auth.py::_requires_strong_auth``): every ``/dashboard/api/ln/*``
path needs real auth (CF-Access OR Bearer) even from 127.0.0.1 unless it is one
of five allowlisted read-only endpoints — ``/value-action`` is not among them.
The earlier note ("a separate PR") was stale and read as if this control surface
were still locally reachable (SENTR).

ADR 0018 §12: ``pay_invoice`` is delegated to the Payment Control Plane
(``ln_control_delegate``) — exactly ONE send path. The confirm ceremony below
(plan_hash binding, fresh idempotency key, HOTP) is unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routers import ln_control_delegate as delegate
from app.api.routers.ln_control_gates import (
    _available_balance_sat,
    _build_hotp_verifier,
    _fresh_capital_balance_sat,
    _money_journal_blocker,
)
from app.lightning import value_layer as vl
from app.lightning.control_gate import (
    plan_hash,
    verify_auto_execute_confirm,
    verify_capital_confirm,
)
from app.lightning.demand_evaluator import evaluate_l402_demand
from app.lightning.idempotency_store import PersistentSeenKeys
from app.lightning.ops_ledger import bolt11_amount_sat, spent_today_sat_v2
from app.lightning.policy import PolicyDecision, PolicyStore, evaluate_policy, is_capital_action

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
    """How to CALL an action — deliberately no risk/capital flag of its own.

    M-8: this used to carry an ``irreversible`` boolean next to
    ``policy.ACTION_RISK_CLASSES``. Two registers of the same fact drift silently,
    and the one that guards money (freshness gate, journal gate, confirm) was the
    copy nobody attested. The spec now describes only the call shape; every
    capital question is answered by ``policy.is_capital_action``.
    """

    fn: Callable[..., Any]
    amount_key: str | None
    recipient_key: str | None


# action → value-layer fn + how to read its (amount, recipient) for the policy.
# INVARIANT (reflection-tested): this register and ``ACTION_RISK_CLASSES`` name
# exactly the same actions — an action reachable here but unclassified there is
# denied by the policy, an action classified there but unreachable here is dead spec.
_ACTIONS: dict[str, _ActionSpec] = {
    "create_invoice": _ActionSpec(vl.create_invoice, None, None),
    # ADR §12: Eintrag haelt die Taxonomie-Invariante, Funktion wird nie gerufen.
    "pay_invoice": _ActionSpec(delegate.legacy_pay_invoice_moved, None, None),
    "keysend": _ActionSpec(vl.keysend, "amt_sat", "dest_pubkey_hex"),
    "send_coins": _ActionSpec(vl.send_coins, "amount_sat", "addr"),
    "open_channel": _ActionSpec(vl.open_channel, "local_funding_sat", "node_pubkey_hex"),
    "close_channel": _ActionSpec(vl.close_channel, None, None),
}

# The value gate owns these kwargs; a caller must never be able to smuggle them in
# via ``params`` (an injected ``authorization`` would write a LIE into the money
# journal — "confirmed by HOTP" on an auto-executed action).
_RESERVED_PARAMS = frozenset({"cfg", "dry_run", "confirm", "intent_id", "authorization"})


class ConfirmBody(BaseModel):
    hotp: str
    plan_hash: str
    idempotency_key: str


class ActionBody(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    confirm: ConfirmBody | None = None


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


@router.post("/value-action")
async def value_action(request: Request, body: ActionBody) -> dict[str, Any]:
    """Plan or execute a gated value-layer action (inert until pay_enabled)."""
    spec = _ACTIONS.get(body.action)
    if spec is None:
        raise HTTPException(status_code=422, detail=f"unknown action: {body.action}")
    reserved = _RESERVED_PARAMS.intersection(body.params)
    if reserved:
        raise HTTPException(
            status_code=422,
            detail=f"reserved params are controlled by the value gate: {sorted(reserved)}",
        )

    capital = is_capital_action(body.action)  # M-8: the single taxonomy decides
    amount, amount_known = _effective_amount_sat(body.action, body.params, spec)
    recipient = body.params.get(spec.recipient_key) if spec.recipient_key else None
    envelope = PolicyStore().load()
    # W0-P1: capital actions are evaluated ONLY against a fresh node snapshot; the
    # dashboard cache may serve stale, money may not.
    fresh_capital_sat: int | None = None
    if capital:
        fresh_capital_sat = await _fresh_capital_balance_sat()
        available = fresh_capital_sat if fresh_capital_sat is not None else 0
    else:
        available = await _available_balance_sat()
    spent_today = spent_today_sat_v2()
    decision = evaluate_policy(
        body.action,
        amount_sat=amount,
        recipient=recipient,
        # Gesamtaudit-P0 geschlossen: Tages-Cap zählt jetzt die real executed,
        # wert-abfließenden Sends aus dem v2-Geldjournal (inkl. offener Intents und
        # des m-15-Rolling-Fensters). Für pay_invoice stammt ``amount`` aus dem
        # BOLT11 (nicht aus params) → Caps/Floor greifen jetzt.
        # UNKNOWN is converted to 0 only for the pure evaluator's type contract;
        # the capital gate below replaces its provisional verdict with hard deny.
        spent_today_sat=0 if spent_today is None else spent_today,
        available_balance_sat=available,
        envelope=envelope,
    )
    # Fail-closed: a spend whose amount we could NOT determine (amountless BOLT11)
    # must never auto-execute — force operator confirm (HOTP) instead of silent pass.
    if not amount_known and decision.decision == "auto_execute":
        decision = PolicyDecision("needs_confirm", "amount unknown (amountless invoice)")
    # m-13 (order preserved from #638): both hard denies below apply ONLY to an
    # action the envelope actually allows. Otherwise a stale node or a broken
    # journal would MASK the more fundamental verdict ("action not allowed") and the
    # operator would chase node health while the envelope is what refuses.
    if capital and body.action in envelope.allowed_actions:
        # W0-P1 fail-closed: no fresh balance-bearing snapshot ⇒ no capital action.
        if fresh_capital_sat is None:
            decision = PolicyDecision(
                "denied", "node state stale/unavailable — capital action fails closed"
            )
        # W0-B1: missing/unreadable/invalid v2 history is not "spent 0 today".
        # Granting the full cap from an unknown baseline would reopen the budget.
        if spent_today is None:
            decision = PolicyDecision(
                "denied", "daily spend cap unknown — money journal must be repaired"
            )
        # PR-C fail-closed: no write-ahead journal ⇒ no spend. Evaluated last so its
        # reason wins over the staleness reason: an unaccountable spend is the deeper
        # blocker, and it is the one the operator must repair first.
        journal_blocker = _money_journal_blocker()
        if journal_blocker:
            decision = PolicyDecision("denied", journal_blocker)
    ph = plan_hash(body.action, body.params)

    async def _call(**extra: Any) -> Any:
        try:
            return await spec.fn(**body.params, **extra)
        except TypeError as exc:  # bad/typo'd params for this action
            raise HTTPException(status_code=422, detail=f"invalid params: {exc}") from exc

    # ── ADR 0018 §12: pay_invoice gehoert dem Control Plane ──────────────────
    if body.action == "pay_invoice":
        amount_sat, _ = _effective_amount_sat("pay_invoice", body.params, spec)
        return await delegate.handle_pay_invoice(
            request,
            body.params,
            amount_sat=amount_sat,
            plan_hash_value=ph,
            policy={"decision": decision.decision, "reason": decision.reason},
            cockpit_key=body.confirm.idempotency_key if body.confirm else None,
            hotp_code=body.confirm.hotp if body.confirm else "",
        )

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
    #
    # The confirm ceremony is carried INTO the money journal: the burned idempotency
    # key becomes the intent id (so a replayed request cannot open a second intent
    # either) and the authorisation triple records under which verdict this spend was
    # released. ``authorization`` is writable only from here — params carrying it were
    # rejected above.
    authorization = {
        "policy_decision": decision.decision,
        "confirmation": "hotp" if decision.decision == "needs_confirm" else "auto",
        "plan_hash": ph,
    }
    extra: dict[str, Any] = {
        "dry_run": False,
        "intent_id": body.confirm.idempotency_key,
        "authorization": authorization,
    }
    if capital:
        extra["confirm"] = True
    result = await _call(**extra)
    return {"mode": "execute", "action": body.action, "result": result.to_dict()}


@router.get("/demand")
async def demand_verdict() -> dict[str, Any]:
    """G0 demand-probe verdict (U4) — read-only over the demand + earnings ledgers.

    Surfaces the pre-registered G0 metrics (challenges, settled payments, distinct
    fingerprints/days) + the PASS/NO-PASS verdict. No node, no capital."""
    return evaluate_l402_demand()
