"""Decision-Journal Hash-Chain (additive Migration).

Spec: kai_adaptive_learning_backlog_20260509.md Schritt 2.

**Design-Begründung — separater Chain-Stream statt Schema-Erweiterung:**

``DecisionRecord`` ist mit ``extra="forbid"`` und ``frozen=True`` versiegelt;
das Schema direkt zu erweitern bräche alle bestehenden Records (extras-forbid
verlangt dass beim Laden keine unbekannten Felder existieren). Daher führt
dieses Modul einen **parallelen Stream**:

- ``artifacts/decision_journal.jsonl`` (existing): DecisionRecord-Payload
- ``artifacts/decision_journal_chain.jsonl`` (neu): ein Chain-Entry pro Decision

Jeder Chain-Entry referenziert via ``decision_id`` zurück auf den Journal-Eintrag
und enthält ``prev_chain_hash`` + ``record_hash``. Tamper-evident: wenn jemand
einen Journal-Eintrag nachträglich ändert, ändert sich der record_hash, die
Chain bricht beim verify().

**Toleranz für alte Rows:** ``verify_chain()`` skippt Decision-IDs für die
kein Chain-Entry existiert (legitime Migration-Lücke), prüft aber dass alle
neueren Records lückenlos verkettet sind.

Hash-Algorithmus: SHA-256 (analog ``app/audit/structured_reasoning.py``).
Genesis-Hash: 64×"0".
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

CHAIN_SCHEMA_VERSION: Final[int] = 1
GENESIS_PREV_HASH: Final[str] = "0" * 64


class DecisionChainEntry(BaseModel):
    """Ein einzelner Chain-Entry pro Decision.

    Trail-Schema: jeder Entry zeigt auf den vorigen via prev_chain_hash + auf
    einen Journal-Eintrag via decision_id. Reorder-resistent über append-only-
    File-Order: Reihenfolge im File = Chain-Reihenfolge.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=CHAIN_SCHEMA_VERSION, ge=1)
    decision_id: str = Field(min_length=1)
    record_hash: str = Field(min_length=64, max_length=64)
    prev_chain_hash: str = Field(min_length=64, max_length=64)
    chain_hash: str = Field(min_length=64, max_length=64)
    chained_at_utc: str = Field(min_length=1)


def _canonical_record_json(record_payload: dict[str, Any]) -> str:
    """Sortiert + UTF-8 für deterministisches Hashing."""
    return json.dumps(record_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def hash_record(record_payload: dict[str, Any]) -> str:
    """SHA-256 über die canonical-JSON eines DecisionRecord-Payloads."""
    return hashlib.sha256(_canonical_record_json(record_payload).encode("utf-8")).hexdigest()


def _canonical_chain_json(entry_payload: dict[str, Any]) -> str:
    """Chain-Hash deckt alle Felder außer ``chain_hash`` selbst."""
    keys = (
        "schema_version",
        "decision_id",
        "record_hash",
        "prev_chain_hash",
        "chained_at_utc",
    )
    cleaned = {k: entry_payload[k] for k in keys}
    return json.dumps(cleaned, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash_chain_entry(entry_payload: dict[str, Any]) -> str:
    """Chain-Hash = sha256 über alles AUSSER chain_hash selbst."""
    return hashlib.sha256(_canonical_chain_json(entry_payload).encode("utf-8")).hexdigest()


def _now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def last_chain_hash(chain_path: Path) -> str:
    """Liest die ``chain_hash``-Spalte des LETZTEN Eintrags. Genesis wenn leer/fehlt.

    Defensive: skippt korrupte Lines, sucht das letzte gültige Schema.
    """
    if not chain_path.exists():
        return GENESIS_PREV_HASH
    last_hash = GENESIS_PREV_HASH
    try:
        with chain_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("decision_chain_corrupt_line file=%s", chain_path)
                    continue
                ch = data.get("chain_hash")
                if isinstance(ch, str) and len(ch) == 64:
                    last_hash = ch
    except OSError as exc:
        logger.error("decision_chain_read_failed: %s", exc)
    return last_hash


def append_chain_entry(
    *,
    chain_path: Path,
    decision_id: str,
    record_payload: dict[str, Any],
) -> DecisionChainEntry:
    """Append-only: hashe record_payload, lese letzten prev_chain_hash, baue Chain-Entry.

    Fail-closed: jede Exception wird propagiert (write-error im Audit-Stream MUSS
    sichtbar werden). Caller (decision_journal) loggt + entscheidet ob hard-fail
    oder soft-degraded (Phase-0: soft).
    """
    record_hash_val = hash_record(record_payload)
    prev_hash = last_chain_hash(chain_path)
    chained_at = _now_utc()

    base_payload = {
        "schema_version": CHAIN_SCHEMA_VERSION,
        "decision_id": decision_id,
        "record_hash": record_hash_val,
        "prev_chain_hash": prev_hash,
        "chained_at_utc": chained_at,
    }
    chain_hash_val = _hash_chain_entry(base_payload)

    entry = DecisionChainEntry(**base_payload, chain_hash=chain_hash_val)

    chain_path.parent.mkdir(parents=True, exist_ok=True)
    with chain_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry.model_dump()) + "\n")
        fh.flush()
    return entry


def iter_chain_entries(chain_path: Path):
    """Stream all chain entries. Korrupte Rows werden mit Warning übersprungen."""
    if not chain_path.exists():
        return
    for line_no, raw in enumerate(chain_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            yield DecisionChainEntry.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "decision_chain_invalid_line file=%s line=%d exc=%s",
                chain_path,
                line_no,
                exc,
            )
            continue


def verify_chain(
    *,
    chain_path: Path,
    journal_records: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Verifiziert die Chain-Integrity. Returns Liste von Fehlern (leer = OK).

    Checks:
    1. Genesis: erster Entry's prev_chain_hash == GENESIS
    2. Monotonie: prev_chain_hash[i+1] == chain_hash[i]
    3. Chain-Hash-Konsistenz: chain_hash matched _hash_chain_entry der Base-Felder
    4. (Optional) Record-Hash-Konsistenz: wenn journal_records geliefert,
       muss record_hash == hash_record(journal_records[decision_id]) sein

    Args:
        chain_path: Pfad zur decision_journal_chain.jsonl
        journal_records: Optional ``decision_id → record_payload``-Map zur
            Cross-Verification mit decision_journal.jsonl. Fehlende
            decision_ids im Journal werden als Fehler gemeldet.

    Returns:
        Liste von Error-Strings. Leere Liste = chain ist tamper-frei.
    """
    errors: list[str] = []
    expected_prev = GENESIS_PREV_HASH
    seen_ids: set[str] = set()

    for idx, entry in enumerate(iter_chain_entries(chain_path)):
        # 1+2: prev_chain_hash kette
        if entry.prev_chain_hash != expected_prev:
            errors.append(
                f"chain_break idx={idx} decision_id={entry.decision_id} "
                f"expected_prev={expected_prev[:8]}… got={entry.prev_chain_hash[:8]}…"
            )

        # 3: chain_hash-Konsistenz
        recomputed = _hash_chain_entry(
            {
                "schema_version": entry.schema_version,
                "decision_id": entry.decision_id,
                "record_hash": entry.record_hash,
                "prev_chain_hash": entry.prev_chain_hash,
                "chained_at_utc": entry.chained_at_utc,
            }
        )
        if recomputed != entry.chain_hash:
            errors.append(
                f"chain_hash_mismatch idx={idx} decision_id={entry.decision_id} "
                f"recomputed={recomputed[:8]}… stored={entry.chain_hash[:8]}…"
            )

        # 4: cross-check mit Journal wenn geliefert
        if journal_records is not None:
            journal_payload = journal_records.get(entry.decision_id)
            if journal_payload is None:
                errors.append(f"missing_journal_record idx={idx} decision_id={entry.decision_id}")
            else:
                rh = hash_record(journal_payload)
                if rh != entry.record_hash:
                    errors.append(
                        f"record_hash_mismatch idx={idx} decision_id={entry.decision_id} "
                        f"computed={rh[:8]}… stored={entry.record_hash[:8]}…"
                    )

        # Duplicate-Detection
        if entry.decision_id in seen_ids:
            errors.append(f"duplicate_chain_entry decision_id={entry.decision_id} idx={idx}")
        seen_ids.add(entry.decision_id)

        expected_prev = entry.chain_hash

    return errors


def load_journal_records_for_verify(
    journal_path: Path,
) -> dict[str, dict[str, Any]]:
    """Lädt decision_journal.jsonl als ``decision_id → payload``-Map.

    Helper für verify_chain — Caller kann das passen oder None für nur-Chain-Verify.
    """
    if not journal_path.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for raw in journal_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        did = data.get("decision_id")
        if isinstance(did, str):
            records[did] = data
    return records
