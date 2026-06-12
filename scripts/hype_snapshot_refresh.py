#!/usr/bin/env python3
"""Entkoppelter Hype-Snapshot-Refresh (HYPE-S1).

Aggregiert die BEREITS VORHANDENEN analysierten Dokumente (Sentiment +
``crypto_assets``-Tags) zu per-Asset Hype-Scores (``app.risk.hype_score``)
und schreibt sie atomar in den ``HypeSnapshotStore``
(default ``artifacts/hype_cache.json``).

Warum ein eigener Service statt inline im Trading-Loop
======================================================
Identische Begründung wie ``funding_cache_refresh``: der Loop ist ein
cron-one-shot; eine DB-Aggregation über 7 Tage Dokumente pro Tick wäre ein
Latenz-/Hängerisiko im Signal-Pfad. Stattdessen wärmt dieser Service
periodisch eine kleine JSON-Datei; der Loop liest nur diese warme Datei
(schneller Disk-Read, kein DB-Zugriff). Hype bewegt sich auf Stunden-Skala —
ein Refresh alle 15 min ist mehr als ausreichend.

Bounded / fail-safe
===================
- EINE bounded DB-Query (Spalten-Subset, ``refresh_max_documents``-Limit,
  nur analysierte Dokumente im Baseline-Zeitraum mit Asset-Tags).
- Globaler ``asyncio.wait_for``-Deckel → der Service hängt nie unbegrenzt.
- Liefert die Aggregation KEIN Asset, bleibt die alte Snapshot-Datei
  unverändert (kein Leerschreiben → der Loop liest den letzten bekannten
  Stand bis TTL-Ablauf; danach fail-safe keine Evidence).

Default-disabled: der systemd-Timer ist installiert aber nicht enabled. Der
Loop verdrahtet Hype ohnehin nur bei ``hype_evidence.enabled=True``.

Exit codes: 0 ok (auch bei 0 Assets), 2 unerwarteter Fehler.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.settings import get_settings  # noqa: E402
from app.risk.hype_score import (  # noqa: E402
    DocMention,
    HypeScoreConfig,
    aggregate_hype_inputs,
    compute_hype_score,
)
from app.signals.hype_snapshot_store import HypeSnapshot, HypeSnapshotStore  # noqa: E402
from app.storage.db.session import build_session_factory  # noqa: E402
from app.storage.models.document import CanonicalDocumentModel  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("hype_snapshot_refresh")

# Harte Obergrenze für den Gesamtlauf, damit der Service nie hängt.
_GLOBAL_DEADLINE_SECONDS = 120.0


async def _load_mentions(now: datetime, config: HypeScoreConfig, max_rows: int) -> list[DocMention]:
    """Bounded Read der analysierten Dokumente im Baseline-Zeitraum."""
    settings = get_settings()
    session_factory = build_session_factory(settings.db)
    cutoff = now - timedelta(days=config.baseline_days, hours=config.recent_window_hours)

    stmt = (
        select(
            CanonicalDocumentModel.published_at,
            CanonicalDocumentModel.fetched_at,
            CanonicalDocumentModel.source_name,
            CanonicalDocumentModel.sentiment_label,
            CanonicalDocumentModel.crypto_assets,
        )
        .where(
            CanonicalDocumentModel.is_analyzed.is_(True),
            CanonicalDocumentModel.is_duplicate.is_(False),
            CanonicalDocumentModel.fetched_at >= cutoff,
            CanonicalDocumentModel.crypto_assets.is_not(None),
        )
        .order_by(CanonicalDocumentModel.fetched_at.desc())
        .limit(max_rows)
    )

    mentions: list[DocMention] = []
    async with session_factory() as session:
        rows = (await session.execute(stmt)).all()
    for published_at, fetched_at, source_name, sentiment_label, crypto_assets in rows:
        observed = published_at or fetched_at
        if observed is None:
            continue
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        assets = tuple(str(a) for a in (crypto_assets or []) if isinstance(a, str) and a.strip())
        if not assets:
            continue
        mentions.append(
            DocMention(
                observed_at=observed,
                source_name=source_name,
                sentiment_label=sentiment_label,
                assets=assets,
            )
        )
    return mentions


async def _refresh() -> int:
    settings = get_settings()
    he = settings.hype_evidence
    config = HypeScoreConfig(
        recent_window_hours=he.recent_window_hours,
        baseline_days=he.baseline_days,
        min_mentions=he.min_mentions,
        velocity_saturation=he.velocity_saturation,
        breadth_saturation=he.breadth_saturation,
    )
    store = HypeSnapshotStore(he.snapshot_path)
    now = datetime.now(UTC)

    logger.info(
        "[hype-refresh] start window=%.0fh baseline=%.0fd max_rows=%d snapshot=%s",
        he.recent_window_hours,
        he.baseline_days,
        he.refresh_max_documents,
        he.snapshot_path,
    )

    mentions = await _load_mentions(now, config, he.refresh_max_documents)
    per_asset = aggregate_hype_inputs(mentions, now=now, config=config)
    if not per_asset:
        logger.warning("[hype-refresh] 0 assets with recent mentions — old snapshot kept")
        return 0

    snapshots: list[HypeSnapshot] = []
    for asset, inputs in sorted(per_asset.items()):
        result = compute_hype_score(inputs, config)
        snapshots.append(
            HypeSnapshot(
                asset=asset,
                timestamp_utc=now.isoformat(),
                hype_score=result.score,
                velocity_ratio=result.velocity_ratio,
                mentions_recent=inputs.mentions_recent,
                distinct_sources_recent=inputs.distinct_sources_recent,
                one_sidedness=result.one_sidedness,
                insufficient_data=result.insufficient_data,
            )
        )
        if result.score > 0:
            logger.info("[hype-refresh] %s", result.rationale)

    written = store.write_many(snapshots)
    logger.info("[hype-refresh] wrote %d snapshots → %s", written, he.snapshot_path)
    return 0


def main() -> int:
    try:
        return asyncio.run(asyncio.wait_for(_refresh(), timeout=_GLOBAL_DEADLINE_SECONDS))
    except TimeoutError:
        logger.warning(
            "[hype-refresh] global deadline %ss hit — old snapshot kept",
            _GLOBAL_DEADLINE_SECONDS,
        )
        return 0
    except Exception:  # noqa: BLE001
        logger.exception("[hype-refresh] unexpected error")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
