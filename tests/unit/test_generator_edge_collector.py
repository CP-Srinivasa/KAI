"""Generator-Edge side-channel collector (#170 Part B): REAL-only, canary-hard-
excluded, conservative outcome semantics, honest absence."""

from __future__ import annotations

import json
from pathlib import Path

from app.observability.generator_edge import build_generator_edge_report
from app.observability.generator_edge_collector import (
    collect_edge_inputs_from_resolved,
)


def _row(**over) -> dict:
    base = {
        "candidate_id": "cyc_1",
        "symbol": "BTC/USDT",
        "side": "long",
        "regime": "breakout_up",
        "source": "autonomous_generator",
        "signal_confidence": 0.95,
        "is_canary": False,
        "fwd_60s_bps": 10.0,
        "fwd_300s_bps": 5.0,
        "fwd_900s_bps": -3.0,
        "fwd_3600s_bps": 12.0,
        "reached_take": False,
        "reached_stop": False,
        "mfe_before_mae": True,
    }
    base.update(over)
    return base


def _write(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "resolved.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def test_collects_ic_pairs_per_horizon_and_outcome(tmp_path: Path) -> None:
    p = _write(tmp_path, [_row()])
    c = collect_edge_inputs_from_resolved(p)
    assert c.resolved_real == 1
    ic = c.ic_aligned_by_cohort["autonomous_generator"]
    assert ic["1m"] == [(0.95, 10.0)]
    assert ic["1h"] == [(0.95, 12.0)]
    pairs = c.outcome_pairs_by_cohort["autonomous_generator"]
    assert len(pairs) == 1
    # neither take nor stop touched → sign of 1h fwd (+12 bps) → win
    assert pairs[0].actual_outcome == 1
    assert pairs[0].predicted_probability == 0.95


def test_canary_and_non_real_are_hard_excluded(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        [
            _row(candidate_id="c1", is_canary=True),
            _row(candidate_id="c2", source="autonomous_loop"),
            _row(candidate_id="c3", source="canary_probe"),
        ],
    )
    c = collect_edge_inputs_from_resolved(p)
    assert c.resolved_real == 0
    assert c.skipped_canary == 1
    assert c.skipped_non_real == 2
    assert c.ic_aligned_by_cohort == {}


def test_outcome_semantics_take_stop_and_first_touch(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        [
            _row(candidate_id="win", reached_take=True, reached_stop=False),
            _row(candidate_id="loss", reached_take=False, reached_stop=True),
            _row(
                candidate_id="both_take_first",
                reached_take=True,
                reached_stop=True,
                mfe_before_mae=True,
            ),
            _row(
                candidate_id="both_stop_first",
                reached_take=True,
                reached_stop=True,
                mfe_before_mae=False,
            ),
            _row(candidate_id="undecidable", fwd_3600s_bps=0.0),
        ],
    )
    c = collect_edge_inputs_from_resolved(p)
    outcomes = {
        x.decision_id: x.actual_outcome for x in c.outcome_pairs_by_cohort["autonomous_generator"]
    }
    assert outcomes == {"win": 1, "loss": 0, "both_take_first": 1, "both_stop_first": 0}
    assert c.skipped_undecidable_outcome == 1


def test_missing_file_yields_empty_inputs(tmp_path: Path) -> None:
    c = collect_edge_inputs_from_resolved(tmp_path / "nope.jsonl")
    assert c.resolved_real == 0
    assert c.ic_aligned_by_cohort == {}


def test_regime_cohorts(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        [
            _row(candidate_id="a", regime="breakout_up"),
            _row(candidate_id="b", regime="chop_quiet"),
        ],
    )
    c = collect_edge_inputs_from_resolved(p, cohort_type="regime")
    assert set(c.ic_aligned_by_cohort) == {"breakout_up", "chop_quiet"}


def test_side_channel_only_cohort_gets_profile_with_ic_and_brier(tmp_path: Path) -> None:
    """End-to-end into the instrument: a shadow-only cohort (no closed trades)
    must appear in the report with IC + Brier instead of being dropped."""
    # >= 20 aligned samples: compute_ic_by_horizon's min_sample honesty gate
    # (an IC from a handful of pairs would be statistical noise).
    rows = [
        _row(candidate_id=f"c{i}", signal_confidence=0.9, fwd_3600s_bps=20.0 + i) for i in range(12)
    ] + [
        _row(candidate_id=f"d{i}", signal_confidence=0.6, fwd_3600s_bps=-(10.0 + i))
        for i in range(12)
    ]
    p = _write(tmp_path, rows)
    c = collect_edge_inputs_from_resolved(p)
    report = build_generator_edge_report(
        [],  # no closed trades at all
        ic_aligned_by_cohort=c.ic_aligned_by_cohort,
        outcome_pairs_by_cohort=c.outcome_pairs_by_cohort,
    )
    d = report.to_dict()
    profiles = {pr["cohort_key"]: pr for pr in d["profiles"]}
    assert "autonomous_generator" in profiles
    prof = profiles["autonomous_generator"]
    assert prof["resolved_count"] == 0
    assert prof["verdict"] == "INSUFFICIENT"  # honest: no trades to judge
    assert prof["ic_by_horizon"]["1h"] is not None  # IC computed from alignment
    assert prof["brier_score"] is not None  # calibration from outcome pairs
