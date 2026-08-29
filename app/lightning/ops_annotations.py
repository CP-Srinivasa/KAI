"""Append-only annotations ABOUT the money journal — deliberately beside it.

Why a second file instead of a new record type inside ``ln_ops_ledger_v2.jsonl``
(G2, decided 2026-08-29): the v2 journal is the SPEND path. Its writer feeds the
daily cap, the payment dedup and the spend gate. Adding a new record kind there
would put fresh code on the capital path — in a sprint whose whole cause was an
insufficiently guarded write path — and every existing reader would have to learn
to skip it. Miss one reader and the correction corrupts the very numbers it is
meant to fix.

So an annotation NEVER changes a money record. It points AT one (by ``seq`` and
by ``record_hash``, so it cannot silently follow a rewritten row) and states what
was independently established about it. Resolution happens at READ time, as an
overlay, and only for display/forensics — never for the cap, never for dedup.

Two facts this exists to record, both from the G2 forensics:

  * ``TEST_FIXTURE`` — four rows written on 2026-08-05 from a manual session carry
    test values (``node_pubkey="02ab"``, ``funding_txid_str="deadbeef"``). They were
    sealed into the hash chain by the v1→v2 migration. The chain proves nothing was
    ALTERED; it never claimed the values were TRUE.
  * ``RESOLVED_EXECUTED`` — two rows carry ``error``, which per the m-14 rule means
    "we do not know whether value moved". The node has known the answer since July:
    both moved value (400.000 sat channel open, 25.000 sat payment). An UNPROVEN
    outcome with no resolution path decays into a silent untruth.

Retraction, not repair: a wrong annotation is cancelled by appending a
``RETRACTION`` that names it. Nothing here is ever edited.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import portalocker

from app.truth.attestation import compute_attestation

_ANNOTATIONS_DEFAULT_PATH = Path("artifacts/ln_ops_annotations.jsonl")
_ANNOTATIONS_PATH_ENV = "APP_LN_OPS_ANNOTATIONS_PATH"
_SCHEMA = "ln-ops-annotation/v1"
_GENESIS_HASH = "0" * 64

#: What an annotation may assert. Kept deliberately small — every kind here has a
#: defined effect on the read overlay, and an unknown kind is a verification error
#: rather than a silently ignored row.
ANNOTATION_KINDS = frozenset(
    {
        "TEST_FIXTURE",  # the annotated row does not describe a real action
        "RESOLVED_EXECUTED",  # an UNPROVEN outcome: value DID move, proven at the node
        "RESOLVED_NOT_EXECUTED",  # an UNPROVEN outcome: value did NOT move
        "RETRACTION",  # cancels an earlier annotation of this ledger
    }
)


class LightningOpsAnnotationError(RuntimeError):
    """The annotation ledger cannot be safely extended."""


def ln_ops_annotations_path() -> Path:
    """Resolve the annotation ledger path (``APP_LN_OPS_ANNOTATIONS_PATH`` wins)."""
    override = os.environ.get(_ANNOTATIONS_PATH_ENV, "").strip()
    return Path(override) if override else _ANNOTATIONS_DEFAULT_PATH


def _records_from_text(raw: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def append_annotation(
    *,
    kind: str,
    target_seq: list[int],
    target_record_hash: list[str],
    assertion: str,
    evidence: list[str],
    author: str,
    path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one hash-chained annotation. Fail-closed: raises rather than guessing.

    ``target_record_hash`` is what makes an annotation honest across time. Pointing
    at ``seq`` alone would still "apply" if the row underneath were ever replaced;
    binding the row hash means a changed row loses its annotation instead of
    inheriting a claim that was made about different content.
    """
    if kind not in ANNOTATION_KINDS:
        raise LightningOpsAnnotationError(f"unknown annotation kind: {kind}")
    if not target_seq:
        raise LightningOpsAnnotationError("annotation must name at least one target seq")
    if not assertion.strip():
        raise LightningOpsAnnotationError("annotation must carry an assertion")
    if not evidence:
        raise LightningOpsAnnotationError("annotation must cite evidence")

    target = path or ln_ops_annotations_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    moment = now or datetime.now(UTC)
    body: dict[str, Any] = {
        "schema": _SCHEMA,
        "ts": moment.isoformat(),
        "kind": kind,
        "target_seq": sorted(int(s) for s in target_seq),
        "target_record_hash": list(target_record_hash),
        "assertion": assertion.strip(),
        "evidence": list(evidence),
        "author": author,
    }
    try:
        with portalocker.Lock(target, mode="a+", encoding="utf-8", timeout=10) as handle:
            handle.seek(0)
            existing = _records_from_text(handle.read())
            tip = existing[-1] if existing else None
            body["seq"] = int(tip["seq"]) + 1 if tip else 1
            body["prev_hash"] = str(tip["record_hash"]) if tip else _GENESIS_HASH
            body["record_hash"] = compute_attestation(body)["hash"]
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except LightningOpsAnnotationError:
        raise
    except Exception as exc:  # noqa: BLE001 — a half-written annotation is worse than none
        raise LightningOpsAnnotationError(f"annotation append failed: {exc}") from exc
    return body


def verify_annotations(path: Path | None = None) -> dict[str, Any]:
    """Verify seq continuity, hash links and row hashes of the annotation ledger.

    Mirrors :func:`app.lightning.ops_ledger.verify_ln_ops_ledger` in shape so a
    reader has one mental model for both files. A missing file is ``ok`` with zero
    records — an absent annotation ledger is a valid state, not a fault.
    """
    target = path or ln_ops_annotations_path()
    if not target.exists():
        return {"ok": True, "records": 0, "errors": []}

    errors: list[dict[str, Any]] = []
    prev_hash = _GENESIS_HASH
    prev_seq = 0
    count = 0
    for line_no, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            seq = int(record["seq"])
        except (ValueError, TypeError, KeyError):
            errors.append({"seq": line_no, "reason": "unparseable or unchained annotation"})
            break
        count += 1
        if seq != prev_seq + 1:
            errors.append({"seq": seq, "reason": f"seq gap (expected {prev_seq + 1})"})
        if record.get("prev_hash") != prev_hash:
            errors.append({"seq": seq, "reason": "prev_hash mismatch"})
        body = {k: v for k, v in record.items() if k != "record_hash"}
        if compute_attestation(body)["hash"] != record.get("record_hash"):
            errors.append({"seq": seq, "reason": "record_hash mismatch"})
        if record.get("kind") not in ANNOTATION_KINDS:
            errors.append({"seq": seq, "reason": f"unknown kind: {record.get('kind')}"})
        prev_hash = str(record.get("record_hash", ""))
        prev_seq = seq
    return {"ok": not errors, "records": count, "errors": errors}


def annotation_overlay(
    ops_records: list[dict[str, Any]], annotations: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Map money-journal ``seq`` → the annotation in force, after retractions.

    Two rules, both chosen so that a mistake fails toward "no claim" rather than
    toward a wrong claim:

      * a ``RETRACTION`` naming an annotation ``seq`` removes that annotation's
        effect entirely — the row goes back to unannotated;
      * an annotation whose ``target_record_hash`` does not match the money row it
        names is DROPPED, not applied. The row changed underneath it (or the
        annotation was authored against a different ledger), so the claim no longer
        provably belongs to this content.
    """
    hash_by_seq = {int(r["seq"]): str(r.get("record_hash", "")) for r in ops_records if "seq" in r}
    retracted: set[int] = set()
    for ann in annotations:
        if ann.get("kind") == "RETRACTION":
            retracted.update(int(s) for s in ann.get("target_seq", []))

    overlay: dict[int, dict[str, Any]] = {}
    for ann in annotations:
        if ann.get("kind") == "RETRACTION" or int(ann.get("seq", 0)) in retracted:
            continue
        expected = list(ann.get("target_record_hash") or [])
        for idx, seq in enumerate(int(s) for s in ann.get("target_seq", [])):
            if seq not in hash_by_seq:
                continue
            if idx < len(expected) and expected[idx] and hash_by_seq[seq] != expected[idx]:
                continue  # the row underneath is not the one that was annotated
            overlay[seq] = {
                "kind": ann.get("kind"),
                "assertion": ann.get("assertion"),
                "evidence": ann.get("evidence"),
                "annotation_seq": int(ann.get("seq", 0)),
                "ts": ann.get("ts"),
            }
    return overlay


def annotate_for_display(
    ops_records: list[dict[str, Any]], annotations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return COPIES of the money rows with an ``annotation`` field where one applies.

    Copies on purpose: nothing in this module may hand a caller a mutated money
    record. ``state`` is never touched — a reader that computes the cap or the
    dedup from these rows must see exactly what the journal says.
    """
    overlay = annotation_overlay(ops_records, annotations)
    out: list[dict[str, Any]] = []
    for record in ops_records:
        copy = dict(record)
        seq = int(record["seq"]) if "seq" in record else None
        if seq is not None and seq in overlay:
            copy["annotation"] = overlay[seq]
        out.append(copy)
    return out


def read_annotations(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the annotation ledger; ``[]`` when it does not exist yet."""
    target = path or ln_ops_annotations_path()
    if not target.exists():
        return []
    return _records_from_text(target.read_text(encoding="utf-8"))


__all__ = [
    "ANNOTATION_KINDS",
    "LightningOpsAnnotationError",
    "annotate_for_display",
    "annotation_overlay",
    "append_annotation",
    "ln_ops_annotations_path",
    "read_annotations",
    "verify_annotations",
]
