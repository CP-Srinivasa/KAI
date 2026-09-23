from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.jev_shadow_eval.corpus import ROOT
from scripts.jev_shadow_eval.holdout_baseline import capture, main


def _packets(tmp_path: Path) -> tuple[Path, Path]:
    title = "Bitcoin update"
    text = "Bitcoin market infrastructure remains active."
    from scripts.jev_shadow_eval.holdout_baseline import _content_hash

    review = {
        "schema_version": "jev-holdout-blind-review/v1",
        "status": "AWAITING_INDEPENDENT_LABEL_REVIEW",
        "pool_sha256": "a" * 64,
        "case_count": 1,
        "reviewer": None,
        "cases": [
            {
                "review_id": "holdout-0001",
                "title": title,
                "text": text,
                "relevant": None,
                "disputed": None,
                "reason": None,
            }
        ],
    }
    mapping = {
        "schema_version": "jev-holdout-private-map/v1",
        "handling": "DO_NOT_SHARE_WITH_REVIEWER_BEFORE_REVIEW_IS_FROZEN",
        "pool_sha256": "a" * 64,
        "case_count": 1,
        "cases": [
            {
                "review_id": "holdout-0001",
                "doc_id": "doc-1",
                "content_sha256": _content_hash(title, text),
            }
        ],
    }
    review_path = tmp_path / "review.json"
    mapping_path = tmp_path / "mapping.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
    return review_path, mapping_path


def test_capture_is_reproducible_and_unscored(tmp_path: Path) -> None:
    review, mapping = _packets(tmp_path)
    report = capture(review, mapping, ROOT / "monitor")
    assert report == capture(review, mapping, ROOT / "monitor")
    assert report["status"] == "BASELINE_CAPTURED_NOT_SCORED"
    assert report["case_count"] == 1
    assert report["baseline_positive_count"] + report["baseline_negative_count"] == 1
    assert report["jev_called"] is False
    assert report["primary_ready"] is False
    assert len(report["code_sha256"]["scripts/jev_shadow_eval/holdout_baseline.py"]) == 64


@pytest.mark.parametrize("defect", ["hash", "label", "missing", "duplicate"])
def test_capture_rejects_tampered_or_labeled_packets(tmp_path: Path, defect: str) -> None:
    review_path, mapping_path = _packets(tmp_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    if defect == "hash":
        mapping["cases"][0]["content_sha256"] = "b" * 64
        mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
    elif defect == "label":
        review["cases"][0]["relevant"] = True
        review_path.write_text(json.dumps(review), encoding="utf-8")
    elif defect == "missing":
        review["cases"] = []
        review["case_count"] = 0
        review_path.write_text(json.dumps(review), encoding="utf-8")
    else:
        review["cases"].append(dict(review["cases"][0]))
        review["case_count"] = 2
        review_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError):
        capture(review_path, mapping_path, ROOT / "monitor")


def test_cli_never_overwrites_baseline(tmp_path: Path) -> None:
    review, mapping = _packets(tmp_path)
    output = tmp_path / "baseline.json"
    argv = ["--review", str(review), "--mapping", str(mapping), "--output", str(output)]
    assert main(argv) == 0
    original = output.read_bytes()
    assert main(argv) == 2
    assert output.read_bytes() == original
