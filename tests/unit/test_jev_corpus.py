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
    assert main(["--input", str(CORPUS), "--output", str(target), "--blind-review"]) == 0
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["reviewer"] is None
    assert report["corpus_sha256"] == load_corpus(CORPUS)[1]
    assert len(report["cases"]) == 24
    for case in report["cases"]:
        assert set(case) == {"case_id", "title", "text", "relevant", "disputed", "reason"}
        assert case["relevant"] is None
        assert case["disputed"] is None
        assert case["reason"] is None


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
