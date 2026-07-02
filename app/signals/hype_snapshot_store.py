"""Entkoppelte Hype-Snapshot-Schicht (HYPE-S1).

Architektur identisch zu Funding/OI/L/S (Goal V5), bewusst als **eigene**
Datei + eigener Store: Hype ist eine orthogonale Evidence mit eigener Kadenz
(Dokument-Aggregation, default 15-min-Refresh) und eigenem TTL. Anders als
die V5-Schichten kommt der Rohstoff NICHT von einer Exchange, sondern aus
KAIs eigener Dokument-/Sentiment-Pipeline — der Refresh-Service
(``scripts/hype_snapshot_refresh.py``) aggregiert die DB und schreibt den
fertigen Score; der Loop liest ausschließlich diesen Skalar von Platte.
KEIN DB-Zugriff und KEIN Netz-I/O im Signal-Pfad.

Schlüssel-Konvention: Snapshots sind nach **Base-Asset** gekeyt (``BTC``),
weil Dokument-Tags Assets nennen, nicht Handelspaare. ``read(symbol)``
normalisiert ``BTC/USDT`` → ``BTC`` — ein Hype-Score gilt für das Asset,
nicht für ein einzelnes Quote-Pairing.

Fällt der Refresh aus → Datei wird stale → Provider liefert nach TTL keine
Evidence → Loop unverändert (fail-safe, identisch zur V5-Disziplin).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


def base_asset(symbol_or_asset: str) -> str:
    """Normalisiere ``BTC/USDT`` / ``btc`` → ``BTC`` (Snapshot-Schlüssel)."""
    return symbol_or_asset.strip().upper().split("/", 1)[0]


@dataclass(frozen=True)
class HypeSnapshot:
    """Fertig berechneter Hype-Stand EINES Assets (vom Refresh geschrieben)."""

    asset: str  # Base-Asset, z. B. "BTC"
    timestamp_utc: str  # ISO-8601 (Aggregations-Zeitpunkt)
    hype_score: float  # ∈ [0, 1] — compute_hype_score
    velocity_ratio: float
    mentions_recent: int
    distinct_sources_recent: int
    one_sidedness: float
    insufficient_data: bool
    source: str = "internal_docs"


class HypeSnapshotStore:
    """Atomare JSON-Persistenz für Hype-Snapshots (key = Base-Asset).

    Format::

        {"schema": 1, "written_at_utc": "...", "snapshots": {"BTC": {...}}}

    Lese-Fehler (Datei fehlt / korrupt) ⇒ leeres Dict, kein raise.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def write_many(self, snapshots: Iterable[HypeSnapshot]) -> int:
        payload: dict[str, Any] = {
            "schema": _SCHEMA_VERSION,
            "written_at_utc": datetime.now(UTC).isoformat(),
            "snapshots": {base_asset(snap.asset): asdict(snap) for snap in snapshots},
        }
        count = len(payload["snapshots"])
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), prefix=".hype_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return count

    def read_all(self) -> dict[str, HypeSnapshot]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("[hype-store] corrupt snapshot file: %s", self._path)
            return {}
        if not isinstance(data, dict):
            return {}
        snaps = data.get("snapshots")
        if not isinstance(snaps, dict):
            return {}
        out: dict[str, HypeSnapshot] = {}
        for asset, body in snaps.items():
            if not isinstance(body, dict):
                continue
            try:
                out[str(asset)] = HypeSnapshot(
                    asset=str(body["asset"]),
                    timestamp_utc=str(body["timestamp_utc"]),
                    hype_score=float(body["hype_score"]),
                    velocity_ratio=float(body.get("velocity_ratio", 0.0)),
                    mentions_recent=int(body.get("mentions_recent", 0)),
                    distinct_sources_recent=int(body.get("distinct_sources_recent", 0)),
                    one_sidedness=float(body.get("one_sidedness", 0.0)),
                    insufficient_data=bool(body.get("insufficient_data", False)),
                    source=str(body.get("source", "internal_docs")),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def read(self, symbol_or_asset: str) -> HypeSnapshot | None:
        return self.read_all().get(base_asset(symbol_or_asset))


# ── Shadow-Log (measure-first) ─────────────────────────────────────────────


def append_hype_shadow_log(
    path: Path | str,
    *,
    symbol: str,
    hype_score: float,
    direction: str,
    source: str,
    source_trust: float,
    evidence_emitted: bool,
    evidence_value: float,
    evidence_direction_aligned: int,
) -> None:
    """Append-only Mess-Spur für Hype-Evidence-Beiträge.

    Loggt auch NICHT emittierte Beiträge (``evidence_emitted=False`` unter
    ``min_score_for_evidence`` bzw. dampen_only-Short) — genau diese Spur
    beantwortet nach ~7 d Shadow, ob der Schwellwert/Trust richtig sitzt.
    Fail-safe: Schreibfehler werden geloggt + verschluckt.
    """
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "hype_score": hype_score,
        "direction": direction,
        "source": source,
        "source_trust": source_trust,
        "evidence_emitted": evidence_emitted,
        "evidence_value": evidence_value,
        "evidence_direction_aligned": evidence_direction_aligned,
    }
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — Mess-Spur darf Signalpfad nie killen
        logger.warning("[hype-shadow] append failed: %s", exc)


__all__ = [
    "HypeSnapshot",
    "HypeSnapshotStore",
    "append_hype_shadow_log",
    "base_asset",
]
