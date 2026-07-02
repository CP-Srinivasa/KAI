"""Entkoppelte Long/Short-Ratio-Snapshot-Schicht (Goal V5 Phase 3).

Architektur identisch zur OI-Schicht (Phase 2), bewusst als **eigene** Datei +
eigener Store statt einer Erweiterung von ``oi_snapshot_store``:

  - L/S-Ratio, OI und Funding sind orthogonale Evidenzen mit verschiedener
    Kadenz und verschiedenem TTL. Sie in eine Datei zu koppeln würde sie an
    EINEN Refresh-Lebenszyklus + EINE Staleness-Grenze binden — das
    widerspricht dem „orthogonal"-Designziel.
  - Anders als OI braucht L/S KEINE im Refresh vorberechnete Größe: der rohe
    ``long_account_ratio`` (0..1) genügt; die contrarian-Evidence leitet
    ``build_long_short_ratio_evidence`` direkt aus dem Abstand zu 0.5 ab. Der
    Loop liest ausschließlich diesen Skalar von Platte — kein Netz-I/O.

Fällt der Refresh-Service aus → Datei wird stale → ``DiskLongShortRatioAdapter``
liefert nach TTL nichts → Provider gibt leere Evidence → Loop unverändert.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.market_data.models import LongShortRatioSnapshot

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


class _VenueLsProto(Protocol):
    async def get_long_short_ratio(
        self, symbol: str, *, interval: str = ...
    ) -> LongShortRatioSnapshot | None: ...


# ── Multi-Venue-Adapter ────────────────────────────────────────────────────


class LongShortRatioMultiVenueAdapter:
    """Bybit-first, Binance-Futures-Fallback L/S-Quelle.

    Venue-Reihenfolge identisch zur Funding/OI-Kaskade (Bybit ist die native
    Primärquelle der Premium-Channel-Symbole). Fail-safe: jeder Venue-Fehler
    fällt still zur nächsten durch; alle leer ⇒ ``None``. Niemals raise.
    """

    def __init__(self, venues: Sequence[_VenueLsProto], *, interval: str = "1h") -> None:
        self._venues = list(venues)
        self._interval = interval

    async def get_long_short_ratio(self, symbol: str) -> LongShortRatioSnapshot | None:
        for venue in self._venues:
            try:
                snap = await venue.get_long_short_ratio(symbol, interval=self._interval)
            except Exception as exc:  # noqa: BLE001 — Venue darf Refresh nie killen
                logger.warning(
                    "[ls-venue] %s %s raised: %s",
                    type(venue).__name__,
                    symbol,
                    exc,
                )
                continue
            if snap is not None:
                return snap
        return None


def build_default_ls_multi_venue_adapter(
    *, timeout_seconds: float = 8.0, interval: str = "1h"
) -> LongShortRatioMultiVenueAdapter:
    """Default-Kaskade Bybit → Binance-Futures mit gemeinsamem Timeout."""
    from app.market_data.binance_futures_adapter import BinanceFuturesAdapter
    from app.market_data.bybit_adapter import BybitAdapter

    timeout_int = max(1, int(round(timeout_seconds)))
    return LongShortRatioMultiVenueAdapter(
        [
            BybitAdapter(timeout_seconds=timeout_int),
            BinanceFuturesAdapter(timeout_seconds=timeout_int),
        ],
        interval=interval,
    )


# ── Platten-Snapshot-Store ─────────────────────────────────────────────────


class LongShortRatioSnapshotStore:
    """Atomare JSON-Persistenz für L/S-Snapshots (key = canonical symbol).

    Format::

        {"schema": 1, "written_at_utc": "...", "snapshots": {"BTC/USDT": {...}}}

    Lese-Fehler (Datei fehlt / korrupt) ⇒ leeres Dict, kein raise.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def write_many(self, snapshots: Iterable[LongShortRatioSnapshot]) -> int:
        payload: dict[str, Any] = {
            "schema": _SCHEMA_VERSION,
            "written_at_utc": datetime.now(UTC).isoformat(),
            "snapshots": {snap.symbol: asdict(snap) for snap in snapshots},
        }
        count = len(payload["snapshots"])
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), prefix=".ls_", suffix=".tmp")
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

    def read_all(self) -> dict[str, LongShortRatioSnapshot]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("[ls-store] corrupt snapshot file: %s", self._path)
            return {}
        if not isinstance(data, dict):
            return {}
        snaps = data.get("snapshots")
        if not isinstance(snaps, dict):
            return {}
        out: dict[str, LongShortRatioSnapshot] = {}
        for sym, body in snaps.items():
            if not isinstance(body, dict):
                continue
            try:
                out[str(sym)] = LongShortRatioSnapshot(
                    symbol=str(body["symbol"]),
                    timestamp_utc=str(body["timestamp_utc"]),
                    long_account_ratio=float(body["long_account_ratio"]),
                    source=str(body.get("source", "unknown")),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def read(self, symbol: str) -> LongShortRatioSnapshot | None:
        return self.read_all().get(symbol.strip().upper())


# ── Disk-Adapter (Loop-Pfad) ───────────────────────────────────────────────


class DiskLongShortRatioAdapter:
    """``get_long_short_ratio``, das den warmen Snapshot von Platte liest.

    mtime-Caching wie ``DiskOpenInterestAdapter``: pro one-shot-Tick i. d. R.
    genau ein Parse. Reiner Disk-Read, kein Netz.
    """

    def __init__(self, store: LongShortRatioSnapshotStore) -> None:
        self._store = store
        self._cached_mtime: float | None = None
        self._cached: dict[str, LongShortRatioSnapshot] = {}

    async def get_long_short_ratio(self, symbol: str) -> LongShortRatioSnapshot | None:
        try:
            mtime = self._store.path.stat().st_mtime
        except (OSError, FileNotFoundError):
            return None
        if mtime != self._cached_mtime:
            self._cached = self._store.read_all()
            self._cached_mtime = mtime
        return self._cached.get(symbol.strip().upper())


# ── Shadow-Log (measure-first) ─────────────────────────────────────────────


def append_ls_shadow_log(
    path: Path | str,
    *,
    symbol: str,
    long_account_ratio: float,
    direction: str,
    source: str,
    source_trust: float,
    evidence_value: float,
    evidence_direction_aligned: int,
) -> None:
    """Append-only read-only Mess-Spur für L/S-Evidence-Beiträge.

    Fail-safe: Schreibfehler werden geloggt + verschluckt (Shadow-Log darf
    den Signal-Pfad nie killen).
    """
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "long_account_ratio": long_account_ratio,
        "direction": direction,
        "source": source,
        "source_trust": source_trust,
        "evidence_value": evidence_value,
        "evidence_direction_aligned": evidence_direction_aligned,
    }
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — Mess-Spur darf Signalpfad nie killen
        logger.warning("[ls-shadow] append failed: %s", exc)


__all__ = [
    "DiskLongShortRatioAdapter",
    "LongShortRatioMultiVenueAdapter",
    "LongShortRatioSnapshotStore",
    "append_ls_shadow_log",
    "build_default_ls_multi_venue_adapter",
]
