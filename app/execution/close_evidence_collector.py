"""Sammelt Marktevidenz zu einem Close — und fällt dabei kein Urteil.

**Was dieses Modul NICHT tut.** Es entscheidet nichts. Kein ``verified``, kein
``plausibel``, keine Schwelle. Es holt Tatsachen, bindet sie an die Identität des
Closes und veröffentlicht sie als unveränderliches Artefakt. Ob die Evidenz
trägt, entscheidet ausschließlich der Offline-Verifier — der dafür kein Netz
braucht und deshalb bei gleicher Evidenz immer gleich urteilt.

**Venue ist explizit, nie abgeleitet.** Der Aufrufer sagt, von welcher Venue die
Kerzen kommen. Nachträglich aus einem ``price_source``-String zu raten, welcher
Anbieter gemeint war, würde genau die Provenienz erfinden, die zu beweisen ist.

**Das Primärfenster ist fest verdrahtet.** Kein ``interval``- und kein
``window_minutes``-Parameter: ein Aufrufer, der ``1h`` und 120 Minuten setzen
kann, hebelt genau das Argument aus, dass ein weites Fenster jeden Preis
plausibel macht. Eine 5m-Bestätigung gehört später in einen eigenen,
nicht-gatenden Pfad — nicht in dieselbe Funktion als Konfiguration.

**Retry-Idempotenz kommt vor dem Netzabruf, nicht danach.** ``collected_at_utc``
ist Teil des gehashten Artefakts, also erzeugt ein zweiter Sammellauf
zwangsläufig einen anderen Hash — selbst bei identischen Kerzen. Wer erst sammelt
und dann hofft, dass der Hash gleich bleibt, produziert einen
``EVIDENCE_CONFLICT`` aus dem Nichts. Deshalb prüft :func:`collect_and_publish`
**zuerst** das verankerte Manifest und gibt bei passender Identität das
bestehende Artefakt zurück.

**Das Manifest ist der Commit-Marker.** Ein Artefakt ohne Manifest gilt als
*nicht veröffentlicht*: die beiden Dateien sind je für sich atomar, zusammen aber
keine Transaktion. Stürzt der Prozess dazwischen ab, bleibt ein
``UNANCHORED_ARTIFACT_PRESENT`` zurück — ein Zustand, der gesehen werden muss,
statt still überschrieben zu werden.

**Idempotenz statt „neueste Datei gewinnt".** Für dieselbe Close-Identität gilt:

    gleicher payload_sha256   → IDEMPOTENT_NOOP
    anderer payload_sha256    → EVIDENCE_CONFLICT (fail closed, nichts wird überschrieben)

Das ist der Fall, der eintritt, wenn ein Provider dieselben historischen Kerzen
später geringfügig anders zurückliefert. Stillschweigend zu überschreiben würde
eine bereits verankerte Evidenz nachträglich verändern.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.execution.close_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    CloseEvidence,
    VenueCandle,
    canonical_bytes,
)

__all__ = [
    "PRIMARY_INTERVAL",
    "PRIMARY_WINDOW_RADIUS_MINUTES",
    "CandleFetcher",
    "CollectionResult",
    "CollectionStatus",
    "build_close_evidence",
    "collect_and_publish",
    "collector_code_sha",
    "publish_evidence",
]

PRIMARY_INTERVAL = "1m"
"""Minutengenau und NICHT konfigurierbar."""

PRIMARY_WINDOW_RADIUS_MINUTES = 1
"""Radius um den Close: eine Kerze davor, eine danach — Spannweite 2 Minuten.

Frueher hiess das ``PRIMARY_WINDOW_MINUTES = 3`` und wurde intern halbiert; der
Name versprach drei Minuten und lieferte zwei. Der Radius sagt, was passiert.
"""

CORROBORATING_INTERVAL = "5m"
"""Reserviert fuer einen spaeteren, nicht-gatenden Bestaetigungspfad."""

_INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}
_MANIFEST_NAME = "manifest.json"
_LOCK_NAME = ".publish.lock"


class CollectionStatus(StrEnum):
    """Wie die SAMMLUNG ausging — kein Urteil ueber den Close."""

    COLLECTED = "collected"
    IDEMPOTENT_NOOP = "idempotent_noop"
    EVIDENCE_CONFLICT = "evidence_conflict"
    UNANCHORED_ARTIFACT_PRESENT = "unanchored_artifact_present"
    CONCURRENT_WRITER = "concurrent_writer"
    WINDOW_UNAVAILABLE = "window_unavailable"
    FETCH_FAILED = "fetch_failed"
    CLOSE_IDENTITY_INCOMPLETE = "close_identity_incomplete"
    INVALID_CLOSE_TIMESTAMP = "invalid_close_timestamp"
    INVALID_COLLECTION_TIME = "invalid_collection_time"
    CANDLES_IN_FUTURE = "candles_in_future"
    UNSETTLED_CANDLE = "unsettled_candle"
    INVALID_CANDLE_DATA = "invalid_candle_data"


@dataclass(frozen=True)
class CollectionResult:
    status: CollectionStatus
    evidence: CloseEvidence | None = None
    payload_sha256: str = ""
    path: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (CollectionStatus.COLLECTED, CollectionStatus.IDEMPOTENT_NOOP)


class CandleFetcher(Protocol):
    """Holt Kerzen. Injiziert, damit der Bau selbst testbar bleibt."""

    def __call__(
        self, *, symbol: str, venue: str, interval: str, start_ms: int, end_ms: int
    ) -> list[VenueCandle]: ...


@lru_cache(maxsize=1)
def collector_code_sha() -> str:
    """SHA-256 ueber den Quelltext dieses Moduls — welche Sammlerversion sammelte."""
    return hashlib.sha256(inspect.getsource(sys.modules[__name__]).encode("utf-8")).hexdigest()


def _parse_utc(value: object) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    # Naive Zeitstempel werden NICHT als UTC angenommen — raten waere hier das
    # Gegenteil von Evidenz.
    return None if stamp.tzinfo is None else stamp


def _folder_key(fill_id: str) -> str:
    """Verzeichnisname aus dem Hash der fill_id.

    Die fill_id landet NIE als Pfadsegment: ein unerwartetes ``../../foo`` waere
    sonst ein Schreibzugriff ausserhalb des Evidenz-Baums. Statt zu erraten,
    welche Zeichen erlaubt sind, wird deterministisch gehasht; die echte fill_id
    steht im Manifest.
    """
    return hashlib.sha256(fill_id.encode("utf-8")).hexdigest()


def _validate_candles(
    candles: list[VenueCandle], *, collected_at_ms: int
) -> tuple[CollectionStatus, str] | None:
    """Rohdaten-Pruefung. None, wenn alles sauber ist."""
    seen: set[int] = set()
    interval_ms = _INTERVAL_MS[PRIMARY_INTERVAL]
    for c in candles:
        values = (c.open, c.high, c.low, c.close)
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in values):
            return (CollectionStatus.INVALID_CANDLE_DATA, f"unbrauchbare OHLC-Werte: {values}")
        if not (c.low <= c.open <= c.high and c.low <= c.close <= c.high):
            return (CollectionStatus.INVALID_CANDLE_DATA, f"OHLC nicht konsistent: {values}")
        if c.open_time_ms in seen:
            return (CollectionStatus.INVALID_CANDLE_DATA, f"doppelte Kerzenzeit {c.open_time_ms}")
        seen.add(c.open_time_ms)
        if c.open_time_ms > collected_at_ms:
            return (
                CollectionStatus.CANDLES_IN_FUTURE,
                f"Kerze {c.open_time_ms} liegt hinter dem Sammelzeitpunkt",
            )
        # Eine Kerze, deren Intervall noch laeuft, hat ihre endgueltigen
        # High/Low/Close-Werte noch nicht. Als historische Evidenz taugt sie nicht.
        if c.open_time_ms + interval_ms > collected_at_ms:
            return (CollectionStatus.UNSETTLED_CANDLE, f"Kerze {c.open_time_ms} ist noch offen")
    return None


def build_close_evidence(
    close_row: dict[str, object],
    *,
    venue: str,
    fetch: CandleFetcher,
    now_utc: datetime,
) -> CollectionResult:
    """Baut das Artefakt. Holt Kerzen, urteilt nicht.

    ``now_utc`` wird hereingereicht statt gelesen: der Bau bleibt damit
    reproduzierbar und die Zukunftspruefung testbar. Fenster und Intervall sind
    bewusst KEINE Parameter.
    """
    fill_id = str(close_row.get("fill_id", "") or "").strip()
    order_id = str(close_row.get("order_id", "") or "").strip()
    symbol = str(close_row.get("symbol", "") or "").strip()
    close_ts_raw = str(close_row.get("timestamp_utc", "") or "").strip()
    venue = str(venue or "").strip()

    missing = [
        name
        for name, value in (
            ("fill_id", fill_id),
            ("order_id", order_id),
            ("symbol", symbol),
            ("venue", venue),
        )
        if not value
    ]
    if missing:
        return CollectionResult(
            status=CollectionStatus.CLOSE_IDENTITY_INCOMPLETE,
            detail=f"fehlend: {', '.join(missing)}",
        )

    if now_utc.tzinfo is None:
        # Sonst haengt `.timestamp()` still an der Maschinen-Zeitzone.
        return CollectionResult(
            status=CollectionStatus.INVALID_COLLECTION_TIME,
            detail="now_utc ohne Zeitzone",
        )

    close_at = _parse_utc(close_ts_raw)
    if close_at is None:
        return CollectionResult(
            status=CollectionStatus.INVALID_CLOSE_TIMESTAMP,
            detail=f"unlesbar oder ohne Zeitzone: {close_ts_raw!r}",
        )

    close_ms = int(close_at.timestamp() * 1000)
    radius_ms = PRIMARY_WINDOW_RADIUS_MINUTES * 60_000
    start_ms = close_ms - radius_ms
    end_ms = close_ms + radius_ms

    try:
        candles = list(
            fetch(
                symbol=symbol,
                venue=venue,
                interval=PRIMARY_INTERVAL,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )
    except Exception as exc:  # noqa: BLE001 - jeder Anbieterfehler ist ein Sammelausfall
        # Ein Netzfehler ist weder ein leeres Fenster noch eine Exception, die der
        # naechste Layer interpretieren muesste.
        return CollectionResult(
            status=CollectionStatus.FETCH_FAILED,
            detail=f"{type(exc).__name__}: {exc}",
        )

    if not candles:
        return CollectionResult(
            status=CollectionStatus.WINDOW_UNAVAILABLE,
            detail=f"{venue} lieferte keine {PRIMARY_INTERVAL}-Kerzen fuer {symbol}",
        )

    collected_at = now_utc.astimezone(UTC)
    problem = _validate_candles(candles, collected_at_ms=int(collected_at.timestamp() * 1000))
    if problem is not None:
        status, detail = problem
        return CollectionResult(status=status, detail=detail)

    evidence = CloseEvidence(
        close_fill_id=fill_id,
        close_order_id=order_id,
        symbol=symbol,
        close_timestamp_utc=close_ts_raw,
        venue=venue,
        interval=PRIMARY_INTERVAL,
        window_start_ms=start_ms,
        window_end_ms=end_ms,
        candles=tuple(sorted(candles, key=lambda c: c.open_time_ms)),
        collected_at_utc=collected_at.isoformat(),
        collector_code_sha=collector_code_sha(),
        schema_version=EVIDENCE_SCHEMA_VERSION,
    )
    return CollectionResult(
        status=CollectionStatus.COLLECTED,
        evidence=evidence,
        payload_sha256=evidence.payload_sha256(),
    )


def _fsync_dir(folder: Path) -> None:
    try:
        fd = os.open(str(folder), os.O_RDONLY)
    except OSError:  # pragma: no cover - Plattformen ohne Verzeichnis-Handles
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - z.B. Windows
        pass
    finally:
        os.close(fd)


def _atomic_write(path: Path, payload: bytes) -> None:
    """Ganz oder gar nicht: tempfile im Zielverzeichnis, fsync, rename, dir-fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        # Ohne Verzeichnis-fsync ueberlebt der Rename einen Stromausfall nicht,
        # obwohl die Datei selbst schon durchgeschrieben war.
        _fsync_dir(path.parent)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_manifest(folder: Path) -> dict[str, object] | None:
    path = folder / _MANIFEST_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"__unreadable__": True}
    return data if isinstance(data, dict) else {"__unreadable__": True}


def _artifact_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return [p for p in folder.glob("*.json") if p.name != _MANIFEST_NAME]


def _manifest_payload(evidence: CloseEvidence, sha: str) -> dict[str, object]:
    return {
        "fill_id": evidence.close_fill_id,
        "order_id": evidence.close_order_id,
        "symbol": evidence.symbol,
        "venue": evidence.venue,
        "close_timestamp_utc": evidence.close_timestamp_utc,
        "payload_sha256": sha,
        "schema_version": evidence.schema_version,
        "collector_code_sha": evidence.collector_code_sha,
        "collected_at_utc": evidence.collected_at_utc,
    }


def publish_evidence(evidence: CloseEvidence, base_dir: str | Path) -> CollectionResult:
    """Veroeffentlicht das Artefakt unter seiner Identitaet — atomar und idempotent.

    Layout::

        <base>/<sha256(fill_id)>/<payload_sha256>.json
        <base>/<sha256(fill_id)>/manifest.json      <- COMMIT MARKER

    Das Manifest ist der Commit-Marker: ein Artefakt ohne Manifest gilt als NICHT
    veroeffentlicht. Fuer dieselbe Close-Identitaet gilt fail-closed — ein
    abweichender Hash ueberschreibt nichts.
    """
    sha = evidence.payload_sha256()
    folder = Path(base_dir) / _folder_key(evidence.close_fill_id)
    artifact = folder / f"{sha}.json"

    manifest = _read_manifest(folder)
    if manifest is not None:
        if manifest.get("__unreadable__"):
            return CollectionResult(
                status=CollectionStatus.EVIDENCE_CONFLICT,
                payload_sha256=sha,
                detail="Manifest unlesbar",
            )
        previous = str(manifest.get("payload_sha256", ""))
        if previous and previous != sha:
            return CollectionResult(
                status=CollectionStatus.EVIDENCE_CONFLICT,
                evidence=evidence,
                payload_sha256=sha,
                path=str(artifact),
                detail=f"bereits verankert mit {previous}",
            )
        if previous == sha and artifact.exists():
            return CollectionResult(
                status=CollectionStatus.IDEMPOTENT_NOOP,
                evidence=evidence,
                payload_sha256=sha,
                path=str(artifact),
            )
    elif _artifact_files(folder):
        # Artefakt ohne Manifest: der Prozess ist zwischen den beiden Schreib-
        # vorgaengen gestorben. Das ist ein Befund, keine Einladung zum stillen
        # Neuanlegen.
        return CollectionResult(
            status=CollectionStatus.UNANCHORED_ARTIFACT_PRESENT,
            evidence=evidence,
            payload_sha256=sha,
            path=str(folder),
            detail="Artefakt ohne Commit-Marker gefunden",
        )

    # Ein Schreiber je Close. Ohne das koennten zwei Sammler beide "kein Manifest"
    # sehen und anschliessend unterschiedliche Artefakte plus zuletzt-schreibendes
    # Manifest erzeugen — dann gilt "abweichende Evidenz ueberschreibt nie" unter
    # Parallelitaet nicht mehr.
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / _LOCK_NAME
    try:
        lock_fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return CollectionResult(
            status=CollectionStatus.CONCURRENT_WRITER,
            evidence=evidence,
            payload_sha256=sha,
            detail="ein anderer Sammler veroeffentlicht gerade diesen Close",
        )
    try:
        os.close(lock_fd)
        _atomic_write(artifact, canonical_bytes(evidence.as_payload()))
        _atomic_write(folder / _MANIFEST_NAME, canonical_bytes(_manifest_payload(evidence, sha)))
    finally:
        lock.unlink(missing_ok=True)

    return CollectionResult(
        status=CollectionStatus.COLLECTED,
        evidence=evidence,
        payload_sha256=sha,
        path=str(artifact),
    )


def collect_and_publish(
    close_row: dict[str, object],
    *,
    venue: str,
    fetch: CandleFetcher,
    now_utc: datetime,
    base_dir: str | Path,
) -> CollectionResult:
    """Der orchestrierende Pfad — und der einzige mit echter Retry-Idempotenz.

    Zuerst wird das verankerte Manifest gelesen. Passt die Close-Identitaet und
    liegt das Artefakt vollstaendig vor, wird GENAU DIESES zurueckgegeben, ohne
    erneuten Netzabruf. Andernfalls entstuende bei jedem Retry ein neues
    ``collected_at_utc`` — und damit ein neuer Hash, obwohl sich an den Kerzen
    nichts geaendert hat.
    """
    fill_id = str(close_row.get("fill_id", "") or "").strip()
    if not fill_id:
        return CollectionResult(
            status=CollectionStatus.CLOSE_IDENTITY_INCOMPLETE, detail="fehlend: fill_id"
        )

    folder = Path(base_dir) / _folder_key(fill_id)
    manifest = _read_manifest(folder)
    if manifest is not None and not manifest.get("__unreadable__"):
        previous = str(manifest.get("payload_sha256", ""))
        artifact = folder / f"{previous}.json"
        identity_ok = (
            str(manifest.get("fill_id", "")) == fill_id
            and str(manifest.get("order_id", ""))
            == str(close_row.get("order_id", "") or "").strip()
            and str(manifest.get("symbol", "")) == str(close_row.get("symbol", "") or "").strip()
            and str(manifest.get("close_timestamp_utc", ""))
            == str(close_row.get("timestamp_utc", "") or "").strip()
        )
        if not identity_ok:
            return CollectionResult(
                status=CollectionStatus.EVIDENCE_CONFLICT,
                payload_sha256=previous,
                path=str(folder),
                detail="verankertes Manifest gehoert zu einer anderen Close-Identitaet",
            )
        if previous and artifact.exists():
            return CollectionResult(
                status=CollectionStatus.IDEMPOTENT_NOOP,
                payload_sha256=previous,
                path=str(artifact),
                detail="bereits verankert — kein erneuter Abruf",
            )
        return CollectionResult(
            status=CollectionStatus.UNANCHORED_ARTIFACT_PRESENT,
            payload_sha256=previous,
            path=str(folder),
            detail="Manifest verweist auf ein fehlendes Artefakt",
        )

    built = build_close_evidence(close_row, venue=venue, fetch=fetch, now_utc=now_utc)
    if built.evidence is None:
        return built
    return publish_evidence(built.evidence, base_dir)
