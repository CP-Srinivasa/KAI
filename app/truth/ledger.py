"""Hash-chained truth attestation ledger (ADR 0013 Tier 1, realises ADR 0012).

Where ``app.truth.attestation`` is the pure primitive, THIS module makes it
produce verifiable statements about REAL artifacts: every record binds a payload
attestation (recomputable SHA-256 over canonical JSON) into a forward-only hash
chain, so removal, reordering or after-the-fact edits of any attested claim are
detectable by anyone holding the ledger file. Each append is mirrored into the
KAI audit stream (``KAI_TRUTH_ATTESTATION``) for operator visibility.

Wired consumers: pre-registration entries (``attest_prereg_ledger``) and
canonical-edge reports (``trading canonical-edge --attest``). Read/append-only —
no execution, no capital.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.audit.kai_audit_service import KaiAuditService, get_default_kai_audit_service
from app.core.file_lock import FileLockError, append_lock
from app.truth.attestation import compute_attestation

DEFAULT_TRUTH_LEDGER_PATH = Path("artifacts/truth/attestation_ledger.jsonl")
GENESIS_HASH = "0" * 64
SCHEMA = "truth-attestation/v1"


class TruthLedgerError(RuntimeError):
    """Raised when the ledger cannot be safely extended or read."""


def _chain_tip(path: Path) -> tuple[str, int]:
    """Return (record_hash, seq) of the last record — fail-closed on a broken tail.

    A corrupt final line means the chain state is unknown; extending it would
    silently fork history, so this raises instead.
    """
    if not path.exists():
        return GENESIS_HASH, 0
    last_line = ""
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                last_line = line.strip()
    if not last_line:
        return GENESIS_HASH, 0
    try:
        record = json.loads(last_line)
        return str(record["record_hash"]), int(record["seq"])
    except (ValueError, TypeError, KeyError) as exc:
        raise TruthLedgerError(
            f"truth ledger tail unreadable ({path}) — refusing to extend a broken chain"
        ) from exc


def append_attestation(
    kind: str,
    subject_id: str | None,
    payload: dict[str, Any],
    *,
    path: Path = DEFAULT_TRUTH_LEDGER_PATH,
    audit: KaiAuditService | None = None,
    mirror_audit: bool = True,
    attested_at_utc: str | None = None,
) -> dict[str, Any]:
    """Attest one payload and chain it onto the ledger (append-only).

    ``subject_id=None`` derives a deterministic id from the payload hash.
    Returns the persisted record incl. ``record_hash``/``prev_hash``/``seq``.
    """
    attestation = compute_attestation(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Audit-Befund P0-1 (2026-08-06): Tip-Lesen und Append MÜSSEN unter EINEM
    # exklusiven Lock liegen. Zwei parallele Writer (Persistent=true-Boot-Storm:
    # truth-anchor + canonical-edge-attest; oder Timer + Operator-CLI) lasen
    # sonst denselben Tip und schrieben beide seq=N+1 — die Kette forkt, ein
    # gesetzter OTS-Anker beweist einen toten Tip, append-only ⇒ irreparabel.
    # strict: kein Lock ⇒ kein Append (ein verschobener Append ist billiger als
    # ein gegabelter; Anchor-Timer und CLI wiederholen).
    try:
        with append_lock(path, strict=True):
            prev_hash, prev_seq = _chain_tip(path)
            record: dict[str, Any] = {
                "schema": SCHEMA,
                "seq": prev_seq + 1,
                "kind": kind,
                "subject_id": subject_id or f"{kind}:{attestation['hash'][:16]}",
                "attested_at_utc": attested_at_utc or datetime.now(UTC).isoformat(),
                "algo": attestation["algo"],
                "payload": payload,
                "payload_hash": attestation["hash"],
                "prev_hash": prev_hash,
            }
            record["record_hash"] = compute_attestation(record)["hash"]

            line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except FileLockError as exc:
        raise TruthLedgerError(
            f"truth ledger lock unavailable ({path}) — refusing an unserialized append"
        ) from exc

    if mirror_audit:
        (audit or get_default_kai_audit_service()).append(
            "KAI_TRUTH_ATTESTATION",
            state="ANALYSIS",
            severity="info",
            source="truth_ledger",
            message=f"attested {record['kind']} {record['subject_id']} (seq {record['seq']})",
            payload={
                "kind": record["kind"],
                "subject_id": record["subject_id"],
                "seq": record["seq"],
                "payload_hash": record["payload_hash"],
                "record_hash": record["record_hash"],
                "ledger_path": str(path),
            },
        )
    return record


def verify_ledger(path: Path = DEFAULT_TRUTH_LEDGER_PATH) -> dict[str, Any]:
    """Recompute every attestation and chain link (pure, read-only).

    Checks per record: payload reproducibility (payload -> payload_hash), record
    integrity (record -> record_hash) and forward-only linkage (prev_hash/seq).
    Returns ``{"ok", "records", "errors": [{"seq", "reason"}]}`` — an empty or
    missing ledger verifies ok with 0 records.
    """
    errors: list[dict[str, Any]] = []
    checked = 0
    prev_hash, prev_seq = GENESIS_HASH, 0
    if not path.exists():
        return {"ok": True, "records": 0, "errors": []}

    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
            seq = int(record["seq"])
        except (ValueError, TypeError, KeyError):
            errors.append({"seq": line_no, "reason": "unparseable record — chain unverifiable"})
            break
        checked += 1
        if seq != prev_seq + 1:
            errors.append({"seq": seq, "reason": f"seq gap (expected {prev_seq + 1})"})
        if record.get("prev_hash") != prev_hash:
            errors.append({"seq": seq, "reason": "chain broken (prev_hash mismatch)"})
        recomputed_payload = compute_attestation(record.get("payload", {}))["hash"]
        if recomputed_payload != record.get("payload_hash"):
            errors.append({"seq": seq, "reason": "payload_hash mismatch (payload tampered)"})
        body = {k: v for k, v in record.items() if k != "record_hash"}
        if compute_attestation(body)["hash"] != record.get("record_hash"):
            errors.append({"seq": seq, "reason": "record_hash mismatch (record tampered)"})
        # Linkage follows the STORED hash so one tampered record does not
        # cascade phantom chain errors over every later record.
        prev_hash, prev_seq = str(record.get("record_hash")), seq

    return {"ok": not errors, "records": checked, "errors": errors}


def read_record(seq: int, *, path: Path = DEFAULT_TRUTH_LEDGER_PATH) -> dict[str, Any] | None:
    """Return the ledger record with ``seq`` (tolerant read), or ``None`` if absent.

    Read-only building block for third-party verification (``trading canonical-edge
    --verify <seq>``): fetch one attested record so its payload can be
    recomputed. Malformed/blank lines are skipped rather than raised, so a single
    corrupt line elsewhere never blocks reading a valid record.
    """
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("seq") == seq:
            return record
    return None


def attested_subject_ids(path: Path, kind: str | None = None) -> set[str]:
    """Subject ids already attested (tolerant read; dedupe helper for backfills)."""
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except ValueError:
            continue
        if kind is None or record.get("kind") == kind:
            subject = record.get("subject_id")
            if subject:
                out.add(str(subject))
    return out


def chain_tip(path: Path = DEFAULT_TRUTH_LEDGER_PATH) -> dict[str, Any]:
    """Public read of the ledger head — ``{"record_hash", "seq"}`` (genesis if empty).

    The tip ``record_hash`` transitively commits the ENTIRE chain (each record embeds
    the prior ``record_hash``), so anchoring the tip anchors every record before it.
    """
    record_hash, seq = _chain_tip(path)
    return {"record_hash": record_hash, "seq": seq}


def attest_verdict_reports(
    *,
    verdicts_dir: Path | None = None,
    truth_path: Path = DEFAULT_TRUTH_LEDGER_PATH,
    audit: KaiAuditService | None = None,
    mirror_audit: bool = True,
) -> dict[str, Any]:
    """Attest every attested verdict report not yet in the truth ledger (idempotent).

    Scans the verdict-report directory and chains each report's payload into the
    truth ledger, deduped by the report's own attestation hash. This binds the
    falsification VERDICTS ("we tested H under pre-registered criteria and it
    passed/failed") into the same tamper-evident chain as the pre-registrations they
    resolve — so the platform's core claim can no longer drift after the fact.
    Corrupt/foreign files are skipped (never crash a run).
    """
    from app.research.verdict_report import DEFAULT_VERDICTS_DIR

    src_dir = verdicts_dir or DEFAULT_VERDICTS_DIR
    if not src_dir.is_dir():
        return {"total": 0, "attested": 0, "skipped": 0, "ledger": str(truth_path)}

    already = attested_subject_ids(truth_path, kind="verdict")
    attested = skipped = 0
    for path in sorted(src_dir.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            payload = report["payload"]
            att_hash = str(report["attestation"]["hash"])
        except (ValueError, KeyError, OSError):
            continue  # skip corrupt/foreign files — an unreadable report is not a verdict
        if att_hash in already:
            skipped += 1
            continue
        append_attestation(
            "verdict",
            att_hash,
            payload,
            path=truth_path,
            audit=audit,
            mirror_audit=mirror_audit,
        )
        already.add(att_hash)
        attested += 1
    return {
        "total": attested + skipped,
        "attested": attested,
        "skipped": skipped,
        "ledger": str(truth_path),
    }


def attest_prereg_ledger(
    *,
    prereg_path: Path | None = None,
    truth_path: Path = DEFAULT_TRUTH_LEDGER_PATH,
    audit: KaiAuditService | None = None,
    mirror_audit: bool = True,
) -> dict[str, Any]:
    """Attest every pre-registered hypothesis not yet in the truth ledger.

    Idempotent: entries are deduped by ``prereg_id``. This turns the prereg
    ledger's commit-before-measurement doctrine into a verifiable claim — the
    registered hypothesis text can no longer drift silently after the fact.
    """
    from app.research.prereg_ledger import DEFAULT_PREREG_LEDGER_PATH, PreRegistrationLedger

    source = PreRegistrationLedger(prereg_path or DEFAULT_PREREG_LEDGER_PATH)
    already = attested_subject_ids(truth_path, kind="prereg")
    attested = skipped = 0
    for entry in source.entries():
        if entry.prereg_id in already:
            skipped += 1
            continue
        append_attestation(
            "prereg",
            entry.prereg_id,
            json.loads(entry.to_json()),
            path=truth_path,
            audit=audit,
            mirror_audit=mirror_audit,
        )
        already.add(entry.prereg_id)
        attested += 1
    return {
        "total": attested + skipped,
        "attested": attested,
        "skipped": skipped,
        "ledger": str(truth_path),
    }


__all__ = [
    "DEFAULT_TRUTH_LEDGER_PATH",
    "GENESIS_HASH",
    "SCHEMA",
    "TruthLedgerError",
    "append_attestation",
    "attest_prereg_ledger",
    "attest_verdict_reports",
    "attested_subject_ids",
    "chain_tip",
    "read_record",
    "verify_ledger",
]
