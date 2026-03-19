import json

import pytest

from app.core.enums import SentimentLabel
from app.research.evaluation import (
    EvaluationMetrics,
    EvaluationReport,
    compare_datasets,
    compare_outputs,
    load_jsonl,
)
from tests.unit.factories import make_document


def test_compare_outputs_identical_documents():
    doc1 = make_document(
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BULLISH,
        relevance_score=0.9,
        impact_score=0.8,
        novelty_score=0.7
    )
    doc2 = doc1.model_copy()

    result = compare_outputs([doc1], [doc2])

    assert result.document_count == 1
    assert result.sentiment_accuracy == 1.0
    assert result.actionable_accuracy == 1.0
    assert result.priority_mse == 0.0
    assert result.relevance_mse == 0.0

def test_compare_outputs_different_documents():
    doc1 = make_document(
        is_analyzed=True,
        priority_score=8,
        sentiment_label=SentimentLabel.BULLISH,
        relevance_score=0.8,
        impact_score=0.8,
        novelty_score=0.8
    )

    doc2 = doc1.model_copy()
    doc2.priority_score = 6
    doc2.sentiment_label = SentimentLabel.BEARISH
    doc2.relevance_score = 0.4

    result = compare_outputs([doc1], [doc2])

    assert result.document_count == 1
    assert result.sentiment_accuracy == 0.0
    assert result.actionable_accuracy == 0.0  # doc1 is 8 (actionable), doc2 is 6 (not)
    assert result.priority_mse == 4.0  # (8-6)^2
    assert result.relevance_mse == pytest.approx(0.16)  # (0.8-0.4)^2

def test_compare_outputs_mismatched_ids():
    doc1 = make_document()
    doc2 = make_document()

    with pytest.raises(ValueError, match="Document ID mismatch"):
        compare_outputs([doc1], [doc2])


# ---------------------------------------------------------------------------
# Sprint 6 — JSONL-based compare_datasets
# ---------------------------------------------------------------------------


def _make_jsonl_row(
    doc_id: str,
    sentiment_label: str = "bullish",
    priority_score: int = 7,
    relevance_score: float = 0.8,
    impact_score: float = 0.6,
    tags: list[str] | None = None,
    analysis_source: str = "external_llm",
    provider: str = "openai",
) -> dict:
    target = {
        "affected_assets": [],
        "impact_score": impact_score,
        "market_scope": "crypto",
        "novelty_score": 0.5,
        "priority_score": priority_score,
        "relevance_score": relevance_score,
        "sentiment_label": sentiment_label,
        "sentiment_score": 0.7,
        "spam_probability": 0.05,
        "summary": "",
        "tags": tags or [],
    }
    return {
        "messages": [
            {"role": "system", "content": "You are a highly precise financial AI analyst."},
            {"role": "user", "content": "Analyze..."},
            {"role": "assistant", "content": json.dumps(target, sort_keys=True)},
        ],
        "metadata": {
            "document_id": doc_id,
            "provider": provider,
            "analysis_source": analysis_source,
        },
    }


def test_compare_datasets_identical_rows():
    row = _make_jsonl_row("doc-1", sentiment_label="bullish", priority_score=8)
    report = compare_datasets([row], [row], dataset_type="rule_baseline")

    assert isinstance(report, EvaluationReport)
    assert isinstance(report.metrics, EvaluationMetrics)
    assert report.metrics.sentiment_agreement == 1.0
    assert report.metrics.priority_mae == 0.0
    assert report.metrics.relevance_mae == pytest.approx(0.0)
    assert report.metrics.impact_mae == pytest.approx(0.0)
    assert report.metrics.tag_overlap_mean == 1.0
    assert report.metrics.sample_count == 1
    assert report.metrics.missing_pairs == 0
    assert report.paired_count == 1
    assert report.dataset_type == "rule_baseline"


def test_compare_datasets_sentiment_disagreement():
    teacher = _make_jsonl_row("doc-1", sentiment_label="bullish")
    baseline = _make_jsonl_row("doc-1", sentiment_label="neutral", analysis_source="rule")

    report = compare_datasets([teacher], [baseline])

    assert report.metrics.sentiment_agreement == 0.0
    assert report.metrics.sample_count == 1


def test_compare_datasets_priority_mae():
    teacher = _make_jsonl_row("doc-1", priority_score=8)
    baseline = _make_jsonl_row("doc-1", priority_score=5, analysis_source="rule")

    report = compare_datasets([teacher], [baseline])

    assert report.metrics.priority_mae == pytest.approx(3.0)


def test_compare_datasets_relevance_mae():
    teacher = _make_jsonl_row("doc-1", relevance_score=0.9)
    baseline = _make_jsonl_row("doc-1", relevance_score=0.4, analysis_source="rule")

    report = compare_datasets([teacher], [baseline])

    assert report.metrics.relevance_mae == pytest.approx(0.5)


def test_compare_datasets_tag_overlap_jaccard():
    teacher = _make_jsonl_row("doc-1", tags=["btc", "etf", "halving"])
    baseline = _make_jsonl_row("doc-1", tags=["btc", "defi"], analysis_source="rule")
    # intersection={"btc"}, union={"btc","etf","halving","defi"} → 1/4 = 0.25

    report = compare_datasets([teacher], [baseline])

    assert report.metrics.tag_overlap_mean == pytest.approx(0.25)


def test_compare_datasets_missing_pairs():
    teacher = _make_jsonl_row("doc-1")
    baseline_unmatched = _make_jsonl_row("doc-99", analysis_source="rule")

    report = compare_datasets([teacher], [baseline_unmatched])

    assert report.metrics.sample_count == 0
    assert report.metrics.missing_pairs == 1
    assert report.paired_count == 0


def test_compare_datasets_multiple_rows_partial_match():
    teacher_rows = [
        _make_jsonl_row("doc-1", priority_score=9),
        _make_jsonl_row("doc-2", priority_score=7),
    ]
    baseline_rows = [
        _make_jsonl_row("doc-1", priority_score=6, analysis_source="rule"),
        _make_jsonl_row("doc-99", priority_score=4, analysis_source="rule"),  # no match
    ]

    report = compare_datasets(teacher_rows, baseline_rows, dataset_type="rule_baseline")

    assert report.teacher_count == 2
    assert report.baseline_count == 2
    assert report.paired_count == 1
    assert report.metrics.sample_count == 1
    assert report.metrics.missing_pairs == 1
    assert report.metrics.priority_mae == pytest.approx(3.0)  # |9-6|


def test_compare_datasets_empty_tags_both_sides():
    teacher = _make_jsonl_row("doc-1", tags=[])
    baseline = _make_jsonl_row("doc-1", tags=[], analysis_source="internal")

    report = compare_datasets([teacher], [baseline], dataset_type="internal_benchmark")

    assert report.metrics.tag_overlap_mean == 1.0  # both empty → full agreement


def test_load_jsonl(tmp_path):
    rows = [
        _make_jsonl_row("doc-1"),
        _make_jsonl_row("doc-2"),
    ]
    out_file = tmp_path / "test.jsonl"
    with out_file.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    loaded = load_jsonl(out_file)

    assert len(loaded) == 2
    assert loaded[0]["metadata"]["document_id"] == "doc-1"
    assert loaded[1]["metadata"]["document_id"] == "doc-2"
