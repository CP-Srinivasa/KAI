"""Verdrahtung der Hype-Evidence in den SignalGenerator (HYPE-S1).

Spiegelt ``funding_wiring`` / ``ls_wiring`` exakt:

  - ``hype_evidence.enabled is False`` (default) ⇒ ``None`` ⇒ kein Provider ⇒
    Generator unverändert. KEIN Verhaltenswechsel ohne Operator-Opt-in.
  - ``enabled is True`` ⇒ ein Provider, der
      1. den *warmen* Hype-Snapshot von Platte liest (kein DB-/Netz-I/O im
         Loop; geschrieben vom entkoppelten ``hype_snapshot_refresh``),
      2. die contrarian-Evidence über die verifizierte
         ``build_sentiment_overheat_evidence`` baut (``signal_is_long`` aus
         der ``direction``, ``dampen_only`` aus den Settings),
      3. JEDEN Lookup read-only in den Hype-Shadow-Log schreibt — auch wenn
         keine Evidence emittiert wird (Score unter Schwelle / dampen_only-
         Short). Measure-first: die Shadow-Spur ist die Datenbasis für die
         spätere Trust-/Schwellwert-Entscheidung.

Emissions-Gate (S1-Sicherheitsvertrag): Evidence geht nur in die Engine, wenn
``hype_score ≥ min_score_for_evidence`` UND die Factory eine Richtung gesetzt
hat (Long-Dämpfung; Shorts bleiben unter ``dampen_only`` unberührt).
Snapshots mit ``insufficient_data=True`` emittieren nie.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from app.core.domain.document import AnalysisResult
from app.core.evidence_settings import HypeEvidenceSettings
from app.market_data.models import MarketDataPoint
from app.signals.bayesian_confidence import Evidence, build_sentiment_overheat_evidence
from app.signals.generator import ExtraEvidencesProvider
from app.signals.hype_snapshot_store import HypeSnapshotStore, append_hype_shadow_log
from app.signals.models import SignalDirection

logger = logging.getLogger(__name__)


def build_hype_evidence_provider(
    settings: HypeEvidenceSettings,
) -> ExtraEvidencesProvider | None:
    """Liefere den Hype-Provider — oder ``None`` wenn disabled.

    Synchron, nur Disk-Read. Die Aggregation passiert entkoppelt im
    Refresh-Service.
    """
    if not settings.enabled:
        return None

    store = HypeSnapshotStore(settings.snapshot_path)
    ttl_seconds = settings.ttl_seconds
    source_trust = settings.source_trust
    shadow_path = settings.shadow_log_path
    dampen_only = settings.dampen_only
    min_score = settings.min_score_for_evidence

    def _provider(
        _analysis: AnalysisResult,
        market_data: MarketDataPoint,
        direction: SignalDirection,
    ) -> Sequence[Evidence]:
        snap = store.read(market_data.symbol)
        if snap is None:
            return ()
        # Fail-safe Staleness-Gate: veralteter Snapshot (Refresh ausgefallen)
        # liefert KEINE Evidence — keine Dämpfung auf totem Hype-Stand.
        if _is_stale(snap.timestamp_utc, ttl_seconds):
            return ()
        signal_is_long = direction == SignalDirection.LONG
        evidence = build_sentiment_overheat_evidence(
            hype_score=snap.hype_score,
            signal_is_long=signal_is_long,
            dampen_only=dampen_only,
            source_trust=source_trust,
            source_id=snap.source,
        )
        emit = (
            not snap.insufficient_data
            and snap.hype_score >= min_score
            and evidence.direction_aligned != 0
        )
        append_hype_shadow_log(
            shadow_path,
            symbol=market_data.symbol,
            hype_score=snap.hype_score,
            direction=direction.value if hasattr(direction, "value") else str(direction),
            source=snap.source,
            source_trust=source_trust,
            evidence_emitted=emit,
            evidence_value=evidence.value,
            evidence_direction_aligned=evidence.direction_aligned,
        )
        return [evidence] if emit else ()

    logger.info(
        "[hype-wiring] sentiment-overheat provider WIRED "
        "(trust=%.2f, ttl=%.0fs, min_score=%.2f, dampen_only=%s, snapshot=%s)",
        source_trust,
        ttl_seconds,
        min_score,
        dampen_only,
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


__all__ = ["build_hype_evidence_provider"]
