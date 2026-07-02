"""Wiring of the L2 on-chain (fee/mempool) evidence into the SignalGenerator.

Mirrors ``app.signals.funding_wiring`` (default-off contract), but with the
B-003 difference: it is **direction-agnostic**. The provider reads KAI's OWN warm
L1 fee-shadow stream (Disk-Read, no loop network I/O), computes raw fee/mempool
percentile features, writes them to the shadow log (measure-first), and returns an
INERT evidence (``direction_aligned=0``) — zero sizing impact until
``scripts/evaluate_l2_evidence.py`` learns a direction (and the operator promotes
trust on proof).

  - ``settings.enabled is False`` (default) ⇒ ``None`` ⇒ SignalGenerator unchanged.
  - ``enabled is True`` ⇒ a provider that is fail-safe at every gate: missing /
    stale stream, or too little history ⇒ empty sequence (no evidence).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from app.core.domain.document import AnalysisResult
from app.core.evidence_settings import L2OnChainEvidenceSettings
from app.market_data.models import MarketDataPoint
from app.signals.bayesian_confidence import Evidence, build_l2_onchain_evidence
from app.signals.generator import ExtraEvidencesProvider
from app.signals.l2_features import (
    append_l2_shadow_log,
    compute_l2_features,
    read_onchain_fee_shadow,
)
from app.signals.models import SignalDirection

logger = logging.getLogger(__name__)


def build_l2_onchain_evidence_provider(
    settings: L2OnChainEvidenceSettings,
) -> ExtraEvidencesProvider | None:
    """Return the L2 on-chain provider — or ``None`` when disabled.

    Synchronous, Disk-Read only (no network in the loop); the L1 fee-shadow
    scheduler keeps the source stream warm out-of-band.
    """
    if not settings.enabled:
        return None

    stream_path = settings.stream_path
    shadow_path = settings.shadow_log_path
    source_trust = settings.source_trust
    window = settings.window
    min_window = settings.min_window
    ttl_seconds = settings.ttl_seconds

    def _provider(
        _analysis: AnalysisResult,
        market_data: MarketDataPoint,
        direction: SignalDirection,
    ) -> Sequence[Evidence]:
        records = read_onchain_fee_shadow(stream_path, limit=window + 1)
        if not records:
            return ()
        current = records[-1]
        history = records[:-1]
        # Fail-safe staleness gate: a stale stream (L1 scheduler down) yields no
        # features — never measure on dead on-chain data.
        if _is_stale(current.get("ts"), ttl_seconds):
            return ()
        # Need enough history for a meaningful percentile.
        if len(history) < min_window:
            return ()

        fee_raw = current.get("fee_sat_vb")
        features = compute_l2_features(
            history,
            fee_sat_vb=float(fee_raw) if fee_raw is not None else None,
            mempool_tx=int(current.get("mempool_tx", 0) or 0),
        )
        direction_str = direction.value if hasattr(direction, "value") else str(direction)
        # measure-first: raw features only (B-003) — no pre-chosen direction.
        append_l2_shadow_log(
            shadow_path,
            symbol=market_data.symbol,
            direction=direction_str,
            features=features,
            source_trust=source_trust,
        )
        evidence = build_l2_onchain_evidence(
            fee_percentile=features.fee_percentile,
            mempool_percentile=features.mempool_percentile,
            direction_aligned=0,  # B-003: undetermined → inert until eval learns it
            source_trust=source_trust,
            source_id="l2_onchain",
        )
        return [evidence]

    logger.info(
        "[l2-wiring] L2 on-chain evidence provider WIRED (trust=%.2f, ttl=%.0fs, stream=%s) "
        "— shadow-only, direction-agnostic",
        source_trust,
        ttl_seconds,
        stream_path,
    )
    return _provider


def _is_stale(timestamp_utc: object, ttl_seconds: float) -> bool:
    """True if the latest stream record is older than ttl. Unparseable/absent ts ⇒
    conservatively STALE (no measurement on an untrustworthy timestamp)."""
    if not isinstance(timestamp_utc, str) or not timestamp_utc:
        return True
    try:
        observed = datetime.fromisoformat(timestamp_utc)
    except (ValueError, TypeError):
        return True
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - observed).total_seconds() > ttl_seconds


__all__ = ["build_l2_onchain_evidence_provider"]
