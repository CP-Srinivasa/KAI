from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.jev_shadow_eval.holdout_score import main, score


def _evidence(tmp_path: Path, *, count: int = 100) -> tuple[Path, Path, Path]:
    review_cases = []
    mapping_cases = []
    baseline_cases = []
    for index in range(count):
        review_id = f"holdout-{index:04d}"
        title = f"Title {index}"
        text = f"Text {index}"
        from scripts.jev_shadow_eval.holdout_score import _content_hash

        content_hash = _content_hash(title, text)
        review_cases.append(
            {
                "review_id": review_id,
                "title": title,
                "text": text,
                "relevant": index % 2 == 0,
                "disputed": False,
                "reason": "Independent review reason.",
            }
        )
        mapping_cases.append(
            {
                "review_id": review_id,
                "doc_id": f"doc-{index}",
                "stratum": "gate_skipped" if index < 50 else "external_llm",
                "content_sha256": content_hash,
            }
        )
        baseline_cases.append(
            {
                "review_id": review_id,
                "content_sha256": content_hash,
                "baseline_relevant": index % 4 == 0,
                "baseline_reason": "test",
            }
        )
    pool_hash = "a" * 64
    documents = (
        (
            "review.json",
            {
                "schema_version": "jev-holdout-blind-review/v1",
                "pool_sha256": pool_hash,
                "case_count": count,
                "reviewer": "independent-reviewer",
                "cases": review_cases,
            },
        ),
        (
            "mapping.json",
            {
                "schema_version": "jev-holdout-private-map/v1",
                "pool_sha256": pool_hash,
                "case_count": count,
                "cases": mapping_cases,
            },
        ),
        (
            "baseline.json",
            {
                "schema_version": "jev-holdout-baseline/v1",
                "pool_sha256": pool_hash,
                "case_count": count,
                "cases": baseline_cases,
            },
        ),
    )
    paths = []
    for name, document in documents:
        path = tmp_path / name
        path.write_text(json.dumps(document), encoding="utf-8")
        paths.append(path)
    return paths[0], paths[1], paths[2]


def test_score_complete_holdout_and_strata(tmp_path: Path) -> None:
    review, mapping, baseline = _evidence(tmp_path)
    report = score(review, mapping, baseline)
    assert report["status"] == "BASELINE_SCORED"
    assert report["case_count"] == report["undisputed_count"] == 100
    assert report["disputed_count"] == 0
    assert report["independent_labels_verified"] is False
    assert len(report["scoring_code_sha256"]) == 64
    assert report["metrics"]["true_positive"] == 25
    assert report["metrics"]["false_negative"] == 25
    assert set(report["metrics_by_stratum"]) == {"external_llm", "gate_skipped"}


def test_dispute_is_excluded_and_requires_adjudication(tmp_path: Path) -> None:
    review_path, mapping, baseline = _evidence(tmp_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["cases"][0]["disputed"] = True
    review_path.write_text(json.dumps(review), encoding="utf-8")
    report = score(review_path, mapping, baseline)
    assert report["status"] == "NEEDS_ADJUDICATION"
    assert report["undisputed_count"] == 99
    assert report["disputed_count"] == 1


@pytest.mark.parametrize("defect", ["pool", "content", "baseline", "reason"])
def test_score_rejects_mismatched_evidence(tmp_path: Path, defect: str) -> None:
    review_path, mapping_path, baseline_path = _evidence(tmp_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if defect == "pool":
        mapping["pool_sha256"] = "b" * 64
        mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
    elif defect == "content":
        review["cases"][0]["text"] += " tampered"
        review_path.write_text(json.dumps(review), encoding="utf-8")
    elif defect == "baseline":
        baseline["cases"][0]["baseline_relevant"] = None
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    else:
        review["cases"][0]["reason"] = ""
        review_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError):
        score(review_path, mapping_path, baseline_path)


def test_cli_never_overwrites_score(tmp_path: Path) -> None:
    review, mapping, baseline = _evidence(tmp_path)
    output = tmp_path / "score.json"
    argv = [
        "--review",
        str(review),
        "--mapping",
        str(mapping),
        "--baseline",
        str(baseline),
        "--output",
        str(output),
    ]
    assert main(argv) == 0
    original = output.read_bytes()
    assert main(argv) == 2
    assert output.read_bytes() == original


def test_adjudication_resolves_dispute_and_preserves_raw_metrics(tmp_path: Path) -> None:
    review_path, mapping, baseline = _evidence(tmp_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["cases"][0]["disputed"] = True
    review_path.write_text(json.dumps(review), encoding="utf-8")
    adjudication = tmp_path / "adjudication.json"
    adjudication.write_text(
        json.dumps(
            {
                "schema_version": "jev-adjudication/v1",
                "decided_at_utc": "2026-09-24T08:11:02+00:00",
                "cases": [
                    {
                        "case_id": "holdout-0000",
                        "final_label": False,
                        "rule_ref": "R-TEST",
                        "decided_by": "operator",
                        "rationale": "frozen decision",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report = score(review_path, mapping, baseline, adjudication)
    assert report["status"] == "BASELINE_SCORED"
    assert report["adjudicated_count"] == 1
    assert report["raw_metrics"]["case_count"] == 99
    assert report["raw_metrics"]["true_positive"] == 24
    assert report["metrics"]["case_count"] == 100
    assert report["metrics"]["true_positive"] == 24


def _adjudication(tmp_path: Path, case_id: str, **extra: object) -> Path:
    path = tmp_path / f"adjudication-{case_id}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "jev-adjudication/v1",
                "decided_at_utc": "2026-09-24T08:11:02+00:00",
                **extra,
                "cases": [
                    {
                        "case_id": case_id,
                        "final_label": False,
                        "rule_ref": "R-TEST",
                        "decided_by": "operator",
                        "rationale": "frozen decision",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_adjudication_cannot_relabel_an_undisputed_case(tmp_path: Path) -> None:
    review, mapping, baseline = _evidence(tmp_path)
    with pytest.raises(ValueError, match="undisputed"):
        score(review, mapping, baseline, _adjudication(tmp_path, "holdout-0002"))


def test_adjudication_bound_to_other_label_file_is_rejected(tmp_path: Path) -> None:
    review_path, mapping, baseline = _evidence(tmp_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["cases"][0]["disputed"] = True
    review_path.write_text(json.dumps(review), encoding="utf-8")
    foreign = _adjudication(tmp_path, "holdout-0000", labels_sha256="f" * 64)
    with pytest.raises(ValueError, match="different label file"):
        score(review_path, mapping, baseline, foreign)


def test_pipeline_replay_report_is_scored_via_mapping(tmp_path: Path) -> None:
    review, mapping_path, _ = _evidence(tmp_path, count=4)
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    replay = tmp_path / "replay.json"
    replay.write_text(
        json.dumps(
            {
                "schema_version": "jev-pipeline-replay/v1",
                "git_sha": "c" * 40,
                "crypto_gate_mode": "enforce",
                "input_sha256": "d" * 64,
                "case_count": 4,
                "cases": [
                    {
                        "case_id": row["doc_id"],
                        "would_call_llm": index in (0, 1),
                        "skip_reason": None if index in (0, 1) else "crypto_relevance_gate",
                        "error": None,
                    }
                    for index, row in enumerate(mapping["cases"])
                ],
            }
        ),
        encoding="utf-8",
    )
    report = score(review, mapping_path, replay)
    assert report["prediction_source"]["kind"] == "pipeline_replay"
    assert report["prediction_source"]["git_sha"] == "c" * 40
    # labels: 0 and 2 relevant; predicted: 0 and 1
    assert report["metrics"]["true_positive"] == 1
    assert report["metrics"]["false_positive"] == 1
    assert report["metrics"]["false_negative"] == 1
