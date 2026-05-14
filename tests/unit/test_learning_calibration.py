from __future__ import annotations

from app.learning.calibration import OutcomePair, compute_calibration


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
