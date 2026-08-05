"""Lightning value layer (L4) — invoice creation + channel opening, HARD-GATED.

This is the only write path to KAI's funded lnd node. Every entry is gated, and
nothing moves real value unless the operator deliberately flips MULTIPLE gates:

  * ``pay_enabled`` (``APP_LN_PAY_ENABLED``, default False) — the master
    kill-switch. While False, NOTHING here touches the node.
  * ``dry_run`` (default True) — even with pay_enabled, the default is to return
    the PLAN only (no lnd write). The caller must pass ``dry_run=False``.
  * ``confirm`` (channel open only) — opening a channel SPENDS on-chain and is
    IRREVERSIBLE, so it additionally requires an explicit ``confirm=True``.

Invoice creation is receive-side (no spend) but still gated as L4. Enabling the
write surface also requires a SCOPE-MINIMAL macaroon on the node (invoices /
channel-open) — NEVER the readonly macaroon, NEVER admin. Default state is fully
inert: read-only Phase-1 behaviour is unchanged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.core.lightning_settings import LightningSettings
from app.lightning.adapter import _build_client
from app.lightning.client import LightningUnavailableError
from app.lightning.ops_ledger import LightningOpsLedgerError, append_ln_op, prepare_ln_intent


@dataclass(frozen=True)
class ValueLayerResult:
    """Outcome of a gated value-layer action. ``state`` is the honest disposition."""

    action: str  # "create_invoice" | "open_channel"
    state: str  # "disabled" | "planned" | "executed" | "error"
    detail: str = ""
    plan: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    intent_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "state": self.state,
            "detail": self.detail,
            "plan": self.plan,
            "response": self.response,
            "intent_id": self.intent_id,
        }


def _settings(cfg: LightningSettings | None) -> LightningSettings:
    if cfg is not None:
        return cfg
    from app.core.settings import get_settings

    return get_settings().lightning


# U1 fail-closed allowlist: the ONLY actions that may be classified receive-side
# (capital-free, gated by ``receive_enabled``). EVERYTHING else is a spend and gates
# on ``pay_enabled``. Keeping this a 1-element set of a spend-free action name means a
# future misclassification falls to the SAFE side (receive breaks, no spend opens).
RECEIVE_ACTIONS = frozenset({"create_invoice"})


def _assert_send_allowed(
    action: str,
    *,
    cfg: LightningSettings,
    dry_run: bool,
    confirm: bool,
    irreversible: bool,
    plan: dict[str, Any],
    direction: str = "send",
) -> ValueLayerResult | None:
    """B-002 — the SINGLE chokepoint every value-layer write must pass BEFORE the
    node is touched. Returns a terminal ``ValueLayerResult`` (disabled/planned) to
    short-circuit, or ``None`` when the action is cleared to execute.

    Centralising the gates here (instead of copy-pasting per method) means a new
    write method cannot silently forget one — and the reflection test
    (test_ln_value_layer_send_gate) structurally enforces that every public write
    routes through this function:

      * ``direction`` is declared EXPLICITLY by each call-site (next to
        ``irreversible=``). ``receive`` (capital-free invoice minting) gates on
        ``receive_enabled``; everything else gates on the ``pay_enabled`` kill-switch.
      * Fail-closed backstop: a non-allowlisted action declaring ``receive`` is a
        programming error and RAISES; any unrecognised ``direction`` uses the stricter
        send gate.
      * ``dry_run`` default → ``planned`` (plan only, no node write);
      * ``irreversible`` actions (on-chain spend / channel ops) additionally need an
        explicit ``confirm=True`` → else ``planned``.
    """
    if direction == "receive":
        if action not in RECEIVE_ACTIONS:
            raise ValueError(
                f"action {action!r} declared direction='receive' but is not in RECEIVE_ACTIONS"
            )
        if not cfg.receive_enabled:
            return ValueLayerResult(action, "disabled", "receive_enabled is False", plan)
    else:
        # send (default) — also the fail-closed branch for any unrecognised direction.
        if not cfg.pay_enabled:
            return ValueLayerResult(action, "disabled", "pay_enabled is False", plan)
    if dry_run:
        return ValueLayerResult(action, "planned", "dry_run", plan)
    if irreversible and not confirm:
        return ValueLayerResult(action, "planned", "confirm=False", plan)
    return None


async def create_invoice(
    *,
    value_sat: int,
    memo: str = "",
    dry_run: bool = True,
    intent_id: str | None = None,
    authorization: dict[str, Any] | None = None,
    cfg: LightningSettings | None = None,
) -> ValueLayerResult:
    """Create a BOLT11 invoice (receive-side, no spend) — gated + dry-run-default."""
    cfg = _settings(cfg)
    plan = {"value_sat": int(value_sat), "memo": memo}
    blocked = _assert_send_allowed(
        "create_invoice",
        cfg=cfg,
        dry_run=dry_run,
        confirm=True,
        irreversible=False,
        plan=plan,
        direction="receive",
    )
    if blocked is not None:
        return blocked
    if value_sat <= 0:
        return ValueLayerResult("create_invoice", "error", "value_sat must be > 0", plan)
    prepared, prepare_error = _prepare("create_invoice", plan, intent_id, authorization)
    if prepare_error is not None:
        return prepare_error
    try:
        resp = await _build_client(cfg, credential_scope="invoice").add_invoice(
            value_sat=value_sat, memo=memo
        )
    except LightningUnavailableError as exc:
        return _audit(
            ValueLayerResult("create_invoice", "error", str(exc), plan, intent_id=prepared)
        )
    return _audit(
        ValueLayerResult("create_invoice", "executed", "", plan, resp, intent_id=prepared)
    )


async def open_channel(
    *,
    node_pubkey_hex: str,
    local_funding_sat: int,
    sat_per_vbyte: int = 0,
    confirm: bool = False,
    dry_run: bool = True,
    intent_id: str | None = None,
    authorization: dict[str, Any] | None = None,
    cfg: LightningSettings | None = None,
) -> ValueLayerResult:
    """Open a channel — SPENDS on-chain, irreversible. Maximally gated.

    Requires ALL of: ``pay_enabled`` True, ``dry_run`` False, and explicit
    ``confirm`` True. Any missing gate returns ``planned``/``disabled`` WITHOUT
    touching the node.
    """
    cfg = _settings(cfg)
    plan = {
        "node_pubkey_hex": node_pubkey_hex,
        "local_funding_sat": int(local_funding_sat),
        "sat_per_vbyte": int(sat_per_vbyte),
    }
    blocked = _assert_send_allowed(
        "open_channel",
        cfg=cfg,
        dry_run=dry_run,
        confirm=confirm,
        irreversible=True,
        plan=plan,
        direction="send",
    )
    if blocked is not None:
        return blocked
    if not node_pubkey_hex or local_funding_sat <= 0:
        return ValueLayerResult(
            "open_channel", "error", "node_pubkey_hex + positive sats required", plan
        )
    prepared, prepare_error = _prepare("open_channel", plan, intent_id, authorization)
    if prepare_error is not None:
        return prepare_error
    try:
        resp = await _build_client(cfg, credential_scope="channel").open_channel(
            node_pubkey_hex=node_pubkey_hex,
            local_funding_sat=local_funding_sat,
            sat_per_vbyte=sat_per_vbyte,
        )
    except LightningUnavailableError as exc:
        return _audit(
            ValueLayerResult("open_channel", "error", str(exc), plan, intent_id=prepared)
        )
    return _audit(
        ValueLayerResult("open_channel", "executed", "", plan, resp, intent_id=prepared)
    )


def _prepare(
    action: str,
    plan: dict[str, Any],
    requested_intent_id: str | None,
    authorization: dict[str, Any] | None,
) -> tuple[str, ValueLayerResult | None]:
    """Write-ahead intent.  Failure returns a terminal no-node-touch result."""
    try:
        record = prepare_ln_intent(
            action,
            plan=plan,
            intent_id=requested_intent_id,
            authorization=authorization,
        )
    except LightningOpsLedgerError as exc:
        return "", ValueLayerResult(
            action,
            "error",
            f"intent journal unavailable; node not touched: {exc}",
            plan,
        )
    return str(record["intent_id"]), None


def _audit(result: ValueLayerResult) -> ValueLayerResult:
    """Append node-touching outcomes to the tamper-evident ops
    ledger. ``disabled``/``planned`` are non-events (no node touch) → not logged, so
    the inert default + dry-run previews don't spam the audit trail. ``unknown``
    deliberately leaves the write-ahead intent open for reconciliation."""
    if result.state in ("executed", "error", "in_flight", "unknown"):
        append_ln_op(
            result.action,
            result.state,
            plan=result.plan,
            response=result.response,
            intent_id=result.intent_id,
        )
    return result


async def pay_invoice(
    *,
    payment_request: str,
    fee_limit_sat: int = 0,
    dry_run: bool = True,
    confirm: bool = False,
    intent_id: str | None = None,
    authorization: dict[str, Any] | None = None,
    cfg: LightningSettings | None = None,
) -> ValueLayerResult:
    """Pay a BOLT11 invoice — SPENDS, irreversible. Max-gated (confirm required)."""
    cfg = _settings(cfg)
    plan = {"payment_request": payment_request, "fee_limit_sat": int(fee_limit_sat)}
    blocked = _assert_send_allowed(
        "pay_invoice",
        cfg=cfg,
        dry_run=dry_run,
        confirm=confirm,
        irreversible=True,
        plan=plan,
        direction="send",
    )
    if blocked is not None:
        return blocked
    if not payment_request:
        return ValueLayerResult("pay_invoice", "error", "payment_request required", plan)
    client = _build_client(cfg, credential_scope="payment")
    try:
        decoded = await client.decode_pay_req(payment_request)
    except LightningUnavailableError as exc:
        return ValueLayerResult(
            "pay_invoice", "error", f"invoice decode failed; no send attempted: {exc}", plan
        )
    payment_hash = str(decoded.get("payment_hash", "")).strip()
    if not payment_hash:
        return ValueLayerResult(
            "pay_invoice",
            "error",
            "invoice decode returned no payment_hash; no send attempted",
            plan,
        )
    plan["payment_hash"] = payment_hash
    try:
        decoded_amount = int(decoded.get("num_satoshis", 0) or 0)
    except (TypeError, ValueError):
        decoded_amount = 0
    if decoded_amount > 0:
        plan["amount_sat"] = decoded_amount
    try:
        created_at = int(decoded.get("timestamp", 0) or 0)
        expiry_seconds = int(decoded.get("expiry", 0) or 0)
    except (TypeError, ValueError):
        created_at = expiry_seconds = 0
    if created_at > 0 and expiry_seconds > 0:
        expires_at = created_at + expiry_seconds
        plan["expires_at_unix"] = expires_at
        if expires_at <= int(time.time()):
            return ValueLayerResult(
                "pay_invoice", "error", "invoice expired; no send attempted", plan
            )

    prepared, prepare_error = _prepare("pay_invoice", plan, intent_id, authorization)
    if prepare_error is not None:
        return prepare_error
    try:
        resp = await client.pay_invoice(
            payment_request=payment_request, fee_limit_sat=fee_limit_sat
        )
    except LightningUnavailableError as exc:
        # A transport timeout after submission is not evidence that the payment
        # failed. Query the router by the durable payment hash before classifying.
        try:
            tracked = await client.track_payment_v2(payment_hash)
        except LightningUnavailableError as track_exc:
            return _audit(
                ValueLayerResult(
                    "pay_invoice",
                    "unknown",
                    f"send outcome unknown ({exc}); TrackPaymentV2 unavailable ({track_exc})",
                    plan,
                    {"payment_hash": payment_hash, "status": "UNKNOWN"},
                    intent_id=prepared,
                )
            )
        status = str(tracked.get("status", "")).upper()
        if status == "SUCCEEDED":
            return _audit(
                ValueLayerResult(
                    "pay_invoice", "executed", "reconciled by TrackPaymentV2", plan, tracked,
                    intent_id=prepared,
                )
            )
        if status == "FAILED":
            return _audit(
                ValueLayerResult(
                    "pay_invoice", "error", "terminal failure from TrackPaymentV2", plan, tracked,
                    intent_id=prepared,
                )
            )
        return _audit(
            ValueLayerResult(
                "pay_invoice", "in_flight", f"send response unavailable: {exc}", plan, tracked,
                intent_id=prepared,
            )
        )
    payment_error = str(resp.get("payment_error", "")).strip()
    sync_status = "FAILED" if payment_error else "SUCCEEDED"
    # P3 shadow gate: compare every synchronous terminal result with the router's
    # durable payment database. The existing send primitive remains in place until
    # 20 real comparisons show zero semantic drift; this call is read-only.
    try:
        tracked = await client.track_payment_v2(payment_hash)
    except LightningUnavailableError:
        shadowed = {**resp, "sync_status": sync_status, "track_v2_status": "UNAVAILABLE"}
        state = "error" if payment_error else "executed"
        return _audit(
            ValueLayerResult(
                "pay_invoice", state, payment_error, plan, shadowed, intent_id=prepared
            )
        )

    track_status = str(tracked.get("status", "")).upper()
    shadowed = {
        **resp,
        "sync_status": sync_status,
        "track_v2_status": track_status or "UNKNOWN",
    }
    if track_status == sync_status:
        state = "error" if payment_error else "executed"
        return _audit(
            ValueLayerResult(
                "pay_invoice", state, payment_error, plan, shadowed, intent_id=prepared
            )
        )
    return _audit(
        ValueLayerResult(
            "pay_invoice",
            "unknown",
            f"SendPaymentSync/TrackPaymentV2 mismatch: {sync_status}/{track_status or 'UNKNOWN'}",
            plan,
            shadowed,
            intent_id=prepared,
        )
    )


async def keysend(
    *,
    dest_pubkey_hex: str,
    amt_sat: int,
    fee_limit_sat: int = 0,
    dry_run: bool = True,
    confirm: bool = False,
    intent_id: str | None = None,
    authorization: dict[str, Any] | None = None,
    cfg: LightningSettings | None = None,
) -> ValueLayerResult:
    """Spontaneous keysend payment — SPENDS, irreversible. Max-gated."""
    cfg = _settings(cfg)
    plan = {
        "dest_pubkey_hex": dest_pubkey_hex,
        "amt_sat": int(amt_sat),
        "fee_limit_sat": int(fee_limit_sat),
    }
    blocked = _assert_send_allowed(
        "keysend",
        cfg=cfg,
        dry_run=dry_run,
        confirm=confirm,
        irreversible=True,
        plan=plan,
        direction="send",
    )
    if blocked is not None:
        return blocked
    if not dest_pubkey_hex or amt_sat <= 0:
        return ValueLayerResult(
            "keysend", "error", "dest_pubkey_hex + positive amt required", plan
        )
    prepared, prepare_error = _prepare("keysend", plan, intent_id, authorization)
    if prepare_error is not None:
        return prepare_error
    try:
        resp = await _build_client(cfg, credential_scope="payment").keysend(
            dest_pubkey_hex=dest_pubkey_hex, amt_sat=amt_sat, fee_limit_sat=fee_limit_sat
        )
    except LightningUnavailableError as exc:
        return _audit(ValueLayerResult("keysend", "error", str(exc), plan, intent_id=prepared))
    return _audit(ValueLayerResult("keysend", "executed", "", plan, resp, intent_id=prepared))


async def send_coins(
    *,
    addr: str,
    amount_sat: int,
    sat_per_vbyte: int = 0,
    dry_run: bool = True,
    confirm: bool = False,
    intent_id: str | None = None,
    authorization: dict[str, Any] | None = None,
    cfg: LightningSettings | None = None,
) -> ValueLayerResult:
    """On-chain withdraw — SPENDS on-chain, irreversible. Max-gated."""
    cfg = _settings(cfg)
    plan = {"addr": addr, "amount_sat": int(amount_sat), "sat_per_vbyte": int(sat_per_vbyte)}
    blocked = _assert_send_allowed(
        "send_coins",
        cfg=cfg,
        dry_run=dry_run,
        confirm=confirm,
        irreversible=True,
        plan=plan,
        direction="send",
    )
    if blocked is not None:
        return blocked
    if not addr or amount_sat <= 0:
        return ValueLayerResult("send_coins", "error", "addr + positive amount required", plan)
    prepared, prepare_error = _prepare("send_coins", plan, intent_id, authorization)
    if prepare_error is not None:
        return prepare_error
    try:
        resp = await _build_client(cfg, credential_scope="onchain").send_coins(
            addr=addr, amount_sat=amount_sat, sat_per_vbyte=sat_per_vbyte
        )
    except LightningUnavailableError as exc:
        return _audit(ValueLayerResult("send_coins", "error", str(exc), plan, intent_id=prepared))
    return _audit(ValueLayerResult("send_coins", "executed", "", plan, resp, intent_id=prepared))


async def close_channel(
    *,
    funding_txid: str,
    output_index: int,
    force: bool = False,
    sat_per_vbyte: int = 0,
    dry_run: bool = True,
    confirm: bool = False,
    intent_id: str | None = None,
    authorization: dict[str, Any] | None = None,
    cfg: LightningSettings | None = None,
) -> ValueLayerResult:
    """Close a channel — irreversible (on-chain settle). Max-gated."""
    cfg = _settings(cfg)
    plan = {
        "funding_txid": funding_txid,
        "output_index": int(output_index),
        "force": bool(force),
        "sat_per_vbyte": int(sat_per_vbyte),
    }
    blocked = _assert_send_allowed(
        "close_channel",
        cfg=cfg,
        dry_run=dry_run,
        confirm=confirm,
        irreversible=True,
        plan=plan,
        direction="send",
    )
    if blocked is not None:
        return blocked
    if not funding_txid:
        return ValueLayerResult("close_channel", "error", "funding_txid required", plan)
    prepared, prepare_error = _prepare("close_channel", plan, intent_id, authorization)
    if prepare_error is not None:
        return prepare_error
    try:
        resp = await _build_client(cfg, credential_scope="channel").close_channel(
            funding_txid=funding_txid,
            output_index=output_index,
            force=force,
            sat_per_vbyte=sat_per_vbyte,
        )
    except LightningUnavailableError as exc:
        return _audit(
            ValueLayerResult("close_channel", "error", str(exc), plan, intent_id=prepared)
        )
    return _audit(
        ValueLayerResult("close_channel", "executed", "", plan, resp, intent_id=prepared)
    )


async def rebalance_plan(
    *,
    out_channel: str,
    in_channel: str,
    amount_sat: int,
    cfg: LightningSettings | None = None,
) -> ValueLayerResult:
    """Plan a circular rebalance — PLAN ONLY, never executes (dry_run forced True).

    Rebalancing is a circular self-payment; this helper returns the intended plan
    and never touches the node. It still routes through the central send-gate (so
    the kill-switch reports ``disabled`` when off) — the reflection test requires it.
    """
    cfg = _settings(cfg)
    plan = {"out_channel": out_channel, "in_channel": in_channel, "amount_sat": int(amount_sat)}
    blocked = _assert_send_allowed(
        "rebalance_plan",
        cfg=cfg,
        dry_run=True,
        confirm=False,
        irreversible=True,
        plan=plan,
        direction="send",
    )
    # dry_run forced True → the gate always returns a terminal disabled/planned result.
    return (
        blocked
        if blocked is not None
        else ValueLayerResult("rebalance_plan", "planned", "plan-only", plan)
    )
