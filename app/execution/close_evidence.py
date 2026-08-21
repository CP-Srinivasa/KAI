"""Das unveränderliche Evidenz-Artefakt eines Closes.

Zwischen dem Sammeln (Netz) und dem Urteilen (kein Netz) steht bewusst ein
persistiertes Artefakt. Nur so ergibt derselbe Close mit demselben Artefakt
**immer** dasselbe Urteil — unabhängig davon, ob Bybit gerade antwortet oder ob
sich eine Kerzen-Historie zwischen zwei Prüfungen ändert.

**Kanonische Hash-Semantik.** Der Hash darf sich nicht ändern, weil jemand die
Datei hübscher formatiert. Deshalb ist die Byte-Darstellung festgelegt:

  * sortierte Schlüssel,
  * feste Trennzeichen ohne Leerraum,
  * UTF-8,
  * ``allow_nan=False`` — NaN/Infinity brechen die Serialisierung, statt als
    ``NaN`` im JSON zu landen, wo sie später wie eine Messung aussähen,
  * SHA-256 über genau diese Bytes.

Das Artefakt trägt außerdem ``schema_version`` und ``collector_code_sha``: wer
später ein Urteil nachvollzieht, sieht, nach welchem Format und mit welcher
Sammler-Version die Evidenz entstanden ist.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

EVIDENCE_SCHEMA_VERSION = "close_evidence/v1"

__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "CloseEvidence",
    "VenueCandle",
    "canonical_bytes",
    "canonical_sha256",
]


def canonical_bytes(payload: Any) -> bytes:
    """Die eine erlaubte Byte-Darstellung. Wirft bei NaN/Infinity."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class VenueCandle:
    """Eine Kerze der Venue. Zeiten in ms seit Epoch, wie die Börsen sie liefern."""

    open_time_ms: int
    open: float
    high: float
    low: float
    close: float

    def contains(self, price: float, *, tolerance_pct: float = 0.0) -> bool:
        """Liegt ``price`` im Band dieser Kerze?

        Bewusst mit Toleranz-Option: der Vergleich eines *intern* gebuchten
        Preises mit *externen* Marktdaten darf nie bit-exakt verlangt werden.
        Bit-exakt ist nur die Rekonstruktion innerhalb derselben Engine-Arithmetik.
        """
        if self.low <= 0 or self.high <= 0:
            return False
        pad = self.high * (tolerance_pct / 100.0)
        return (self.low - pad) <= price <= (self.high + pad)


@dataclass(frozen=True)
class CloseEvidence:
    """Was der Collector über das Marktumfeld eines Closes festgehalten hat."""

    # --- Identität des Closes, auf den sich die Evidenz bezieht ---------------
    close_fill_id: str
    close_order_id: str
    symbol: str
    close_timestamp_utc: str

    # --- woher und welches Fenster -------------------------------------------
    venue: str
    interval: str
    window_start_ms: int
    window_end_ms: int
    candles: tuple[VenueCandle, ...] = ()

    # --- Herkunft des Artefakts selbst ---------------------------------------
    collected_at_utc: str = ""
    collector_code_sha: str = ""
    schema_version: str = EVIDENCE_SCHEMA_VERSION
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_payload(self) -> dict[str, Any]:
        """Die Struktur, über die gehasht wird — Tupel werden zu Listen."""
        payload = asdict(self)
        payload["candles"] = [asdict(c) for c in self.candles]
        payload["notes"] = list(self.notes)
        return payload

    def payload_sha256(self) -> str:
        """Hash über die kanonischen Bytes. Formatierung ändert ihn nicht."""
        return canonical_sha256(self.as_payload())

    @property
    def is_empty(self) -> bool:
        """Kein Kerzenband — dann gibt es nichts zu prüfen, und das ist fehlende
        Evidenz, kein Freispruch."""
        return not self.candles

    def candle_covering(self, timestamp_ms: int) -> VenueCandle | None:
        """Die Kerze, in deren Intervall ``timestamp_ms`` fällt."""
        if not self.candles:
            return None
        width = self._interval_ms()
        if width is None:
            return None
        for candle in self.candles:
            if candle.open_time_ms <= timestamp_ms < candle.open_time_ms + width:
                return candle
        return None

    def _interval_ms(self) -> int | None:
        table = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}
        return table.get(self.interval)

    def band(self) -> tuple[float, float] | None:
        """(low, high) über alle Kerzen — der weiteste zulässige Korridor."""
        if not self.candles:
            return None
        return (min(c.low for c in self.candles), max(c.high for c in self.candles))
