import json

import pytest

from app.core.enums import AnalysisSource, SentimentLabel
from app.research.evaluation import (
    EvaluationComparisonReport,
    EvaluationMetrics,
    EvaluationReport,
    PromotionValidation,
    build_shadow_run_record,
    compare_datasets,
    compare_evaluation_reports,
    compare_outputs,
    load_jsonl,
    load_saved_evaluation_report,
    save_benchmark_artifact,
    save_evaluation_comparison_report,
    save_evaluation_report,
    save_shadow_run_records,
    validate_promotion,
)
from tests.unit.factories import make_analysis_result, make_document, make_llm_output


def test_compare_outputs_identical_documents():
    doc1 = make_document(
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BULLISH,
        relevance_score=0.9,
        impact_score=0.8,
        novelty_score=0.7,
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
        novelty_score=0.8,
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
    actionable: bool | None = None,
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
    if actionable is not None:
        target["actionable"] = actionable
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
    assert report.metrics.actionable_accuracy == 1.0
    assert report.metrics.false_actionable_rate == 0.0
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


def test_compare_datasets_actionable_metrics_use_paired_rows_only() -> None:
    teacher_rows = [_make_jsonl_row("doc-1", priority_score=3, actionable=False)]
    baseline_rows = [
        _make_jsonl_row("doc-1", priority_score=9, actionable=True, analysis_source="internal"),
        _make_jsonl_row("doc-99", priority_score=9, actionable=True, analysis_source="internal"),
    ]

    report = compare_datasets(
        teacher_rows,
        baseline_rows,
        dataset_type="internal_benchmark",
    )

    assert report.metrics.sample_count == 1
    assert report.metrics.missing_pairs == 1
    assert report.metrics.actionable_accuracy == 0.0
    assert report.metrics.false_actionable_rate == 1.0


def test_compare_datasets_uses_explicit_actionable_labels_when_present() -> None:
    teacher = _make_jsonl_row("doc-1", priority_score=8, actionable=False)
    baseline = _make_jsonl_row(
        "doc-1",
        priority_score=8,
        actionable=True,
        analysis_source="internal",
    )

    report = compare_datasets([teacher], [baseline], dataset_type="internal_benchmark")

    assert report.metrics.actionable_accuracy == 0.0
    assert report.metrics.false_actionable_rate == 1.0


def test_compare_datasets_without_explicit_actionable_labels_uses_priority_threshold() -> None:
    teacher = _make_jsonl_row("doc-1", priority_score=4)
    baseline = _make_jsonl_row("doc-1", priority_score=5, analysis_source="rule")

    report = compare_datasets([teacher], [baseline], dataset_type="rule_baseline")

    assert report.metrics.actionable_accuracy == 1.0
    assert report.metrics.false_actionable_rate == 0.0


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


# ---------------------------------------------------------------------------
# Sprint 7 — validate_promotion() gate tests
# ---------------------------------------------------------------------------


def _passing_metrics(**overrides) -> EvaluationMetrics:
    """Return EvaluationMetrics that pass all gates by default."""
    defaults: dict = {
        "sentiment_agreement": 0.90,
        "priority_mae": 1.0,
        "relevance_mae": 0.10,
        "impact_mae": 0.15,
        "tag_overlap_mean": 0.40,
        "actionable_accuracy": 0.90,
        "false_actionable_rate": 0.0,
        "sample_count": 50,
        "missing_pairs": 0,
    }
    defaults.update(overrides)
    return EvaluationMetrics(**defaults)


def test_validate_promotion_all_gates_pass() -> None:
    metrics = _passing_metrics()
    result = validate_promotion(metrics)

    assert isinstance(result, PromotionValidation)
    assert result.is_promotable is True
    assert result.sentiment_pass is True
    assert result.priority_pass is True
    assert result.relevance_pass is True
    assert result.impact_pass is True
    assert result.tag_overlap_pass is True
    assert result.false_actionable_pass is True


def test_validate_promotion_sentiment_fails() -> None:
    metrics = _passing_metrics(sentiment_agreement=0.80)  # < 0.85
    result = validate_promotion(metrics)

    assert result.sentiment_pass is False
    assert result.is_promotable is False


def test_validate_promotion_priority_fails() -> None:
    metrics = _passing_metrics(priority_mae=2.0)  # > 1.5
    result = validate_promotion(metrics)

    assert result.priority_pass is False
    assert result.is_promotable is False


def test_validate_promotion_relevance_fails() -> None:
    metrics = _passing_metrics(relevance_mae=0.20)  # > 0.15
    result = validate_promotion(metrics)

    assert result.relevance_pass is False
    assert result.is_promotable is False


def test_validate_promotion_impact_fails() -> None:
    metrics = _passing_metrics(impact_mae=0.25)  # > 0.20
    result = validate_promotion(metrics)

    assert result.impact_pass is False
    assert result.is_promotable is False


def test_validate_promotion_tag_overlap_fails() -> None:
    metrics = _passing_metrics(tag_overlap_mean=0.20)  # < 0.30
    result = validate_promotion(metrics)

    assert result.tag_overlap_pass is False
    assert result.is_promotable is False


def test_validate_promotion_false_actionable_fails() -> None:
    metrics = _passing_metrics(false_actionable_rate=0.06)  # > 0.05
    result = validate_promotion(metrics)

    assert result.false_actionable_pass is False
    assert result.is_promotable is False


def test_validate_promotion_boundary_values_are_passing() -> None:
    """Exact threshold values must pass (≥ / ≤ boundary is inclusive)."""
    metrics = EvaluationMetrics(
        sentiment_agreement=0.85,
        priority_mae=1.5,
        relevance_mae=0.15,
        impact_mae=0.20,
        tag_overlap_mean=0.30,
        actionable_accuracy=0.85,
        false_actionable_rate=0.05,
        sample_count=10,
        missing_pairs=0,
    )
    result = validate_promotion(metrics)

    assert result.is_promotable is True
    assert all(
        [
            result.sentiment_pass,
            result.priority_pass,
            result.relevance_pass,
            result.impact_pass,
            result.tag_overlap_pass,
            result.false_actionable_pass,
        ]
    )


# ---------------------------------------------------------------------------
# Sprint 6/7 — persistence helpers
# ---------------------------------------------------------------------------


def test_save_evaluation_report_writes_structured_json(tmp_path) -> None:
    teacher_rows = [_make_jsonl_row("doc-1", analysis_source="external_llm")]
    candidate_rows = [_make_jsonl_row("doc-1", analysis_source="internal", provider="companion")]
    report = compare_datasets(teacher_rows, candidate_rows, dataset_type="internal_benchmark")

    output_path = tmp_path / "reports" / "evaluation_report.json"
    saved_path = save_evaluation_report(
        report,
        output_path,
        teacher_dataset=tmp_path / "teacher.jsonl",
        candidate_dataset=tmp_path / "candidate.jsonl",
    )

    payload = json.loads(saved_path.read_text(encoding="utf-8"))
    assert payload["report_type"] == "dataset_evaluation"
    assert payload["dataset_type"] == "internal_benchmark"
    assert payload["paired_count"] == 1
    assert payload["inputs"]["teacher_dataset"].endswith("teacher.jsonl")
    assert payload["inputs"]["candidate_dataset"].endswith("candidate.jsonl")
    assert payload["metrics"]["sample_count"] == 1
    assert payload["metrics"]["actionable_accuracy"] == pytest.approx(1.0)
    assert payload["metrics"]["false_actionable_rate"] == pytest.approx(0.0)


def test_save_benchmark_artifact_tracks_ready_and_empty_states(tmp_path) -> None:
    ready_report = compare_datasets(
        [_make_jsonl_row("doc-1", analysis_source="external_llm")],
        [_make_jsonl_row("doc-1", analysis_source="internal", provider="companion")],
        dataset_type="internal_benchmark",
    )
    ready_path = save_benchmark_artifact(
        tmp_path / "ready_artifact.json",
        teacher_dataset=tmp_path / "teacher.jsonl",
        candidate_dataset=tmp_path / "candidate.jsonl",
        report=ready_report,
        report_path=tmp_path / "report.json",
    )
    ready_payload = json.loads(ready_path.read_text(encoding="utf-8"))
    assert ready_payload["artifact_type"] == "companion_benchmark"
    assert ready_payload["status"] == "benchmark_ready"
    assert ready_payload["evaluation_report"].endswith("report.json")
    assert ready_payload["paired_count"] == 1

    empty_report = compare_datasets(
        [_make_jsonl_row("doc-1", analysis_source="external_llm")],
        [_make_jsonl_row("doc-2", analysis_source="internal", provider="companion")],
        dataset_type="internal_benchmark",
    )
    empty_path = save_benchmark_artifact(
        tmp_path / "empty_artifact.json",
        teacher_dataset=tmp_path / "teacher.jsonl",
        candidate_dataset=tmp_path / "candidate.jsonl",
        report=empty_report,
    )
    empty_payload = json.loads(empty_path.read_text(encoding="utf-8"))
    assert empty_payload["status"] == "needs_more_data"
    assert empty_payload["evaluation_report"] is None
    assert empty_payload["paired_count"] == 0


def test_load_saved_evaluation_report_roundtrip(tmp_path) -> None:
    teacher_rows = [_make_jsonl_row("doc-1", analysis_source="external_llm")]
    candidate_rows = [_make_jsonl_row("doc-1", analysis_source="internal", provider="companion")]
    report = compare_datasets(teacher_rows, candidate_rows, dataset_type="internal_benchmark")
    report_path = save_evaluation_report(
        report,
        tmp_path / "evaluation_report.json",
        teacher_dataset=tmp_path / "teacher.jsonl",
        candidate_dataset=tmp_path / "candidate.jsonl",
    )

    loaded = load_saved_evaluation_report(report_path)

    assert loaded.dataset_type == "internal_benchmark"
    assert loaded.teacher_count == 1
    assert loaded.baseline_count == 1
    assert loaded.paired_count == 1
    assert loaded.metrics.sentiment_agreement == pytest.approx(1.0)


def test_load_saved_evaluation_report_rejects_incomplete_payload(tmp_path) -> None:
    report_path = tmp_path / "broken_report.json"
    report_path.write_text(
        json.dumps(
            {
                "report_type": "dataset_evaluation",
                "inputs": {
                    "teacher_dataset": str(tmp_path / "teacher.jsonl"),
                    "candidate_dataset": str(tmp_path / "candidate.jsonl"),
                },
                "metrics": {
                    "sentiment_agreement": 0.9,
                    "priority_mae": 1.0,
                    "relevance_mae": 0.1,
                    "impact_mae": 0.1,
                    "tag_overlap_mean": 0.4,
                    "actionable_accuracy": 0.9,
                    "false_actionable_rate": 0.0,
                    "sample_count": 10,
                    "missing_pairs": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="dataset_type"):
        load_saved_evaluation_report(report_path)


def test_compare_evaluation_reports_detects_regressions_and_gate_changes() -> None:
    baseline = EvaluationReport(
        metrics=EvaluationMetrics(
            sentiment_agreement=0.90,
            priority_mae=1.0,
            relevance_mae=0.10,
            impact_mae=0.15,
            tag_overlap_mean=0.40,
            actionable_accuracy=0.90,
            false_actionable_rate=0.06,
            sample_count=10,
            missing_pairs=0,
        ),
        dataset_type="internal_benchmark",
        teacher_count=10,
        baseline_count=10,
        paired_count=10,
    )
    candidate = EvaluationReport(
        metrics=EvaluationMetrics(
            sentiment_agreement=0.80,
            priority_mae=1.8,
            relevance_mae=0.18,
            impact_mae=0.12,
            tag_overlap_mean=0.35,
            actionable_accuracy=0.85,
            false_actionable_rate=0.03,
            sample_count=8,
            missing_pairs=2,
        ),
        dataset_type="internal_benchmark",
        teacher_count=10,
        baseline_count=8,
        paired_count=8,
    )

    comparison = compare_evaluation_reports(baseline, candidate)

    assert isinstance(comparison, EvaluationComparisonReport)
    assert comparison.paired_count.delta == -2
    assert comparison.metric_deltas.sentiment_agreement.delta == pytest.approx(-0.10)
    assert comparison.metric_deltas.priority_mae.delta == pytest.approx(0.8)
    assert comparison.pass_fail_changes.sentiment.regressed is True
    assert comparison.pass_fail_changes.priority.regressed is True
    assert comparison.pass_fail_changes.false_actionable.improved is True
    assert comparison.regression_summary.has_regression is True
    assert "paired_count" in comparison.regression_summary.regressed_metrics
    assert "impact_mae" in comparison.regression_summary.improved_metrics
    assert "false_actionable" in comparison.regression_summary.improved_gates


def test_save_evaluation_comparison_report_writes_structured_json(tmp_path) -> None:
    baseline = EvaluationReport(
        metrics=_passing_metrics(false_actionable_rate=0.06),
        dataset_type="internal_benchmark",
        teacher_count=10,
        baseline_count=10,
        paired_count=10,
    )
    candidate = EvaluationReport(
        metrics=_passing_metrics(sentiment_agreement=0.80, false_actionable_rate=0.02),
        dataset_type="internal_benchmark",
        teacher_count=10,
        baseline_count=9,
        paired_count=9,
    )
    comparison = compare_evaluation_reports(baseline, candidate)

    saved = save_evaluation_comparison_report(
        comparison,
        tmp_path / "comparison.json",
        baseline_report=tmp_path / "baseline_report.json",
        candidate_report=tmp_path / "candidate_report.json",
    )

    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["report_type"] == "evaluation_report_comparison"
    assert payload["paired_count"]["delta"] == -1
    assert payload["metric_deltas"]["sentiment_agreement"]["delta"] == pytest.approx(-0.10)
    assert payload["pass_fail_changes"]["false_actionable"]["improved"] is True
    assert payload["regression_summary"]["has_regression"] is True


def test_build_shadow_run_record_captures_primary_and_shadow_deltas() -> None:
    doc = make_document()
    primary = make_analysis_result(
        doc.id,
        analysis_source=AnalysisSource.EXTERNAL_LLM,
        sentiment_label=SentimentLabel.BULLISH,
        relevance_score=0.9,
        impact_score=0.7,
        recommended_priority=8,
        explanation_short="Primary summary.",
        actionable=True,
        tags=["btc", "etf"],
    )
    shadow = make_llm_output(
        sentiment_label=SentimentLabel.BEARISH,
        relevance_score=0.4,
        impact_score=0.2,
        recommended_priority=5,
        short_reasoning="Shadow summary.",
        actionable=False,
        tags=["btc"],
    )

    record = build_shadow_run_record(
        doc,
        primary,
        primary_provider="openai",
        shadow_output=shadow,
        shadow_provider="companion",
    )

    assert record["record_type"] == "companion_shadow_run"
    assert record["document_id"] == str(doc.id)
    assert record["primary_provider"] == "openai"
    assert record["primary_analysis_source"] == "external_llm"
    assert record["shadow_provider"] == "companion"
    assert record["shadow_analysis_source"] == "internal"
    assert record["primary"]["summary"] == "Primary summary."
    assert record["shadow"]["summary"] == "Shadow summary."
    assert record["deviations"]["priority_delta"] == 3
    assert record["deviations"]["sentiment_match"] is False
    assert record["deviations"]["actionable_match"] is False
    assert record["deviations"]["tag_overlap"] == pytest.approx(0.5)


def test_save_shadow_run_records_writes_jsonl(tmp_path) -> None:
    doc = make_document()
    primary = make_analysis_result(
        doc.id,
        analysis_source=AnalysisSource.RULE,
        recommended_priority=4,
        explanation_short="Rule fallback summary.",
    )
    record = build_shadow_run_record(
        doc,
        primary,
        primary_provider="fallback",
        shadow_output=None,
        shadow_provider="companion",
        shadow_error="Companion offline",
    )

    out_file = tmp_path / "shadow.jsonl"
    saved_path = save_shadow_run_records([record], out_file)
    rows = load_jsonl(saved_path)

    assert saved_path == out_file
    assert len(rows) == 1
    assert rows[0]["document_id"] == str(doc.id)
    assert rows[0]["shadow"] is None
    assert rows[0]["shadow_error"] == "Companion offline"
