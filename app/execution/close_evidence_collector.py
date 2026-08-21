"""Sammelt Marktevidenz zu einem Close — und fällt dabei kein Urteil.

**Was dieses Modul NICHT tut.** Es entscheidet nichts. Kein ``verified``, kein
``plausibel``, keine Schwelle. Es holt Tatsachen, bindet sie an die Identität des
Closes und veröffentlicht sie als unveränderliches Artefakt. Ob die Evidenz
trägt, entscheidet ausschließlich der Offline-Verifier — der dafür kein Netz
braucht und deshalb bei gleicher Evidenz immer gleich urteilt.

**Venue ist explizit, nie abgeleitet.** Der Aufrufer sagt, von welcher Venue die
Kerzen kommen. Nachträglich aus einem `price_source`-String zu raten, welcher
Anbieter gemeint war, würde genau die Provenienz erfinden, die zu beweisen ist.

**Fenster: 1m primär, 5m höchstens bestätigend.** Ein weites Fenster macht jeden
Preis „plausibel" — genau das soll die Prüfung ja ausschließen. Deshalb ist das
Primärfenster minutengenau und eng um den Close gelegt; ein 5m-Fenster darf
danebenstehen, aber nie das Primärfenster ersetzen.

**Veröffentlichung ist atomar, und der Hash entsteht VOR dem Schreiben.** Erst
wird das Artefakt vollständig gebaut, dann kanonisch gehasht, dann als Ganzes
per ``tempfile → fsync → rename`` veröffentlicht. Würde man erst JSON schreiben
und den Hash danebenlegen, erzeugte ein Absturz dazwischen einen halbfertigen
Wahrheitszustand.

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
    "PRIMARY_WINDOW_MINUTES",
    "CandleFetcher",
    "CollectionResult",
    "CollectionStatus",
    "build_close_evidence",
    "collector_code_sha",
    "publish_evidence",
]

PRIMARY_INTERVAL = "1m"
"""Minutengenau. Ein breiteres Primaerfenster macht jeden Preis plausibel."""

PRIMARY_WINDOW_MINUTES = 3
"""Eine Kerze vor und eine nach dem Close — eng genug, um etwas auszuschliessen."""

CORROBORATING_INTERVAL = "5m"
"""Darf danebenstehen, ersetzt das Primaerfenster aber nie."""


class CollectionStatus(StrEnum):
    """Wie die SAMMLUNG ausging — kein Urteil ueber den Close."""

    COLLECTED = "collected"
    IDEMPOTENT_NOOP = "idempotent_noop"
    EVIDENCE_CONFLICT = "evidence_conflict"
    WINDOW_UNAVAILABLE = "window_unavailable"
    CLOSE_IDENTITY_INCOMPLETE = "close_identity_incomplete"
    INVALID_CLOSE_TIMESTAMP = "invalid_close_timestamp"
    CANDLES_IN_FUTURE = "candles_in_future"


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


def build_close_evidence(
    close_row: dict[str, object],
    *,
    venue: str,
    fetch: CandleFetcher,
    now_utc: datetime,
    interval: str = PRIMARY_INTERVAL,
    window_minutes: int = PRIMARY_WINDOW_MINUTES,
) -> CollectionResult:
    """Baut das Artefakt. Holt Kerzen, urteilt nicht.

    ``now_utc`` wird hereingereicht statt gelesen: der Bau bleibt damit
    reproduzierbar und die Zukunftspruefung testbar.
    """
    fill_id = str(close_row.get("fill_id", "") or "").strip()
    order_id = str(close_row.get("order_id", "") or "").strip()
    symbol = str(close_row.get("symbol", "") or "").strip()
    close_ts_raw = str(close_row.get("timestamp_utc", "") or "").strip()
    venue = str(venue or "").strip()

    # Ohne vollstaendige Identitaet gibt es nichts zu binden — und ein Artefakt
    # ohne Bindung koennte spaeter den falschen Close "verifizieren".
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

    close_at = _parse_utc(close_ts_raw)
    if close_at is None:
        return CollectionResult(
            status=CollectionStatus.INVALID_CLOSE_TIMESTAMP,
            detail=f"unlesbar oder ohne Zeitzone: {close_ts_raw!r}",
        )

    half = max(1, window_minutes // 2)
    close_ms = int(close_at.timestamp() * 1000)
    start_ms = close_ms - half * 60_000
    end_ms = close_ms + half * 60_000

    candles = list(
        fetch(symbol=symbol, venue=venue, interval=interval, start_ms=start_ms, end_ms=end_ms)
    )
    if not candles:
        return CollectionResult(
            status=CollectionStatus.WINDOW_UNAVAILABLE,
            detail=f"{venue} lieferte keine {interval}-Kerzen fuer {symbol}",
        )

    # Kerzen, die nach dem Sammelzeitpunkt liegen, sind keine Beobachtung.
    now_ms = int(now_utc.timestamp() * 1000)
    if any(c.open_time_ms > now_ms for c in candles):
        return CollectionResult(
            status=CollectionStatus.CANDLES_IN_FUTURE,
            detail="Anbieter lieferte Kerzen aus der Zukunft",
        )

    evidence = CloseEvidence(
        close_fill_id=fill_id,
        close_order_id=order_id,
        symbol=symbol,
        close_timestamp_utc=close_ts_raw,
        venue=venue,
        interval=interval,
        window_start_ms=start_ms,
        window_end_ms=end_ms,
        candles=tuple(sorted(candles, key=lambda c: c.open_time_ms)),
        collected_at_utc=now_utc.astimezone(UTC).isoformat(),
        collector_code_sha=collector_code_sha(),
        schema_version=EVIDENCE_SCHEMA_VERSION,
    )
    return CollectionResult(
        status=CollectionStatus.COLLECTED,
        evidence=evidence,
        payload_sha256=evidence.payload_sha256(),
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    """Ganz oder gar nicht: tempfile im Zielverzeichnis, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def publish_evidence(evidence: CloseEvidence, base_dir: str | Path) -> CollectionResult:
    """Veroeffentlicht das Artefakt unter seiner Identitaet — atomar und idempotent.

    Layout::

        <base>/<fill_id>/<payload_sha256>.json
        <base>/<fill_id>/manifest.json

    Fuer dieselbe Close-Identitaet gilt fail-closed: ein abweichender Hash
    ueberschreibt nichts, sondern meldet ``EVIDENCE_CONFLICT``.
    """
    sha = evidence.payload_sha256()
    folder = Path(base_dir) / evidence.close_fill_id
    artifact = folder / f"{sha}.json"
    manifest_path = folder / "manifest.json"

    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return CollectionResult(
                status=CollectionStatus.EVIDENCE_CONFLICT,
                payload_sha256=sha,
                detail=f"Manifest unlesbar: {exc}",
            )
        previous = str(existing.get("payload_sha256", ""))
        if previous and previous != sha:
            # Der Provider liefert dieselben historischen Kerzen anders. Eine
            # bereits verankerte Evidenz nachtraeglich zu ersetzen hiesse, die
            # Vergangenheit umzuschreiben.
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

    # Hash steht VOR dem Schreiben fest; veroeffentlicht wird das fertige Ganze.
    _atomic_write(artifact, canonical_bytes(evidence.as_payload()))
    _atomic_write(
        manifest_path,
        canonical_bytes(
            {
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
        ),
    )
    return CollectionResult(
        status=CollectionStatus.COLLECTED,
        evidence=evidence,
        payload_sha256=sha,
        path=str(artifact),
    )
