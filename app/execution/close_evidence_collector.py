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
import re
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
"""Radius in Kerzen um den Close-Bucket: eine davor, eine danach.

Der angefragte Bereich umfasst damit DREI 1m-Buckets und drei Minuten. Fuer einen
Close um 09:00:30 ist das ``[08:59:00, 09:02:00)`` — 08:59, der Close-Bucket
09:00 und 09:01. Der Vorgaengername ``PRIMARY_WINDOW_MINUTES = 3`` wurde intern
halbiert und versprach etwas anderes, als er tat; der Radius benennt die Einheit,
in der tatsaechlich gerechnet wird.
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
    PUBLISH_LOCK_PRESENT = "publish_lock_present"
    """Ein Lock liegt. Das kann ein lebender Schreiber ODER ein verwaistes Lock
    nach Strom-/Prozessausfall sein — beweisbar ist nur, DASS es liegt. Fail
    closed; bewusst KEINE "stale lock nach X Minuten loeschen"-Heuristik."""
    WINDOW_UNAVAILABLE = "window_unavailable"
    """Der Anbieter lieferte ueberhaupt keine Kerzen."""

    CLOSE_BUCKET_MISSING = "close_bucket_missing"
    """Kerzen kamen, aber keine deckt den Close-Zeitpunkt ab.

    Ein anderer Zustand als WINDOW_UNAVAILABLE: der Anbieter hat geantwortet,
    seine Antwort traegt die entscheidende Minute nur nicht. Das ist bereits
    Sammel-Unvollstaendigkeit und gehoert hier fail-closed abgewiesen — nicht
    erst spaeter im Verifier als VENUE_WINDOW_DOES_NOT_COVER_CLOSE.
    """

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
    """Nur echte UTC-Zeitstempel. None bei allem anderen.

    Naive Zeiten werden nicht als UTC angenommen — raten waere hier das Gegenteil
    von Evidenz. Und ein Feld namens ``*_utc`` darf auch keinen Offset ``+02:00``
    tragen: der Verifier vergleicht den Evidence-Timestamp mit dem Close-Timestamp
    als exakten String, deshalb wird hier abgelehnt statt still umgerechnet.
    """
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    offset = stamp.utcoffset()
    return stamp if offset is not None and offset.total_seconds() == 0 else None


def _identity_problem(identity: dict[str, str]) -> tuple[CollectionStatus, str] | None:
    """Die eine Identitaets-Regel — fuer Bau UND Orchestrator dieselbe.

    Ohne sie endete ein Retry mit fehlender ``order_id`` als EVIDENCE_CONFLICT,
    obwohl die Wahrheit CLOSE_IDENTITY_INCOMPLETE lautet.
    """
    missing = [k for k in ("fill_id", "order_id", "symbol", "venue") if not identity.get(k)]
    if missing:
        return (CollectionStatus.CLOSE_IDENTITY_INCOMPLETE, f"fehlend: {', '.join(missing)}")
    raw = identity.get("close_timestamp_utc", "")
    if not raw:
        return (CollectionStatus.CLOSE_IDENTITY_INCOMPLETE, "fehlend: timestamp_utc")
    if _parse_utc(raw) is None:
        return (
            CollectionStatus.INVALID_CLOSE_TIMESTAMP,
            f"unlesbar, ohne Zeitzone oder nicht UTC: {raw!r}",
        )
    return None


def _canonical_venue(raw: object) -> str:
    """Die eine Schreibweise einer Venue."""
    return str(raw or "").strip().lower()


def _folder_key(fill_id: str) -> str:
    """Verzeichnisname aus dem Hash der fill_id.

    Die fill_id landet NIE als Pfadsegment: ein unerwartetes ``../../foo`` waere
    sonst ein Schreibzugriff ausserhalb des Evidenz-Baums. Statt zu erraten,
    welche Zeichen erlaubt sind, wird deterministisch gehasht; die echte fill_id
    steht im Manifest.
    """
    return hashlib.sha256(fill_id.encode("utf-8")).hexdigest()


def _is_positive_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_candles(
    candles: list[VenueCandle],
    *,
    collected_at_ms: int,
    start_ms: int,
    end_ms: int,
    close_ms: int,
) -> tuple[CollectionStatus, str] | None:
    """Rohdaten-Pruefung. None, wenn alles sauber ist.

    Das ANGEFRAGTE Fenster war exakt — die ZURUECKGELIEFERTEN Kerzen muessen es
    auch sein. Sonst behauptet ``window_start_ms``/``window_end_ms`` im Artefakt
    etwas anderes als sein Inhalt, und ein fehlerhafter Adapter koennte eine
    08:40-Kerze oder eine Kerze mit Sekunden-Offset einschleusen.
    """
    seen: set[int] = set()
    interval_ms = _INTERVAL_MS[PRIMARY_INTERVAL]
    for c in candles:
        values = (c.open, c.high, c.low, c.close)
        # bool ist eine int-Unterklasse: ohne diesen Ausschluss waere True ein
        # gueltiger OHLC-Wert (dieselbe Falle wie im Verifier und im Detektor).
        if not all(_is_positive_number(v) for v in values):
            return (CollectionStatus.INVALID_CANDLE_DATA, f"unbrauchbare OHLC-Werte: {values!r}")
        if not (c.low <= c.open <= c.high and c.low <= c.close <= c.high):
            return (CollectionStatus.INVALID_CANDLE_DATA, f"OHLC nicht konsistent: {values!r}")
        if not _is_nonnegative_int(c.open_time_ms):
            return (
                CollectionStatus.INVALID_CANDLE_DATA,
                f"unbrauchbare Kerzenzeit: {c.open_time_ms!r}",
            )
        if c.open_time_ms % interval_ms != 0:
            return (
                CollectionStatus.INVALID_CANDLE_DATA,
                f"Kerze {c.open_time_ms} liegt nicht auf einer {PRIMARY_INTERVAL}-Grenze",
            )
        if not (start_ms <= c.open_time_ms < end_ms):
            return (
                CollectionStatus.INVALID_CANDLE_DATA,
                f"Kerze {c.open_time_ms} liegt ausserhalb des angefragten Fensters "
                f"[{start_ms}, {end_ms})",
            )
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

    # Vollstaendigkeit: eine Antwort ohne die Minute des Closes ist keine
    # Close-Evidenz. Eine einzelne 08:59-Kerze kann formal tadellos sein — im
    # Fenster, ausgerichtet, settled, plausible OHLC — und deckt den Close um
    # 09:00:30 trotzdem nicht ab.
    if not any(c.open_time_ms <= close_ms < c.open_time_ms + interval_ms for c in candles):
        return (
            CollectionStatus.CLOSE_BUCKET_MISSING,
            f"keine {PRIMARY_INTERVAL}-Kerze deckt den Close-Zeitpunkt {close_ms} ab",
        )
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
    # Einmal kanonisieren und GENAU diese Form ueberall verwenden — Fetch,
    # Evidenz, Manifest und Retry-Abgleich muessen dieselbe Venue meinen.
    venue = _canonical_venue(venue)

    problem = _identity_problem(
        {
            "fill_id": fill_id,
            "order_id": order_id,
            "symbol": symbol,
            "venue": venue,
            "close_timestamp_utc": close_ts_raw,
        }
    )
    if problem is not None:
        status, detail = problem
        return CollectionResult(status=status, detail=detail)

    if now_utc.tzinfo is None:
        # Sonst haengt `.timestamp()` still an der Maschinen-Zeitzone.
        return CollectionResult(
            status=CollectionStatus.INVALID_COLLECTION_TIME,
            detail="now_utc ohne Zeitzone",
        )

    close_at = _parse_utc(close_ts_raw)
    assert close_at is not None  # von _identity_problem bereits geprueft

    # Bucket-genau statt um den Close herum: viele Kline-APIs filtern nach
    # Candle-OPEN-Zeit. Ein Fenster 08:59:30..09:01:30 laesst die 08:59-Kerze
    # deshalb je nach Anbieter heraus, obwohl "eine davor" behauptet wird.
    close_ms = int(close_at.timestamp() * 1000)
    bucket_ms = _INTERVAL_MS[PRIMARY_INTERVAL]
    bucket_open = (close_ms // bucket_ms) * bucket_ms
    radius = PRIMARY_WINDOW_RADIUS_MINUTES
    start_ms = bucket_open - radius * bucket_ms
    end_ms = bucket_open + (radius + 1) * bucket_ms

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
    problem = _validate_candles(
        candles,
        collected_at_ms=int(collected_at.timestamp() * 1000),
        start_ms=start_ms,
        end_ms=end_ms,
        close_ms=close_ms,
    )
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


_HEX64 = re.compile(r"^[0-9a-f]{64}$")

_MANIFEST_REQUIRED = (
    "fill_id",
    "order_id",
    "symbol",
    "venue",
    "close_timestamp_utc",
    "payload_sha256",
    "schema_version",
    "collector_code_sha",
    "collected_at_utc",
)


def _close_identity(close_row: dict[str, object], venue: str) -> dict[str, str]:
    return {
        "fill_id": str(close_row.get("fill_id", "") or "").strip(),
        "order_id": str(close_row.get("order_id", "") or "").strip(),
        "symbol": str(close_row.get("symbol", "") or "").strip(),
        "venue": _canonical_venue(venue),
        "close_timestamp_utc": str(close_row.get("timestamp_utc", "") or "").strip(),
    }


def _evidence_identity(evidence: CloseEvidence) -> dict[str, str]:
    return {
        "fill_id": evidence.close_fill_id,
        "order_id": evidence.close_order_id,
        "symbol": evidence.symbol,
        "venue": _canonical_venue(evidence.venue),
        "close_timestamp_utc": evidence.close_timestamp_utc,
    }


def _artifact_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return [p for p in folder.glob("*.json") if p.name != _MANIFEST_NAME]


@dataclass(frozen=True)
class _Anchor:
    """Was der verankerte Zustand eines Close-Ordners hergibt."""

    problem: CollectionStatus | None = None
    detail: str = ""
    sha: str = ""
    path: Path | None = None

    @property
    def committed(self) -> bool:
        return self.problem is None and bool(self.sha)


def _identity_of(payload: dict[str, object], *, artifact: bool) -> dict[str, str]:
    fill_key = "close_fill_id" if artifact else "fill_id"
    order_key = "close_order_id" if artifact else "order_id"
    return {
        "fill_id": str(payload.get(fill_key, "")),
        "order_id": str(payload.get(order_key, "")),
        "symbol": str(payload.get("symbol", "")),
        "venue": _canonical_venue(payload.get("venue")),
        "close_timestamp_utc": str(payload.get("close_timestamp_utc", "")),
    }


def _inspect_anchor(folder: Path, identity: dict[str, str]) -> _Anchor:
    """Streng pruefen, was lokal committet ist — nichts davon wird geglaubt.

    Das Manifest ist ein **lokaler Commit-Marker**, keine externe Verankerung: es
    sagt "dieser Sammellauf ist hier vollstaendig abgeschlossen", nicht "dieser
    Hash ist ausserhalb bezeugt". Der Verifier bleibt deshalb strenger und
    verlangt fuer ein VERIFIED-Urteil weiterhin einen von aussen uebergebenen
    ``expected_evidence_sha256``; das Manifest allein genuegt ihm nicht.

    Geprueft werden Schema, die FORM des Hashes, die Existenz, der Hash ueber die
    TATSAECHLICHEN Bytes, deren Kanonizitaet und die Identitaeten von Manifest,
    Artefakt und Anfrage.
    """
    manifest_path = folder / _MANIFEST_NAME
    if not manifest_path.exists():
        if _artifact_files(folder):
            # Der Prozess ist zwischen Artefakt und Commit-Marker gestorben.
            return _Anchor(
                problem=CollectionStatus.UNANCHORED_ARTIFACT_PRESENT,
                detail="Artefakt ohne Commit-Marker gefunden",
                path=folder,
            )
        return _Anchor()

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _Anchor(
            problem=CollectionStatus.EVIDENCE_CONFLICT, detail=f"Manifest unlesbar: {exc}"
        )
    if not isinstance(manifest, dict):
        return _Anchor(problem=CollectionStatus.EVIDENCE_CONFLICT, detail="Manifest kein Objekt")

    sha = str(manifest.get("payload_sha256", ""))
    # Sicherheitsrelevant: dieser Wert bildet gleich einen Dateinamen. Ein
    # manipuliertes Manifest darf ueber ihn keinen Pfad beeinflussen.
    if not _HEX64.match(sha):
        return _Anchor(
            problem=CollectionStatus.EVIDENCE_CONFLICT,
            detail=f"payload_sha256 ist kein 64-stelliger Hex-Wert: {sha!r}",
        )
    for feld in _MANIFEST_REQUIRED:
        if not str(manifest.get(feld, "") or "").strip():
            return _Anchor(
                problem=CollectionStatus.EVIDENCE_CONFLICT, detail=f"Manifest ohne {feld}"
            )
    if str(manifest.get("schema_version")) != EVIDENCE_SCHEMA_VERSION:
        return _Anchor(
            problem=CollectionStatus.EVIDENCE_CONFLICT,
            detail=f"fremde schema_version: {manifest.get('schema_version')!r}",
        )

    artifact = folder / f"{sha}.json"
    if not artifact.exists():
        return _Anchor(
            problem=CollectionStatus.UNANCHORED_ARTIFACT_PRESENT,
            detail="Manifest verweist auf ein fehlendes Artefakt",
            sha=sha,
            path=folder,
        )

    raw = artifact.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha:
        return _Anchor(
            problem=CollectionStatus.EVIDENCE_CONFLICT,
            detail="Artefakt-Bytes passen nicht zum verankerten Hash",
            sha=sha,
            path=artifact,
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _Anchor(
            problem=CollectionStatus.EVIDENCE_CONFLICT, detail=f"Artefakt unlesbar: {exc}", sha=sha
        )
    if not isinstance(payload, dict):
        return _Anchor(
            problem=CollectionStatus.EVIDENCE_CONFLICT, detail="Artefakt kein Objekt", sha=sha
        )

    # Der Writer schreibt IMMER kanonisch. Ein nichtkanonisches Artefakt kann
    # deshalb nicht legitim aus diesem Collector stammen — auch dann nicht, wenn
    # jemand Manifest und Dateinamen passend umgeschrieben hat.
    try:
        if canonical_bytes(payload) != raw:
            return _Anchor(
                problem=CollectionStatus.EVIDENCE_CONFLICT,
                detail="Artefakt-Bytes sind nicht die kanonische Darstellung",
                sha=sha,
            )
    except ValueError as exc:
        return _Anchor(
            problem=CollectionStatus.EVIDENCE_CONFLICT,
            detail=f"Artefakt nicht kanonisierbar: {exc}",
            sha=sha,
        )

    for feld in ("schema_version", "collector_code_sha", "collected_at_utc"):
        if str(manifest.get(feld, "")) != str(payload.get(feld, "")):
            return _Anchor(
                problem=CollectionStatus.EVIDENCE_CONFLICT,
                detail=f"Manifest und Artefakt widersprechen sich bei {feld}",
                sha=sha,
            )

    manifest_identity = _identity_of(manifest, artifact=False)
    artifact_identity = _identity_of(payload, artifact=True)
    if manifest_identity != artifact_identity:
        return _Anchor(
            problem=CollectionStatus.EVIDENCE_CONFLICT,
            detail="Manifest und Artefakt beschreiben verschiedene Closes",
            sha=sha,
        )
    if manifest_identity != identity:
        return _Anchor(
            problem=CollectionStatus.EVIDENCE_CONFLICT,
            detail="verankerte Evidenz gehoert zu einer anderen Close-Identitaet",
            sha=sha,
        )
    return _Anchor(sha=sha, path=artifact)


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


def _ensure_folder(folder: Path) -> None:
    """Ordner anlegen und den Verzeichniseintrag im Parent haltbar machen.

    Ohne den Parent-fsync ueberlebt ein neu angelegter Ordner einen Stromausfall
    nicht zwingend — auf der SD-Karte des Pi ist das kein theoretischer Rand.
    """
    existed = folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    if not existed:
        _fsync_dir(folder.parent)


def _acquire_lock(folder: Path) -> int | None:
    try:
        return os.open(str(folder / _LOCK_NAME), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None


def _release_lock(folder: Path, fd: int) -> None:
    try:
        os.close(fd)
    finally:
        (folder / _LOCK_NAME).unlink(missing_ok=True)
        # Der Ordner entsteht VOR dem Lock (er traegt ihn). Scheitert der
        # Sammellauf danach, bliebe ein leerer Ordner zurueck — kein Schaden, aber
        # Muell, der spaeter wie ein angefangener Commit aussieht. rmdir loescht
        # nur wirklich leere Verzeichnisse und laesst jede Evidenz unangetastet.
        try:
            folder.rmdir()
        except OSError:
            pass


def _lock_present(evidence: CloseEvidence | None = None, sha: str = "") -> CollectionResult:
    return CollectionResult(
        status=CollectionStatus.PUBLISH_LOCK_PRESENT,
        evidence=evidence,
        payload_sha256=sha,
        detail="Publish-Lock liegt (lebender Schreiber oder verwaistes Lock)",
    )


def _anchor_to_result(anchor: _Anchor, evidence: CloseEvidence | None = None) -> CollectionResult:
    return CollectionResult(
        status=anchor.problem or CollectionStatus.EVIDENCE_CONFLICT,
        evidence=evidence,
        payload_sha256=anchor.sha,
        path=str(anchor.path) if anchor.path else "",
        detail=anchor.detail,
    )


def _publish_locked(evidence: CloseEvidence, folder: Path) -> CollectionResult:
    """Schreibt Artefakt und danach den Commit-Marker. Erwartet den Lock."""
    sha = evidence.payload_sha256()
    artifact = folder / f"{sha}.json"
    _atomic_write(artifact, canonical_bytes(evidence.as_payload()))
    _atomic_write(folder / _MANIFEST_NAME, canonical_bytes(_manifest_payload(evidence, sha)))
    return CollectionResult(
        status=CollectionStatus.COLLECTED,
        evidence=evidence,
        payload_sha256=sha,
        path=str(artifact),
    )


def publish_evidence(evidence: CloseEvidence, base_dir: str | Path) -> CollectionResult:
    """Veroeffentlicht das Artefakt unter seiner Identitaet — atomar und idempotent.

    Layout::

        <base>/<sha256(fill_id)>/<payload_sha256>.json
        <base>/<sha256(fill_id)>/manifest.json      <- COMMIT MARKER

    Der Zustand wird UNTER DEM LOCK gelesen. Ohne dieses Lesen im Lock koennten
    zwei Sammler beide "nichts verankert" sehen, nacheinander den Lock bekommen
    und nacheinander schreiben — dann gewaenne wieder der Letzte, genau was
    verboten sein soll.
    """
    folder = Path(base_dir) / _folder_key(evidence.close_fill_id)
    identity = _evidence_identity(evidence)
    sha = evidence.payload_sha256()

    _ensure_folder(folder)
    lock_fd = _acquire_lock(folder)
    if lock_fd is None:
        return _lock_present(evidence, sha)
    try:
        anchor = _inspect_anchor(folder, identity)
        if anchor.problem is not None:
            return _anchor_to_result(anchor, evidence)
        if anchor.committed:
            if anchor.sha == sha:
                return CollectionResult(
                    status=CollectionStatus.IDEMPOTENT_NOOP,
                    evidence=evidence,
                    payload_sha256=sha,
                    path=str(anchor.path) if anchor.path else "",
                )
            return CollectionResult(
                status=CollectionStatus.EVIDENCE_CONFLICT,
                evidence=evidence,
                payload_sha256=sha,
                path=str(anchor.path) if anchor.path else "",
                detail=f"bereits verankert mit {anchor.sha}",
            )
        return _publish_locked(evidence, folder)
    finally:
        _release_lock(folder, lock_fd)


def collect_and_publish(
    close_row: dict[str, object],
    *,
    venue: str,
    fetch: CandleFetcher,
    now_utc: datetime,
    base_dir: str | Path,
) -> CollectionResult:
    """Der orchestrierende Pfad — und der einzige mit echter Retry-Idempotenz.

    Ablauf::

        schnelles Lesen: schon vollstaendig verankert?  -> IDEMPOTENT_NOOP
        sonst: Lock nehmen -> Zustand ERNEUT lesen -> erst DANN abrufen
               -> bauen -> Artefakt -> Commit-Marker -> Lock freigeben

    Der Abruf liegt bewusst hinter dem Lock: sonst starten zwei gleichzeitige
    Laeufe beide einen Netzabruf und konkurrieren anschliessend mit
    verschiedenen ``collected_at_utc`` um denselben Close.
    """
    identity = _close_identity(close_row, venue)
    # VOR dem Fast-Path: sonst endet ein Retry mit fehlender order_id als
    # EVIDENCE_CONFLICT, obwohl die Wahrheit CLOSE_IDENTITY_INCOMPLETE lautet.
    # Ohne Venue bekaeme ein Retry ausserdem fremde Evidenz zurueck.
    problem = _identity_problem(identity)
    if problem is not None:
        status, detail = problem
        return CollectionResult(status=status, detail=detail)

    folder = Path(base_dir) / _folder_key(identity["fill_id"])

    # Schnelles Lesen ohne Lock: der haeufige Fall ist "laengst verankert".
    anchor = _inspect_anchor(folder, identity)
    if anchor.problem is not None:
        return _anchor_to_result(anchor)
    if anchor.committed:
        return CollectionResult(
            status=CollectionStatus.IDEMPOTENT_NOOP,
            payload_sha256=anchor.sha,
            path=str(anchor.path) if anchor.path else "",
            detail="bereits verankert — kein erneuter Abruf",
        )

    _ensure_folder(folder)
    lock_fd = _acquire_lock(folder)
    if lock_fd is None:
        return _lock_present()
    try:
        # Zustand ERNEUT lesen: zwischen dem schnellen Lesen und dem Lock kann ein
        # anderer Lauf fertig geworden sein.
        anchor = _inspect_anchor(folder, identity)
        if anchor.problem is not None:
            return _anchor_to_result(anchor)
        if anchor.committed:
            return CollectionResult(
                status=CollectionStatus.IDEMPOTENT_NOOP,
                payload_sha256=anchor.sha,
                path=str(anchor.path) if anchor.path else "",
                detail="waehrend des Wartens verankert worden",
            )

        built = build_close_evidence(
            close_row, venue=identity["venue"], fetch=fetch, now_utc=now_utc
        )
        if built.evidence is None:
            return built
        return _publish_locked(built.evidence, folder)
    finally:
        _release_lock(folder, lock_fd)
