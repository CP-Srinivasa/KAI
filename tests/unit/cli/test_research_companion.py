"""Tests for experimental companion-ML CLI commands.

These commands are [EXPERIMENTAL] — no companion model is currently deployed.
They test benchmark-companion, evaluate-datasets, check-promotion,
prepare-tuning-artifact, record-promotion, benchmark-companion-run,
shadow-report, evaluate.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from app.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_dataset_row(
    *,
    document_id: str,
    analysis_source: str = "external_llm",
    provider: str = "openai",
    sentiment_label: str = "bullish",
    priority_score: int = 8,
    relevance_score: float = 0.9,
    impact_score: float = 0.6,
    tags: list[str] | None = None,
) -> dict[str, object]:
    target = {
        "affected_assets": ["BTC"],
        "impact_score": impact_score,
        "market_scope": "crypto",
        "novelty_score": 0.4,
        "priority_score": priority_score,
        "relevance_score": relevance_score,
        "sentiment_label": sentiment_label,
        "sentiment_score": 0.7,
        "spam_probability": 0.05,
        "summary": "Synthetic dataset row.",
        "tags": tags or ["btc"],
    }
    return {
        "messages": [
            {"role": "system", "content": "You are a highly precise financial AI analyst."},
            {"role": "user", "content": "Analyze..."},
            {"role": "assistant", "content": json.dumps(target, sort_keys=True)},
        ],
        "metadata": {
            "document_id": document_id,
            "provider": provider,
            "analysis_source": analysis_source,
        },
    }


def _write_jsonl_rows(path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


# ---------------------------------------------------------------------------
# Sprint 18: research evaluate-datasets
# ---------------------------------------------------------------------------


def test_research_evaluate_datasets_prints_metrics_table(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    candidate_file = tmp_path / "candidate.jsonl"

    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )
    _write_jsonl_rows(
        candidate_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="internal")],
    )

    result = runner.invoke(
        app,
        [
            "research",
            "evaluate-datasets",
            str(teacher_file),
            str(candidate_file),
            "--dataset-type",
            "internal_benchmark",
        ],
    )

    assert result.exit_code == 0
    assert "Dataset Evaluation Metrics" in result.output
    assert "Dataset Type" in result.output
    assert "internal_benchmark" in result.output
    assert "Teacher Rows" in result.output
    assert "Candidate Rows" in result.output
    assert "Paired Documents" in result.output
    assert "Sentiment Agreement" in result.output
    assert "100.00%" in result.output


def test_research_evaluate_datasets_missing_candidate_file_fails(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )

    missing_candidate = tmp_path / "missing.jsonl"
    result = runner.invoke(
        app,
        [
            "research",
            "evaluate-datasets",
            str(teacher_file),
            str(missing_candidate),
        ],
    )

    assert result.exit_code == 1
    assert "Candidate dataset file not found" in result.output


def test_research_evaluate_datasets_handles_empty_files(tmp_path) -> None:
    teacher_file = tmp_path / "teacher_empty.jsonl"
    candidate_file = tmp_path / "candidate_empty.jsonl"
    teacher_file.write_text("", encoding="utf-8")
    candidate_file.write_text("", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "evaluate-datasets",
            str(teacher_file),
            str(candidate_file),
        ],
    )

    assert result.exit_code == 0
    assert "Teacher dataset is empty." in result.output
    assert "Candidate dataset is empty." in result.output
    assert "No overlapping document_id pairs found." in result.output
    assert "Paired Documents" in result.output


def test_research_evaluate_datasets_reports_missing_pairs(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    candidate_file = tmp_path / "candidate.jsonl"

    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )
    _write_jsonl_rows(
        candidate_file,
        [_make_dataset_row(document_id="doc-99", analysis_source="rule")],
    )

    result = runner.invoke(
        app,
        [
            "research",
            "evaluate-datasets",
            str(teacher_file),
            str(candidate_file),
            "--dataset-type",
            "rule_baseline",
        ],
    )

    assert result.exit_code == 0
    assert "No overlapping document_id pairs found." in result.output
    assert "Missing Pairs" in result.output
    assert "rule_baseline" in result.output


# ---------------------------------------------------------------------------
# Sprint 19: research benchmark-companion
# ---------------------------------------------------------------------------


def test_research_benchmark_companion_saves_report_and_artifact(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    candidate_file = tmp_path / "candidate.jsonl"
    report_file = tmp_path / "reports" / "benchmark_report.json"
    artifact_file = tmp_path / "artifacts" / "benchmark_artifact.json"

    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )
    _write_jsonl_rows(
        candidate_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="internal", provider="companion")],
    )

    result = runner.invoke(
        app,
        [
            "research",
            "benchmark-companion",
            str(teacher_file),
            str(candidate_file),
            "--report-out",
            str(report_file),
            "--artifact-out",
            str(artifact_file),
        ],
    )

    assert result.exit_code == 0
    assert "Companion Benchmark Metrics" in result.output
    assert "Saved benchmark report to" in result.output
    assert "Saved benchmark artifact to" in result.output

    report_payload = json.loads(report_file.read_text(encoding="utf-8"))
    assert report_payload["report_type"] == "dataset_evaluation"
    assert report_payload["dataset_type"] == "internal_benchmark"
    assert report_payload["inputs"]["teacher_dataset"] == str(teacher_file.resolve())
    assert report_payload["inputs"]["candidate_dataset"] == str(candidate_file.resolve())
    assert report_payload["metrics"]["sample_count"] == 1

    artifact_payload = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert artifact_payload["artifact_type"] == "companion_benchmark"
    assert artifact_payload["status"] == "benchmark_ready"
    assert artifact_payload["dataset_type"] == "internal_benchmark"
    assert artifact_payload["teacher_dataset"] == str(teacher_file.resolve())
    assert artifact_payload["candidate_dataset"] == str(candidate_file.resolve())
    assert artifact_payload["evaluation_report"] == str(report_file.resolve())
    assert artifact_payload["paired_count"] == 1


def test_research_benchmark_companion_handles_empty_candidate_dataset(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    candidate_file = tmp_path / "candidate_empty.jsonl"
    artifact_file = tmp_path / "artifact.json"

    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )
    candidate_file.write_text("", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "benchmark-companion",
            str(teacher_file),
            str(candidate_file),
            "--artifact-out",
            str(artifact_file),
        ],
    )

    assert result.exit_code == 0
    assert "Candidate dataset is empty." in result.output
    assert "No overlapping document_id pairs found." in result.output

    artifact_payload = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert artifact_payload["status"] == "needs_more_data"
    assert artifact_payload["paired_count"] == 0
    assert artifact_payload["evaluation_report"] is None


def test_research_benchmark_companion_missing_teacher_file_fails(tmp_path) -> None:
    candidate_file = tmp_path / "candidate.jsonl"
    _write_jsonl_rows(
        candidate_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="internal")],
    )

    missing_teacher = tmp_path / "missing_teacher.jsonl"
    result = runner.invoke(
        app,
        [
            "research",
            "benchmark-companion",
            str(missing_teacher),
            str(candidate_file),
        ],
    )

    assert result.exit_code == 1
    assert "Teacher dataset file not found" in result.output


def test_research_benchmark_companion_missing_candidate_file_fails(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )

    missing_candidate = tmp_path / "missing_candidate.jsonl"
    result = runner.invoke(
        app,
        [
            "research",
            "benchmark-companion",
            str(teacher_file),
            str(missing_candidate),
        ],
    )

    assert result.exit_code == 1
    assert "Candidate dataset file not found" in result.output


def test_research_benchmark_companion_invalid_jsonl_fails(tmp_path) -> None:
    teacher_file = tmp_path / "teacher.jsonl"
    candidate_file = tmp_path / "candidate_invalid.jsonl"
    _write_jsonl_rows(
        teacher_file,
        [_make_dataset_row(document_id="doc-1", analysis_source="external_llm")],
    )
    candidate_file.write_text("{not-json}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "research",
            "benchmark-companion",
            str(teacher_file),
            str(candidate_file),
        ],
    )

    assert result.exit_code == 1
    assert "Invalid JSONL content in Candidate dataset" in result.output


# ---------------------------------------------------------------------------
# Sprint 31: benchmark-companion-run / check-promotion / prepare-tuning-artifact
#            / record-promotion / evaluate
# ---------------------------------------------------------------------------


def test_research_benchmark_companion_run_in_help() -> None:
    """benchmark-companion-run must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "benchmark-companion-run" in result.output


def test_research_benchmark_companion_run_missing_teacher_file(tmp_path) -> None:
    """benchmark-companion-run exits 1 when teacher JSONL file does not exist."""
    missing = tmp_path / "nonexistent_teacher.jsonl"
    out = tmp_path / "candidate.jsonl"
    result = runner.invoke(app, ["research", "benchmark-companion-run", str(missing), str(out)])
    assert result.exit_code == 1


def test_research_check_promotion_in_help() -> None:
    """check-promotion must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "check-promotion" in result.output


def test_research_check_promotion_missing_report_file(tmp_path) -> None:
    """check-promotion exits 1 when evaluation report file does not exist."""
    missing = tmp_path / "nonexistent_report.json"
    result = runner.invoke(app, ["research", "check-promotion", str(missing)])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_research_check_promotion_all_gates_pass(tmp_path) -> None:
    """check-promotion exits 0 and prints PROMOTABLE when all gates pass."""
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "metrics": {
                    "sentiment_agreement": 0.92,
                    "priority_mae": 1.0,
                    "relevance_mae": 0.10,
                    "impact_mae": 0.15,
                    "tag_overlap_mean": 0.40,
                    "sample_count": 50,
                    "missing_pairs": 0,
                }
            }
        )
    )
    result = runner.invoke(app, ["research", "check-promotion", str(report)])
    assert result.exit_code == 0
    assert "PROMOTABLE" in result.output


def test_research_check_promotion_gate_fails(tmp_path) -> None:
    """check-promotion exits 1 and prints NOT PROMOTABLE when sentiment gate fails."""
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "metrics": {
                    "sentiment_agreement": 0.70,
                    "priority_mae": 1.0,
                    "relevance_mae": 0.10,
                    "impact_mae": 0.15,
                    "tag_overlap_mean": 0.40,
                    "sample_count": 50,
                    "missing_pairs": 0,
                }
            }
        )
    )
    result = runner.invoke(app, ["research", "check-promotion", str(report)])
    assert result.exit_code == 1
    assert "NOT PROMOTABLE" in result.output


def test_research_prepare_tuning_artifact_in_help() -> None:
    """prepare-tuning-artifact must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "prepare-tuning-artifact" in result.output


def test_research_prepare_tuning_artifact_missing_teacher_file(tmp_path) -> None:
    """prepare-tuning-artifact exits 1 when teacher JSONL file does not exist."""
    missing = tmp_path / "nonexistent_teacher.jsonl"
    result = runner.invoke(
        app,
        ["research", "prepare-tuning-artifact", str(missing), "llama3.2:3b"],
    )
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_research_record_promotion_in_help() -> None:
    """record-promotion must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "record-promotion" in result.output


def test_research_record_promotion_missing_report_file(tmp_path) -> None:
    """record-promotion exits 1 when evaluation report file does not exist."""
    missing = tmp_path / "nonexistent_report.json"
    result = runner.invoke(
        app,
        [
            "research",
            "record-promotion",
            str(missing),
            "kai-analyst-v1",
            "--endpoint",
            "http://localhost:11434",
            "--operator-note",
            "Manual promotion test",
        ],
    )
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_research_record_promotion_blocked_when_gates_fail(tmp_path) -> None:
    """record-promotion exits 1 and blocks when evaluation gates do not pass."""
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "metrics": {
                    "sentiment_agreement": 0.70,
                    "priority_mae": 1.0,
                    "relevance_mae": 0.10,
                    "impact_mae": 0.15,
                    "tag_overlap_mean": 0.40,
                    "sample_count": 50,
                    "missing_pairs": 0,
                }
            }
        )
    )
    result = runner.invoke(
        app,
        [
            "research",
            "record-promotion",
            str(report),
            "kai-analyst-v1",
            "--endpoint",
            "http://localhost:11434",
            "--operator-note",
            "Manual promotion test",
        ],
    )
    assert result.exit_code == 1
    assert "Promotion blocked" in result.output


def test_research_evaluate_in_help() -> None:
    """evaluate must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "evaluate" in result.output


def test_research_evaluate_no_teacher_docs(monkeypatch) -> None:
    """evaluate exits 0 and reports no documents when DB returns empty list."""
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    class FakeSessionFactory:
        def begin(self):
            class Ctx:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, *a):
                    return False

            return Ctx()

    async def fake_list(self, **kwargs):
        return []

    monkeypatch.setattr(db_session, "build_session_factory", lambda _: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "list", fake_list)

    result = runner.invoke(app, ["research", "evaluate", "--limit", "5"])
    assert result.exit_code == 0
    assert "No documents" in result.output


# ---------------------------------------------------------------------------
# Sprint 29: shadow-report
# ---------------------------------------------------------------------------


def test_research_shadow_report_no_shadow_docs(monkeypatch) -> None:
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    class FakeSessionFactory:
        def begin(self):
            class Ctx:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, *a):
                    return False

            return Ctx()

    async def fake_list(self, **kwargs):
        return []

    monkeypatch.setattr(db_session, "build_session_factory", lambda _: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "list", fake_list)

    result = runner.invoke(app, ["research", "shadow-report"])
    assert result.exit_code == 0
    assert "No documents with shadow analysis" in result.output
