import json
from pathlib import Path

from app.core.enums import SentimentLabel
from app.research.datasets import export_training_data
from tests.unit.factories import make_document


def test_export_training_data(tmp_path: Path):
    doc1 = make_document(
        title="Test Report",
        raw_text="Test content text.",
        is_analyzed=True,
        priority_score=8,
        sentiment_label=SentimentLabel.BULLISH,
        relevance_score=0.9,
    )
    doc1.provider = "openai"

    out_file = tmp_path / "test.jsonl"
    count = export_training_data([doc1], out_file)

    assert count == 1
    assert out_file.exists()

    with out_file.open("r", encoding="utf-8") as f:
        line = f.readline()
        data = json.loads(line)

        assert "messages" in data
        assert "metadata" in data
        assert data["metadata"]["document_id"] == str(doc1.id)
        assert data["metadata"]["provider"] == "openai"

        messages = data["messages"]
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "Test content text." in messages[1]["content"]
        assert messages[2]["role"] == "assistant"

        # Verify the target scores were serialized
        target = json.loads(messages[2]["content"])
        assert target["priority_score"] == 8
        assert target["sentiment_label"] == "bullish"
        assert target["relevance_score"] == 0.9

def test_export_training_data_skips_unanalyzed(tmp_path: Path):
    doc1 = make_document(is_analyzed=False)
    out_file = tmp_path / "test.jsonl"
    count = export_training_data([doc1], out_file)
    assert count == 0
