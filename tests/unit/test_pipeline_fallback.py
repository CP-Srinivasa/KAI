"""Tests for rule-only (no-LLM) pipeline path and I-12 defensive guardrails.

Covers:
- Rule-only analysis (provider=None) produces a complete AnalysisResult
- apply_to_document() writes scores and entities when llm_output=None
- Priority ceiling for rule-only docs (I-13: ceiling ~5, no signal candidates without boost)
- I-12: analysis_result=None must never result in status=ANALYZED
"""

from __future__ import annotations

import asyncio

import pytest

from app.analysis.keywords.engine import KeywordEngine
from app.analysis.keywords.watchlist import WatchlistEntry
from app.analysis.pipeline import AnalysisPipeline, PipelineResult
from app.core.domain.document import CanonicalDocument
from app.core.enums import DocumentStatus
from app.core.signals import extract_signal_candidates

# ── helpers ───────────────────────────────────────────────────────────────────


def _engine_with_btc() -> KeywordEngine:
    return KeywordEngine(
        keywords=frozenset({"halving", "etf", "regulation"}),
        watchlist_entries=[
            WatchlistEntry(
                symbol="BTC",
                name="Bitcoin",
                aliases=frozenset({"bitcoin"}),
                tags=(),
                category="crypto",
            )
        ],
        entity_aliases=[],
    )


def _make_doc(
    title: str = "Bitcoin halving",
    text: str = "BTC ETF halving update",
) -> CanonicalDocument:
    return CanonicalDocument(url="https://example.com/fallback-test", title=title, raw_text=text)


# ── rule-only pipeline path ────────────────────────────────────────────────────


def test_rule_only_analysis_result_is_never_none():
    """provider=None must still produce a complete AnalysisResult via fallback."""
    engine = _engine_with_btc()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=None)
    result = asyncio.run(pipeline.run(_make_doc()))

    assert result.success
    assert result.llm_output is None
    assert result.analysis_result is not None
    assert result.analysis_result.document_id == str(result.document.id)


def test_apply_to_document_writes_scores_without_llm_output():
    """apply_to_document() writes priority, relevance, credibility when llm_output=None."""
    engine = _engine_with_btc()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=None)
    doc = _make_doc()
    result = asyncio.run(pipeline.run(doc))
    assert result.analysis_result is not None

    result.apply_to_document()

    assert doc.priority_score is not None
    assert doc.relevance_score is not None
    assert doc.credibility_score is not None
    assert doc.spam_probability is not None
    assert doc.novelty_score is not None


def test_apply_to_document_writes_tickers_and_entities_without_llm():
    """Keyword-matched assets are merged into doc.tickers even in rule-only mode."""
    engine = _engine_with_btc()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=None)
    doc = _make_doc("Bitcoin halving analysis", "BTC ETF announcement")
    result = asyncio.run(pipeline.run(doc))

    result.apply_to_document()

    assert "BTC" in doc.tickers
    assert len(doc.entity_mentions) > 0


def test_rule_only_priority_ceiling_is_at_most_five(mocker):
    """I-13: Rule-only analysis must not produce priority > 5 without a watchlist boost."""
    engine = _engine_with_btc()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=None)

    # Use a doc with strong keyword coverage to push scores as high as possible
    doc = CanonicalDocument(
        url="https://example.com/ceiling-test",
        title="Bitcoin BTC halving ETF regulation",
        raw_text="bitcoin btc halving etf regulation " * 10,
    )
    result = asyncio.run(pipeline.run(doc))
    result.apply_to_document()

    assert doc.priority_score is not None
    assert doc.priority_score <= 5, (
        f"Rule-only priority {doc.priority_score} exceeds ceiling of 5 (I-13)"
    )


def test_rule_only_doc_does_not_produce_signal_candidate_at_default_threshold():
    """I-13: Rule-only docs must not cross the default signal threshold of 8."""
    engine = _engine_with_btc()
    pipeline = AnalysisPipeline(keyword_engine=engine, provider=None)
    doc = CanonicalDocument(
        url="https://example.com/rule-signal-test",
        title="Bitcoin halving ETF",
        raw_text="bitcoin btc halving etf " * 10,
        is_analyzed=True,
    )
    result = asyncio.run(pipeline.run(doc))
    result.apply_to_document()
    doc.is_analyzed = True

    candidates = extract_signal_candidates([doc], min_priority=8)
    assert candidates == [], (
        f"Rule-only doc with priority {doc.priority_score} must not produce signals at threshold 8"
    )


# ── I-12 defensive guard ───────────────────────────────────────────────────────


def test_i12_pipeline_result_with_none_analysis_result_does_not_mutate_document():
    """I-12: PipelineResult with analysis_result=None must not write scores to document.

    When a broken or missing provider returns no result, apply_to_document() must
    leave the document in its pre-analysis state — scores must not be zeroed out
    or partially written.
    """
    doc = CanonicalDocument(
        url="https://example.com/no-result",
        title="No result doc",
        priority_score=None,
        relevance_score=None,
    )
    # Simulate a PipelineResult where analysis_result is None (defensive edge case)
    result = PipelineResult(document=doc, analysis_result=None, error=None)
    result.apply_to_document()

    # Scores must remain unset — no partial mutation
    assert doc.priority_score is None
    assert doc.relevance_score is None
    assert doc.sentiment_label is None


@pytest.mark.asyncio
async def test_i12_analyze_pending_write_phase_routes_none_to_failed():
    """I-12: The write phase must call update_status(FAILED) when analysis_result is None.

    Simulates the Phase 3 write loop behavior directly, without invoking the full CLI.
    Verifies that update_analysis is never called and FAILED is set.
    """
    update_analysis_calls: list[str] = []
    update_status_calls: list[tuple[str, DocumentStatus]] = []

    class FakeRepo:
        async def update_analysis(self, doc_id: str, result: object) -> None:
            update_analysis_calls.append(doc_id)

        async def update_status(self, doc_id: str, status: DocumentStatus) -> None:
            update_status_calls.append((doc_id, status))

    doc = CanonicalDocument(url="https://example.com/no-result", title="No result doc")
    pipeline_results = [PipelineResult(document=doc, analysis_result=None, error=None)]
    repo = FakeRepo()

    # Replicate the Phase 3 write logic from analyze_pending
    for res in pipeline_results:
        if not res.success:
            await repo.update_status(str(res.document.id), DocumentStatus.FAILED)
            continue

        res.apply_to_document()

        if res.analysis_result is None:
            await repo.update_status(str(res.document.id), DocumentStatus.FAILED)
            continue

        await repo.update_analysis(str(res.document.id), res.analysis_result)

    # I-12: update_analysis must NOT be called
    assert update_analysis_calls == [], (
        "update_analysis must not be called when analysis_result is None (I-12)"
    )
    # ANALYZED must never be set
    analyzed = [s for _, s in update_status_calls if s == DocumentStatus.ANALYZED]
    assert analyzed == [], "ANALYZED must never be set when analysis_result is None (I-12)"
    # FAILED must be set
    failed = [s for _, s in update_status_calls if s == DocumentStatus.FAILED]
    assert len(failed) == 1, "FAILED must be set exactly once for analysis_result=None (I-12)"
