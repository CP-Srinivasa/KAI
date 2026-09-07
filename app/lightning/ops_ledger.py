"""ARCHIV des alten Lightning-Geldjournals — nur noch lesen und abschliessen.

**Dieses Modul eroeffnet keinen Geldvorgang mehr.** Mit ADR 0018 §12 (PR 1)
sind ``prepare_ln_intent``, ``append_ln_op``, ``migrate_legacy_ln_ops``,
``spent_today_sat``/``_v2``, ``attest_ln_ops_tip``, ``read_recent_ln_ops`` und
die gesamte v1-Haelfte entfallen. Der einzige Sendeweg ist
``app.payments.service.PaymentService.execute``; der Tages-Cap kommt aus
``journal_index.totals_for_day``; der Truth-Anker aus
``payments.journal_chain.attest_payment_journal_tip``; die Redaktionsgrenze
lebt in ``app.lightning.receive_ledger``.

**Was bleibt und warum genau das.** ``artifacts/ln_ops_ledger_v2.jsonl`` liegt
weiter am Geraet (Backup-Vertrag unveraendert, ``VANISHED_MONEY``-Guard scharf)
und wird bis PR 2 noch von zwei Stellen GELESEN:
``app.lightning.reconciliation`` und ``app.payments.reconcile_dual``.

Die eine verbliebene Schreibfaehigkeit ist :func:`append_ln_outcome`: der
Crash-Gap-Reconciler muss einen offenen Alt-Intent mit seinem am Node
BEWIESENEN Ausgang schliessen koennen. Sie kann **keinen neuen Vorgang
eroeffnen** — :func:`_append_chained_record` verlangt strukturell einen bereits
vorhandenen ``intent``-Record. Ein Buch, das man nicht mehr aufschlagen, aber
noch zuklappen kann.

**PR 2 entfernt dieses Modul vollstaendig**, zusammen mit ``reconciliation.py``
und ``reconcile_dual.py``. Ab dort ist die Datei reines Archiv.

Historie und Reparatur: ``docs/runbooks/ln_ops_ledger_v2_migration.md``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import portalocker

from app.lightning.receive_ledger import redact_ln_op_record
from app.truth.attestation import compute_attestation

logger = logging.getLogger(__name__)

_OPS_V2_DEFAULT_PATH = Path("artifacts/ln_ops_ledger_v2.jsonl")
_OPS_V2_PATH_ENV = "APP_LN_OPS_LEDGER_V2_PATH"
_GENESIS_HASH = "0" * 64
_RUNBOOK = "docs/runbooks/ln_ops_ledger_v2_migration.md"

_TERMINAL_STATES = frozenset({"executed", "error"})
_ALLOWED_STATES = frozenset({"intent", "in_flight", "unknown", "executed", "error"})


class LightningOpsLedgerError(RuntimeError):
    """The archived money journal cannot be read or honestly closed."""


def ln_ops_v2_path() -> Path:
    """Pfad des archivierten v2-Journals (``APP_LN_OPS_LEDGER_V2_PATH`` sticht)."""
    override = os.environ.get(_OPS_V2_PATH_ENV, "").strip()
    return Path(override) if override else _OPS_V2_DEFAULT_PATH


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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


def _append_chained_record(record: dict[str, Any], *, path: Path) -> dict[str, Any]:
    """Haenge EINEN verketteten, fsync'ten Ausgang an einen offenen Intent.

    **Strukturell kein Eroeffnungspfad mehr (ADR 0018 §12).** Der Bestand hatte
    hier ein ``require_intent``-Flag; mit ``False`` legte es einen neuen
    Geldvorgang an. Dieser Zweig ist ENTFERNT, nicht abgeschaltet — ein Flag
    haette dieselbe Faehigkeit nur einen Funktionsaufruf weit weggeraeumt.
    Uebrig bleibt: ein Ausgang braucht einen vorhandenen ``intent``, und ein
    bereits terminaler Vorgang nimmt keinen zweiten.

    Ehrlich ueber seine Zusage (m-17): der Append VERTRAUT dem aktuellen Tip —
    er verlinkt auf den ``record_hash`` der letzten Zeile, ohne die ganze Kette
    neu zu pruefen. Manipulations-EVIDENZ liefert :func:`verify_ln_ops_ledger`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    chained: dict[str, Any]
    try:
        with portalocker.Lock(path, mode="a+", encoding="utf-8", timeout=10) as handle:
            records = _locked_records(handle)
            chain = [r for r in records if "record_hash" in r and "seq" in r]
            if records and len(chain) != len(records):
                raise LightningOpsLedgerError(
                    "unchained (v1/legacy) rows present — the archived journal cannot be "
                    f"closed honestly: {_RUNBOOK}"
                )
            tip = chain[-1] if chain else None
            intent_id = str(record["intent_id"])
            same_intent = [r for r in records if str(r.get("intent_id", "")) == intent_id]
            if not same_intent or same_intent[0].get("state") != "intent":
                raise LightningOpsLedgerError(f"outcome has no prepared intent: {intent_id}")
            if any(r.get("state") in _TERMINAL_STATES for r in same_intent):
                raise LightningOpsLedgerError(f"intent already terminal: {intent_id}")

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
    """Schliesse einen offenen Alt-Intent mit seinem Ausgang; ``True`` wenn gebucht.

    Einziger verbliebener Aufrufer ist ``app.lightning.reconciliation`` — der
    Crash-Gap-Reconciler, der einen am Node BEWIESENEN terminalen Zustand
    nachtraegt. Notwendigerweise fail-soft: LND kann den Wert laengst bewegt
    haben, ein Wurf koennte das nicht rueckgaengig machen. Der offene
    ``intent``-Record bleibt dann stehen und IST die Warteschlange.
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
        _append_chained_record(record, path=path or ln_ops_v2_path())
    except Exception as exc:  # noqa: BLE001 — audit must never kill the caller
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


__all__ = [
    "LightningOpsLedgerError",
    "append_ln_outcome",
    "ln_ops_v2_path",
    "read_verified_ln_ops_snapshot",
    "verify_ln_ops_ledger",
]
