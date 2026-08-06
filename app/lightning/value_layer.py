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
channel-open) — NEVER the readonly macaroon, NEVER admin. Since W0/PR-C every
method requests its OWN capability credential (``credential_scope``); there is no
promotion to the read macaroon. Default state is fully inert: read-only Phase-1
behaviour is unchanged.

**Journalling is deliberately ASYMMETRIC (W0/PR-C).** A spend and a receive have
opposite failure economics, so they get opposite failure modes:

  * **SPEND** (pay/keysend/send_coins/open/close): the v2 money journal
    (``ops_ledger``) is a PRECONDITION. The intent is written ahead of the node
    call; if it cannot be written — journal unverifiable, torn, unmigrated, locked —
    the action is DENIED and the node is never touched. A spend we cannot account
    for must not happen.
  * **RECEIVE** (``create_invoice``, i.e. the public ``/oracle`` mint): the audit
    trail (``receive_ledger``) is best-effort and lives in its OWN file. Minting is
    the only real revenue path; it must never answer 503 because a SPEND journal is
    broken (BL-2) and must never take the money journal's exclusive lock on an
    anonymous hot path (M-9). See ``app.lightning.receive_ledger`` for the full
    rationale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.lightning_settings import LightningSettings
from app.lightning.adapter import _build_client
from app.lightning.client import LightningUnavailableError
from app.lightning.jsonl_tail import read_recent_jsonl
from app.lightning.ops_ledger import (
    LightningOpsLedgerError,
    append_ln_outcome,
    bolt11_amount_sat,
    legacy_ln_ops_path,
    ln_ops_v2_path,
    normalize_payment_hash,
    prepare_ln_intent,
    verify_ln_ops_ledger,
)
from app.lightning.receive_ledger import append_receive_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValueLayerResult:
    """Outcome of a gated value-layer action. ``state`` is the honest disposition."""

    action: str  # "create_invoice" | "open_channel"
    state: str  # "disabled" | "planned" | "executed" | "error"
    detail: str = ""
    plan: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    # Money-journal correlation id. Non-empty exactly when a write-ahead intent was
    # durably journalled for this action — i.e. when the node was actually reached.
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


def money_journal_status() -> tuple[bool, str]:
    """May the v2 money journal be extended right now? — ``(ok, reason)``.

    The SPEND precondition (W0/PR-C). Three ways to be not-ok, all fail-closed:

      * the journal exists but does not verify (torn tail, forked chain, tampered
        row) — appending would extend a history we can no longer prove;
      * the journal is MISSING while the legacy v1 ledger still holds rows — the
        cutover migration has not run. Starting a fresh genesis chain here would
        fork the money history in two AND silently reset the daily cap to zero,
        because ``spent_today_sat_v2`` would no longer see the v1 spends;
      * verification itself blew up (unreadable file, permissions).

    Cheap enough for the operator cockpit (O(n) over a journal that grows by two
    lines per operator action) and deliberately NOT on the anonymous mint path.
    """
    v2 = ln_ops_v2_path()
    try:
        report = verify_ln_ops_ledger(v2)
    except Exception as exc:  # noqa: BLE001 — any failure to verify is a denial
        return False, f"money journal unverifiable ({type(exc).__name__}: {exc})"
    if not report["ok"]:
        return False, f"money journal fails verification: {report['errors'][:3]}"
    if not v2.exists() and read_recent_jsonl(legacy_ln_ops_path(), limit=1):
        return False, (
            "money journal v2 is missing while the legacy v1 ledger still holds rows — "
            "run the migration (docs/runbooks/ln_ops_ledger_v2_migration.md) before spending"
        )
    return True, ""


def _prepare(
    action: str,
    plan: dict[str, Any],
    intent_id: str | None,
    authorization: dict[str, Any] | None,
) -> tuple[str, ValueLayerResult | None]:
    """Write-ahead intent for a SPEND. Failure = a terminal, no-node-touch result.

    Fail-closed by construction: the caller must return the second element as-is if
    it is not ``None``; the node has NOT been reached in that case.
    """
    ok, reason = money_journal_status()
    if not ok:
        return "", ValueLayerResult(action, "error", f"{reason}; node not touched", plan)
    try:
        record = prepare_ln_intent(
            action, plan=plan, intent_id=intent_id, authorization=authorization
        )
    except LightningOpsLedgerError as exc:
        return "", ValueLayerResult(
            action, "error", f"money journal unavailable; node not touched: {exc}", plan
        )
    return str(record["intent_id"]), None


async def create_invoice(
    *,
    value_sat: int,
    memo: str = "",
    dry_run: bool = True,
    intent_id: str | None = None,
    authorization: dict[str, Any] | None = None,
    cfg: LightningSettings | None = None,
) -> ValueLayerResult:
    """Create a BOLT11 invoice (receive-side, no spend) — gated + dry-run-default.

    RECEIVE asymmetry: unlike every spend below, this method NEVER fails because of
    its audit trail. The mint is the only real revenue path and is reached by
    anonymous callers; a journal problem may cost a log line, never an invoice.
    """
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
    correlation = str(intent_id or "")
    try:
        resp = await _build_client(cfg, credential_scope="invoice").add_invoice(
            value_sat=value_sat, memo=memo
        )
    except LightningUnavailableError as exc:
        return _audit_receive(
            ValueLayerResult("create_invoice", "error", str(exc), plan, intent_id=correlation),
            authorization=authorization,
        )
    return _audit_receive(
        ValueLayerResult("create_invoice", "executed", "", plan, resp, intent_id=correlation),
        authorization=authorization,
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
    prepared, denied = _prepare("open_channel", plan, intent_id, authorization)
    if denied is not None:
        return denied
    try:
        resp = await _build_client(cfg, credential_scope="channel").open_channel(
            node_pubkey_hex=node_pubkey_hex,
            local_funding_sat=local_funding_sat,
            sat_per_vbyte=sat_per_vbyte,
        )
    except LightningUnavailableError as exc:
        return _audit(ValueLayerResult("open_channel", "error", str(exc), plan, intent_id=prepared))
    return _audit(ValueLayerResult("open_channel", "executed", "", plan, resp, intent_id=prepared))


def _audit(result: ValueLayerResult) -> ValueLayerResult:
    """Close the write-ahead intent of a SPEND with its terminal outcome (v2).

    ``disabled``/``planned`` are non-events (no node touch, no intent) → not logged,
    so the inert default and dry-run previews never spam the money journal.
    Necessarily fail-soft: LND may already have moved value, so a journal error
    cannot undo it — the intent row stays open and IS the reconciliation queue
    (and keeps reserving its amount against the daily cap until then).
    """
    if result.state not in ("executed", "error"):
        return result
    if not result.intent_id:
        # Structurally impossible: a node-touching outcome always follows _prepare.
        logger.error(
            "[ln-ops] node-touching %s/%s without a prepared intent — outcome NOT journalled",
            result.action,
            result.state,
        )
        return result
    append_ln_outcome(
        result.action,
        result.state,
        plan=result.plan,
        response=result.response,
        intent_id=result.intent_id,
    )
    return result


def _audit_receive(
    result: ValueLayerResult, *, authorization: dict[str, Any] | None
) -> ValueLayerResult:
    """Record a node-touching RECEIVE outcome in the separate receive journal.

    Returns the result UNCHANGED in every case — this is an audit, not a gate.
    """
    append_receive_event(
        result.action,
        result.state,
        plan=result.plan,
        response=result.response,
        intent_id=result.intent_id,
        authorization=authorization,
    )
    return result


def _decoded_invoice_plan(
    payment_request: str,
    fee_limit_sat: int,
    decoded: dict[str, Any],
) -> dict[str, Any]:
    """Bind lnd's signed invoice facts into the write-ahead payment intent.

    The BOLT11 HRP is an independent amount commitment. Comparing it with lnd's
    decoded amount catches a substituted or inconsistently parsed invoice before
    the journal intent exists and, crucially, before the send endpoint is called.
    Amountless invoices remain denied because this API has no explicit amount
    argument to bind into the operator's plan.
    """

    expected_sat = bolt11_amount_sat(payment_request)
    if expected_sat <= 0:
        raise ValueError("amountless or unparseable BOLT11 invoice")
    try:
        decoded_msat = int(decoded.get("num_msat") or 0)
        decoded_sat_field = int(decoded.get("num_satoshis") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("decodepayreq returned a non-numeric amount") from exc
    decoded_sat = (decoded_msat + 999) // 1000 if decoded_msat > 0 else decoded_sat_field
    if decoded_sat <= 0:
        raise ValueError("decodepayreq returned no positive amount")
    if decoded_sat != expected_sat:
        raise ValueError(
            f"invoice amount mismatch: BOLT11 plan={expected_sat} sat, "
            f"decodepayreq={decoded_sat} sat"
        )

    payment_hash = normalize_payment_hash(decoded.get("payment_hash"))
    try:
        payment_hash_bytes = bytes.fromhex(payment_hash)
    except ValueError as exc:
        raise ValueError("decodepayreq returned an invalid payment_hash") from exc
    if len(payment_hash_bytes) != 32:
        raise ValueError("decodepayreq returned an invalid payment_hash")

    plan: dict[str, Any] = {
        "payment_request": payment_request,
        "fee_limit_sat": int(fee_limit_sat),
        "amount_sat": decoded_sat,
        "payment_hash": payment_hash,
    }
    try:
        timestamp = int(decoded.get("timestamp") or 0)
        expiry = int(decoded.get("expiry") or 0)
    except (TypeError, ValueError):
        timestamp = expiry = 0
    if timestamp > 0 and expiry > 0:
        plan["expires_at_unix"] = timestamp + expiry
    return plan


def _payment_failure_detail(response: dict[str, Any], expected_hash: str) -> str:
    """Classify semantic lnd failures that are transported inside HTTP 200."""

    payment_error = str(response.get("payment_error") or "").strip()
    if payment_error:
        return f"lnd payment failed: {payment_error}"
    failure_reason = str(response.get("failure_reason") or "").strip()
    if failure_reason.upper() not in {"", "0", "NONE", "FAILURE_REASON_NONE"}:
        return f"lnd payment failed: {failure_reason}"

    response_hash = normalize_payment_hash(response.get("payment_hash"))
    if not response_hash:
        return "lnd payment response omitted payment_hash"
    if response_hash != expected_hash:
        return "lnd payment response payment_hash mismatches the prepared intent"
    return ""


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
    """Pay a BOLT11 invoice — SPENDS, irreversible. Max-gated (confirm required).

    Before writing the intent, lnd decodes the invoice and its signed amount and
    payment hash are bound into the plan. That closes the duplicate-payment gap:
    the v2 journal can now reject an already-prepared/executed payment hash.
    """
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

    # Preserve the money journal as the FIRST external precondition. A broken
    # journal must deny without even a read-only node touch; _prepare repeats the
    # check after decode to close the check/decode/append race.
    journal_ok, journal_reason = money_journal_status()
    if not journal_ok:
        return ValueLayerResult("pay_invoice", "error", f"{journal_reason}; node not touched", plan)
    try:
        client = _build_client(cfg, credential_scope="payment")
        decoded = await client.decode_pay_req(payment_request=payment_request)
        plan = _decoded_invoice_plan(payment_request, fee_limit_sat, decoded)
    except (LightningUnavailableError, ValueError) as exc:
        return ValueLayerResult("pay_invoice", "error", f"decodepayreq denied payment: {exc}", plan)

    prepared, denied = _prepare("pay_invoice", plan, intent_id, authorization)
    if denied is not None:
        return denied
    try:
        resp = await client.pay_invoice(
            payment_request=payment_request, fee_limit_sat=fee_limit_sat
        )
    except LightningUnavailableError as exc:
        return _audit(ValueLayerResult("pay_invoice", "error", str(exc), plan, intent_id=prepared))
    failure = _payment_failure_detail(resp, str(plan["payment_hash"]))
    state = "error" if failure else "executed"
    return _audit(ValueLayerResult("pay_invoice", state, failure, plan, resp, intent_id=prepared))


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
        return ValueLayerResult("keysend", "error", "dest_pubkey_hex + positive amt required", plan)
    prepared, denied = _prepare("keysend", plan, intent_id, authorization)
    if denied is not None:
        return denied
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
    prepared, denied = _prepare("send_coins", plan, intent_id, authorization)
    if denied is not None:
        return denied
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
    prepared, denied = _prepare("close_channel", plan, intent_id, authorization)
    if denied is not None:
        return denied
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
    return _audit(ValueLayerResult("close_channel", "executed", "", plan, resp, intent_id=prepared))


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
