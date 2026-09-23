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
