import json
from pathlib import Path

from app.core.enums import AnalysisSource, MarketScope, SentimentLabel
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
    doc.analysis_source = AnalysisSource.EXTERNAL_LLM

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
        analysis_source=AnalysisSource.RULE,
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
        provider="companion",
        analysis_source=AnalysisSource.INTERNAL,
    )

    out_file = tmp_path / "training.jsonl"
    count = export_training_data([doc], out_file)

    assert count == 1
    row = _read_jsonl_row(out_file)

    assert row["metadata"]["provider"] == "companion"
    assert row["metadata"]["analysis_source"] == "internal"


def test_export_training_data_supports_legacy_rows_without_explicit_analysis_source(
    tmp_path: Path,
) -> None:
    doc = make_document(
        raw_text="Legacy analyzed text.",
        is_analyzed=True,
        provider="openai",
    )

    out_file = tmp_path / "legacy.jsonl"
    count = export_training_data([doc], out_file)

    assert count == 1
    row = _read_jsonl_row(out_file)

    assert row["metadata"]["provider"] == "openai"
    assert row["metadata"]["analysis_source"] == "external_llm"


def test_export_training_data_prefers_explicit_analysis_source_over_provider(
    tmp_path: Path,
) -> None:
    doc = make_document(
        raw_text="Ensemble analyzed text.",
        is_analyzed=True,
        analysis_source=AnalysisSource.EXTERNAL_LLM,
    )
    doc.provider = "ensemble(openai,internal)"

    out_file = tmp_path / "ensemble.jsonl"
    count = export_training_data([doc], out_file)

    assert count == 1
    row = _read_jsonl_row(out_file)

    assert row["metadata"]["provider"] == "ensemble(openai,internal)"
    assert row["metadata"]["analysis_source"] == "external_llm"


def test_export_training_data_skips_documents_without_text(tmp_path: Path) -> None:
    doc = make_document(is_analyzed=True, raw_text="   ", cleaned_text=None)

    out_file = tmp_path / "training.jsonl"
    count = export_training_data([doc], out_file)

    assert count == 0


# ---------------------------------------------------------------------------
# Sprint 6 — teacher_only enforcement (I-27)
# ---------------------------------------------------------------------------


def test_export_training_data_teacher_only_exports_only_external_llm(tmp_path: Path) -> None:
    """teacher_only=True must filter at function level, not just CLI layer (I-27)."""
    teacher = make_document(
        raw_text="Teacher content.",
        is_analyzed=True,
        provider="openai",
        analysis_source=AnalysisSource.EXTERNAL_LLM,
    )
    rule_doc = make_document(
        raw_text="Rule content.",
        is_analyzed=True,
        provider=None,
        analysis_source=AnalysisSource.RULE,
    )
    internal_doc = make_document(
        raw_text="Internal content.",
        is_analyzed=True,
        provider="internal",
        analysis_source=AnalysisSource.INTERNAL,
    )

    out_file = tmp_path / "teacher.jsonl"
    count = export_training_data([teacher, rule_doc, internal_doc], out_file, teacher_only=True)

    assert count == 1
    row = _read_jsonl_row(out_file)
    assert row["metadata"]["analysis_source"] == "external_llm"
    assert row["metadata"]["provider"] == "openai"


def test_export_training_data_teacher_only_false_exports_all_analyzed(tmp_path: Path) -> None:
    """teacher_only=False (default) must export all analyzed tiers."""
    docs = [
        make_document(
            raw_text=f"Content {i}.",
            is_analyzed=True,
            analysis_source=src,
        )
        for i, src in enumerate(
            [AnalysisSource.EXTERNAL_LLM, AnalysisSource.INTERNAL, AnalysisSource.RULE]
        )
    ]

    out_file = tmp_path / "all.jsonl"
    count = export_training_data(docs, out_file)

    assert count == 3


def test_export_training_data_teacher_only_excludes_legacy_rows_without_explicit_source(
    tmp_path: Path,
) -> None:
    """teacher_only=True uses strict doc.analysis_source check (not effective_analysis_source).

    Legacy rows without an explicit analysis_source field are excluded even when
    doc.provider implies EXTERNAL_LLM. This prevents corpus contamination from pre-5B rows.
    See §16c and I-27.
    """
    legacy_doc = make_document(
        raw_text="Legacy openai content.",
        is_analyzed=True,
        provider="openai",
        # analysis_source=None (not set) — legacy row, no explicit tier stored
    )

    out_file = tmp_path / "legacy_teacher.jsonl"
    count = export_training_data([legacy_doc], out_file, teacher_only=True)

    # Intentionally conservative: legacy rows are excluded to prevent contamination
    assert count == 0
