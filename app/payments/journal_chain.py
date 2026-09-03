"""Was eine gueltige Journal-Kette ausmacht (ADR 0017 §5).

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
from typing import Any

#: Der Vorgaenger des ersten Records.
GENESIS_HASH = "0" * 64

SCHEMA = "payment-journal/v1"

RUNBOOK = "docs/adr/0017-payment-fabric-control-plane.md"


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


__all__ = [
    "GENESIS_HASH",
    "RUNBOOK",
    "SCHEMA",
    "ChainStatus",
    "JournalIntegrityError",
    "canonical_bytes",
    "compute_record_hash",
    "parse_record",
    "verify_link",
]
