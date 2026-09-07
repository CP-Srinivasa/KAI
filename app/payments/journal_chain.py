"""Was eine gueltige Journal-Kette ausmacht (ADR 0018 §5).

Getrennt vom Schreibpfad (:mod:`app.payments.journal`), weil es zwei
verschiedene Fragen sind: *"ist diese Folge von Records ehrlich?"* laesst sich
ohne Lock, ohne Datei und ohne Prozesskontext beantworten — *"wie haenge ich
sicher etwas an?"* nicht. Die Trennung macht die Kettenregeln einzeln pruefbar
und haelt beide Module unter der 350-Zeilen-Grenze.

Die Kette macht eine nachtraegliche Aenderung **erkennbar**, nicht unmoeglich.
Wer das Journal umschreibt, kann jeden Hash neu rechnen; wogegen sie schuetzt,
ist die stille Aenderung EINER Zeile — und genau die ist der realistische Fall.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.payments.models import PaymentAuditEvent

#: Der Vorgaenger des ersten Records.
GENESIS_HASH = "0" * 64

SCHEMA = "payment-journal/v1"

RUNBOOK = "docs/adr/0018-payment-fabric-control-plane.md"


class JournalIntegrityError(RuntimeError):
    """Das Geld-Journal kann nicht ehrlich fortgeschrieben werden."""


@dataclass(frozen=True)
class ChainStatus:
    """Ergebnis einer vollstaendigen Kettenpruefung."""

    ok: bool
    records: int = 0
    tip_hash: str = GENESIS_HASH
    reason: str = ""
    broken_at_seq: int | None = None


def canonical_bytes(record: dict[str, Any]) -> bytes:
    """Kanonische Serialisierung — sortierte Schluessel, keine Leerzeichen.

    Kanonisch heisst hier: dieselbe Menge von Feldern ergibt immer dieselben
    Bytes. Ohne das haette derselbe Record je nach Dict-Reihenfolge einen
    anderen Hash, und die Kette waere nicht pruefbar, sondern nur behauptet.
    """
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def compute_record_hash(record: dict[str, Any]) -> str:
    """SHA-256 ueber den Record OHNE sein eigenes ``record_hash``."""
    without = {key: value for key, value in record.items() if key != "record_hash"}
    return hashlib.sha256(canonical_bytes(without)).hexdigest()


def build_record(
    *,
    seq: int,
    ts: datetime,
    intent_id: str,
    event_type: str,
    payload: dict[str, Any],
    prev_hash: str,
) -> tuple[dict[str, Any], PaymentAuditEvent]:
    """Baue einen verketteten Record und pruefe seine Form, BEVOR er auf Platte geht.

    Ein unbekannter ``event_type`` oder ein defekter Hash darf nicht erst beim
    Lesen auffallen — dann steht er bereits unwiderruflich im append-only
    Journal. Die Validierung gehoert deshalb vor den ``write``.
    """
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "seq": seq,
        "ts": ts.isoformat(),
        "intent_id": intent_id,
        "event_type": event_type,
        "payload": payload,
        "prev_hash": prev_hash,
    }
    record["record_hash"] = compute_record_hash(record)
    return record, as_event(record)


def as_event(record: dict[str, Any]) -> PaymentAuditEvent:
    """Typisierte Sicht auf einen Record (ohne das rein technische ``schema``)."""
    return PaymentAuditEvent.model_validate(
        {key: value for key, value in record.items() if key != "schema"}
    )


def parse_record(raw: bytes, *, after_seq: int) -> dict[str, Any]:
    """Lies eine Journalzeile. Jede unlesbare Zeile beendet das Schreiben."""
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise JournalIntegrityError(
            f"payment journal record after seq {after_seq} is unreadable: {exc} — "
            f"repair first: {RUNBOOK}"
        ) from exc
    if not isinstance(record, dict):
        raise JournalIntegrityError(
            f"payment journal record after seq {after_seq} is not a JSON object"
        )
    return record


def verify_link(record: dict[str, Any], *, tip_seq: int, tip_hash: str) -> None:
    """Pruefe die drei Aussagen, die ein Record ueber seine Herkunft macht.

    Reihenfolge mit Absicht: erst die Luecke (ein entfernter Record faellt
    sonst als Hash-Fehler auf und liest sich wie eine Manipulation), dann die
    Verkettung, dann der Inhalt.
    """
    expected_seq = tip_seq + 1
    if record.get("seq") != expected_seq:
        raise JournalIntegrityError(
            f"payment journal seq gap: expected {expected_seq}, found {record.get('seq')!r} — "
            "a record was removed or reordered"
        )
    if record.get("prev_hash") != tip_hash:
        raise JournalIntegrityError(
            f"payment journal prev_hash mismatch at seq {expected_seq}: the chain does "
            "not link to the previous record"
        )
    if record.get("record_hash") != compute_record_hash(record):
        raise JournalIntegrityError(
            f"payment journal record_hash mismatch at seq {expected_seq}: the record "
            "was modified after it was written"
        )


def attest_payment_journal_tip(
    *,
    journal_path: Path | None = None,
    truth_path: Path | None = None,
    mirror_audit: bool = True,
) -> dict[str, Any]:
    """Binde den verifizierten Tip des Geld-Journals idempotent in KAI Truth.

    Der Ersatz fuer ``ops_ledger.attest_ln_ops_tip``, das mit dem Altpfad
    faellt (ADR 0018 §12). Ohne ihn waere nach dem Rueckbau **keine
    Geldbewegung mehr on-chain verankert**: die Truth-Kette wird per OTS
    gestempelt, das Payment-Journal ist zwar in sich verkettet, aber nicht
    attestiert — und eine Kette, die nur sich selbst bezeugt, datiert nichts.

    Die Zusage ist woertlich uebernommen, inklusive ihrer unbequemen Haelfte:
    eine gebrochene Kette wird **verweigert**. Ein defektes Geldjournal in die
    Truth-Kette zu schreiben hiesse, es dort zu waschen. Aufrufer auf dem
    gemeinsamen Anker-Pfad muessen das als Warnung behandeln, nie als Grund,
    den Rest des Laufs zu ueberspringen (BL-1).

    Args:
        journal_path: Journal; ``None`` nimmt den konfigurierten Pfad.
        truth_path: Truth-Ledger; ``None`` nimmt den Standardpfad.
        mirror_audit: Spiegelung in den KAI-Audit-Strom.

    Returns:
        ``{"total", "attested", "skipped"}`` — ``total=0`` heisst leeres Journal.

    Raises:
        JournalIntegrityError: die Kette verifiziert nicht.
    """
    # Verzoegerte Importe: ``journal`` importiert dieses Modul (Zyklus), und
    # ``app.truth`` waere zur Importzeit eine Paketkante, die der
    # Richtungs-Test von ADR §2 als Zyklus zaehlt.
    from app.core.payment_settings import get_payment_settings
    from app.payments.journal import PaymentJournal
    from app.truth.ledger import (
        DEFAULT_TRUTH_LEDGER_PATH,
        append_attestation,
        attested_subject_ids,
    )

    source = journal_path or get_payment_settings().resolved_journal_path()
    target = truth_path or DEFAULT_TRUTH_LEDGER_PATH
    journal = PaymentJournal(source)
    status = journal.verify_chain()
    if not status.ok:
        raise JournalIntegrityError(f"refusing to attest a broken payment journal: {status.reason}")
    if status.records == 0:
        return {"total": 0, "attested": 0, "skipped": 0}

    subject = f"payment-tip:{status.tip_hash}"
    if subject in attested_subject_ids(target, kind="payment_journal_tip"):
        return {"total": 1, "attested": 0, "skipped": 1}
    # Der Index kommt aus einem eigenen, vollstaendigen Lauf — ``verify_chain``
    # arbeitet bewusst auf einer Sonde und laesst den Index des Aufrufers in Ruhe.
    journal.open()
    append_attestation(
        "payment_journal_tip",
        subject,
        {
            "schema": "payment-journal-tip/v1",
            "record_hash": status.tip_hash,
            "seq": status.records,
            "open_intents": sorted(journal.index.open_intents()),
        },
        path=target,
        mirror_audit=mirror_audit,
    )
    return {"total": 1, "attested": 1, "skipped": 0}


__all__ = [
    "GENESIS_HASH",
    "RUNBOOK",
    "SCHEMA",
    "ChainStatus",
    "JournalIntegrityError",
    "as_event",
    "attest_payment_journal_tip",
    "build_record",
    "canonical_bytes",
    "compute_record_hash",
    "parse_record",
    "verify_link",
]
