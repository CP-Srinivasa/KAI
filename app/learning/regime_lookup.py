"""Regime-Key Lookup — verbindet Decision-Time-Timestamp zu R1-Regime.

Step 5 der Adaptive-Learning-Reihenfolge (1→3→5→4→2→7→6).

Vertrag
-------

Der R1-Observer (``app/regime/``) schreibt stündlich pro Asset einen JSONL-Snapshot
nach ``artifacts/regime_state/{asset}_regime.jsonl``:

    {
      "asset": "BTC",
      "timestamp": "2026-05-13T07:00:00Z",
      "regime": "breakout_up",       // 6 Klassen
      "vol_class": "vol_low",         // 3 Klassen
      "confidence": 1.0,
      ...
    }

Dieses Modul liest die Snapshots und beantwortet die Frage:
*„Welches Regime war zur Zeit X für Asset Y aktiv?"*

Antwort = der **letzte** Snapshot mit ``timestamp ≤ X``. Wenn kein Snapshot vor X
existiert (z. B. weil der Observer noch nicht lief) → ``None`` (Caller mappt das
typischerweise auf ``UNKNOWN_REGIME_KEY``).

Canonical Regime-Key
--------------------

Wir kombinieren ``regime`` und ``vol_class`` zu einem flachen String:

    "breakout_up|vol_low"

Begründung: Die Bayes-Engine driftet sowohl trend-abhängig (breakout vs. chop)
als auch vol-abhängig (high-vol meist overconfident). Ein kombinierter Key
schöpft beides ab. Sparse-Buckets fallen über ``RegimeCalibratorBundle``
sauber auf den Global-Fallback zurück — die Bundle-Logik (Step 1)
behandelt das Problem bereits.

Wir bewusst NICHT:
- nur ``regime`` (verschenkt Vol-Information)
- nur ``vol_class`` (verschenkt Trend-Information)
- Triple-Key mit ``confidence`` (kontinuierlich, kein sinnvolles Bucketing)

Performance
-----------

Lookup ist binary-search pro Asset (sortierter Snapshot-List). Für typische
Hourly-Cadence + 14d-Window sind das ~336 Einträge pro Asset — Lookup ist
trivial in <1µs. Re-Load wird vom Caller gemanaged (loader ist stateless
nach Konstruktion).
"""

from __future__ import annotations

import bisect
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from app.learning.regime_calibration import UNKNOWN_REGIME_KEY

logger = logging.getLogger(__name__)

DEFAULT_REGIME_STATE_DIR: Final[Path] = Path("artifacts/regime_state")
REGIME_KEY_SEPARATOR: Final[str] = "|"


class RegimeSnapshot(BaseModel):
    """Ein einzelner Snapshot des R1-Observers, normalisiert."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    asset: str
    timestamp_utc: datetime
    regime: str
    vol_class: str
    confidence: float

    @property
    def regime_key(self) -> str:
        """Canonical key für RegimeCalibratorBundle.

        Format: ``"{regime}|{vol_class}"`` (z. B. ``"breakout_up|vol_low"``).
        """
        return f"{self.regime}{REGIME_KEY_SEPARATOR}{self.vol_class}"


class RegimeLookup:
    """In-Memory-Index über alle Asset-Snapshots, mit Binary-Search-Lookup.

    Konstruktion ist read-only — neue Snapshots auf der Disk werden NICHT
    automatisch reflektiert. Caller muss bei Bedarf neu instanziieren.
    """

    def __init__(self, snapshots_by_asset: dict[str, list[RegimeSnapshot]]) -> None:
        # Sortierung in __init__: Garantie für binary search.
        self._by_asset: dict[str, list[RegimeSnapshot]] = {
            asset: sorted(snaps, key=lambda s: s.timestamp_utc)
            for asset, snaps in snapshots_by_asset.items()
        }
        # Parallele Liste der reinen Timestamps pro Asset — bisect braucht
        # einen sortierten Vergleichswert, und wir wollen Snapshot-Objekte
        # nicht über ihre __lt__ vergleichen müssen.
        self._timestamps_by_asset: dict[str, list[datetime]] = {
            asset: [s.timestamp_utc for s in snaps]
            for asset, snaps in self._by_asset.items()
        }

    @classmethod
    def from_artifacts(
        cls,
        regime_state_dir: Path | str = DEFAULT_REGIME_STATE_DIR,
    ) -> RegimeLookup:
        """Lade alle ``{asset}_regime.jsonl`` aus dem Verzeichnis.

        Asset-Key wird aus dem Dateinamen extrahiert (``btc_regime.jsonl`` →
        ``"BTC"``). Pro Datei werden alle Zeilen geparst; malformed Zeilen
        werden geloggt + übersprungen (Audit darf nie blocken).

        Verzeichnis fehlt → leerer Lookup (alle Queries returnen None).
        """
        root = Path(regime_state_dir)
        snapshots_by_asset: dict[str, list[RegimeSnapshot]] = {}
        if not root.exists() or not root.is_dir():
            return cls(snapshots_by_asset)

        for jsonl_path in sorted(root.glob("*_regime.jsonl")):
            asset_name = jsonl_path.stem.removesuffix("_regime").upper()
            if not asset_name:
                continue
            snapshots = _load_snapshots(jsonl_path)
            if snapshots:
                snapshots_by_asset[asset_name] = snapshots

        return cls(snapshots_by_asset)

    def lookup(self, asset: str, ts: datetime) -> RegimeSnapshot | None:
        """Finde den jüngsten Snapshot mit ``timestamp ≤ ts`` für ``asset``.

        Returns None wenn:
          - Asset nicht im Index (kein JSONL geladen).
          - ts liegt vor dem ersten Snapshot dieses Assets.
          - ts ist kein timezone-aware UTC-Datetime.

        Beide Eingabe-Datetimes müssen timezone-aware sein. Naive Datetimes
        werden defensiv als UTC interpretiert (Logged-Warning) — strict
        wäre besser, aber Audit-Lese-Pfade liefern oft naive Strings.
        """
        normalized_asset = _normalize_asset(asset)
        snaps = self._by_asset.get(normalized_asset)
        if not snaps:
            return None

        ts_utc = _coerce_to_utc(ts)
        timestamps = self._timestamps_by_asset[normalized_asset]
        # bisect_right liefert den Insert-Punkt rechts vom letzten ts == ts_utc.
        # idx-1 ist damit der jüngste Snapshot mit timestamp ≤ ts_utc.
        idx = bisect.bisect_right(timestamps, ts_utc)
        if idx == 0:
            return None
        return snaps[idx - 1]

    def regime_key(self, asset: str, ts: datetime) -> str | None:
        """Convenience: Lookup + Key-Extraktion in einem Schritt.

        Returns None statt UNKNOWN_REGIME_KEY — die Entscheidung,
        ob ein Miss als unknown gebucht wird oder als None propagiert
        bleibt beim Caller (regime_calibration.fit_regime_calibrators
        nimmt None gar nicht erst auf — siehe dort).
        """
        snap = self.lookup(asset, ts)
        return snap.regime_key if snap else None

    @property
    def assets(self) -> list[str]:
        """Liste aller bekannten Asset-Keys (z. B. ['BTC', 'ETH'])."""
        return sorted(self._by_asset.keys())

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_asset.values())


# ─── Internals ────────────────────────────────────────────────────────────────


def _load_snapshots(jsonl_path: Path) -> list[RegimeSnapshot]:
    """Parse JSONL und filtere malformed Zeilen out."""
    snapshots: list[RegimeSnapshot] = []
    try:
        with jsonl_path.open("r", encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "[regime-lookup] skipped malformed JSON %s:%d (%s)",
                        jsonl_path,
                        line_no,
                        exc,
                    )
                    continue
                snap = _payload_to_snapshot(payload, jsonl_path, line_no)
                if snap is not None:
                    snapshots.append(snap)
    except OSError as exc:
        logger.warning("[regime-lookup] read failed (%s): %s", jsonl_path, exc)
    return snapshots


def _payload_to_snapshot(
    payload: object,
    path: Path,
    line_no: int,
) -> RegimeSnapshot | None:
    if not isinstance(payload, dict):
        logger.warning(
            "[regime-lookup] non-dict row %s:%d", path, line_no
        )
        return None
    raw_ts = payload.get("timestamp") or payload.get("timestamp_utc")
    if not isinstance(raw_ts, str):
        return None
    try:
        ts = _parse_iso_utc(raw_ts)
    except ValueError:
        logger.warning(
            "[regime-lookup] bad timestamp %s:%d (%s)", path, line_no, raw_ts
        )
        return None
    try:
        return RegimeSnapshot(
            asset=str(payload.get("asset", "")).upper() or "UNKNOWN",
            timestamp_utc=ts,
            regime=str(payload["regime"]),
            vol_class=str(payload["vol_class"]),
            confidence=float(payload.get("confidence", 0.0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "[regime-lookup] skipped row %s:%d (%s)", path, line_no, exc
        )
        return None


def _normalize_asset(symbol: str) -> str:
    """Schneidet Pair-Suffix ab: ``"BTC/USDT"`` → ``"BTC"``.

    Unterstützt sowohl ``/`` als auch ``-`` als Separator. Pure-asset
    Strings (``"BTC"``) bleiben unverändert. Case-insensitive — wir
    upper-case'n grundsätzlich.
    """
    s = symbol.upper().strip()
    for sep in ("/", "-", ":"):
        if sep in s:
            return s.split(sep, 1)[0]
    return s


def _parse_iso_utc(raw: str) -> datetime:
    """Parse ISO-8601-String, akzeptiere Z-Suffix als UTC."""
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    ts = datetime.fromisoformat(raw)
    if ts.tzinfo is None:
        # Schwach typisierte Quelle — als UTC interpretieren (siehe Docstring).
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _coerce_to_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        logger.debug(
            "[regime-lookup] naive datetime received, assuming UTC: %s", ts
        )
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


__all__ = [
    "DEFAULT_REGIME_STATE_DIR",
    "REGIME_KEY_SEPARATOR",
    "RegimeLookup",
    "RegimeSnapshot",
    "UNKNOWN_REGIME_KEY",
]
