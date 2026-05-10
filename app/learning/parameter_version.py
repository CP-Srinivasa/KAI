"""Hash-chained Parameter-Versionierung — auditierbares Lern-Journal.

Jeder Lern-Schritt (z. B. ein neuer Bayes-Calibrator) wird hier als
versionierter, manipulationssicher verkettetet Eintrag persistiert.  Drei
Garantien:

1. **Append-only** — bestehende Zeilen werden nie verändert.
2. **Hash-chained** — jede Zeile referenziert den sha256 der *gesamten*
   Vorgängerzeile.  Tampering an irgendeiner Zeile zerstört die Kette.
3. **Versioniert per Pfad** — pro `parameter_path` (z. B.
   ``bayes.calibrator.global``) gibt es eine geordnete History; eine Version
   wird durch ein eigenes ``version_activated``-Event scharfgeschaltet.

Das Modul *schreibt nur* in den Journal-Stream; es greift NIE direkt in
``app.core.settings`` oder andere laufende Konfiguration.  Approval +
Live-Aktivierung sind Schicht-5-Themen (Operator-CLI).

Vertrag
-------
- Pure Python + Pydantic (konsistent mit ``app/learning/calibration.py``).
- Schreibfehler werden geloggt; Verifikation gibt strukturierte Fehler.
- Keine Concurrent-Locking — Caller serialisiert Writes (Trading-Loop
  schreibt hier nicht; Learning-Run ist single-process).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

DEFAULT_PARAMETER_JOURNAL_PATH: Final[Path] = Path(
    "artifacts/learning/parameter_journal.jsonl"
)
SCHEMA_VERSION: Final[int] = 1
GENESIS_PREV_HASH: Final[str] = "0" * 64

RecordType = Literal[
    "version_proposed",
    "version_activated",
    "version_rolled_back",
    "version_rejected",
]


class ParameterChange(BaseModel):
    """Eine Zeile im Parameter-Journal.

    `prev_chain_hash` ist der sha256 der unmittelbar vorhergehenden Zeile
    (kanonisches JSON, sortierte Keys, UTF-8). Genesis-Zeile setzt
    GENESIS_PREV_HASH.

    `parameter_set` ist nur bei `record_type == "version_proposed"` befüllt;
    Aktivierungs-/Rollback-/Reject-Events tragen leere Sets und referenzieren
    die zugehörige Version per `version_id`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    timestamp_utc: str
    record_type: RecordType
    version_id: str = Field(min_length=8, max_length=64)
    parameter_path: str = Field(min_length=1)
    parameter_set: dict[str, object] = Field(default_factory=dict)
    parent_version_id: str | None = None
    evidence: dict[str, object] = Field(default_factory=dict)
    notes: str | None = None
    created_by: str = Field(default="auto", min_length=1)
    prev_chain_hash: str = Field(min_length=64, max_length=64)


# ─── Hash-Chain helpers ────────────────────────────────────────────────────


def _canonical_json(record: ParameterChange) -> str:
    """Stable byte representation: sort_keys, no ASCII-escapes, no spaces."""
    return json.dumps(
        record.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _hash_record(record: ParameterChange) -> str:
    return hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def _new_version_id() -> str:
    return f"pv_{uuid.uuid4().hex[:12]}"


# ─── Store ─────────────────────────────────────────────────────────────────


class ParameterVersionStore:
    """Append-only Reader/Writer um das Parameter-Journal."""

    def __init__(self, path: Path | str = DEFAULT_PARAMETER_JOURNAL_PATH) -> None:
        self._path = Path(path)
        # Cache: (file_size_at_cache_time, last_chain_hash). Invalidates
        # automatically when file_size changes (new write, truncation,
        # external write). Avoids re-reading the entire JSONL on every
        # append (Neo-F-001: O(n²) → O(1) amortised).
        self._cached_size: int = -1
        self._cached_last_hash: str = GENESIS_PREV_HASH

    @property
    def path(self) -> Path:
        return self._path

    # --- read --------------------------------------------------------------

    def iter_records(self) -> Iterator[ParameterChange]:
        """Stream alle Records in Schreibreihenfolge.

        Fehlerhafte Zeilen werden geloggt + übersprungen — verify_chain()
        meldet das danach als Strukturbruch.
        """
        if not self._path.exists():
            return
        for line_no, raw in enumerate(
            self._path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
                yield ParameterChange.model_validate(payload)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning(
                    "[parameter-journal] skipped malformed row %s:%d (%s)",
                    self._path,
                    line_no,
                    exc,
                )

    def latest_active(self, parameter_path: str) -> ParameterChange | None:
        """Die zuletzt aktivierte (und nicht zurückgerollte) Version eines Pfads.

        Scan vom Ende: die letzte ``version_activated``- oder
        ``version_rolled_back``-Zeile für ``parameter_path`` gewinnt; ihre
        ``version_id`` zeigt auf den vorgeschlagenen Eintrag, dessen
        ``parameter_set`` zurückgegeben wird.
        """
        records = list(self.iter_records())
        for rec in reversed(records):
            if rec.parameter_path != parameter_path:
                continue
            if rec.record_type in ("version_activated", "version_rolled_back"):
                target_id = rec.version_id
                # find the matching proposal
                for proposal in records:
                    if (
                        proposal.parameter_path == parameter_path
                        and proposal.record_type == "version_proposed"
                        and proposal.version_id == target_id
                    ):
                        return proposal
                return None
        return None

    def history(self, parameter_path: str) -> list[ParameterChange]:
        return [r for r in self.iter_records() if r.parameter_path == parameter_path]

    # --- verify ------------------------------------------------------------

    def verify_chain(self) -> tuple[bool, str | None]:
        """Walk the journal and recompute prev_chain_hash for every line.

        Returns (True, None) on intact chain, (False, error) otherwise.
        Errors are precise: line number, expected vs. actual hash.
        """
        expected_prev = GENESIS_PREV_HASH
        for line_no, rec in enumerate(self.iter_records(), start=1):
            if rec.prev_chain_hash != expected_prev:
                return (
                    False,
                    (
                        f"chain broken at record #{line_no} "
                        f"(version_id={rec.version_id}): "
                        f"expected prev={expected_prev[:12]}…, "
                        f"got prev={rec.prev_chain_hash[:12]}…"
                    ),
                )
            expected_prev = _hash_record(rec)
        return True, None

    # --- write -------------------------------------------------------------

    def _last_chain_hash(self) -> str:
        """Last record's hash, cached by file size (Neo-F-001 fix).

        Cache hit: O(1). Cache miss (file grew/shrunk externally, or first
        call): full re-walk via iter_records, then re-cache. This collapses
        the previous O(n²) append behaviour to O(1) amortised; the cache
        invalidates safely whenever the file changes from outside.
        """
        try:
            current_size = self._path.stat().st_size if self._path.exists() else 0
        except OSError:
            current_size = -1
        if current_size == self._cached_size:
            return self._cached_last_hash

        last: ParameterChange | None = None
        for rec in self.iter_records():
            last = rec
        new_hash = GENESIS_PREV_HASH if last is None else _hash_record(last)
        self._cached_last_hash = new_hash
        self._cached_size = current_size
        return new_hash

    def _append(self, record: ParameterChange) -> ParameterChange:
        """Append record with portalocker file lock (Neo-F-002 fix).

        Lock prevents half-written rows when the trading-loop and an
        operator-CLI write concurrently. We refresh the cache to the new
        record's hash so the next append is O(1).
        """
        import portalocker  # local import — only needed at write time

        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            record.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._path.open("a", encoding="utf-8") as fh:
            portalocker.lock(fh, portalocker.LOCK_EX)
            try:
                fh.write(line + "\n")
                fh.flush()
            finally:
                portalocker.unlock(fh)

        # Refresh cache: we just wrote `record`, so its hash is the new tail.
        self._cached_last_hash = _hash_record(record)
        try:
            self._cached_size = self._path.stat().st_size
        except OSError:
            self._cached_size = -1
        return record

    def propose_version(
        self,
        *,
        parameter_path: str,
        parameter_set: dict[str, object],
        evidence: dict[str, object] | None = None,
        parent_version_id: str | None = None,
        notes: str | None = None,
        created_by: str = "auto",
    ) -> ParameterChange:
        """Schreibe einen neuen Vorschlag (status = proposed).

        Auto-Detect des `parent_version_id`, falls nicht angegeben: latest
        active Version desselben Pfads.
        """
        if parent_version_id is None:
            current = self.latest_active(parameter_path)
            parent_version_id = current.version_id if current else None

        record = ParameterChange(
            timestamp_utc=_now_utc(),
            record_type="version_proposed",
            version_id=_new_version_id(),
            parameter_path=parameter_path,
            parameter_set=parameter_set,
            parent_version_id=parent_version_id,
            evidence=evidence or {},
            notes=notes,
            created_by=created_by,
            prev_chain_hash=self._last_chain_hash(),
        )
        return self._append(record)

    def activate_version(
        self,
        *,
        parameter_path: str,
        version_id: str,
        notes: str | None = None,
        created_by: str = "auto",
    ) -> ParameterChange:
        """Aktiviere einen vorhandenen `version_proposed`-Eintrag.

        Strenge Validierung: der referenzierte Vorschlag muss existieren und
        zu `parameter_path` gehören — sonst ValueError (NIE silent corruption).
        """
        proposal = self._find_proposal(parameter_path, version_id)
        if proposal is None:
            raise ValueError(
                f"unknown_version:{version_id} for path={parameter_path}"
            )

        record = ParameterChange(
            timestamp_utc=_now_utc(),
            record_type="version_activated",
            version_id=version_id,
            parameter_path=parameter_path,
            parameter_set={},
            parent_version_id=proposal.parent_version_id,
            evidence={},
            notes=notes,
            created_by=created_by,
            prev_chain_hash=self._last_chain_hash(),
        )
        return self._append(record)

    def rollback_to(
        self,
        *,
        parameter_path: str,
        version_id: str,
        notes: str | None = None,
        created_by: str = "auto",
    ) -> ParameterChange:
        """Setze einen früheren Vorschlag wieder als aktiv.

        Identisch zu `activate_version`, aber mit anderem `record_type` —
        macht das Audit-Diff klar: hier wurde bewusst zurückgerollt, nicht
        eine neue Version live geschaltet.
        """
        proposal = self._find_proposal(parameter_path, version_id)
        if proposal is None:
            raise ValueError(
                f"unknown_version:{version_id} for path={parameter_path}"
            )

        record = ParameterChange(
            timestamp_utc=_now_utc(),
            record_type="version_rolled_back",
            version_id=version_id,
            parameter_path=parameter_path,
            parameter_set={},
            parent_version_id=proposal.parent_version_id,
            evidence={},
            notes=notes,
            created_by=created_by,
            prev_chain_hash=self._last_chain_hash(),
        )
        return self._append(record)

    def reject_version(
        self,
        *,
        parameter_path: str,
        version_id: str,
        reason: str,
        created_by: str = "auto",
    ) -> ParameterChange:
        """Markiere einen Vorschlag explizit als abgelehnt.

        Kein No-Op: das Reject-Event ist Teil des Audit-Trails ("wir haben
        diese Calibration gesehen und bewusst verworfen — Grund: …").
        """
        proposal = self._find_proposal(parameter_path, version_id)
        if proposal is None:
            raise ValueError(
                f"unknown_version:{version_id} for path={parameter_path}"
            )

        record = ParameterChange(
            timestamp_utc=_now_utc(),
            record_type="version_rejected",
            version_id=version_id,
            parameter_path=parameter_path,
            parameter_set={},
            parent_version_id=proposal.parent_version_id,
            evidence={},
            notes=reason,
            created_by=created_by,
            prev_chain_hash=self._last_chain_hash(),
        )
        return self._append(record)

    # --- internals ---------------------------------------------------------

    def _find_proposal(
        self, parameter_path: str, version_id: str
    ) -> ParameterChange | None:
        for rec in self.iter_records():
            if (
                rec.record_type == "version_proposed"
                and rec.parameter_path == parameter_path
                and rec.version_id == version_id
            ):
                return rec
        return None


__all__ = [
    "DEFAULT_PARAMETER_JOURNAL_PATH",
    "GENESIS_PREV_HASH",
    "SCHEMA_VERSION",
    "ParameterChange",
    "ParameterVersionStore",
    "RecordType",
]
