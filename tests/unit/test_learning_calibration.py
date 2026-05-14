from __future__ import annotations

import json
from pathlib import Path

from app.learning.calibration import OutcomePair, compute_calibration
from app.learning.calibration_loader import pairs_from_bayes_audit


def test_compute_calibration_empty_input_is_non_blocking() -> None:
    report = compute_calibration([])

    assert report.n_pairs == 0
    assert report.sample_sufficient is False
    assert report.brier_score is None
    assert report.bins == ()


def test_compute_calibration_reports_brier_logloss_and_bins() -> None:
    report = compute_calibration(
        [
            OutcomePair(decision_id="d1", predicted_probability=0.80, actual_outcome=1),
            OutcomePair(decision_id="d2", predicted_probability=0.20, actual_outcome=0),
            OutcomePair(decision_id="d3", predicted_probability=0.70, actual_outcome=1),
        ],
        n_bins=5,
        min_sample_for_judgment=2,
    )

    assert report.n_pairs == 3
    assert report.sample_sufficient is True
    assert report.brier_score is not None
    assert report.brier_score < 0.1
    assert report.log_loss is not None
    assert len(report.bins) == 5


def test_pairs_from_bayes_audit_matches_outcomes_and_flips_short(tmp_path: Path) -> None:
    audit_path = tmp_path / "bayes.jsonl"
    rows = [
        {
            "schema_version": 1,
            "timestamp_utc": "2026-05-09T12:00:00+00:00",
            "decision_id": "long-1",
            "symbol": "BTC/USDT",
            "direction": "long",
            "report": {"posterior_probability": 0.75},
        },
        {
            "schema_version": 1,
            "timestamp_utc": "2026-05-09T12:01:00+00:00",
            "decision_id": "short-1",
            "symbol": "ETH/USDT",
            "direction": "short",
            "report": {"posterior_probability": 0.20},
        },
    ]
    audit_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    pairs = pairs_from_bayes_audit(
        bayes_audit_path=audit_path,
        outcomes={"long-1": 1, "short-1": 0},
    )

    assert [p.decision_id for p in pairs] == ["long-1", "short-1"]
    assert pairs[0].predicted_probability == 0.75
    assert pairs[1].predicted_probability == 0.80
