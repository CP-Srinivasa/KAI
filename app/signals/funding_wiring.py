"""Verdrahtung der Funding-Evidence in den SignalGenerator (Goal V5 Phase 1).

Eine Stelle, an der ``FundingEvidenceSettings`` → ein
``bayes_extra_evidences_provider`` übersetzt wird. Default-off-Vertrag:

  - ``funding_evidence.enabled is False`` (default) ⇒ ``None`` zurück ⇒
    ``build_bayes_signal_kwargs`` bekommt keinen Provider ⇒ Generator
    verhält sich exakt wie vor dieser Schicht. KEIN Verhaltenswechsel.
  - ``enabled is True`` ⇒ ein Provider, der:
      1. den *warmen* Funding-Snapshot von Platte liest (kein Loop-Netz-I/O),
      2. die Funding-Evidence über die verifizierte Library-Funktion
         ``build_funding_rate_evidence`` baut (Units: rate ist Fraction im
         Snapshot, ``*100`` → pct passiert hier GENAU EINMAL),
      3. jeden Beitrag read-only in den Shadow-Log schreibt (measure-first).

Bewusste Entscheidung: der Provider ruft ``build_funding_rate_evidence``
direkt statt ``FundingEvidenceCache.make_provider()``. Grund: der
``FundingEvidenceCache`` ist auf *async refresh + in-memory* ausgelegt; im
one-shot-Loop liegt der warme Wert bereits auf Platte. Den Cache nur zum
Halten eines schon-warmen Snapshots zu missbrauchen würde private-State-
Poking erfordern. Die Library-Funktion ist die verifizierte SSOT für die
Funding→Evidence-Transformation (inkl. der ``*100``-Skalierung) — wir nutzen
exakt sie, mit identischer Signatur wie der Cache-Provider.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.core.domain.document import AnalysisResult
from app.core.settings import FundingEvidenceSettings
from app.market_data.models import MarketDataPoint
from app.signals.bayesian_confidence import Evidence, build_funding_rate_evidence
from app.signals.funding_snapshot_store import (
    FundingSnapshotStore,
    append_funding_shadow_log,
)
from app.signals.generator import ExtraEvidencesProvider
from app.signals.models import SignalDirection

logger = logging.getLogger(__name__)


def build_funding_evidence_provider(
    settings: FundingEvidenceSettings,
) -> ExtraEvidencesProvider | None:
    """Liefere den Funding-Provider — oder ``None`` wenn disabled.

    Der zurückgegebene Provider ist synchron und macht NUR einen Disk-Read,
    kein Netz-I/O. Das eigentliche Funding-Fetching passiert entkoppelt im
    Refresh-Service (``scripts/funding_cache_refresh.py``).
    """
    if not settings.enabled:
        return None

    store = FundingSnapshotStore(settings.snapshot_path)
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
        # Fail-safe Staleness-Gate: ein veralteter Snapshot (Refresh-Service
        # ausgefallen) liefert KEINE Evidence — kein Trade auf totem Funding.
        if _is_stale(snap.timestamp_utc, ttl_seconds):
            return ()
        evidence = build_funding_rate_evidence(
            funding_rate_pct=snap.rate * 100.0,  # rate ist Fraction → pct (EINMAL)
            signal_is_long=(direction == SignalDirection.LONG),
            source_trust=source_trust,
            source_id=snap.source,
        )
        # measure-first: read-only Mess-Spur.
        append_funding_shadow_log(
            shadow_path,
            symbol=market_data.symbol,
            rate=snap.rate,
            direction=direction.value if hasattr(direction, "value") else str(direction),
            source=snap.source,
            source_trust=source_trust,
            evidence_value=evidence.value,
            evidence_direction_aligned=evidence.direction_aligned,
        )
        return [evidence]

    logger.info(
        "[funding-wiring] funding-evidence provider WIRED (trust=%.2f, ttl=%.0fs, snapshot=%s)",
        source_trust,
        ttl_seconds,
        settings.snapshot_path,
    )
    return _provider


def _is_stale(timestamp_utc: str, ttl_seconds: float) -> bool:
    """True wenn der Snapshot älter als ttl ist. Unparsbarer Timestamp ⇒
    konservativ NICHT stale (die Datei-mtime-Frische ist die harte Grenze;
    diese Prüfung ist die zusätzliche Funding-Cadence-Grenze)."""
    from datetime import UTC, datetime

    try:
        observed = datetime.fromisoformat(timestamp_utc)
    except (ValueError, TypeError):
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - observed).total_seconds()
    return age > ttl_seconds


__all__ = ["build_funding_evidence_provider"]
