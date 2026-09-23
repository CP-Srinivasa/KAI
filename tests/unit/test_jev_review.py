from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.jev_shadow_eval.corpus import ROOT, load_corpus, prepare_blind_review
from scripts.jev_shadow_eval.review import main, reconcile

CORPUS = ROOT / "tests/fixtures/jev/development_corpus.json"


def _completed_files(tmp_path: Path) -> tuple[Path, Path]:
    rows, corpus_hash = load_corpus(CORPUS)
    review, mapping = prepare_blind_review(rows, corpus_hash)
    review["reviewer"] = "independent-reviewer"
    for case in review["cases"]:
        case["relevant"] = True
        case["disputed"] = False
        case["reason"] = "Reviewed against the frozen policy."
    review_path = tmp_path / "review.json"
    mapping_path = tmp_path / "mapping.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
    return review_path, mapping_path


def test_reconcile_seals_complete_review(tmp_path: Path) -> None:
    review_path, mapping_path = _completed_files(tmp_path)
    report = reconcile(CORPUS, review_path, mapping_path)
    assert report["status"] == "REVIEW_COMPLETE"
    assert report["independent_labels_verified"] is True
    assert report["primary_ready"] is False
    assert report["jev_called"] is False
    assert report["case_count"] == 24
    assert report["disputed_count"] == 0
    assert len(report["review_sha256"]) == 64
    assert len(report["mapping_sha256"]) == 64
    assert [label["case_id"] for label in report["labels"]] == sorted(
        label["case_id"] for label in report["labels"]
    )


def test_dispute_requires_adjudication_and_nonzero_exit(tmp_path: Path) -> None:
    review_path, mapping_path = _completed_files(tmp_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["cases"][0]["disputed"] = True
    review_path.write_text(json.dumps(review), encoding="utf-8")
    output = tmp_path / "labels.json"
    assert (
        main(
            [
                "--corpus",
                str(CORPUS),
                "--review",
                str(review_path),
                "--mapping",
                str(mapping_path),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "NEEDS_ADJUDICATION"


@pytest.mark.parametrize("defect", ["mapping", "text", "duplicate", "reason"])
def test_reconcile_rejects_tampering_or_incomplete_review(tmp_path: Path, defect: str) -> None:
    review_path, mapping_path = _completed_files(tmp_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    if defect == "mapping":
        mapping["cases"][0]["case_id"] = "tampered"
        mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
    elif defect == "text":
        review["cases"][0]["text"] += " changed"
        review_path.write_text(json.dumps(review), encoding="utf-8")
    elif defect == "duplicate":
        review["cases"][1]["review_id"] = review["cases"][0]["review_id"]
        review_path.write_text(json.dumps(review), encoding="utf-8")
    else:
        review["cases"][0]["reason"] = ""
        review_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError):
        reconcile(CORPUS, review_path, mapping_path)
