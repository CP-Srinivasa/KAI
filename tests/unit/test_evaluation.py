import pytest

from app.core.enums import SentimentLabel
from app.research.evaluation import compare_outputs
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
