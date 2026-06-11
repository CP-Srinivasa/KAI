#!/usr/bin/env python3
"""Entkoppelter Funding+OI Refresh-Service (Goal V5 Phase 1+2).

Zieht die aktuellen Perp-Funding-Raten (Bybit-first → Binance-Futures-
Fallback) für eine bounded Symbol-Liste und schreibt sie atomar in den
``FundingSnapshotStore`` (default ``artifacts/funding_cache.json``).

Phase 2: zieht zusätzlich die Open-Interest-**Zeitreihe** je Symbol, berechnet
den ``oi_change_zscore`` (latest-Δ vs rolling mean/std über ``zscore_window``)
und schreibt OI-Snapshots in den separaten ``OpenInterestSnapshotStore``
(default ``artifacts/oi_cache.json``). Der z-score wird HIER vorberechnet, der
Loop liest nur den Skalar — kein Loop-Bottleneck. Eigene Datei, weil OI/Funding
verschiedene Kadenz/TTL haben (orthogonal).

Warum ein eigener Service statt inline im Trading-Loop
======================================================
Der Trading-Loop ist ein cron-one-shot (frischer Prozess pro Tick). Würde
er pro Zyklus synchron Funding über viele Symbole ziehen, wäre das ein
Latenz-Flaschenhals + ein Hängerisiko bei langsamer Exchange. Stattdessen:
dieser Service wärmt periodisch eine kleine JSON-Datei; der Loop liest nur
diese warme Datei (schneller Disk-Read, kein Netz). Funding ändert sich auf
8h-Skala — ein Refresh alle paar Minuten ist mehr als ausreichend.

Bounded / fail-safe
===================
- Per-Symbol-HTTP-Timeout über die Adapter (``refresh_timeout_seconds``).
- Globaler ``asyncio.wait_for``-Deckel über den Gesamtlauf → der Service
  hängt nie unbegrenzt, selbst wenn eine Venue tot ist.
- Jeder Symbol-Fehler ⇒ Symbol wird übersprungen (kein Abbruch).
- Wird KEINE Funding-Rate aufgelöst, bleibt die alte Snapshot-Datei
  unverändert (kein Leerschreiben → der Loop liest weiter den letzten
  bekannten Stand bis TTL-Ablauf).

Symbol-Universe: ``APP_FUNDING_REFRESH_SYMBOLS`` (CSV) override; sonst ein
konservativer Default (BTC/ETH/SOL). Bewusst NICHT an das Diversification-
Universe gekoppelt — das wäre Scope-Creep für Phase 1.

Default-disabled: der systemd-Timer ist installiert aber nicht enabled. Der
Loop verdrahtet Funding ohnehin nur bei ``funding_evidence.enabled=True``.

Exit codes: 0 ok (auch bei 0 aufgelösten Symbolen), 2 unerwarteter Fehler.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.core.settings import get_settings  # noqa: E402
from app.market_data.models import (  # noqa: E402
    FundingRateSnapshot,
    OpenInterestSnapshot,
)
from app.signals.funding_snapshot_store import (  # noqa: E402
    FundingSnapshotStore,
    build_default_multi_venue_adapter,
)
from app.signals.oi_snapshot_store import (  # noqa: E402
    OpenInterestSnapshotStore,
    build_default_oi_multi_venue_adapter,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("funding_cache_refresh")

_DEFAULT_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
# Harte Obergrenze für den Gesamtlauf, damit der Service nie hängt.
_GLOBAL_DEADLINE_SECONDS = 120.0


def _resolve_symbols() -> list[str]:
    raw = os.environ.get("APP_FUNDING_REFRESH_SYMBOLS", "").strip()
    if not raw:
        return list(_DEFAULT_SYMBOLS)
    syms = [s.strip().upper() for s in raw.split(",") if s.strip()]
    return syms or list(_DEFAULT_SYMBOLS)


async def _refresh(symbols: list[str], timeout_seconds: float) -> list[FundingRateSnapshot]:
    adapter = build_default_multi_venue_adapter(timeout_seconds=timeout_seconds)
    out: list[FundingRateSnapshot] = []
    for sym in symbols:
        try:
            snap = await adapter.get_funding_rate(sym)
        except Exception as exc:  # noqa: BLE001 — ein Symbol darf den Lauf nie killen
            logger.warning("[funding-refresh] %s failed: %s", sym, exc)
            continue
        if snap is None:
            logger.info("[funding-refresh] %s → no funding (skipped)", sym)
            continue
        out.append(snap)
        logger.info(
            "[funding-refresh] %s rate=%.6f source=%s",
            snap.symbol,
            snap.rate,
            snap.source,
        )
    return out


async def _refresh_oi(
    symbols: list[str], timeout_seconds: float, *, interval: str, window: int
) -> list[OpenInterestSnapshot]:
    adapter = build_default_oi_multi_venue_adapter(
        timeout_seconds=timeout_seconds, interval=interval, window=window
    )
    out: list[OpenInterestSnapshot] = []
    for sym in symbols:
        try:
            snap = await adapter.get_open_interest(sym)
        except Exception as exc:  # noqa: BLE001 — ein Symbol darf den Lauf nie killen
            logger.warning("[oi-refresh] %s failed: %s", sym, exc)
            continue
        if snap is None:
            logger.info("[oi-refresh] %s → no open-interest (skipped)", sym)
            continue
        out.append(snap)
        logger.info(
            "[oi-refresh] %s oi=%.4f z=%.3f source=%s",
            snap.symbol,
            snap.open_interest,
            snap.oi_change_zscore,
            snap.source,
        )
    return out


def _run_funding(symbols: list[str]) -> int:
    settings = get_settings()
    fe = settings.funding_evidence
    store = FundingSnapshotStore(fe.snapshot_path)

    logger.info(
        "[funding-refresh] start symbols=%s timeout=%.1fs snapshot=%s",
        symbols,
        fe.refresh_timeout_seconds,
        fe.snapshot_path,
    )
    try:
        snaps = asyncio.run(
            asyncio.wait_for(
                _refresh(symbols, fe.refresh_timeout_seconds),
                timeout=_GLOBAL_DEADLINE_SECONDS,
            )
        )
    except TimeoutError:
        logger.warning(
            "[funding-refresh] global deadline %ss hit — old snapshot kept",
            _GLOBAL_DEADLINE_SECONDS,
        )
        return 0
    except Exception:  # noqa: BLE001
        logger.exception("[funding-refresh] unexpected error")
        return 2

    if not snaps:
        logger.warning("[funding-refresh] 0 symbols resolved — old snapshot kept")
        return 0

    written = store.write_many(snaps)
    logger.info("[funding-refresh] wrote %d snapshots → %s", written, fe.snapshot_path)
    return 0


def _run_oi(symbols: list[str]) -> int:
    settings = get_settings()
    oi = settings.oi_evidence
    store = OpenInterestSnapshotStore(oi.snapshot_path)

    logger.info(
        "[oi-refresh] start symbols=%s timeout=%.1fs window=%d interval=%s snapshot=%s",
        symbols,
        oi.refresh_timeout_seconds,
        oi.zscore_window,
        oi.interval,
        oi.snapshot_path,
    )
    try:
        snaps = asyncio.run(
            asyncio.wait_for(
                _refresh_oi(
                    symbols,
                    oi.refresh_timeout_seconds,
                    interval=oi.interval,
                    window=oi.zscore_window,
                ),
                timeout=_GLOBAL_DEADLINE_SECONDS,
            )
        )
    except TimeoutError:
        logger.warning(
            "[oi-refresh] global deadline %ss hit — old snapshot kept",
            _GLOBAL_DEADLINE_SECONDS,
        )
        return 0
    except Exception:  # noqa: BLE001
        logger.exception("[oi-refresh] unexpected error")
        return 2

    if not snaps:
        logger.warning("[oi-refresh] 0 symbols resolved — old snapshot kept")
        return 0

    written = store.write_many(snaps)
    logger.info("[oi-refresh] wrote %d snapshots → %s", written, oi.snapshot_path)
    return 0


def main() -> int:
    symbols = _resolve_symbols()
    # Funding + OI are warmed in one unit but write SEPARATE caches. A failure
    # in one must not block the other (orthogonal evidences). Worst exit wins.
    rc_funding = _run_funding(symbols)
    rc_oi = _run_oi(symbols)
    return max(rc_funding, rc_oi)


if __name__ == "__main__":
    raise SystemExit(main())
