"""Real-analysis paper-learning SELECTOR (Goal 2026-06-10).

Pure, read-only selection of REAL analysed documents (long AND short) that are
eligible to feed the paper-learning stream as ``source=real_analysis``. It is the
twin selector to the shadow-path ``real_analysis_provider.select_pending`` but
adds the full directional QUALITY-gate chain and a paper-only bearish unblock.

Design constraints honoured here (Red-Team):
- B-001: it does NOT parametrise the shared ``evaluate_directional_eligibility``.
  Bullish/neutral run the strict public function; bearish runs the EXTRACTED
  shared ``evaluate_directional_quality_gates`` directly — the D-142 mode-block is
  merely SKIPPED for the paper-only feeder, every other quality gate stays active
  and the dispatch/metrics path remains strictly bearish-blocked.
- B-002: source attribution is hard ``real_analysis`` (the caller tags the cycle).
- B-004: freshness cutoff + dedup per document_id "latest wins".
- It performs NO execution, NO DB write, NO entry_mode touch. The actual paper
  fill is owned by the loop's run_cycle decoupled path, gated by the fail-closed
  three-arm override in ``app/execution/real_analysis_paper.py``.

The synthetic control-plane probes never enter here: they are not stored
documents, and even a stray ``loop_control_*`` id is rejected as non-directional /
non-eligible upstream and additionally hard-excluded at the loop gate.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.alerts.eligibility import (
    evaluate_directional_eligibility,
    evaluate_directional_quality_gates,
)
from app.core.domain.document import AnalysisResult, CanonicalDocument
from app.core.enums import SentimentLabel
from app.observability.real_analysis_provider import (
    _symbol_for,
    canonical_to_analysis_result,
)

logger = logging.getLogger(__name__)

_DIRECTIONAL_LABELS = (SentimentLabel.BULLISH, SentimentLabel.BEARISH)


@dataclass(frozen=True)
class RealAnalysisCandidate:
    """An eligible real-analysis document ready to feed the paper loop."""

    document_id: str
    symbol: str
    direction: str  # "long" | "short"
    analysis: AnalysisResult


def _is_fresh(doc: CanonicalDocument, *, now: datetime, max_age_hours: int) -> bool:
    """True iff the document was published within ``max_age_hours`` of ``now``.

    A document without a ``published_at`` is treated as NOT fresh — stale or
    timeless analyses do not produce honest forward-learning data.
    """
    pub = doc.published_at
    if pub is None:
        return False
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=UTC)
    return pub >= (now - timedelta(hours=max_age_hours))


def _sentiment_str(doc: CanonicalDocument) -> str:
    return doc.sentiment_label.value if doc.sentiment_label else ""


def _passes_quality_gates(doc: CanonicalDocument, *, min_priority: int) -> tuple[bool, str | None]:
    """Run the directional quality gates for one document.

    - Bullish: the strict public ``evaluate_directional_eligibility`` (no D-142
      interaction for bullish).
    - Bearish: the extracted shared ``evaluate_directional_quality_gates`` — the
      D-142 mode-block is skipped (paper-only feeder) but every other gate
      (priority, low-precision source, promo, weak, reactive narrative,
      asymmetric bearish confidence, asset resolution) still applies.

    Paper-Learning P3 (Goal 2026-06-10): the D-122 LOW_PRIORITY gate is
    parametrised for THIS feeder path only via ``low_priority_max=min_priority-1``
    (block ``<= min_priority-1`` ⇔ block ``< min_priority``). The dispatch/metrics
    callers never pass this and keep the hard ``<=7``. The other quality gates are
    untouched.

    Returns ``(eligible, block_reason)``. ``block_reason`` is None when eligible.
    """
    sentiment = _sentiment_str(doc).lower()
    assets = list(doc.tickers or []) or list(getattr(doc, "crypto_assets", []) or [])
    low_priority_max = min_priority - 1
    if sentiment == "bearish":
        decision = evaluate_directional_quality_gates(
            sentiment="bearish",
            affected_assets=assets,
            sentiment_score=doc.sentiment_score,
            impact_score=doc.impact_score,
            title=doc.title,
            directional_confidence=doc.directional_confidence,
            priority=doc.priority_score,
            source_name=doc.source_name,
            low_priority_max=low_priority_max,
        )
    else:
        decision = evaluate_directional_eligibility(
            sentiment_label=sentiment,
            affected_assets=assets,
            sentiment_score=doc.sentiment_score,
            impact_score=doc.impact_score,
            title=doc.title,
            directional_confidence=doc.directional_confidence,
            actionable=bool(doc.priority_score and doc.directional_confidence),
            priority=doc.priority_score,
            source_name=doc.source_name,
            low_priority_max=low_priority_max,
        )
    if decision.directional_eligible is True:
        return True, None
    return False, decision.directional_block_reason or "blocked"


def select_real_analysis_candidates(
    docs: Iterable[CanonicalDocument],
    *,
    freshness_max_age_hours: int,
    min_priority: int = 10,
    now: datetime | None = None,
) -> tuple[list[RealAnalysisCandidate], dict[str, int]]:
    """Pure selection over already-fetched analysed docs. No DB / no IO.

    Order of filters (fail-closed, cheapest first):
      seen → stale → non_directional → no_symbol → quality-gate(reason) → eligible.

    Dedup (B-004): documents are processed newest-first by published_at; the
    FIRST eligible candidate per document_id wins and later duplicates of the
    same id are counted as ``duplicate``. Returns (candidates, funnel counters).

    Paper-Learning P3 (Goal 2026-06-10): ``min_priority`` is the feeder's
    min-allowed-priority (default 10 ⇒ strict, current 0-fill behaviour). It is
    forwarded to the Gate-1 LOW_PRIORITY override; only this feeder path is
    affected.
    """
    now_utc = now or datetime.now(UTC)
    ordered = sorted(
        docs,
        key=lambda d: d.published_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    funnel: dict[str, int] = {
        "seen": 0,
        "stale": 0,
        "duplicate": 0,
        "non_directional": 0,
        "no_symbol": 0,
        "quality_blocked": 0,
        "eligible": 0,
    }
    out: list[RealAnalysisCandidate] = []
    seen_ids: set[str] = set()
    for doc in ordered:
        funnel["seen"] += 1
        doc_id = str(doc.id)
        if doc_id in seen_ids:
            funnel["duplicate"] += 1
            continue
        seen_ids.add(doc_id)
        if not _is_fresh(doc, now=now_utc, max_age_hours=freshness_max_age_hours):
            funnel["stale"] += 1
            continue
        if doc.sentiment_label not in _DIRECTIONAL_LABELS:
            funnel["non_directional"] += 1
            continue
        symbol = _symbol_for(doc)
        if symbol is None:
            funnel["no_symbol"] += 1
            continue
        ok, _reason = _passes_quality_gates(doc, min_priority=min_priority)
        if not ok:
            funnel["quality_blocked"] += 1
            continue
        funnel["eligible"] += 1
        direction = "short" if doc.sentiment_label == SentimentLabel.BEARISH else "long"
        out.append(
            RealAnalysisCandidate(
                document_id=doc_id,
                symbol=symbol,
                direction=direction,
                analysis=canonical_to_analysis_result(doc),
            )
        )
    return out, funnel


__all__ = [
    "RealAnalysisCandidate",
    "select_real_analysis_candidates",
]
