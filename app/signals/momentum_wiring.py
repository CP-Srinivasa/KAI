"""Wiring of the Momentum-Universe evidence into the SignalGenerator (G3).

Mirrors ``app.signals.l2_wiring`` (default-off, direction-agnostic): the provider
reads KAI's OWN warm universe snapshot (Disk-Read, no loop network I/O), looks up
the symbol's momentum percentile, writes it to the shadow log (measure-first),
and returns an INERT evidence (``direction_aligned`` from settings, default 0) —
zero sizing impact until ``scripts/evaluate_momentum_evidence.py`` learns a sign
and the operator promotes it.

  - ``settings.enabled is False`` (default) ⇒ ``None`` ⇒ SignalGenerator unchanged.
  - ``enabled is True`` ⇒ a provider, fail-safe at every gate: no snapshot, stale
    snapshot, or symbol not in the current universe ⇒ empty sequence.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from app.core.domain.document import AnalysisResult
from app.core.evidence_settings import MomentumUniverseEvidenceSettings
from app.market_data.models import MarketDataPoint
from app.signals.bayesian_confidence import Evidence, build_momentum_evidence
from app.signals.generator import ExtraEvidencesProvider
from app.signals.models import SignalDirection
from app.signals.momentum_evidence_features import (
    append_momentum_shadow_log,
    read_universe_scores,
)

logger = logging.getLogger(__name__)


def build_momentum_evidence_provider(
    settings: MomentumUniverseEvidenceSettings,
) -> ExtraEvidencesProvider | None:
    """Return the momentum-universe provider — or ``None`` when disabled.

    Synchronous, Disk-Read only (no network in the loop); the G0 refresh keeps the
    universe snapshot warm out-of-band.
    """
    if not settings.enabled:
        return None

    ledger_path = settings.ledger_path
    shadow_path = settings.shadow_log_path
    source_trust = settings.source_trust
    direction_aligned = settings.direction_aligned
    ttl_seconds = settings.ttl_seconds

    def _provider(
        _analysis: AnalysisResult,
        market_data: MarketDataPoint,
        direction: SignalDirection,
    ) -> Sequence[Evidence]:
        snap_ts, scores_by_symbol = read_universe_scores(ledger_path)
        if not scores_by_symbol:
            return ()
        # Fail-safe staleness gate: a stale snapshot (G0 refresh down) yields no
        # evidence — never measure on a dead universe.
        if _is_stale(snap_ts, ttl_seconds):
            return ()
        scores = scores_by_symbol.get(market_data.symbol)
        if scores is None:
            return ()  # symbol not in the current universe
        direction_str = direction.value if hasattr(direction, "value") else str(direction)
        append_momentum_shadow_log(
            shadow_path,
            symbol=market_data.symbol,
            direction=direction_str,
            scores=scores,
            source_trust=source_trust,
        )
        evidence = build_momentum_evidence(
            momentum_score=scores.get("momentum_score"),
            direction_aligned=direction_aligned,
            source_trust=source_trust,
            source_id="momentum_universe",
        )
        return [evidence]

    logger.info(
        "[momentum-wiring] momentum evidence provider WIRED (trust=%.2f, dir=%d, ttl=%.0fs) "
        "— shadow-only, direction-agnostic",
        source_trust,
        direction_aligned,
        ttl_seconds,
    )
    return _provider


def _is_stale(timestamp_utc: object, ttl_seconds: float) -> bool:
    """True if the snapshot is older than ttl. Unparseable/absent ts ⇒ STALE."""
    if not isinstance(timestamp_utc, str) or not timestamp_utc:
        return True
    try:
        observed = datetime.fromisoformat(timestamp_utc)
    except (ValueError, TypeError):
        return True
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - observed).total_seconds() > ttl_seconds


__all__ = ["build_momentum_evidence_provider"]
