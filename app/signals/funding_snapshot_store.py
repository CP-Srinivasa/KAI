"""Entkoppelte Funding-Snapshot-Schicht (Goal V5 Phase 1).

Warum diese Schicht überhaupt existiert
========================================
Der Trading-Loop läuft als **cron-one-shot** (``build_trading_loop`` →
``run_once`` alle ~10 min, frischer Prozess pro Tick). Der
``FundingEvidenceCache`` ist rein in-memory: ein separater, langlebiger
Refresh-Prozess kann den Cache des Loop-Prozesses NICHT wärmen — getrennte
Speicher.

Operator-Invariante ist trotzdem: *der Loop darf nicht inline pro Zyklus
synchron über viele Symbole HTTP ziehen* (Latenz/Flaschenhals). Die einzige
korrekte Auflösung bei one-shot-Prozessen ist eine **platten-gepufferte**
Snapshot-Schicht:

  - Der entkoppelte Refresh-Service (``scripts/funding_cache_refresh.py``,
    systemd-Timer, default disabled) zieht die echten Venue-Funding-Raten
    (Bybit→Binance Fallback), bounded/timeout, und schreibt sie atomar in
    eine kleine JSON-Datei (``FundingSnapshotStore``).
  - Der Loop baut beim Start einen ``FundingEvidenceCache`` über einen
    ``DiskFundingAdapter`` — dessen ``get_funding_rate`` ist ein reiner,
    schneller **Disk-Read** (kein Netz). Der Loop wärmt seinen In-Memory-
    Cache aus der bereits warmen Datei. Kein Netz-I/O im Loop, kein Hängen.

Damit ist der Refresh echt entkoppelt: fällt der Service aus, wird die Datei
stale → ``DiskFundingAdapter`` liefert nach TTL nichts → der Provider gibt
leere Evidence zurück → der Loop läuft unverändert weiter (fail-safe).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.market_data.models import FundingRateSnapshot

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


class _VenueFundingProto(Protocol):
    async def get_funding_rate(self, symbol: str) -> FundingRateSnapshot | None: ...


# ── Multi-Venue-Adapter ────────────────────────────────────────────────────


class FundingMultiVenueAdapter:
    """Bybit-first, Binance-Futures-Fallback Funding-Quelle.

    Begründung der Venue-Reihenfolge: der Premium-Telegram-Channel postet
    Bybit-Linear-Paare wörtlich (inkl. Exoten wie SWARMS/1000LUNC), die
    Binance-Spot nicht listet — Bybit ist die native Primärquelle, exakt
    wie in der bestehenden Markt-Daten-Fallback-Kaskade. Binance-Futures
    fängt die Fälle ab, in denen Bybit den Wert nicht hat.

    Fail-safe: jeder Venue-Fehler (None / Exception) fällt still zur
    nächsten Quelle durch; sind alle leer ⇒ ``None``. Niemals raise.
    """

    def __init__(self, venues: Sequence[_VenueFundingProto]) -> None:
        self._venues = list(venues)

    async def get_funding_rate(self, symbol: str) -> FundingRateSnapshot | None:
        for venue in self._venues:
            try:
                snap = await venue.get_funding_rate(symbol)
            except Exception as exc:  # noqa: BLE001 — Venue darf Refresh nie killen
                logger.warning(
                    "[funding-venue] %s %s raised: %s",
                    type(venue).__name__,
                    symbol,
                    exc,
                )
                continue
            if snap is not None:
                return snap
        return None


def build_default_multi_venue_adapter(*, timeout_seconds: float = 8.0) -> FundingMultiVenueAdapter:
    """Default-Kaskade Bybit → Binance-Futures mit gemeinsamem Timeout.

    Single-Default-Adapter + Multi-Venue-Fallback: kein Per-Symbol-Routing,
    weil beide Venues dieselbe Symbol-Konvention nutzen und der Fallback die
    Coverage-Lücken (Bybit-only / Binance-only) ohnehin abdeckt. Per-Symbol-
    venue-pinning wäre Overengineering für Phase 1 — TODO Phase 2, falls eine
    Venue systematisch falsche Werte für eine Symbol-Klasse liefert.
    """
    # Lokale Imports: nur der Refresh-Service zieht echte httpx-Adapter; der
    # Loop-Pfad (Disk-Adapter) braucht keine Venue-Imports.
    from app.market_data.binance_futures_adapter import BinanceFuturesAdapter
    from app.market_data.bybit_adapter import BybitAdapter

    timeout_int = max(1, int(round(timeout_seconds)))
    return FundingMultiVenueAdapter(
        [
            BybitAdapter(timeout_seconds=timeout_int),
            BinanceFuturesAdapter(timeout_seconds=timeout_int),
        ]
    )


# ── Platten-Snapshot-Store ─────────────────────────────────────────────────


class FundingSnapshotStore:
    """Atomare JSON-Persistenz für Funding-Snapshots (key = canonical symbol).

    Format::

        {"schema": 1, "written_at_utc": "...", "snapshots": {"BTC/USDT": {...}}}

    Der Refresh-Service ruft ``write_many``; der Loop ruft ``read_all`` /
    ``read``. Lese-Fehler (Datei fehlt / korrupt) ⇒ leeres Dict, kein raise:
    der Loop läuft dann ohne Funding-Evidence weiter.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def write_many(self, snapshots: Iterable[FundingRateSnapshot]) -> int:
        payload: dict[str, Any] = {
            "schema": _SCHEMA_VERSION,
            "written_at_utc": datetime.now(UTC).isoformat(),
            "snapshots": {snap.symbol: asdict(snap) for snap in snapshots},
        }
        count = len(payload["snapshots"])
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomar schreiben: temp + os.replace → der Loop liest nie eine
        # halb-geschriebene Datei (kein Crash bei Race mit dem Refresh).
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".funding_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp_name, self._path)
        except Exception:
            # Aufräumen, dann hochreichen — der Refresh-Service loggt den Fehler.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return count

    def read_all(self) -> dict[str, FundingRateSnapshot]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("[funding-store] corrupt snapshot file: %s", self._path)
            return {}
        if not isinstance(data, dict):
            return {}
        snaps = data.get("snapshots")
        if not isinstance(snaps, dict):
            return {}
        out: dict[str, FundingRateSnapshot] = {}
        for sym, body in snaps.items():
            if not isinstance(body, dict):
                continue
            try:
                out[str(sym)] = FundingRateSnapshot(
                    symbol=str(body["symbol"]),
                    timestamp_utc=str(body["timestamp_utc"]),
                    rate=float(body["rate"]),
                    mark_price=_as_opt_float(body.get("mark_price")),
                    index_price=_as_opt_float(body.get("index_price")),
                    next_funding_time_utc=_as_opt_str(body.get("next_funding_time_utc")),
                    source=str(body.get("source", "unknown")),
                )
            except (KeyError, TypeError, ValueError):
                # Eine kaputte Zeile darf nicht den ganzen Store unlesbar machen.
                continue
        return out

    def read(self, symbol: str) -> FundingRateSnapshot | None:
        return self.read_all().get(symbol.strip().upper())


def _as_opt_float(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _as_opt_str(raw: Any) -> str | None:
    if raw is None:
        return None
    return str(raw)


# ── Disk-Adapter (Loop-Pfad) ───────────────────────────────────────────────


class DiskFundingAdapter:
    """``get_funding_rate``, das den warmen Snapshot von Platte liest.

    Das ist der Adapter, den der Loop dem ``FundingEvidenceCache`` gibt:
    ``cache.refresh(symbol)`` wird damit ein reiner Disk-Read (kein Netz),
    sodass die existierende Cache-Mechanik unverändert funktioniert, aber
    ohne den Loop zu blockieren.

    Caching auf Datei-mtime: bei wiederholten ``get_funding_rate``-Aufrufen
    im selben Tick wird die Datei nur dann neu geparst, wenn sich mtime
    geändert hat — innerhalb eines one-shot-Ticks i. d. R. genau ein Parse.
    """

    def __init__(self, store: FundingSnapshotStore) -> None:
        self._store = store
        self._cached_mtime: float | None = None
        self._cached: dict[str, FundingRateSnapshot] = {}

    async def get_funding_rate(self, symbol: str) -> FundingRateSnapshot | None:
        try:
            mtime = self._store.path.stat().st_mtime
        except (OSError, FileNotFoundError):
            return None
        if mtime != self._cached_mtime:
            self._cached = self._store.read_all()
            self._cached_mtime = mtime
        return self._cached.get(symbol.strip().upper())


# ── Shadow-Log (measure-first) ─────────────────────────────────────────────


def append_funding_shadow_log(
    path: Path | str,
    *,
    symbol: str,
    rate: float,
    direction: str,
    source: str,
    source_trust: float,
    evidence_value: float,
    evidence_direction_aligned: int,
) -> None:
    """Append-only read-only Mess-Spur für Funding-Evidence-Beiträge.

    Schreibt EINE JSON-Zeile pro Evidence-Beitragung — damit messbar ist,
    ob/wie Funding die Confidence verschiebt, BEVOR voller Trust vergeben
    wird. Fail-safe: Schreibfehler werden geloggt + verschluckt (der
    Shadow-Log darf den Signal-Pfad nie killen).
    """
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "rate": rate,
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
        logger.warning("[funding-shadow] append failed: %s", exc)


__all__ = [
    "DiskFundingAdapter",
    "FundingMultiVenueAdapter",
    "FundingSnapshotStore",
    "append_funding_shadow_log",
    "build_default_multi_venue_adapter",
]


# Kept for monotonic-time staleness checks if a caller needs them (refresh service).
def monotonic_now() -> float:
    return time.monotonic()
