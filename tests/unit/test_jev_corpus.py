from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.jev_shadow_eval.corpus import ROOT, load_corpus, main, prepare

CORPUS = ROOT / "tests/fixtures/jev/development_corpus.json"


def test_baseline_is_reproducible_and_not_approved() -> None:
    report = prepare(CORPUS, ROOT / "monitor")
    assert report == prepare(CORPUS, ROOT / "monitor")
    assert report["case_count"] == 24
    assert report["proposed_positive_count"] == 12
    assert report["proposed_negative_count"] == 12
    assert report["split_counts"] == {"development": 24}
    assert report["jev_called"] is False
    assert report["independent_labels_verified"] is False
    assert report["primary_ready"] is False
    assert report["status"] == "AWAITING_INDEPENDENT_LABEL_REVIEW"
    metrics = report["provisional_metrics"]
    eligible = [case for case in report["cases"] if case["classification_eligible"]]
    assert metrics["true_positive"] + metrics["false_negative"] == 12
    assert metrics["true_negative"] + metrics["false_positive"] == 11
    assert metrics["accuracy"] == round(
        (metrics["true_positive"] + metrics["true_negative"]) / len(eligible), 6
    )
    assert metrics["precision"] == round(
        metrics["true_positive"] / (metrics["true_positive"] + metrics["false_positive"]),
        6,
    )
    assert metrics["recall"] == round(
        metrics["true_positive"] / (metrics["true_positive"] + metrics["false_negative"]),
        6,
    )
    assert metrics["specificity"] == round(
        metrics["true_negative"] / (metrics["true_negative"] + metrics["false_positive"]),
        6,
    )
    assert report["classification_eligible_count"] == 23
    assert report["preclassification_rejected_ids"] == ["empty"]
    empty = next(case for case in report["cases"] if case["case_id"] == "empty")
    assert empty["classification_eligible"] is False
    assert empty["baseline_relevant"] is None
    assert empty["baseline_reason"] == "preclassification_empty"
    assert all(len(value) == 64 for value in report["monitor_sha256"].values())


@pytest.mark.parametrize("defect", ["id", "content", "group", "label", "fields"])
def test_invalid_or_leaking_corpus_is_rejected(tmp_path: Path, defect: str) -> None:
    rows, _ = load_corpus(CORPUS)
    rows = rows[:2]
    if defect == "id":
        rows[1]["case_id"] = rows[0]["case_id"]
    elif defect == "content":
        rows[1]["title"] = rows[0]["title"].upper()
        rows[1]["text"] = "  " + rows[0]["text"] + "\n"
    elif defect == "group":
        rows[1]["group_id"] = rows[0]["group_id"]
        rows[1]["split"] = "holdout"
    elif defect == "label":
        rows[1]["proposed_relevant"] = "false"
    else:
        rows[1]["response"] = {"fake": True}
    source = tmp_path / "bad.json"
    source.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError):
        load_corpus(source)


def test_missing_monitor_does_not_silently_change_baseline(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        prepare(CORPUS, tmp_path)


def test_blind_review_does_not_disclose_proposed_labels_or_predictions(tmp_path: Path) -> None:
    target = tmp_path / "review.json"
    mapping_target = tmp_path / "private-map.json"
    assert (
        main(
            [
                "--input",
                str(CORPUS),
                "--output",
                str(target),
                "--blind-review",
                "--blind-map-output",
                str(mapping_target),
            ]
        )
        == 0
    )
    report = json.loads(target.read_text(encoding="utf-8"))
    mapping = json.loads(mapping_target.read_text(encoding="utf-8"))
    assert report["reviewer"] is None
    assert report["corpus_sha256"] == load_corpus(CORPUS)[1]
    assert len(report["cases"]) == 24
    assert report["schema_version"] == "jev-blind-review/v2"
    assert mapping["handling"] == "DO_NOT_SHARE_WITH_REVIEWER_BEFORE_REVIEW_IS_FROZEN"
    assert {case["review_id"] for case in report["cases"]} == {
        case["review_id"] for case in mapping["cases"]
    }
    assert [case["case_id"] for case in mapping["cases"]] != [
        row["case_id"] for row in load_corpus(CORPUS)[0]
    ]
    for case in report["cases"]:
        assert set(case) == {"review_id", "title", "text", "relevant", "disputed", "reason"}
        assert case["relevant"] is None
        assert case["disputed"] is None
        assert case["reason"] is None

    repeat = tmp_path / "review-repeat.json"
    repeat_mapping = tmp_path / "private-map-repeat.json"
    assert (
        main(
            [
                "--input",
                str(CORPUS),
                "--output",
                str(repeat),
                "--blind-review",
                "--blind-map-output",
                str(repeat_mapping),
            ]
        )
        == 0
    )
    assert target.read_bytes() == repeat.read_bytes()
    assert mapping_target.read_bytes() == repeat_mapping.read_bytes()


def test_blind_review_requires_a_separate_mapping_file(tmp_path: Path) -> None:
    target = tmp_path / "review.json"
    argv = ["--input", str(CORPUS), "--output", str(target), "--blind-review"]
    assert main(argv) == 2
    assert not target.exists()


def test_case_ids_are_normalized_and_holdout_labels_stay_sealed(tmp_path: Path) -> None:
    rows, _ = load_corpus(CORPUS)
    rows = rows[:1]
    rows[0]["case_id"] = "  normalized-id  "
    source = tmp_path / "corpus.json"
    source.write_text(json.dumps(rows), encoding="utf-8")
    loaded, _ = load_corpus(source)
    assert loaded[0]["case_id"] == "normalized-id"
    rows[0]["split"] = "holdout"
    source.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError, match="holdout labels must stay sealed"):
        prepare(source, ROOT / "monitor")


def test_cli_never_overwrites_previous_evidence(tmp_path: Path) -> None:
    target = tmp_path / "baseline.json"
    argv = ["--input", str(CORPUS), "--output", str(target)]
    assert main(argv) == 0
    original = target.read_bytes()
    assert main(argv) == 2
    assert target.read_bytes() == original


def test_baseline_runs_the_actual_gate_with_a_known_monitor(tmp_path: Path) -> None:
    (tmp_path / "keywords.txt").write_text("", encoding="utf-8")
    (tmp_path / "entity_aliases.yml").write_text("{}", encoding="utf-8")
    (tmp_path / "watchlists.yml").write_text(
        "crypto:\n  - symbol: BTC\n    name: Bitcoin\n", encoding="utf-8"
    )
    report = prepare(CORPUS, tmp_path)
    cases = {row["case_id"]: row for row in report["cases"]}
    assert cases["btc-outage"]["baseline_relevant"] is True
    assert cases["btc-outage"]["baseline_reason"] == "has_tickers"
    assert cases["football"]["baseline_relevant"] is False
    assert cases["football"]["baseline_reason"] == "no_crypto_signal"
