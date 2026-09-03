"""Lightning value-layer operations ledger — v2 (live) + v1 (frozen legacy).

**v2 — ``artifacts/ln_ops_ledger_v2.jsonl`` (LIVE since W0/PR-C).** The money
journal: write-ahead, redacted at the writer, hash-chained. ``prepare_ln_intent``
durably records the intent BEFORE the LND call (fail-closed — no intent, no spend),
``append_ln_outcome`` records the terminal result (fail-soft — LND may already have
moved value, so raising cannot undo it), ``verify_ln_ops_ledger`` checks
links/hashes/lifecycle, ``attest_ln_ops_tip`` binds the verified tip into the KAI
truth chain and ``spent_today_sat_v2`` is the daily-cap source.

**v1 — ``artifacts/ln_ops_ledger.jsonl`` (FROZEN legacy).** Flat, unchained,
fail-soft JSONL. It was migrated into v2 by ``migrate_legacy_ln_ops`` and is never
written again: ``append_ln_op`` and ``spent_today_sat`` have no live caller after
the PR-C cutover and are retained ONLY as the documented rollback surface and as
the readable historical file (a guard test keeps them caller-free). Writing both
journals in parallel would fork the money history — that is the failure this split
exists to prevent.

**RECEIVE events do not live here.** Invoice mints are audited in
``app.lightning.receive_ledger`` — a separate, lock-free, best-effort file. See its
module docstring for why the public mint path must not touch this journal (M-9/BL-2).

See ``docs/runbooks/ln_ops_ledger_v2_migration.md`` for migration + repair.
No capital path of its own in either version.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import portalocker

from app.lightning.jsonl_tail import read_recent_jsonl
from app.lightning.plan_guards import plan_structural_defects
from app.payments.input_rejections import (
    MoneyInputRejectionAuditError,
    append_money_input_rejection,
)
from app.truth.attestation import compute_attestation

logger = logging.getLogger(__name__)

_OPS_PATH = Path("artifacts/ln_ops_ledger.jsonl")


# =========================================================================== #
# v1 — FROZEN legacy ledger. ``append_ln_op``/``spent_today_sat`` lost their last
# live caller in the PR-C cutover and are kept as the documented rollback surface
# (a guard test asserts no app module writes v1). Do not "improve" them; the
# improvements live in the v2 section below.
# =========================================================================== #


def legacy_ln_ops_path() -> Path:
    """Path of the frozen v1 ledger (module attribute, so the test seam applies)."""
    return _OPS_PATH


def read_recent_ln_ops(path: Path | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    """Read the most recent value-layer ops for DISPLAY (newest last), redacted.

    Source resolution (``path=None``): the live v2 money journal if it exists, else
    the frozen v1 file — so the dashboard follows the cutover without a second
    endpoint, and a pre-migration box still shows its history.

    Two display invariants (MI-2 / m-18):

      * **Redaction on read.** Every row is passed through
        :func:`redact_ln_op_record` even though the v2 WRITER already redacts. The
        v1 rows predate that boundary and carry raw BOLT11 invoices, preimages and
        route hops; ``/dashboard/api/ln/ops`` must not be the one place that leaks
        them. Defence in depth costs one pass over the bounded window below.
      * **One row per money event.** A v2 action is TWO records (intent + outcome).
        Rendering both would double the panel's "N Aktionen" count and show a
        pending intent next to its own settled outcome. Records sharing an
        ``intent_id`` therefore collapse to their terminal outcome, or — while none
        exists — to the still-open intent (which is the honest state). Legacy rows
        have no ``intent_id`` and are never merged with each other.

    Tolerant: missing file / blank / corrupt lines skipped.
    """
    if path is not None:
        target = path
    else:
        v2 = ln_ops_v2_path()
        target = v2 if v2.exists() else _OPS_PATH
    # Bounded window: a v2 event is at most two rows, so 2×limit (+ slack) can never
    # under-fill the page. A partial view stays correct because all three cases still
    # collapse to exactly one row: intent+outcome, outcome alone (its intent scrolled
    # out of the window) and intent alone (not yet resolved).
    window = 0 if limit <= 0 else limit * 2 + 50
    rows = [redact_ln_op_record(row) for row in read_recent_jsonl(target, limit=window)]

    merged: list[dict[str, Any]] = []
    position: dict[str, int] = {}
    for row in rows:
        intent_id = str(row.get("intent_id", ""))
        if not intent_id:
            merged.append(row)
            continue
        if intent_id not in position:
            position[intent_id] = len(merged)
            merged.append(row)
        elif row.get("state") in _TERMINAL_STATES:
            merged[position[intent_id]] = row
    return merged[-limit:] if limit > 0 else merged


def append_ln_op(
    action: str,
    state: str,
    *,
    plan: dict[str, Any],
    response: dict[str, Any] | None = None,
    path: Path | None = None,
) -> None:
    """Append one value-layer op (plan + outcome) to the FROZEN v1 audit ledger.

    No live caller since the W0/PR-C cutover — the money path writes
    :func:`prepare_ln_intent` + :func:`append_ln_outcome` (v2). Kept as the rollback
    surface: re-pointing the value layer here restores the pre-cutover behaviour
    byte for byte. Writing it in parallel with v2 would fork the money history.

    Fail-soft: a write error is logged and swallowed — the audit trail must NEVER
    kill the (already-gated) send path. Append-only JSONL, one line per op, NOT
    redacted (which is precisely why the read path redacts).
    """
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "action": action,
        "state": state,
        "plan": plan,
        "response": response or {},
    }
    out = path or _OPS_PATH
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — audit must never kill the send path
        logger.warning("[ln-ops] append failed: %s", exc)


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
    route = response.get("payment_route") or {}
    try:
        total_amt = int(route.get("total_amt", 0) or 0)  # inkl. Routing-Fees
    except (TypeError, ValueError):
        total_amt = 0
    if total_amt > 0:
        return total_amt
    plan = record.get("plan") or {}
    action = record.get("action")
    if action == "pay_invoice":
        amount = bolt11_amount_sat(str(plan.get("payment_request", "")))
        if amount == 0:
            logger.warning("[ln-ops] spend amount unknown (amountless invoice?): %s", action)
        return amount
    try:
        return int(plan.get("amt_sat") or plan.get("amount_sat") or 0)
    except (TypeError, ValueError):
        return 0


def spent_today_sat(path: Path | None = None, *, now: datetime | None = None) -> int:
    """Summe der heute (UTC) wert-abfließenden Sends — FROZEN v1-Cap-Quelle.

    Kein Live-Aufrufer mehr seit dem W0/PR-C-Cutover: das Tages-Cap kommt aus
    :func:`spent_today_sat_v2`. Bleibt als Rollback-Fläche erhalten.

    Zählt ``executed`` UND ``error`` — dieselbe m-14-Regel wie v2
    (``UNPROVEN_OUTCOME_COUNTS_IN_CAP``): ein error-Record kann ein real settled
    Spend sein (Client-Timeout NACH dem Senden — live belegt durch den 25k-Spend vom
    07-02, error geloggt, Channel-Balancen beweisen Settlement). Unbekannt =
    mitzählen; ein echter Fehlschlag over-counted dann nur Richtung needs_confirm.
    ``planned``/``disabled`` berühren den Node nie. v1 reserviert KEINE offenen
    Intents (es kennt keine) und kennt das m-15-Rolling-Fenster nicht — genau darum
    ist v2 die Quelle. Tolerant gegen fehlende Datei/korrupte Zeilen.
    """
    today = (now or datetime.now(UTC)).date()
    total = 0
    for record in read_recent_jsonl(path or _OPS_PATH, limit=2000):
        if record.get("state") not in ("executed", "error"):
            continue
        if record.get("action") not in SPEND_ACTIONS:
            continue
        try:
            ts = datetime.fromisoformat(str(record.get("ts", "")))
        except ValueError:
            continue
        if ts.astimezone(UTC).date() != today:
            continue
        total += _spend_amount_sat(record)
    return total


# =========================================================================== #
# v2 — LIVE hash-chained money journal since PR-C. The frozen v1 section above is
# deliberately independent and remains only as a readable rollback surface.
# =========================================================================== #

_OPS_V2_DEFAULT_PATH = Path("artifacts/ln_ops_ledger_v2.jsonl")
_OPS_V2_PATH_ENV = "APP_LN_OPS_LEDGER_V2_PATH"
_PUBLIC_SCHEMA = "ln-ops-public/v2"
_GENESIS_HASH = "0" * 64
_MIGRATION_SCHEMA = "ln-ops-migration/v2"
_RUNBOOK = "docs/runbooks/ln_ops_ledger_v2_migration.md"

# M-4: how long an intent without a terminal outcome keeps blocking a retry of the
# same invoice when the plan carries no ``expires_at_unix``. One hour is the BOLT11
# default invoice expiry — after it the invoice itself is unpayable, so a retry
# cannot double-spend. A plan-provided ``expires_at_unix`` always wins over this.
_INTENT_TTL_SECONDS = 3600

_TERMINAL_STATES = frozenset({"executed", "error"})
_ALLOWED_STATES = frozenset({"intent", "in_flight", "unknown", "executed", "error"})

# --------------------------------------------------------------------------- #
# m-14 — ONE rule for an UNPROVEN outcome ("error"), decided here, referenced by
# both places that used to imply opposite things:
#
#   An ``error`` outcome means "we do not know whether value moved". Live-proven
#   by the 25k spend of 07-02: the client timed out, the row says error, the
#   channel balances say settled.
#
#   * CAP (:func:`spent_today_sat_v2`): it COUNTS. Budget must never be handed out
#     twice on the strength of an unproven failure; over-counting only pushes the
#     next action toward ``needs_confirm``.
#   * DEDUP (:func:`_payment_hash_conflict`): it ALLOWS a retry. Double-spend
#     safety for a retried invoice does not rest on our journal at all — lnd
#     refuses a second payment to the same payment_hash. Blocking forever here
#     would brick a legitimately failed invoice permanently (the reproduced M-4).
#
# Both directions are conservative for the value they protect; the pair is only a
# contradiction if one reads "counts in the cap" as "we know it succeeded".
# --------------------------------------------------------------------------- #
UNPROVEN_OUTCOME_COUNTS_IN_CAP = True
UNPROVEN_OUTCOME_ALLOWS_RETRY = True


class LightningOpsLedgerError(RuntimeError):
    """The money-path journal cannot be safely extended."""


def ln_ops_v2_path() -> Path:
    """Resolve the v2 journal path (``APP_LN_OPS_LEDGER_V2_PATH`` overrides).

    Configurable so the migration dry-run, the test suite and a future PR-C
    cutover can point the machinery at a scratch file without touching v1.
    """
    override = os.environ.get(_OPS_V2_PATH_ENV, "").strip()
    return Path(override) if override else _OPS_V2_DEFAULT_PATH


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


def normalize_payment_hash(value: Any) -> str:
    """Normalise a payment hash to lowercase hex (MI-1).

    LND speaks base64 (``r_hash`` in the REST JSON) while the pay path and the
    operator speak hex. Storing both forms verbatim would make the M-4 duplicate
    check blind: the same invoice would appear twice under two spellings. Every
    v2 record therefore stores HEX. A value that is neither 64-char hex nor a
    32-byte base64 blob is kept verbatim and logged — dropping it would silently
    disarm the duplicate guard.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) == 64:
        try:
            bytes.fromhex(text)
        except ValueError:
            pass
        else:
            return text.lower()
    candidate = text.replace("-", "+").replace("_", "/")
    padded = candidate + "=" * (-len(candidate) % 4)
    try:
        raw = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) == 32:
        return raw.hex()
    logger.warning("[ln-ops] payment_hash not normalisable to hex — stored verbatim")
    return text


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


def _fsync_directory(directory: Path) -> None:
    """fsync the directory entry so a NEW ledger file survives a power cut (m-16).

    Without this the first record is fsync'd into a file whose directory entry may
    still be in the page cache — the money journal could vanish entirely. POSIX
    only; Windows has no directory-handle fsync (dev boxes, not the Pi).
    """
    if os.name != "posix":
        return
    flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
    try:
        fd = os.open(directory, flags)
    except OSError as exc:
        logger.warning("[ln-ops] directory fsync skipped for %s: %s", directory, exc)
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        logger.warning("[ln-ops] directory fsync failed for %s: %s", directory, exc)
    finally:
        os.close(fd)


def _locked_records(handle: Any) -> list[dict[str, Any]]:
    """Parse every row under the write lock; ANY unreadable row refuses the append.

    M-5: a torn tail (power cut mid-line) or a corrupt interior row means the chain
    cannot be extended honestly — appending onto the last *parseable* row would fork
    the money journal silently. Fail-closed with a pointer to the repair runbook.
    """
    handle.seek(0)
    lines = [line for line in handle if line.strip()]
    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        try:
            parsed = json.loads(line)
            if not isinstance(parsed, dict):
                raise ValueError("record is not a JSON object")
        except ValueError as exc:
            where = "tail" if index == len(lines) else f"interior line {index}"
            raise LightningOpsLedgerError(
                f"LN ops ledger {where} unreadable; refusing to fork the money journal "
                f"— repair first: {_RUNBOOK} (section 'Tail-Recovery')"
            ) from exc
        records.append(parsed)
    return records


def _intent_is_expired(intent: dict[str, Any], *, now: datetime) -> bool:
    """Has the invoice behind an open intent become unpayable? (M-4 retry window)"""
    expires_at = _int((intent.get("plan") or {}).get("expires_at_unix"))
    if expires_at > 0:
        return now.timestamp() >= expires_at
    try:
        created = datetime.fromisoformat(str(intent.get("ts", "")))
    except ValueError:
        return False  # unknown age → treat as live (fail-closed)
    return (now - created.astimezone(UTC)).total_seconds() >= _INTENT_TTL_SECONDS


def _payment_hash_conflict(
    payment_hash: str, records: list[dict[str, Any]], *, now: datetime
) -> str | None:
    """M-4: reject a new pay_invoice intent only for a settled or LIVE invoice.

    The original guard rejected on "a prior intent row exists" — permanent, because
    the intent row is append-only. A crash between intent-write and send (or an
    honest ``error`` outcome) then bricked that invoice forever. The honest rule:

      * a terminal ``executed`` blocks FOREVER (never re-send a settled invoice);
      * an intent without any terminal outcome blocks until it expires (in flight);
      * a terminal ``error`` or an expired open intent allows a retry
        (``UNPROVEN_OUTCOME_ALLOWS_RETRY`` — see the m-14 rule above: an unproven
        failure is not evidence of a spend, and lnd itself refuses to pay the same
        payment_hash twice).

    A retried invoice still costs cap twice (``spent_today_sat_v2`` keeps reserving
    the open intent) — over-counting toward ``needs_confirm`` is the safe direction.
    """
    by_intent: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        if row.get("action") != "pay_invoice":
            continue
        by_intent.setdefault(str(row.get("intent_id", "")), []).append(row)
    for intent_id, rows in by_intent.items():
        intent = next((row for row in rows if row.get("state") == "intent"), None)
        if intent is None:
            continue
        if str((intent.get("plan") or {}).get("payment_hash", "")) != payment_hash:
            continue
        states = {str(row.get("state", "")) for row in rows}
        if "executed" in states:
            return f"payment_hash already settled: {payment_hash} (intent {intent_id})"
        if states & _TERMINAL_STATES:
            continue  # honest failure → a retry is legitimate
        if not _intent_is_expired(intent, now=now):
            return (
                f"payment_hash has a live intent: {payment_hash} (intent {intent_id}) — "
                "retry after the invoice expires or once an outcome is journalled"
            )
    return None


def _append_chained_record(
    record: dict[str, Any],
    *,
    path: Path,
    require_intent: bool,
    enforce_payment_dedup: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one fsync'd hash-chained row while holding an inter-process lock.

    Honest about its guarantee (m-17): the append TRUSTS the current tip — it links
    to the last row's ``record_hash`` without re-verifying the whole chain (that
    would be O(n) under an exclusive lock on the money path). Tamper-EVIDENCE comes
    from :func:`verify_ln_ops_ledger` and the OTS-anchored :func:`attest_ln_ops_tip`,
    which do re-verify every link. A rewritten history is therefore detected, not
    prevented, by this function.
    """
    moment = now or datetime.now(UTC)
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    chained: dict[str, Any]
    try:
        with portalocker.Lock(path, mode="a+", encoding="utf-8", timeout=10) as handle:
            records = _locked_records(handle)
            chain = [r for r in records if "record_hash" in r and "seq" in r]
            if records and len(chain) != len(records):
                raise LightningOpsLedgerError(
                    "unchained (v1/legacy) rows present — migrate before new money "
                    f"events: {_RUNBOOK}"
                )
            tip = chain[-1] if chain else None
            intent_id = str(record["intent_id"])
            same_intent = [r for r in records if str(r.get("intent_id", "")) == intent_id]
            if require_intent:
                if not same_intent or same_intent[0].get("state") != "intent":
                    raise LightningOpsLedgerError(f"outcome has no prepared intent: {intent_id}")
                if any(r.get("state") in _TERMINAL_STATES for r in same_intent):
                    raise LightningOpsLedgerError(f"intent already terminal: {intent_id}")
            else:
                if same_intent:
                    raise LightningOpsLedgerError(f"intent replay: {intent_id}")
                payment_hash = str((record.get("plan") or {}).get("payment_hash", ""))
                if enforce_payment_dedup and record.get("action") == "pay_invoice" and payment_hash:
                    conflict = _payment_hash_conflict(payment_hash, records, now=moment)
                    if conflict:
                        raise LightningOpsLedgerError(conflict)

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
    except LightningOpsLedgerError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize every persistence/lock failure
        raise LightningOpsLedgerError(
            f"LN ops ledger unavailable: {type(exc).__name__}: {exc}"
        ) from exc
    if not existed:
        _fsync_directory(path.parent)
    return chained


def prepare_ln_intent(
    action: str,
    *,
    plan: dict[str, Any],
    intent_id: str | None = None,
    authorization: dict[str, Any] | None = None,
    path: Path | None = None,
    rejection_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Durably write a value intent BEFORE the LND call (fail-closed, v2 only).

    Raises :class:`LightningOpsLedgerError` if the intent cannot be persisted — no
    durable write-ahead record means the caller must not touch the node.
    """
    moment = now or datetime.now(UTC)
    # G2: reject structurally impossible plan values BEFORE they can be journalled
    # and sealed. Fail-closed here is safe precisely because the checks admit no
    # false positive -- see app/lightning/plan_guards for why the split exists.
    defects = plan_structural_defects(action, plan)
    if defects:
        audit_failure = ""
        try:
            append_money_input_rejection(
                action=action,
                reasons=defects,
                path=rejection_path,
                now=moment,
            )
        except MoneyInputRejectionAuditError as exc:
            audit_failure = "; rejection_audit_failed"
            logger.error(
                "money_input_rejection_audit_failed",
                extra={
                    "action": action,
                    "reason_count": len(defects),
                    "error_type": type(exc).__name__,
                },
            )
        raise LightningOpsLedgerError(
            "input_contract_rejected: refusing to journal a structurally impossible "
            f"{action} plan: {'; '.join(defects)}{audit_failure}"
        )
    public = redact_ln_op_record(
        {
            "ts": moment.isoformat(),
            "intent_id": intent_id or uuid.uuid4().hex,
            "action": action,
            "state": "intent",
            "plan": plan,
            "response": {},
            "authorization": authorization or {},
        }
    )
    return _append_chained_record(
        public, path=path or ln_ops_v2_path(), require_intent=False, now=moment
    )


def append_ln_outcome(
    action: str,
    state: str,
    *,
    plan: dict[str, Any],
    intent_id: str,
    response: dict[str, Any] | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    """Append one v2 outcome linked to its prepared intent; ``True`` if journalled.

    Necessarily fail-soft: LND may already have moved value, so raising cannot undo
    it. The durable ``intent`` row stays open and IS the reconciliation queue.
    Preparing the intent is the fail-closed half (:func:`prepare_ln_intent`).
    """
    record = redact_ln_op_record(
        {
            "ts": (now or datetime.now(UTC)).isoformat(),
            "intent_id": intent_id,
            "action": action,
            "state": state,
            "plan": plan,
            "response": response or {},
        }
    )
    try:
        _append_chained_record(record, path=path or ln_ops_v2_path(), require_intent=True)
    except Exception as exc:  # noqa: BLE001 — audit must never kill the send path
        logger.warning("[ln-ops] v2 outcome append failed: %s", exc)
        return False
    return True


def _verify_ln_ops_text(raw: str) -> dict[str, Any]:
    """Pure full-chain verification shared by public verify and locked cap reads."""
    errors: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    prev_hash = _GENESIS_HASH
    prev_seq = 0
    by_intent: dict[str, list[str]] = {}
    for line_no, line in enumerate(raw.splitlines(), start=1):
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
        if any(state not in _ALLOWED_STATES for state in states):
            errors.append({"seq": 0, "reason": f"invalid state for {intent_id}: {states}"})
        if "intent" in states[1:]:
            errors.append({"seq": 0, "reason": f"repeated intent state: {intent_id}"})
        terminal = [state for state in states if state in _TERMINAL_STATES]
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


def verify_ln_ops_ledger(path: Path | None = None) -> dict[str, Any]:
    """Verify hash links, row hashes and intent→terminal lifecycle invariants."""
    target = path or ln_ops_v2_path()
    if not target.exists():
        return {"ok": True, "records": 0, "open_intents": [], "errors": []}
    return _verify_ln_ops_text(target.read_text(encoding="utf-8"))


def read_verified_ln_ops_snapshot(path: Path | None = None) -> dict[str, Any]:
    """Return records from one locked, fully verified v2-journal snapshot.

    Unlike the legacy public verifier, a missing/non-file journal is an explicit
    failure here.  Reconciliation needs the record bodies which produced
    ``open_intents`` and must never verify one read and consume another (TOCTOU).
    On every read, lock or chain failure ``records`` is empty so no caller can
    accidentally act on a valid-looking prefix.
    """
    target = path or ln_ops_v2_path()
    if not target.is_file():
        return {
            "ok": False,
            "checked": 0,
            "records": [],
            "open_intents": [],
            "errors": [{"seq": 0, "reason": "ledger missing or not a file"}],
        }
    try:
        flags = portalocker.LockFlags.SHARED | portalocker.LockFlags.NON_BLOCKING
        with portalocker.Lock(
            target,
            mode="r",
            encoding="utf-8",
            timeout=10,
            flags=flags,
        ) as handle:
            raw = handle.read()
    except Exception as exc:  # noqa: BLE001 — any snapshot uncertainty is failure
        logger.error(
            "[ln-ops] verified snapshot unavailable: %s: %s",
            type(exc).__name__,
            exc,
        )
        return {
            "ok": False,
            "checked": 0,
            "records": [],
            "open_intents": [],
            "errors": [{"seq": 0, "reason": f"ledger unreadable ({type(exc).__name__})"}],
        }

    verification = _verify_ln_ops_text(raw)
    if not verification["ok"]:
        return {
            "ok": False,
            "checked": int(verification["records"]),
            "records": [],
            "open_intents": [],
            "errors": list(verification["errors"]),
        }
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return {
        "ok": True,
        "checked": len(records),
        "records": records,
        "open_intents": list(verification["open_intents"]),
        "errors": [],
    }


def migrate_legacy_ln_ops(source: Path, destination: Path) -> dict[str, Any]:
    """Build a redacted, chained v2 ledger from a v1 file (non-destructive).

    The source is never modified or deleted (ADR-0016 invariant 1). Every legacy
    terminal row is paired with a SYNTHETIC preceding intent so the migrated ledger
    has an honest lifecycle; those synthetic rows carry provenance
    (``migrated``/``synthetic_intent``/``source_line``) so no reader can mistake
    them for an operator-prepared intent (BL-3).

    The M-4 payment-hash guard is deliberately OFF for the historical replay
    (M-12d): a duplicate invoice in the past is a fact to be recorded, not a reason
    to abort the migration. Rows that are not migratable are reported line by line
    in ``skipped`` — never silently dropped.
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
    source_records = written = 0
    skipped: list[dict[str, Any]] = []

    def _skip(line: int, reason: str, state: str) -> None:
        logger.warning("[ln-ops] migration skipped line %s (%s, state=%r)", line, reason, state)
        skipped.append({"line": line, "reason": reason, "state": state})

    for line_no, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            _skip(line_no, "unparseable json", "")
            continue
        if not isinstance(row, dict):
            _skip(line_no, "not a json object", "")
            continue
        source_records += 1
        state = str(row.get("state", ""))
        if state not in _TERMINAL_STATES:
            _skip(line_no, "non-terminal legacy state", state)
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
                "migrated": True,
                "synthetic_intent": True,
                "source_line": line_no,
            }
        )
        outcome = redact_ln_op_record(
            {**row, "intent_id": intent_id, "migrated": True, "source_line": line_no}
        )
        _append_chained_record(
            intent, path=destination, require_intent=False, enforce_payment_dedup=False
        )
        _append_chained_record(outcome, path=destination, require_intent=True)
        written += 2

    # A successful zero-row migration needs a durable identity of its own. Without
    # an existing destination, the cap reader cannot distinguish "known empty" from
    # a journal that vanished after prior spends and must return UNKNOWN.
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("x", encoding="utf-8") as handle:
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise LightningOpsLedgerError(
                f"cannot persist empty migrated ledger: {type(exc).__name__}: {exc}"
            ) from exc
        _fsync_directory(destination.parent)

    verification = verify_ln_ops_ledger(destination)
    if not verification["ok"]:
        raise LightningOpsLedgerError(f"migrated ledger failed verification: {verification}")
    destination_hash = (
        hashlib.sha256(destination.read_bytes()).hexdigest() if destination.exists() else ""
    )
    return {
        "schema": _MIGRATION_SCHEMA,
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "source": str(source),
        "destination": str(destination),
        "source_sha256": source_hash,
        "destination_sha256": destination_hash,
        "source_records": source_records,
        "written_records": written,
        "skipped_records": len(skipped),
        "skipped": skipped,
        "verification": verification,
    }


def attest_ln_ops_tip(
    *,
    ops_path: Path | None = None,
    truth_path: Path | None = None,
    mirror_audit: bool = True,
) -> dict[str, Any]:
    """Bind the current verified money-journal tip into KAI Truth idempotently.

    Raises on an invalid/unmigrated ledger — attesting a broken money journal would
    launder it into the truth chain. Callers on the shared anchor path MUST treat
    that as a warning, never as a reason to skip the rest of the run (BL-1).
    """
    from app.truth.ledger import (
        DEFAULT_TRUTH_LEDGER_PATH,
        append_attestation,
        attested_subject_ids,
    )

    source = ops_path or ln_ops_v2_path()
    target = truth_path or DEFAULT_TRUTH_LEDGER_PATH
    verification = verify_ln_ops_ledger(source)
    if not verification["ok"]:
        raise LightningOpsLedgerError(
            f"refusing to attest an invalid LN ops ledger: {verification['errors']}"
        )
    if verification["records"] == 0:
        return {"total": 0, "attested": 0, "skipped": 0}
    # Blank trailing lines pass verification (it skips them) but would blow up a naive
    # ``splitlines()[-1]`` — on the shared anchor path that is a crash, not a warning.
    rows = [line for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    last = json.loads(rows[-1])
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


def _spend_amount_sat_v2(record: dict[str, Any]) -> int:
    """Outflowing sat of a redacted v2 row (settled route first, then the plan)."""
    response = record.get("response") or {}
    route = response.get("route_summary") or {}
    total_amt = _int(route.get("total_amt_sat")) if isinstance(route, dict) else 0
    if total_amt > 0:
        return total_amt
    if _int(response.get("amount_sat")) > 0:
        return _int(response["amount_sat"])
    plan = record.get("plan") or {}
    amount = _int(plan.get("amount_sat"))
    if amount == 0:
        logger.warning(
            "[ln-ops] v2 spend amount unknown (amountless invoice?): %s", record.get("action")
        )
    return amount


def spent_today_sat_v2(path: Path | None = None, *, now: datetime | None = None) -> int | None:
    """Daily-cap source over the v2 journal — reserves OPEN intents (fail-closed).

    Beyond the v1 semantics (``executed`` and ``error`` both count — see the m-14
    rule ``UNPROVEN_OUTCOME_COUNTS_IN_CAP``) the v2 source also RESERVES an intent
    that has no terminal outcome yet: if the process dies after the LND call but
    before the outcome fsync, the cap must not hand the same budget out twice.
    Intent + outcome of one action count exactly once, and the whole file is read
    (no unsafe 2000-line tail limit).

    **m-15 — the UTC-midnight leak, closed conservatively.** A pure calendar-day
    window lets an operator spend the full cap at 23:59 and the full cap again at
    00:01 — 2× the intended daily exposure inside two minutes, with no rule broken.
    The returned figure is therefore ``max(calendar-day, trailing 24 h)``: the
    calendar day keeps the operator's mental model (a cap that visibly resets), the
    rolling window removes the boundary hop, and taking the maximum can only ever
    push toward ``needs_confirm``. A resolved action is bucketed by its OUTCOME
    timestamp, an open one by its intent; an action spanning midnight counts on both
    days — again over-counting, never under-counting the live window.

    ``None`` is the explicit UNKNOWN sentinel. Missing/unreadable/locked/corrupt
    state must never become numeric zero, because zero grants the full daily budget.
    A present, valid, empty journal is distinguishable and returns the honest ``0``.
    The full read and verification share a read lock with the append writer, so the
    cap cannot be calculated from a half-written or mid-transition snapshot.
    """
    moment = now or datetime.now(UTC)
    today = moment.date()
    window_start = moment - timedelta(hours=24)
    target = path or ln_ops_v2_path()
    if not target.is_file():
        logger.error("[ln-ops] v2 spend cap unknown: ledger missing/non-file: %s", target)
        return None

    try:
        flags = portalocker.LockFlags.SHARED | portalocker.LockFlags.NON_BLOCKING
        with portalocker.Lock(
            target,
            mode="r",
            encoding="utf-8",
            timeout=10,
            flags=flags,
        ) as handle:
            raw = handle.read()
    except Exception as exc:  # noqa: BLE001 — every read/lock uncertainty is UNKNOWN
        logger.error(
            "[ln-ops] v2 spend cap unknown: ledger unreadable: %s: %s",
            type(exc).__name__,
            exc,
        )
        return None

    verification = _verify_ln_ops_text(raw)
    if not verification["ok"]:
        logger.error(
            "[ln-ops] v2 spend cap unknown: ledger verification failed: %s",
            verification["errors"],
        )
        return None

    grouped: dict[str, list[dict[str, Any]]] = {}
    unlinked: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict) or record.get("action") not in SPEND_ACTIONS:
            continue
        intent_id = str(record.get("intent_id", ""))
        if intent_id:
            grouped.setdefault(intent_id, []).append(record)
        else:
            unlinked.append(record)

    countable: list[dict[str, Any]] = []
    for records in grouped.values():
        terminal = next(
            (record for record in reversed(records) if record.get("state") in _TERMINAL_STATES),
            None,
        )
        # No terminal outcome → the durable intent is still potentially spendable or
        # already spent. Reserve its amount until reconciliation resolves it.
        countable.append(terminal or records[0])
    countable.extend(record for record in unlinked if record.get("state") in _TERMINAL_STATES)

    calendar_day = rolling_24h = 0
    for record in countable:
        try:
            ts = datetime.fromisoformat(str(record.get("ts", ""))).astimezone(UTC)
        except ValueError:
            continue
        amount = _spend_amount_sat_v2(record)
        if ts.date() == today:
            calendar_day += amount
        if window_start <= ts <= moment:
            rolling_24h += amount
    return max(calendar_day, rolling_24h)


__all__ = [
    "SPEND_ACTIONS",
    "UNPROVEN_OUTCOME_ALLOWS_RETRY",
    "UNPROVEN_OUTCOME_COUNTS_IN_CAP",
    "LightningOpsLedgerError",
    "append_ln_op",
    "append_ln_outcome",
    "attest_ln_ops_tip",
    "bolt11_amount_sat",
    "legacy_ln_ops_path",
    "ln_ops_v2_path",
    "migrate_legacy_ln_ops",
    "normalize_payment_hash",
    "prepare_ln_intent",
    "read_recent_ln_ops",
    "read_verified_ln_ops_snapshot",
    "redact_ln_op_record",
    "spent_today_sat",
    "spent_today_sat_v2",
    "verify_ln_ops_ledger",
]
