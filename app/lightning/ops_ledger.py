"""Lightning value-layer operations ledger (tamper-evident audit trail).

The append-only ``artifacts/ln_ops_ledger.jsonl`` records every node-touching
value-layer action (plan + outcome) for an L3-OTS-anchorable audit trail. Read side
feeds the dashboard; write side (Sprint 4) is called by the gated value layer on
every executed/error outcome. No capital path of its own.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import portalocker

from app.lightning.jsonl_tail import read_recent_jsonl
from app.truth.attestation import compute_attestation

logger = logging.getLogger(__name__)

_OPS_PATH = Path("artifacts/ln_ops_ledger.jsonl")
_PUBLIC_SCHEMA = "ln-ops-public/v2"
_GENESIS_HASH = "0" * 64


class LightningOpsLedgerError(RuntimeError):
    """The money-path journal cannot be safely extended."""


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
            out["payment_hash"] = str(plan["payment_hash"])
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
    """Extract an allowlisted outcome summary; drop route hops and raw proofs."""
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
        out["payment_hash"] = str(payment_hash)
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
    public = {
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
    return public


def _locked_records(handle: Any) -> list[dict[str, Any]]:
    handle.seek(0)
    records: list[dict[str, Any]] = []
    last_nonblank = ""
    for line in handle:
        if line.strip():
            last_nonblank = line.strip()
            try:
                parsed = json.loads(last_nonblank)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    if last_nonblank:
        try:
            tail = json.loads(last_nonblank)
            if not isinstance(tail, dict):
                raise ValueError("tail is not an object")
        except ValueError as exc:
            raise LightningOpsLedgerError(
                "LN ops ledger tail unreadable; refusing to fork the money journal"
            ) from exc
    return records


def _append_chained_record(
    record: dict[str, Any],
    *,
    path: Path,
    require_intent: bool,
) -> dict[str, Any]:
    """Append one fsync'd hash-chained row while holding an inter-process lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with portalocker.Lock(path, mode="a+", encoding="utf-8", timeout=10) as handle:
            records = _locked_records(handle)
            chain = [r for r in records if "record_hash" in r and "seq" in r]
            if records and not chain:
                raise LightningOpsLedgerError(
                    "legacy unchained LN ops ledger requires migration before new money events"
                )
            tip = chain[-1] if chain else None
            intent_id = str(record["intent_id"])
            same_intent = [r for r in records if str(r.get("intent_id", "")) == intent_id]
            if require_intent:
                if not same_intent or same_intent[0].get("state") != "intent":
                    raise LightningOpsLedgerError(f"outcome has no prepared intent: {intent_id}")
                if any(r.get("state") in {"executed", "error"} for r in same_intent):
                    raise LightningOpsLedgerError(f"intent already terminal: {intent_id}")
            else:
                if same_intent:
                    raise LightningOpsLedgerError(f"intent replay: {intent_id}")
                payment_hash = str((record.get("plan") or {}).get("payment_hash", ""))
                if record.get("action") == "pay_invoice" and payment_hash:
                    duplicate = any(
                        prior.get("action") == "pay_invoice"
                        and prior.get("state") == "intent"
                        and str((prior.get("plan") or {}).get("payment_hash", ""))
                        == payment_hash
                        for prior in records
                    )
                    if duplicate:
                        raise LightningOpsLedgerError(
                            f"payment_hash already journalled: {payment_hash}"
                        )

            chained = dict(record)
            chained["seq"] = int(tip["seq"]) + 1 if tip else 1
            chained["prev_hash"] = str(tip["record_hash"]) if tip else _GENESIS_HASH
            chained["record_hash"] = compute_attestation(chained)["hash"]
            handle.seek(0, os.SEEK_END)
            handle.write(
                json.dumps(chained, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
            return chained
    except LightningOpsLedgerError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize every persistence/lock failure
        raise LightningOpsLedgerError(
            f"LN ops ledger unavailable: {type(exc).__name__}: {exc}"
        ) from exc


def prepare_ln_intent(
    action: str,
    *,
    plan: dict[str, Any],
    intent_id: str | None = None,
    authorization: dict[str, Any] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Durably write a value intent before the LND call (fail-closed)."""
    public = redact_ln_op_record(
        {
            "ts": datetime.now(UTC).isoformat(),
            "intent_id": intent_id or uuid.uuid4().hex,
            "action": action,
            "state": "intent",
            "plan": plan,
            "response": {},
            "authorization": authorization or {},
        }
    )
    return _append_chained_record(public, path=path or _OPS_PATH, require_intent=False)


def read_recent_ln_ops(path: Path | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    """Read the most recent value-layer ops (newest last); ``[]`` until the gated
    writer produces any. Tolerant: missing file / blank / corrupt lines skipped."""
    # Defense-in-depth for legacy rows until the one-time migration is run.  New
    # rows are already redacted by the writer below; the public API never emits a
    # historical preimage/BOLT11/route merely because the old file still has it.
    return [redact_ln_op_record(row) for row in read_recent_jsonl(path or _OPS_PATH, limit=limit)]


def append_ln_op(
    action: str,
    state: str,
    *,
    plan: dict[str, Any],
    intent_id: str,
    response: dict[str, Any] | None = None,
    path: Path | None = None,
) -> bool:
    """Append one value-layer outcome linked to its prepared intent.

    Outcome persistence is necessarily fail-soft: LND may already have moved value,
    so raising cannot undo it.  The durable ``intent`` row remains open and is the
    reconciliation queue.  Preparing the intent itself is fail-closed via
    :func:`prepare_ln_intent`.
    """
    record = redact_ln_op_record({
        "ts": datetime.now(UTC).isoformat(),
        "intent_id": intent_id,
        "action": action,
        "state": state,
        "plan": plan,
        "response": response or {},
    })
    try:
        _append_chained_record(record, path=path or _OPS_PATH, require_intent=True)
    except Exception as exc:  # noqa: BLE001 — audit must never kill the send path
        logger.warning("[ln-ops] append failed: %s", exc)
        return False
    return True


def verify_ln_ops_ledger(path: Path | None = None) -> dict[str, Any]:
    """Verify hash links, row hashes and intent→terminal lifecycle invariants."""
    target = path or _OPS_PATH
    if not target.exists():
        return {"ok": True, "records": 0, "open_intents": [], "errors": []}
    errors: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    prev_hash = _GENESIS_HASH
    prev_seq = 0
    by_intent: dict[str, list[str]] = {}
    for line_no, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            seq = int(record["seq"])
        except (ValueError, TypeError, KeyError):
            errors.append({"seq": line_no, "reason": "unparseable or unchained record"})
            break
        records.append(record)
        if seq != prev_seq + 1:
            errors.append({"seq": seq, "reason": f"seq gap (expected {prev_seq + 1})"})
        if record.get("prev_hash") != prev_hash:
            errors.append({"seq": seq, "reason": "prev_hash mismatch"})
        body = {k: v for k, v in record.items() if k != "record_hash"}
        if compute_attestation(body)["hash"] != record.get("record_hash"):
            errors.append({"seq": seq, "reason": "record_hash mismatch"})
        intent_id = str(record.get("intent_id", ""))
        by_intent.setdefault(intent_id, []).append(str(record.get("state", "")))
        prev_hash = str(record.get("record_hash", ""))
        prev_seq = seq

    open_intents: list[str] = []
    for intent_id, states in by_intent.items():
        if not intent_id:
            errors.append({"seq": 0, "reason": "missing intent_id"})
            continue
        if states[0] != "intent":
            errors.append({"seq": 0, "reason": f"outcome before intent: {intent_id}"})
        allowed_states = {"intent", "in_flight", "unknown", "executed", "error"}
        if any(state not in allowed_states for state in states):
            errors.append({"seq": 0, "reason": f"invalid state for {intent_id}: {states}"})
        if "intent" in states[1:]:
            errors.append({"seq": 0, "reason": f"repeated intent state: {intent_id}"})
        terminal = [state for state in states if state in {"executed", "error"}]
        if len(terminal) > 1:
            errors.append({"seq": 0, "reason": f"multiple outcomes: {intent_id}"})
        if not terminal:
            open_intents.append(intent_id)
    return {
        "ok": not errors,
        "records": len(records),
        "open_intents": open_intents,
        "errors": errors,
    }


def read_open_ln_intents(path: Path | None = None) -> list[dict[str, Any]]:
    """Return verified write-ahead intents that do not yet have a terminal outcome."""
    target = path or _OPS_PATH
    verification = verify_ln_ops_ledger(target)
    if not verification["ok"]:
        raise LightningOpsLedgerError(
            f"cannot reconcile an invalid LN ops ledger: {verification['errors']}"
        )
    wanted = set(verification["open_intents"])
    if not wanted or not target.exists():
        return []
    intents: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("state") == "intent" and record.get("intent_id") in wanted:
            intents.append(record)
    return intents


def payment_shadow_evidence(
    path: Path | None = None, *, required_samples: int = 20
) -> dict[str, Any]:
    """Evaluate the P3 SendPaymentSync↔TrackPaymentV2 shadow cutover gate."""
    if required_samples <= 0:
        raise ValueError("required_samples must be positive")
    comparisons: list[dict[str, Any]] = []
    for record in read_recent_ln_ops(path, limit=100_000):
        if record.get("action") != "pay_invoice":
            continue
        response = record.get("response") or {}
        sync_status = str(response.get("sync_status", "")).upper()
        track_status = str(response.get("track_v2_status", "")).upper()
        if sync_status in {"SUCCEEDED", "FAILED"} and track_status in {
            "SUCCEEDED",
            "FAILED",
        }:
            comparisons.append(
                {
                    "ts": record.get("ts"),
                    "intent_id": record.get("intent_id"),
                    "sync_status": sync_status,
                    "track_v2_status": track_status,
                    "match": sync_status == track_status,
                }
            )
    window = comparisons[-required_samples:]
    mismatches = [sample for sample in window if not sample["match"]]
    return {
        "schema": "ln-payment-shadow-evidence/v1",
        "required_samples": required_samples,
        "total_comparisons": len(comparisons),
        "window_samples": len(window),
        "window_mismatches": len(mismatches),
        "eligible_for_v2_cutover": len(window) == required_samples and not mismatches,
        "mismatches": mismatches,
    }


def migrate_legacy_ln_ops(source: Path, destination: Path) -> dict[str, Any]:
    """Create a redacted, chained v2 ledger from a legacy file (non-destructive).

    The source is never modified or deleted.  Every legacy terminal row is paired
    with a synthetic preceding intent so the migrated ledger has an honest
    lifecycle and can be verified before an operator performs the final file swap.
    """
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise LightningOpsLedgerError("migration destination must differ from source")
    if not source.is_file():
        raise LightningOpsLedgerError(f"legacy source is not a file: {source}")
    if destination.exists():
        raise LightningOpsLedgerError(f"migration destination already exists: {destination}")

    raw = source.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    source_records = skipped = written = 0
    for line_no, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(row, dict):
            skipped += 1
            continue
        source_records += 1
        state = str(row.get("state", ""))
        if state not in {"executed", "error"}:
            skipped += 1
            continue
        action = str(row.get("action", ""))
        stable = compute_attestation(
            {"line_no": line_no, "action": action, "plan": row.get("plan") or {}}
        )["hash"]
        intent_id = f"legacy-{stable[:32]}"
        intent = redact_ln_op_record(
            {
                "ts": str(row.get("ts", "")),
                "intent_id": intent_id,
                "action": action,
                "state": "intent",
                "plan": row.get("plan") or {},
                "response": {},
            }
        )
        outcome = redact_ln_op_record({**row, "intent_id": intent_id})
        _append_chained_record(intent, path=destination, require_intent=False)
        _append_chained_record(outcome, path=destination, require_intent=True)
        written += 2

    verification = verify_ln_ops_ledger(destination)
    if not verification["ok"]:
        raise LightningOpsLedgerError(f"migrated ledger failed verification: {verification}")
    destination_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "schema": "ln-ops-migration/v1",
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "source": str(source),
        "destination": str(destination),
        "source_sha256": source_hash,
        "destination_sha256": destination_hash,
        "source_records": source_records,
        "written_records": written,
        "skipped_records": skipped,
        "verification": verification,
    }


def attest_ln_ops_tip(
    *,
    ops_path: Path | None = None,
    truth_path: Path | None = None,
    mirror_audit: bool = True,
) -> dict[str, Any]:
    """Bind the current verified money-journal tip into KAI Truth idempotently."""
    from app.truth.ledger import (
        DEFAULT_TRUTH_LEDGER_PATH,
        append_attestation,
        attested_subject_ids,
    )

    source = ops_path or _OPS_PATH
    target = truth_path or DEFAULT_TRUTH_LEDGER_PATH
    verification = verify_ln_ops_ledger(source)
    if not verification["ok"]:
        raise LightningOpsLedgerError(
            f"refusing to attest an invalid LN ops ledger: {verification['errors']}"
        )
    if verification["records"] == 0:
        return {"total": 0, "attested": 0, "skipped": 0}
    last = json.loads(source.read_text(encoding="utf-8").splitlines()[-1])
    tip_hash = str(last["record_hash"])
    subject = f"ln-ops-tip:{tip_hash}"
    if subject in attested_subject_ids(target, kind="lightning_ops_tip"):
        return {"total": 1, "attested": 0, "skipped": 1}
    append_attestation(
        "lightning_ops_tip",
        subject,
        {
            "schema": "ln-ops-tip/v1",
            "record_hash": tip_hash,
            "seq": int(last["seq"]),
            "open_intents": list(verification["open_intents"]),
        },
        path=target,
        mirror_audit=mirror_audit,
    )
    return {"total": 1, "attested": 1, "skipped": 0}


# --------------------------------------------------------------------------- #
# Daily-Cap-Quelle (Gesamtaudit-P0): Wert-ABFLIESSENDE Aktionen des UTC-Tages.
# ``open_channel``/``close_channel`` bewegen Wert nur innerhalb der Self-Custody
# und zählen bewusst NICHT — sonst würde jede Channel-Eröffnung das Tages-Cap
# für echte Sends blockieren, obwohl kein Sat den Operator verlässt.
# --------------------------------------------------------------------------- #

SPEND_ACTIONS = frozenset({"pay_invoice", "keysend", "send_coins"})

_BOLT11_HRP_RE = re.compile(r"^ln(?:bc|tb|bcrt)(\d+)([munp]?)1", re.IGNORECASE)
_HRP_MULTIPLIER_MSAT_PER_UNIT = {
    # msat pro HRP-Einheit: 1 BTC = 1e11 msat; m=1e-3, u=1e-6, n=1e-9, p=1e-12 BTC
    "": 100_000_000_000,
    "m": 100_000_000,
    "u": 100_000,
    "n": 100,
    "p": 0,  # Pico unter msat-Granularität nur bei nicht-10er-Vielfachen; s. unten
}


def bolt11_amount_sat(payment_request: str) -> int:
    """Betrag (sat) aus dem BOLT11-HRP — 0 wenn amountless/unparsebar.

    Konservativ aufgerundet (ein Cap darf nie durch Abrunden unterlaufen werden).
    """
    match = _BOLT11_HRP_RE.match(payment_request.strip())
    if not match:
        return 0
    digits, unit = int(match.group(1)), match.group(2).lower()
    if unit == "p":
        msat = -(-digits * 100 // 1000)  # p: 1e-12 BTC = 0.1 msat -> ceil auf msat
    else:
        msat = digits * _HRP_MULTIPLIER_MSAT_PER_UNIT[unit]
    return -(-msat // 1000)  # ceil msat -> sat


def _spend_amount_sat(record: dict[str, Any]) -> int:
    """Tatsächlich abgeflossene sat eines executed Spends (response-first)."""
    response = record.get("response") or {}
    route = response.get("payment_route") or response.get("route_summary") or {}
    try:
        total_amt = int(
            route.get("total_amt", 0) or route.get("total_amt_sat", 0) or 0
        )  # inkl. Routing-Fees
    except (TypeError, ValueError):
        total_amt = 0
    if total_amt > 0:
        return total_amt
    plan = record.get("plan") or {}
    action = record.get("action")
    if action == "pay_invoice":
        amount = _int(plan.get("amount_sat")) or bolt11_amount_sat(
            str(plan.get("payment_request", ""))
        )
        if amount == 0:
            logger.warning("[ln-ops] spend amount unknown (amountless invoice?): %s", action)
        return amount
    try:
        return int(plan.get("amt_sat") or plan.get("amount_sat") or 0)
    except (TypeError, ValueError):
        return 0


def spent_today_sat(path: Path | None = None, *, now: datetime | None = None) -> int:
    """Summe der heute (UTC) wert-abfließenden Sends — Daily-Cap-Quelle (fail-closed).

    Zählt ``executed`` UND ``error``: ein error-Record kann ein real settled Spend
    sein (Client-Timeout NACH dem Senden — live belegt durch den 25k-Spend vom
    07-02, error geloggt, Channel-Balancen beweisen Settlement). Für ein
    Sicherheits-Cap gilt: Unbekannt = mitzählen; ein echter Fehlschlag over-counted
    dann nur Richtung needs_confirm. ``planned``/``disabled`` berühren den Node nie.
    Ein offener ``intent``/``unknown`` wird ebenfalls reserviert: fällt der Prozess
    nach dem LND-Aufruf, aber vor dem outcome-fsync aus, darf das Tages-Cap nicht
    erneut denselben Betrag freigeben. Intent+Outcome zählen zusammen genau einmal.
    Die gesamte Datei wird gelesen (kein unsicheres 2000-Zeilen-Tail-Limit).
    """
    today = (now or datetime.now(UTC)).date()
    target = path or _OPS_PATH
    if not target.exists():
        return 0
    grouped: dict[str, list[dict[str, Any]]] = {}
    legacy: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("action") not in SPEND_ACTIONS:
            continue
        intent_id = str(record.get("intent_id", ""))
        if intent_id:
            grouped.setdefault(intent_id, []).append(record)
        else:
            legacy.append(record)

    countable: list[dict[str, Any]] = []
    for records in grouped.values():
        terminal = next(
            (
                record
                for record in reversed(records)
                if record.get("state") in {"executed", "error"}
            ),
            None,
        )
        # No terminal means the durable intent is still potentially spendable or
        # already spent. Reserve its amount until TrackPaymentV2 reconciliation.
        countable.append(terminal or records[0])
    countable.extend(
        record for record in legacy if record.get("state") in {"executed", "error"}
    )

    total = 0
    for record in countable:
        try:
            ts = datetime.fromisoformat(str(record.get("ts", "")))
        except ValueError:
            continue
        if ts.astimezone(UTC).date() == today:
            total += _spend_amount_sat(record)
    return total
