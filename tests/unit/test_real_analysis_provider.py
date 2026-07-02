"""NEO-P-002-r3 — RealAnalysisProvider (mapper + eligibility + dedup) tests."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.domain.document import CanonicalDocument, SentimentLabel
from app.observability.real_analysis_provider import (
    canonical_to_analysis_result,
    is_eligible,
    mark_fed,
    select_pending,
)


def _doc(
    doc_id: str | None = None,
    *,
    tickers: list[str] | None = None,
    directional: float = 0.7,
    priority: int | None = 8,
    sentiment: SentimentLabel = SentimentLabel.BULLISH,
) -> CanonicalDocument:
    did = doc_id or str(uuid.uuid4())
    return CanonicalDocument(
        id=did,
        url=f"https://example.test/{did}",
        title=f"Title {did}",
        sentiment_label=sentiment,
        sentiment_score=0.6,
        relevance_score=0.8,
        impact_score=0.7,
        novelty_score=0.5,
        credibility_score=0.9,
        spam_probability=0.1,
        directional_confidence=directional,
        priority_score=priority,
        tickers=list(tickers) if tickers is not None else ["BTC"],
    )


def test_mapper_produces_valid_analysis_result() -> None:
    d = _doc(tickers=["ETH"], directional=0.8, priority=9)
    ar = canonical_to_analysis_result(d)
    assert ar.document_id == str(d.id)
    assert ar.sentiment_label == SentimentLabel.BULLISH
    assert ar.recommended_priority == 9
    assert ar.directional_confidence == 0.8
    assert ar.affected_assets == ["ETH"]
    # confidence_score proxy = credibility_score (0.9), clamped 0..1
    assert ar.confidence_score == 0.9
    # required reasoning strings are never empty (strict model would reject)
    assert ar.explanation_short and ar.explanation_long


def _doc_no_quality(doc_id: str | None = None) -> CanonicalDocument:
    """A doc with NEITHER credibility_score NOR spam_probability persisted."""
    did = doc_id or str(uuid.uuid4())
    return CanonicalDocument(
        id=did,
        url=f"https://example.test/{did}",
        title=f"Title {did}",
        sentiment_label=SentimentLabel.BULLISH,
        directional_confidence=0.8,
        priority_score=8,
        tickers=["BTC"],
        # credibility_score and spam_probability intentionally left unset (None)
    )


def test_unknown_confidence_maps_to_floor_not_max() -> None:
    """unknown/unknown must NEVER become confidence 1.0 — an unknown score may not
    read as maximal confidence (would bias the edge measurement optimistically)."""
    d = _doc_no_quality()
    ar = canonical_to_analysis_result(d)
    assert ar.confidence_score == 0.0  # conservative floor, not 1.0


def test_spam_only_still_derives_confidence() -> None:
    """Regression: with credibility None but spam persisted, confidence = 1 - spam."""
    did = str(uuid.uuid4())
    d = CanonicalDocument(
        id=did,
        url=f"https://example.test/{did}",
        title=f"Title {did}",
        sentiment_label=SentimentLabel.BULLISH,
        directional_confidence=0.8,
        priority_score=8,
        tickers=["BTC"],
        spam_probability=0.2,  # credibility_score stays None
    )
    ar = canonical_to_analysis_result(d)
    assert ar.confidence_score == pytest.approx(0.8)


def test_unknown_confidence_is_ineligible() -> None:
    """A doc with no calibratable confidence signal must not reach the generator."""
    assert is_eligible(_doc_no_quality()) == (False, "no_confidence_signal")


def test_select_pending_funnels_no_confidence_signal(tmp_path: Path) -> None:
    ledger = tmp_path / "fed.jsonl"
    cands, funnel = select_pending([_doc_no_quality()], fed_ledger_path=ledger)
    assert cands == []
    assert funnel["no_confidence_signal"] == 1
    assert funnel["eligible"] == 0


def test_clamp_helpers_are_defensive() -> None:
    # The source CanonicalDocument enforces ranges, but the mapper clamps
    # defensively so a raw/loaded out-of-range value never raises in the strict
    # AnalysisResult model.
    from app.observability.real_analysis_provider import _clamp01, _clamp_signed

    assert _clamp01(1.5) == 1.0
    assert _clamp01(-0.2) == 0.0
    assert _clamp01(None) == 0.0
    assert _clamp_signed(-2.0) == -1.0
    assert _clamp_signed(2.0) == 1.0


def test_eligibility_requires_symbol_and_direction() -> None:
    assert is_eligible(_doc(tickers=["BTC"], directional=0.7))[0] is True
    assert is_eligible(_doc(tickers=[], directional=0.7)) == (False, "no_symbol")
    assert is_eligible(_doc(tickers=["BTC"], directional=0.0)) == (False, "non_directional")


def test_select_pending_funnel_and_dedup(tmp_path: Path) -> None:
    ledger = tmp_path / "fed.jsonl"
    ok1 = _doc(tickers=["BTC"], directional=0.7)
    ok2 = _doc(tickers=["ETH"], directional=0.6)
    docs = [ok1, ok2, _doc(tickers=[], directional=0.7), _doc(tickers=["SOL"], directional=0.0)]
    cands, funnel = select_pending(docs, fed_ledger_path=ledger)
    assert funnel["seen"] == 4
    assert funnel["no_symbol"] == 1
    assert funnel["non_directional"] == 1
    assert funnel["eligible"] == 2
    assert {c.symbol for c in cands} == {"BTC/USDT", "ETH/USDT"}

    # idempotency: marking ok1 as fed removes it from the next selection
    mark_fed(ok1.id, path=ledger)
    cands2, funnel2 = select_pending(docs, fed_ledger_path=ledger)
    assert funnel2["already_fed"] == 1
    assert funnel2["eligible"] == 1
    assert {c.symbol for c in cands2} == {"ETH/USDT"}
