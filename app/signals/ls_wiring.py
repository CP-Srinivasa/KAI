"""Verdrahtung der Long/Short-Ratio-Evidence in den SignalGenerator (Phase 3).

Spiegelt ``oi_wiring`` exakt:

  - ``ls_evidence.enabled is False`` (default) ⇒ ``None`` ⇒ kein Provider ⇒
    Generator unverändert.
  - ``enabled is True`` ⇒ ein Provider, der
      1. den *warmen* L/S-Snapshot von Platte liest (kein Loop-Netz-I/O),
      2. die contrarian-Evidence über die verifizierte
         ``build_long_short_ratio_evidence`` baut (``signal_is_long`` aus der
         ``direction``),
      3. jeden Beitrag read-only in den L/S-Shadow-Log schreibt.

Anders als OI braucht L/S kein ``price_move_aligned`` — die contrarian-Semantik
hängt nur von ``long_account_ratio`` (crowd) und der Signalrichtung ab. Der
Provider rechnet KEINE Serie, kein Netz: nur Disk-Read + Library-Call.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from app.core.domain.document import AnalysisResult
from app.core.settings import LongShortRatioEvidenceSettings
from app.market_data.models import MarketDataPoint
from app.signals.bayesian_confidence import Evidence, build_long_short_ratio_evidence
from app.signals.generator import ExtraEvidencesProvider
from app.signals.ls_snapshot_store import (
    LongShortRatioSnapshotStore,
    append_ls_shadow_log,
)
from app.signals.models import SignalDirection

logger = logging.getLogger(__name__)


def build_ls_evidence_provider(
    settings: LongShortRatioEvidenceSettings,
) -> ExtraEvidencesProvider | None:
    """Liefere den L/S-Provider — oder ``None`` wenn disabled.

    Synchron, nur Disk-Read. Das L/S-Fetching passiert entkoppelt im
    Refresh-Service.
    """
    if not settings.enabled:
        return None

    store = LongShortRatioSnapshotStore(settings.snapshot_path)
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
        signal_is_long = direction == SignalDirection.LONG
        evidence = build_long_short_ratio_evidence(
            long_account_ratio=snap.long_account_ratio,
            signal_is_long=signal_is_long,
            source_trust=source_trust,
            source_id=snap.source,
        )
        append_ls_shadow_log(
            shadow_path,
            symbol=market_data.symbol,
            long_account_ratio=snap.long_account_ratio,
            direction=direction.value if hasattr(direction, "value") else str(direction),
            source=snap.source,
            source_trust=source_trust,
            evidence_value=evidence.value,
            evidence_direction_aligned=evidence.direction_aligned,
        )
        return [evidence]

    logger.info(
        "[ls-wiring] long-short-ratio-evidence provider WIRED (trust=%.2f, ttl=%.0fs, snapshot=%s)",
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


__all__ = ["build_ls_evidence_provider"]
