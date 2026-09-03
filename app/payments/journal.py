"""Das eine Geld-Journal: append-only, hash-verkettet, ein Lock (ADR 0017 §5).

``artifacts/payments/payment_journal.jsonl``. Ein Artefakt, ein Format, ein
Lock — und nie rotiert, weil es die Wahrheit ueber jede Wertbewegung traegt.

**Was diese Kette leistet und was nicht.** Sie macht eine nachtraegliche
Aenderung *erkennbar*, nicht *unmoeglich*. Der Append verlaesst sich auf den
Tip, den er selbst inkrementell mitgelesen hat; er verifiziert nicht bei jedem
Schreibvorgang die ganze Datei. Die volle Pruefung passiert beim Start
(:meth:`PaymentJournal.open`) und in :meth:`PaymentJournal.verify_chain`.

**Warum inkrementell.** ``ops_ledger._append_chained_record`` parst unter dem
Exclusive-Lock jedes Mal ALLE Records; ``receive_ledger.py:11-17`` beziffert
die Folge mit *"2000 mints ≈ 95 s cumulative, growing O(n²)"*. Hier liest ein
Append nur, was seit dem letzten Mal dazukam — der Reconcile-Timer schreibt in
dieselbe Datei, also muss der Server vor jedem Append nachlesen, aber eben nur
den Rest.

**Torn Tail = Deny.** Eine halb geschriebene letzte Zeile beendet das
Schreiben. Auf die letzte LESBARE Zeile aufzusetzen wuerde das Journal still
forken; ab da haetten zwei Ketten dieselbe Herkunft und verschiedene Wahrheit.

**Zwei Prozesse, ein Lock.** ``kai-server`` sendet, ``kai-ln-reconcile.timer``
haengt Outcomes an. Beide gehen durch ``portalocker`` (Interprozess), nicht
durch ein ``threading.Lock`` — genau der Unterschied, an dem
``idempotency_store`` Lost Updates produziert.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import portalocker

from app.core.payment_settings import get_payment_settings
from app.payments.journal_chain import (
    GENESIS_HASH,
    RUNBOOK,
    SCHEMA,
    ChainStatus,
    JournalIntegrityError,
    canonical_bytes,
    compute_record_hash,
    parse_record,
    verify_link,
)
from app.payments.journal_index import JournalIndex
from app.payments.models import PaymentAuditEvent
from app.payments.redaction import redact_payload

#: Dateiname des Stroms — als Konstante, damit Leser sie importieren statt das
#: Literal zu wiederholen (Stream-Ratchet G4).
PAYMENT_JOURNAL_FILENAME = "payment_journal.jsonl"

_LOCK_TIMEOUT_SECONDS = 15.0


def _fsync_directory(directory: Path) -> None:
    """fsync des Verzeichniseintrags, damit eine NEUE Datei einen Stromausfall
    ueberlebt (Muster ``ops_ledger._fsync_directory``). POSIX only."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
    try:
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - best effort
        pass
    finally:
        os.close(fd)


class PaymentJournal:
    """Writer und Leser des einen Geld-Journals.

    Args:
        path: Ziel-Journal. ``None`` nimmt den Pfad aus
            :class:`~app.core.payment_settings.PaymentSettings`, absolut zur
            Repo-Wurzel aufgeloest. Ein Test biegt das Journal ueber DIESES
            Argument um — es gibt bewusst kein Env-Hintertuerchen, sonst
            koennte eine vergessene Variable den Produktivpfad umlenken.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = (
            Path(path) if path is not None else get_payment_settings().resolved_journal_path()
        )
        self._index = JournalIndex()
        self._tip_hash = GENESIS_HASH
        self._tip_seq = 0
        self._offset = 0
        self._rlock = threading.RLock()
        self._depth = 0
        self._handle: Any = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def index(self) -> JournalIndex:
        return self._index

    @property
    def tip_hash(self) -> str:
        return self._tip_hash

    # -- Start -------------------------------------------------------------- #

    def open(self) -> ChainStatus:
        """Verifiziere die Kette vollstaendig und baue den Index (ADR §5).

        Raises:
            JournalIntegrityError: die Kette ist gebrochen oder der Tail
                zerrissen. Ein Prozess, der darauf weiterschreibt, forkt das
                Journal — er startet lieber nicht.
        """
        with self._rlock:
            self._index = JournalIndex()
            self._tip_hash = GENESIS_HASH
            self._tip_seq = 0
            self._offset = 0
            with self._hold(create=False) as handle:
                data = self._read_from(0, handle=handle)
                self._consume(data, offset_base=0)
        return ChainStatus(ok=True, records=self._tip_seq, tip_hash=self._tip_hash)

    def refresh_tail(self) -> int:
        """Lies alles, was ein anderer Prozess seither angehaengt hat.

        Returns:
            Anzahl neu gelesener Records.
        """
        with self._rlock, self._hold(create=False) as handle:
            before = self._tip_seq
            data = self._read_from(self._offset, handle=handle)
            self._consume(data, offset_base=self._offset)
            return self._tip_seq - before

    # -- Schreiben ---------------------------------------------------------- #

    @contextmanager
    def _hold(self, *, create: bool) -> Iterator[Any]:
        """Halte den Interprozess-Lock — auch fuer LESENDE Zugriffe.

        Lesen ohne Lock waere auf POSIX unauffaellig (flock ist advisory) und
        auf Windows ein harter ``PermissionError``, sobald ein anderer Prozess
        gerade anhaengt. Wichtiger als die Plattformfrage ist aber, dass ein
        ungesperrter Leser einen halb geschriebenen Record sehen und ihn fuer
        einen zerrissenen Tail halten koennte.

        Reentrant: ein verschachtelter Aufruf im selben Prozess benutzt das
        bereits gehaltene Handle. ``portalocker`` ein zweites Mal auf dieselbe
        Datei zu setzen wuerde je nach Plattform blockieren oder fehlschlagen.
        """
        with self._rlock:
            if self._depth > 0:
                yield self._handle
                return
            if not create and not self._path.exists():
                yield None
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with portalocker.Lock(
                    self._path, mode="ab+", timeout=_LOCK_TIMEOUT_SECONDS
                ) as handle:
                    self._handle = handle
                    self._depth += 1
                    try:
                        yield handle
                    finally:
                        self._depth -= 1
                        self._handle = None
            except JournalIntegrityError:
                raise
            except OSError as exc:
                raise JournalIntegrityError(
                    f"payment journal unavailable: {type(exc).__name__}: {exc}"
                ) from exc

    @contextmanager
    def transaction(self) -> Iterator[JournalTransaction]:
        """Halte den Interprozess-Lock ueber mehrere Schritte.

        Das ist der Serialisierungspunkt aus ADR §5: Idempotenz-Konsum,
        Cap-Pruefung, Policy-Verdikt und Append gehoeren unter EIN Lock. Alles,
        was davor gelesen wurde, ist Vorschau.
        """
        existed = self._path.exists()
        with self._hold(create=True) as handle:
            data = self._read_from(self._offset, handle=handle)
            self._consume(data, offset_base=self._offset)
            yield JournalTransaction(self, handle=handle, created=not existed)

    def append(
        self,
        intent_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        ts: datetime | None = None,
    ) -> PaymentAuditEvent:
        """Haenge genau einen Record an — redigiert, verkettet, fsync'd."""
        with self.transaction() as tx:
            return tx.append(intent_id, event_type, payload, ts=ts)

    # -- Pruefen ------------------------------------------------------------ #

    def verify_chain(self) -> ChainStatus:
        """Pruefe jede Verkettung von vorn. Wirft nicht — berichtet.

        Nimmt einen eigenen Lock und darf deshalb NICHT innerhalb einer
        laufenden :meth:`transaction` gerufen werden.
        """
        probe = PaymentJournal(self._path)
        try:
            with probe._hold(create=False) as handle:
                data = probe._read_from(0, handle=handle)
                probe._consume(data, offset_base=0)
        except JournalIntegrityError as exc:
            return ChainStatus(
                ok=False,
                records=probe._tip_seq,
                tip_hash=probe._tip_hash,
                reason=str(exc),
                broken_at_seq=probe._tip_seq + 1,
            )
        return ChainStatus(ok=True, records=probe._tip_seq, tip_hash=probe._tip_hash)

    # -- Intern ------------------------------------------------------------- #

    def _read_from(self, offset: int, *, handle: Any = None) -> bytes:
        if handle is not None:
            handle.seek(offset)
            return bytes(handle.read())
        if not self._path.exists():
            return b""
        with self._path.open("rb") as fh:
            fh.seek(offset)
            return fh.read()

    def _consume(self, data: bytes, *, offset_base: int) -> None:
        """Verifiziere und indiziere die Records ab ``offset_base``."""
        if not data:
            return
        if not data.endswith(b"\n"):
            raise JournalIntegrityError(
                "payment journal has a torn tail (last line incomplete) — refusing to "
                f"extend a forked money journal; repair first: {RUNBOOK}"
            )
        for raw in data.split(b"\n"):
            if not raw.strip():
                continue
            record = parse_record(raw, after_seq=self._tip_seq)
            verify_link(record, tip_seq=self._tip_seq, tip_hash=self._tip_hash)
            self._index.ingest(record)
            self._tip_seq = int(record["seq"])
            self._tip_hash = str(record["record_hash"])
        self._offset = offset_base + len(data)


class JournalTransaction:
    """Ein offener Schreibvorgang unter gehaltenem Lock."""

    def __init__(
        self, journal: PaymentJournal, *, handle: Any = None, created: bool = False
    ) -> None:
        self._journal = journal
        self._handle = handle
        self._created = created

    @property
    def index(self) -> JournalIndex:
        return self._journal.index

    def append(
        self,
        intent_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        ts: datetime | None = None,
    ) -> PaymentAuditEvent:
        """Schreibe einen Record und aktualisiere Tip und Index."""
        handle = self._handle
        if handle is None:  # pragma: no cover - nur im verschachtelten Fall
            raise JournalIntegrityError("nested transaction cannot write without the lock owner")
        journal = self._journal
        moment = (ts or datetime.now(UTC)).astimezone(UTC)
        record: dict[str, Any] = {
            "schema": SCHEMA,
            "seq": journal._tip_seq + 1,
            "ts": moment.isoformat(),
            "intent_id": intent_id,
            "event_type": event_type,
            "payload": redact_payload(payload),
            "prev_hash": journal._tip_hash,
        }
        record["record_hash"] = compute_record_hash(record)
        # Form pruefen BEVOR geschrieben wird: ein unbekannter event_type oder
        # ein defekter Hash darf nicht erst beim Lesen auffallen.
        event = PaymentAuditEvent.model_validate(
            {key: value for key, value in record.items() if key != "schema"}
        )

        line = canonical_bytes(record) + b"\n"
        handle.seek(0, os.SEEK_END)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
        if self._created:
            _fsync_directory(journal.path.parent)
            self._created = False
            if os.name == "posix":
                try:
                    os.chmod(journal.path, 0o600)
                except OSError:  # pragma: no cover - best effort
                    pass

        journal._tip_seq = record["seq"]
        journal._tip_hash = record["record_hash"]
        journal._offset += len(line)
        journal.index.ingest(record)
        return event


__all__ = [
    "GENESIS_HASH",
    "PAYMENT_JOURNAL_FILENAME",
    "SCHEMA",
    "ChainStatus",
    "JournalIntegrityError",
    "JournalTransaction",
    "PaymentJournal",
    "compute_record_hash",
]
