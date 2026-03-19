import json
from pathlib import Path

from app.core.enums import MarketScope, SentimentLabel
from app.research.datasets import export_training_data
from tests.unit.factories import make_document


def _read_jsonl_row(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        return json.loads(handle.readline())


def test_export_training_data_exports_structured_targets_and_metadata(tmp_path: Path) -> None:
    doc = make_document(
        title="Test Report",
        raw_text="Test content text.",
        summary="ETF inflows remain supportive.",
        ai_tags=["etf", "bitcoin"],
        tickers=["BTC", "BTC"],
        crypto_assets=["ETH"],
        is_analyzed=True,
        priority_score=8,
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.7,
        relevance_score=0.9,
        impact_score=0.6,
        novelty_score=0.4,
        spam_probability=0.1,
        market_scope=MarketScope.ETF,
    )
    doc.provider = "openai"

    out_file = tmp_path / "training.jsonl"
    count = export_training_data([doc], out_file)

    assert count == 1
    row = _read_jsonl_row(out_file)

    assert row["metadata"] == {
        "document_id": str(doc.id),
        "provider": "openai",
        "analysis_source": "external_llm",
    }

    messages = row["messages"]
    assert isinstance(messages, list)
    assert len(messages) == 3
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "Test content text." in messages[1]["content"]
    assert messages[2]["role"] == "assistant"

    target = json.loads(messages[2]["content"])
    assert "co_thought" not in target
    assert target == {
        "affected_assets": ["BTC", "ETH"],
        "impact_score": 0.6,
        "market_scope": "etf",
        "novelty_score": 0.4,
        "priority_score": 8,
        "relevance_score": 0.9,
        "sentiment_label": "bullish",
        "sentiment_score": 0.7,
        "spam_probability": 0.1,
        "summary": "ETF inflows remain supportive.",
        "tags": ["etf", "bitcoin"],
    }


def test_export_training_data_skips_unanalyzed(tmp_path: Path) -> None:
    doc = make_document(raw_text="Should not export", is_analyzed=False)

    out_file = tmp_path / "training.jsonl"
    count = export_training_data([doc], out_file)

    assert count == 0


def test_export_training_data_exports_rule_based_defaults_without_optional_reasoning(
    tmp_path: Path,
) -> None:
    doc = make_document(
        raw_text="Rule-based analyzed text.",
        is_analyzed=True,
        provider=None,
        sentiment_label=SentimentLabel.NEUTRAL,
        relevance_score=0.3,
    )

    out_file = tmp_path / "training.jsonl"
    count = export_training_data([doc], out_file)

    assert count == 1
    row = _read_jsonl_row(out_file)

    assert row["metadata"] == {
        "document_id": str(doc.id),
        "provider": "unknown",
        "analysis_source": "rule",
    }

    target = json.loads(row["messages"][2]["content"])
    assert "co_thought" not in target
    assert target["summary"] == ""
    assert target["tags"] == []
    assert target["affected_assets"] == []


def test_export_training_data_marks_internal_provider_metadata(tmp_path: Path) -> None:
    doc = make_document(
        raw_text="Internal companion output text.",
        is_analyzed=True,
        provider="internal",
    )

    out_file = tmp_path / "training.jsonl"
    count = export_training_data([doc], out_file)

    assert count == 1
    row = _read_jsonl_row(out_file)

    assert row["metadata"]["provider"] == "internal"
    assert row["metadata"]["analysis_source"] == "internal"


def test_export_training_data_skips_documents_without_text(tmp_path: Path) -> None:
    doc = make_document(is_analyzed=True, raw_text="   ", cleaned_text=None)

    out_file = tmp_path / "training.jsonl"
    count = export_training_data([doc], out_file)

    assert count == 0
