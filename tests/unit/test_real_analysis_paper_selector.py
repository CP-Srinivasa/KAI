"""Real-analysis paper-learning SELECTOR + injection-seam invariants
(Goal 2026-06-10).

Pins:
  - bearish is paper-eligible via the selector (quality gates active) WHILE the
    dispatch/metrics path stays strictly bearish-blocked (Red-Team B-001),
  - the selector does NOT bypass quality gates (weak/low-priority still blocked),
  - freshness cutoff + dedup "latest wins" (B-004),
  - the injection seam can never reach live (ExecutionMode.PAPER only).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.alerts.eligibility import (
    BLOCK_REASON_BEARISH_DISABLED,
    evaluate_directional_eligibility,
)
from app.core.domain.document import CanonicalDocument
from app.core.enums import ExecutionMode, MarketScope, SentimentLabel
from app.observability.real_analysis_paper_selector import (
    select_real_analysis_candidates,
)
from app.orchestrator.trading_loop import _run_once_guard


def _doc(
    *,
    doc_id: str = "11111111-1111-1111-1111-111111111111",
    sentiment: SentimentLabel = SentimentLabel.BEARISH,
    published_at: datetime | None = None,
    impact: float = 0.90,
    priority: int = 10,
    dconf: float = 0.97,
    sscore: float = -0.9,
    tickers: list[str] | None = None,
) -> CanonicalDocument:
    return CanonicalDocument(
        id=doc_id,
        url=f"https://example.com/{doc_id}",
        title="Major exchange hacked, funds drained",
        sentiment_label=sentiment,
        sentiment_score=sscore,
        impact_score=impact,
        directional_confidence=dconf,
        credibility_score=0.9,
        priority_score=priority,
        market_scope=MarketScope.CRYPTO,
        tickers=tickers if tickers is not None else ["BTC/USDT"],
        published_at=published_at or datetime.now(UTC),
    )


# ── selector vs. dispatch (B-001) ─────────────────────────────────────────────


def test_dispatch_path_keeps_bearish_blocked() -> None:
    """REVERT-invariant: the public eligibility function (dispatch/metrics) still
    hard-blocks bearish — the selector relaxation must not leak here."""
    decision = evaluate_directional_eligibility(
        sentiment_label="bearish",
        affected_assets=["BTC/USDT"],
        sentiment_score=-0.9,
        impact_score=0.9,
        directional_confidence=0.97,
        actionable=True,
        priority=10,
        source_name="coindesk",
    )
    assert decision.directional_eligible is False
    assert decision.directional_block_reason == BLOCK_REASON_BEARISH_DISABLED


def test_selector_makes_strong_bearish_eligible() -> None:
    """A strong bearish doc IS selected for the paper feeder (D-142 skipped for
    paper-only) → direction short."""
    cands, funnel = select_real_analysis_candidates([_doc()], freshness_max_age_hours=48)
    assert len(cands) == 1
    assert cands[0].direction == "short"
    assert cands[0].symbol == "BTC/USDT"
    assert funnel["eligible"] == 1


def test_selector_still_blocks_weak_bearish() -> None:
    """Quality gates remain active: a weak bearish doc is NOT selected."""
    cands, funnel = select_real_analysis_candidates([_doc(impact=0.05)], freshness_max_age_hours=48)
    assert cands == []
    assert funnel["quality_blocked"] == 1


def test_selector_still_blocks_low_priority_bearish() -> None:
    cands, funnel = select_real_analysis_candidates([_doc(priority=7)], freshness_max_age_hours=48)
    assert cands == []
    assert funnel["quality_blocked"] == 1


def test_selector_selects_strong_bullish_too() -> None:
    cands, _ = select_real_analysis_candidates(
        [_doc(sentiment=SentimentLabel.BULLISH, sscore=0.9, dconf=0.85)],
        freshness_max_age_hours=48,
    )
    assert len(cands) == 1
    assert cands[0].direction == "long"


# ── freshness + dedup (B-004) ─────────────────────────────────────────────────


def test_stale_documents_are_excluded() -> None:
    old = datetime.now(UTC) - timedelta(hours=72)
    cands, funnel = select_real_analysis_candidates(
        [_doc(published_at=old)], freshness_max_age_hours=48
    )
    assert cands == []
    assert funnel["stale"] == 1


def test_dedup_latest_wins_per_document_id() -> None:
    now = datetime.now(UTC)
    same_id = "22222222-2222-2222-2222-222222222222"
    newer = _doc(doc_id=same_id, published_at=now)
    older = _doc(doc_id=same_id, published_at=now - timedelta(hours=1))
    cands, funnel = select_real_analysis_candidates([older, newer], freshness_max_age_hours=48)
    assert len(cands) == 1
    assert funnel["duplicate"] == 1


def test_non_directional_excluded() -> None:
    cands, funnel = select_real_analysis_candidates(
        [_doc(sentiment=SentimentLabel.NEUTRAL)], freshness_max_age_hours=48
    )
    assert cands == []
    assert funnel["non_directional"] == 1


def test_no_symbol_excluded() -> None:
    cands, funnel = select_real_analysis_candidates([_doc(tickers=[])], freshness_max_age_hours=48)
    assert cands == []
    assert funnel["no_symbol"] == 1


# ── Paper-Learning P3: feeder min_priority threshold (Goal 2026-06-10) ────────


def test_p3_feeder_default_strict_blocks_p5_bearish() -> None:
    """Default min_priority=10 keeps the feeder strict: a P5 bearish doc is
    blocked at Gate 1 (the current 0-fill behaviour)."""
    cands, funnel = select_real_analysis_candidates([_doc(priority=5)], freshness_max_age_hours=48)
    assert cands == []
    assert funnel["quality_blocked"] == 1


def test_p3_feeder_threshold5_unblocks_p5_bearish() -> None:
    """With min_priority=5 a P5 bearish doc becomes eligible (block < 5)."""
    cands, funnel = select_real_analysis_candidates(
        [_doc(priority=5)], freshness_max_age_hours=48, min_priority=5
    )
    assert len(cands) == 1
    assert cands[0].direction == "short"
    assert funnel["eligible"] == 1


def test_p3_feeder_threshold5_unblocks_p5_bullish() -> None:
    """Long side too: a P5 bullish doc becomes eligible at min_priority=5."""
    cands, funnel = select_real_analysis_candidates(
        [_doc(sentiment=SentimentLabel.BULLISH, sscore=0.9, dconf=0.85, priority=5)],
        freshness_max_age_hours=48,
        min_priority=5,
    )
    assert len(cands) == 1
    assert cands[0].direction == "long"
    assert funnel["eligible"] == 1


def test_p3_feeder_threshold5_still_blocks_p4() -> None:
    """min_priority=5 blocks strictly below 5: a P4 doc is still blocked."""
    cands, funnel = select_real_analysis_candidates(
        [_doc(priority=4)], freshness_max_age_hours=48, min_priority=5
    )
    assert cands == []
    assert funnel["quality_blocked"] == 1


def test_p3_gate1_dispatch_path_unchanged_p7_blocked_p8_eligible() -> None:
    """Gate-1 INVARIANT: the public dispatch/metrics eligibility function, called
    WITHOUT low_priority_max, keeps the hard <=7 — P7 blocked, P8 eligible.
    Uses bullish to isolate the LOW_PRIORITY gate from D-142."""
    from app.alerts.eligibility import BLOCK_REASON_LOW_PRIORITY

    p7 = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.9,
        impact_score=0.9,
        directional_confidence=0.85,
        actionable=True,
        priority=7,
        source_name="coindesk",
    )
    assert p7.directional_eligible is False
    assert p7.directional_block_reason == BLOCK_REASON_LOW_PRIORITY

    p8 = evaluate_directional_eligibility(
        sentiment_label="bullish",
        affected_assets=["BTC/USDT"],
        sentiment_score=0.9,
        impact_score=0.9,
        directional_confidence=0.85,
        actionable=True,
        priority=8,
        source_name="coindesk",
    )
    assert p8.directional_eligible is True


# ── live-unreachable invariant at the injection seam ──────────────────────────


@pytest.mark.parametrize("mode", [ExecutionMode.PAPER, ExecutionMode.SHADOW])
def test_run_once_guard_allows_paper_and_shadow(mode: ExecutionMode) -> None:
    allowed, _ = _run_once_guard(mode)
    assert allowed is True


@pytest.mark.parametrize("mode", [ExecutionMode.LIVE])
def test_run_once_guard_refuses_live(mode: ExecutionMode) -> None:
    allowed, reason = _run_once_guard(mode)
    assert allowed is False
    assert reason is not None
