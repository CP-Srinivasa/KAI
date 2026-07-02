"""Verdrahtung der Open-Interest-Evidence in den SignalGenerator (Phase 2).

Spiegelt ``funding_wiring`` exakt:

  - ``oi_evidence.enabled is False`` (default) ⇒ ``None`` ⇒ kein Provider ⇒
    Generator unverändert.
  - ``enabled is True`` ⇒ ein Provider, der
      1. den *warmen* OI-Snapshot von Platte liest (kein Loop-Netz-I/O),
      2. ``price_move_aligned_with_signal`` aus ``MarketDataPoint`` +
         ``direction`` ableitet,
      3. die Evidence über die verifizierte
         ``build_open_interest_evidence`` baut,
      4. jeden Beitrag read-only in den OI-Shadow-Log schreibt.

Der vorberechnete ``oi_change_zscore`` kommt aus dem Refresh — der Provider
rechnet hier KEINE Serie, kein Netz: nur Disk-Read + Alignment + Library-Call.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from app.core.domain.document import AnalysisResult
from app.core.settings import OpenInterestEvidenceSettings
from app.market_data.models import MarketDataPoint
from app.signals.bayesian_confidence import Evidence, build_open_interest_evidence
from app.signals.generator import ExtraEvidencesProvider
from app.signals.models import SignalDirection
from app.signals.oi_snapshot_store import (
    OpenInterestSnapshotStore,
    append_oi_shadow_log,
)

logger = logging.getLogger(__name__)


def price_move_aligned_with_signal(change_pct: float, direction: SignalDirection) -> bool:
    """``True`` wenn die Preisbewegung die Signalrichtung bestätigt.

    aligned = (Preis hoch ∧ LONG) ∨ (Preis runter ∧ SHORT).

    Flacher Preis (change == 0) gilt als NICHT aligned: ohne Preisbestätigung
    soll ein OI-Anstieg nicht als Bestätigung zählen (konservativ — der OI-
    Anstieg wird dann von ``build_open_interest_evidence`` als contra gewertet).
    """
    if change_pct > 0:
        return direction == SignalDirection.LONG
    if change_pct < 0:
        return direction == SignalDirection.SHORT
    return False


def build_oi_evidence_provider(
    settings: OpenInterestEvidenceSettings,
) -> ExtraEvidencesProvider | None:
    """Liefere den OI-Provider — oder ``None`` wenn disabled.

    Synchron, nur Disk-Read. Das OI-Fetching + die z-score-Berechnung
    passieren entkoppelt im Refresh-Service.
    """
    if not settings.enabled:
        return None

    store = OpenInterestSnapshotStore(settings.snapshot_path)
    ttl_seconds = settings.ttl_seconds
    source_trust = settings.source_trust
    shadow_path = settings.shadow_log_path

    def _provider(
        _analysis: AnalysisResult,
        market_data: MarketDataPoint,
        direction: SignalDirection,
    ) -> Sequence[Evidence]:
        snap = store.read(market_data.symbol)
        if snap is None:
            return ()
        if _is_stale(snap.timestamp_utc, ttl_seconds):
            return ()
        aligned = price_move_aligned_with_signal(market_data.change_pct_24h, direction)
        evidence = build_open_interest_evidence(
            oi_change_zscore=snap.oi_change_zscore,
            price_move_aligned_with_signal=aligned,
            source_trust=source_trust,
            source_id=snap.source,
        )
        append_oi_shadow_log(
            shadow_path,
            symbol=market_data.symbol,
            oi_change_zscore=snap.oi_change_zscore,
            price_move_aligned=aligned,
            direction=direction.value if hasattr(direction, "value") else str(direction),
            source=snap.source,
            source_trust=source_trust,
            evidence_value=evidence.value,
            evidence_direction_aligned=evidence.direction_aligned,
        )
        return [evidence]

    logger.info(
        "[oi-wiring] open-interest-evidence provider WIRED (trust=%.2f, ttl=%.0fs, snapshot=%s)",
        source_trust,
        ttl_seconds,
        settings.snapshot_path,
    )
    return _provider


def _is_stale(timestamp_utc: str, ttl_seconds: float) -> bool:
    try:
        observed = datetime.fromisoformat(timestamp_utc)
    except (ValueError, TypeError):
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - observed).total_seconds()
    return age > ttl_seconds


__all__ = ["build_oi_evidence_provider", "price_move_aligned_with_signal"]
