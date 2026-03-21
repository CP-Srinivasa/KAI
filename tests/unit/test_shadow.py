"""Tests for app/research/shadow.py (Sprint 10, I-51–I-55).

Covers:
- compute_divergence: identical, full mismatch, partial tag overlap, both empty tags
- write_shadow_record: creates valid JSONL, appends multiple records
- run_shadow_batch: calls companion per doc, handles companion error
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.core.domain.document import CanonicalDocument
from app.core.enums import MarketScope, SentimentLabel
from app.research.shadow import (
    ShadowRunRecord,
    compute_divergence,
    run_shadow_batch,
    write_shadow_record,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_doc(
    *,
    sentiment: SentimentLabel = SentimentLabel.BULLISH,
    priority: int = 7,
    relevance: float = 0.8,
    impact: float = 0.6,
    actionable: bool = True,
    tags: list[str] | None = None,
) -> CanonicalDocument:
    # actionable is not a CanonicalDocument field; it lives in metadata
    return CanonicalDocument(
        url="https://example.com/doc",
        title="BTC rally",
        raw_text="Bitcoin surged today.",
        sentiment_label=sentiment,
        priority_score=priority,
        relevance_score=relevance,
        impact_score=impact,
        tags=tags if tags is not None else ["crypto", "btc"],
        provider="openai",
        metadata={"actionable": actionable},
    )


def _make_output(
    *,
    sentiment: SentimentLabel = SentimentLabel.BULLISH,
    priority: int = 7,
    relevance: float = 0.8,
    impact: float = 0.6,
    actionable: bool = True,
    tags: list[str] | None = None,
) -> LLMAnalysisOutput:
    return LLMAnalysisOutput(
        sentiment_label=sentiment,
        sentiment_score=0.7,
        relevance_score=relevance,
        impact_score=impact,
        confidence_score=0.9,
        novelty_score=0.5,
        spam_probability=0.01,
        market_scope=MarketScope.CRYPTO,
        affected_assets=["BTC"],
        affected_sectors=[],
        event_type="price_movement",
        short_reasoning="test",
        long_reasoning="test long",
        actionable=actionable,
        tags=tags if tags is not None else ["crypto", "btc"],
        recommended_priority=priority,
    )


def _make_record(
    doc_id: str = "abc123",
    companion_result: dict | None = None,
    divergence: dict | None = None,
) -> ShadowRunRecord:
    return ShadowRunRecord(
        document_id=doc_id,
        run_at="2026-03-19T12:00:00+00:00",
        primary_provider="openai",
        primary_analysis_source="llm",
        companion_endpoint="http://localhost:11434",
        companion_model="kai-v1",
        primary_result={"sentiment_label": "bullish", "priority_score": 7},
        companion_result=companion_result,
        divergence=divergence,
    )


# ── compute_divergence ────────────────────────────────────────────────────────


def test_compute_divergence_identical_results() -> None:
    """All scores identical → all match, all diffs = 0."""
    doc = _make_doc(sentiment=SentimentLabel.BULLISH, priority=7, relevance=0.8, impact=0.6,
                    actionable=True, tags=["crypto", "btc"])
    output = _make_output(sentiment=SentimentLabel.BULLISH, priority=7, relevance=0.8, impact=0.6,
                          actionable=True, tags=["crypto", "btc"])
    div = compute_divergence(doc, output)
    assert div.sentiment_match is True
    assert div.priority_diff == 0
    assert div.relevance_diff == pytest.approx(0.0)
    assert div.impact_diff == pytest.approx(0.0)
    assert div.actionable_match is True
    assert div.tag_overlap == pytest.approx(1.0)


def test_compute_divergence_full_mismatch() -> None:
    """All scores different → nothing matches, all diffs > 0."""
    doc = _make_doc(sentiment=SentimentLabel.BULLISH, priority=9, relevance=0.9, impact=0.8,
                    actionable=True, tags=["crypto"])
    output = _make_output(sentiment=SentimentLabel.BEARISH, priority=2, relevance=0.1, impact=0.1,
                          actionable=False, tags=["equities"])
    div = compute_divergence(doc, output)
    assert div.sentiment_match is False
    assert div.priority_diff == 7
    assert div.relevance_diff == pytest.approx(0.8)
    assert div.impact_diff == pytest.approx(0.7)
    assert div.actionable_match is False
    assert div.tag_overlap == pytest.approx(0.0)


def test_compute_divergence_tag_overlap_partial() -> None:
    """Jaccard = 0.5 for 1 shared out of 3 total distinct tags."""
    doc = _make_doc(tags=["crypto", "btc"])
    output = _make_output(tags=["crypto", "defi"])
    div = compute_divergence(doc, output)
    # union = {crypto, btc, defi} = 3, intersection = {crypto} = 1 → 1/3
    assert div.tag_overlap == pytest.approx(1 / 3)


def test_compute_divergence_both_tags_empty() -> None:
    """tag_overlap = 0.0 when both tag sets are empty (no ZeroDivisionError)."""
    doc = _make_doc(tags=[])
    output = _make_output(tags=[])
    div = compute_divergence(doc, output)
    assert div.tag_overlap == pytest.approx(0.0)


# ── write_shadow_record ───────────────────────────────────────────────────────


def test_write_shadow_record_creates_valid_jsonl(tmp_path: Path) -> None:
    """write_shadow_record creates a file with one valid JSON line."""
    out = tmp_path / "shadow.jsonl"
    record = _make_record()
    write_shadow_record(record, out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["document_id"] == "abc123"
    assert parsed["primary_provider"] == "openai"


def test_write_shadow_record_canonical_deviations_format(tmp_path: Path) -> None:
    """I-69: write_shadow_record emits canonical 'deviations' (_delta keys) + record_type."""
    out = tmp_path / "shadow.jsonl"
    div = {
        "priority_diff": 3,
        "relevance_diff": 0.1,
        "impact_diff": 0.05,
        "sentiment_match": True,
        "actionable_match": False,
        "tag_overlap": 0.5,
    }
    record = _make_record(divergence=div)
    write_shadow_record(record, out)
    parsed = json.loads(out.read_text(encoding="utf-8").strip())

    assert parsed["record_type"] == "companion_shadow_run"
    assert "deviations" in parsed
    dev = parsed["deviations"]
    assert dev["priority_delta"] == 3
    assert dev["relevance_delta"] == pytest.approx(0.1)
    assert dev["impact_delta"] == pytest.approx(0.05)
    assert dev["sentiment_match"] is True
    assert dev["actionable_match"] is False
    # Deprecated alias still present (backward compat)
    assert "divergence" in parsed


def test_write_shadow_record_null_divergence_no_deviations(tmp_path: Path) -> None:
    """I-69: companion error records (divergence=None) omit deviations field."""
    out = tmp_path / "shadow.jsonl"
    record = _make_record(divergence=None)
    write_shadow_record(record, out)
    parsed = json.loads(out.read_text(encoding="utf-8").strip())

    assert parsed["record_type"] == "companion_shadow_run"
    assert parsed["divergence"] is None
    assert "deviations" not in parsed


def test_write_shadow_record_appends_multiple(tmp_path: Path) -> None:
    """Two writes produce two JSON lines in the same file."""
    out = tmp_path / "shadow.jsonl"
    write_shadow_record(_make_record("doc1"), out)
    write_shadow_record(_make_record("doc2"), out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["document_id"] == "doc1"
    assert json.loads(lines[1])["document_id"] == "doc2"


# ── run_shadow_batch ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_shadow_batch_calls_companion_per_doc(tmp_path: Path) -> None:
    """run_shadow_batch calls companion.analyze() once per document."""
    docs = [_make_doc() for _ in range(3)]
    output = _make_output()
    companion = AsyncMock()
    companion.analyze = AsyncMock(return_value=output)
    companion.endpoint = "http://localhost:11434"
    companion.model = "kai-v1"

    out_path = tmp_path / "shadow.jsonl"
    records = await run_shadow_batch(docs, companion, out_path)

    assert companion.analyze.call_count == 3
    assert len(records) == 3
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        parsed = json.loads(line)
        assert parsed["companion_result"] is not None
        assert parsed["divergence"] is not None


@pytest.mark.asyncio
async def test_run_shadow_batch_handles_companion_error(tmp_path: Path) -> None:
    """Companion error → record written with companion_result=None, batch continues."""
    docs = [_make_doc(), _make_doc()]
    companion = AsyncMock()
    companion.analyze = AsyncMock(side_effect=RuntimeError("endpoint down"))
    companion.endpoint = "http://localhost:11434"
    companion.model = "kai-v1"

    out_path = tmp_path / "shadow.jsonl"
    records = await run_shadow_batch(docs, companion, out_path)

    assert len(records) == 2
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)
        assert parsed["companion_result"] is None
        assert parsed["divergence"] is None
