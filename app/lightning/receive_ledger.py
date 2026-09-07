"""Receive-side audit journal — the mint path's OWN trail (W0/PR-C, M-9 + BL-2).

``artifacts/ln_receive_ledger.jsonl`` records every node-touching invoice MINT
(``create_invoice``) with the same redaction boundary as the money journal, and it
is deliberately a SEPARATE file with a deliberately weaker writer than
``ops_ledger`` v2. That asymmetry is the whole point:

**Why separate (and not the chained v2 journal).** The public ``/oracle/*`` mint is
the only real revenue path KAI has, it is unauthenticated, and it mints one invoice
per unpaid request. Journalling it through ``prepare_ln_intent`` would put the
anonymous hot path behind the money journal's EXCLUSIVE inter-process lock and its
O(n) full-file re-parse (measured: 2000 mints ≈ 95 s cumulative, growing O(n²)), and
it would make a torn/forked SPEND journal answer 503 to every anonymous caller
(BL-2: two journal rows + a 503 per request). Receive-side auditing must survive the
mint, not gate it — so:

  * **no chaining, no lock, no read** — one O(1) ``O_APPEND`` write of a single line
    well under ``PIPE_BUF`` (atomic on POSIX), then ``fsync``;
  * **fail-soft with a LOUD log** — a failure is logged at ERROR and returns False;
    it never changes the caller's result and never raises into the request path;
  * **no cap/authorisation semantics** — nothing here gates money. Receive moves
    value INWARD; there is no double-spend to prevent and no budget to reserve.

**What guarantees the truth then.** The mint is not the settlement event. Money that
actually arrived is booked from LND's own invoice database into
``ln_earnings_ledger.jsonl`` (``earnings_booking``) keyed by ``payment_hash`` — that
is the treasury source, and it is reconstructable from the node alone. This file is
the operational trail of what KAI OFFERED (mints, failures), not what it earned; a
lost line here costs a log entry, never a sat. Anchoring/verification of the receive
trail is intentionally out of this PR.

**Die Redaktionsgrenze steht seit ADR 0018 §12 HIER.** Sie lag in
``ops_ledger.py``, das mit dem alten Sendeweg auf ein Archiv zusammenfaellt.
Damit haette das lebende Empfangs-Journal seine eigene Redaktion aus einem
Modul importiert, das als Naechstes geloescht wird. Jetzt ist es umgekehrt:
der lebende Pfad besitzt den Code, das Archiv leiht ihn sich bis PR 2.

``app/payments/redaction.py`` konnte sie NICHT uebernehmen — dort ist eine
Allowlist ueber einen flachen ``payload`` mit Formpruefung je Feld, hier eine
Aktions-abhaengige Projektion auf ``{schema, ts, intent_id, action, state,
plan, response, authorization}``. Gleiche Absicht, verschiedene Form; sie
zusammenzuwerfen haette eine der beiden Zusagen verwaessert.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.bolt11 import bolt11_amount_sat, normalize_payment_hash

logger = logging.getLogger(__name__)

#: Schema der oeffentlichen Fassung. Unveraendert aus ``ops_ledger`` uebernommen —
#: die bereits geschriebenen Zeilen tragen genau diesen Wert.
_PUBLIC_SCHEMA = "ln-ops-public/v2"

_RECEIVE_DEFAULT_PATH = Path("artifacts/ln_receive_ledger.jsonl")
_RECEIVE_PATH_ENV = "APP_LN_RECEIVE_LEDGER_PATH"


def _secret_hash(value: Any) -> str:
    """One-way correlation token; hex preimages hash to their real payment hash."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        raw = bytes.fromhex(text) if len(text) == 64 else text.encode("utf-8")
    except ValueError:
        raw = text.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _redact_plan(action: str, plan: dict[str, Any]) -> dict[str, Any]:
    """Allowlist the public plan fields; raw recipients/invoices never survive."""
    if action == "create_invoice":
        out: dict[str, Any] = {"value_sat": _int(plan.get("value_sat"))}
        if plan.get("memo_hash"):
            out["memo_hash"] = str(plan["memo_hash"])
        elif plan.get("memo"):
            out["memo_hash"] = _secret_hash(plan["memo"])
        return out
    if action == "pay_invoice":
        request = str(plan.get("payment_request", ""))
        amount = _int(plan.get("amount_sat")) or bolt11_amount_sat(request)
        out = {
            "amount_sat": amount,
            "fee_limit_sat": _int(plan.get("fee_limit_sat")),
            "payment_request_hash": str(plan.get("payment_request_hash") or _secret_hash(request)),
        }
        if _int(plan.get("expires_at_unix")) > 0:
            out["expires_at_unix"] = _int(plan["expires_at_unix"])
        if plan.get("payment_hash"):
            out["payment_hash"] = normalize_payment_hash(plan["payment_hash"])
        return out
    if action == "keysend":
        return {
            "amount_sat": _int(plan.get("amount_sat") or plan.get("amt_sat")),
            "fee_limit_sat": _int(plan.get("fee_limit_sat")),
            "recipient_hash": str(
                plan.get("recipient_hash") or _secret_hash(plan.get("dest_pubkey_hex"))
            ),
        }
    if action == "send_coins":
        return {
            "amount_sat": _int(plan.get("amount_sat")),
            "sat_per_vbyte": _int(plan.get("sat_per_vbyte")),
            "recipient_hash": str(plan.get("recipient_hash") or _secret_hash(plan.get("addr"))),
        }
    if action == "open_channel":
        return {
            "local_funding_sat": _int(plan.get("local_funding_sat")),
            "sat_per_vbyte": _int(plan.get("sat_per_vbyte")),
            "peer_hash": str(plan.get("peer_hash") or _secret_hash(plan.get("node_pubkey_hex"))),
        }
    if action == "close_channel":
        return {
            "funding_outpoint_hash": str(
                plan.get("funding_outpoint_hash") or _secret_hash(plan.get("funding_txid"))
            ),
            "output_index": _int(plan.get("output_index")),
            "force": bool(plan.get("force", False)),
            "sat_per_vbyte": _int(plan.get("sat_per_vbyte")),
        }
    # Unknown/legacy action: retain no caller-controlled payload.  The action and
    # state still show that an event occurred without risking a new secret field.
    return {}


def _redact_response(response: dict[str, Any]) -> dict[str, Any]:
    """Extract an allowlisted outcome summary; drop route hops and raw proofs.

    Deliberately narrow: LND failure strings (``payment_error``) can echo a
    destination back and are therefore NOT allowlisted. The unredacted original
    stays on-box as ``ln_ops_ledger.v1.jsonl`` for forensics (see runbook).
    """
    out: dict[str, Any] = {}
    for key in (
        "state",
        "status",
        "sync_status",
        "track_v2_status",
        "failure_reason",
        "settled",
        "add_index",
    ):
        value = response.get(key)
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
    for key in ("payment_request_hash", "preimage_hash"):
        if response.get(key):
            out[key] = str(response[key])
    for key in ("amount_sat", "fee_sat"):
        if _int(response.get(key)) > 0:
            out[key] = _int(response[key])

    payment_request = response.get("payment_request")
    if payment_request:
        out["payment_request_hash"] = _secret_hash(payment_request)
        out["amount_sat"] = bolt11_amount_sat(str(payment_request))
    payment_hash = response.get("payment_hash") or response.get("r_hash")
    if payment_hash:
        out["payment_hash"] = normalize_payment_hash(payment_hash)
    preimage = response.get("payment_preimage") or response.get("preimage")
    if preimage:
        out["preimage_hash"] = _secret_hash(preimage)

    route = response.get("payment_route") or response.get("route_summary")
    if isinstance(route, dict):
        # Only aggregate settlement facts; ``hops`` and channel identities are
        # intentionally discarded at the writer boundary.
        total_amt = _int(route.get("total_amt") or route.get("total_amt_sat"))
        total_fees = _int(route.get("total_fees") or route.get("total_fees_sat"))
        out["route_summary"] = {
            "total_amt_sat": total_amt,
            "total_fees_sat": total_fees,
            "total_time_lock": _int(route.get("total_time_lock")),
        }
        if total_amt > 0:
            out["amount_sat"] = total_amt
        if total_fees > 0:
            out["fee_sat"] = total_fees
    for key in ("txid", "tx_hash", "funding_txid_str", "closing_txid"):
        if response.get(key):
            out[f"{key}_hash"] = _secret_hash(response[key])
    return out


def _redact_authorization(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: str(value[key])
        for key in ("policy_decision", "confirmation", "plan_hash")
        if value.get(key)
    }


def redact_ln_op_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return the stable public representation of a current or legacy record."""
    action = str(record.get("action", ""))
    public: dict[str, Any] = {
        "schema": _PUBLIC_SCHEMA,
        "ts": str(record.get("ts", "")),
        "intent_id": str(record.get("intent_id", "")),
        "action": action,
        "state": str(record.get("state", "")),
        "plan": _redact_plan(action, record.get("plan") or {}),
        "response": _redact_response(record.get("response") or {}),
        "authorization": _redact_authorization(record.get("authorization")),
    }
    # Chaining metadata is public and idempotently preserved.  Legacy records
    # without it remain readable but cannot be mistaken for chain-verified rows.
    for key in ("seq", "prev_hash", "record_hash"):
        if key in record:
            public[key] = record[key]
    # BL-3 provenance: a migrated row must be distinguishable from a natively
    # written one, and a synthetic intent must never pass as an operator intent.
    # Without these in the allowlist the writer boundary silently deleted them.
    if record.get("migrated"):
        public["migrated"] = True
    if record.get("synthetic_intent"):
        public["synthetic_intent"] = True
    if _int(record.get("source_line")) > 0:
        public["source_line"] = _int(record["source_line"])
    return public


def receive_ledger_path() -> Path:
    """Resolve the receive journal path (``APP_LN_RECEIVE_LEDGER_PATH`` overrides)."""
    override = os.environ.get(_RECEIVE_PATH_ENV, "").strip()
    return Path(override) if override else _RECEIVE_DEFAULT_PATH


def append_receive_event(
    action: str,
    state: str,
    *,
    plan: dict[str, Any],
    response: dict[str, Any] | None = None,
    intent_id: str = "",
    authorization: dict[str, Any] | None = None,
    path: Path | None = None,
) -> bool:
    """Append ONE redacted receive event; ``True`` if it landed on disk.

    Never raises. A failure is a LOUD ERROR log (a silently missing audit trail is
    exactly the failure mode a truth platform must not have) and returns ``False`` —
    the mint itself is unaffected, because an invoice that the node has already
    created cannot be un-created by an audit problem.
    """
    out = path or receive_ledger_path()
    record = redact_ln_op_record(
        {
            "ts": datetime.now(UTC).isoformat(),
            "intent_id": intent_id,
            "action": action,
            "state": state,
            "plan": plan,
            "response": response or {},
            "authorization": authorization or {},
        }
    )
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:  # noqa: BLE001 — the audit must never kill the mint
        logger.error(
            "[ln-receive] AUDIT LOST for %s/%s: %s: %s — the mint itself was unaffected; "
            "settled receives remain reconstructable from the node's invoice DB",
            action,
            state,
            type(exc).__name__,
            exc,
        )
        return False
    return True


def read_recent_receive_events(
    path: Path | None = None, *, limit: int = 200
) -> list[dict[str, Any]]:
    """Most recent receive events (newest last); ``[]`` when nothing was minted."""
    from app.lightning.jsonl_tail import read_recent_jsonl

    return read_recent_jsonl(path or receive_ledger_path(), limit=limit)


__all__ = [
    "append_receive_event",
    "read_recent_receive_events",
    "receive_ledger_path",
    "redact_ln_op_record",
]
