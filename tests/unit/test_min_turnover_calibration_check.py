"""Unit tests for the monthly min_turnover calibration check (pure logic)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# The script lives in scripts/ (not an importable package) — load it by path.
_SPEC = importlib.util.spec_from_file_location(
    "min_turnover_calibration_check",
    Path(__file__).resolve().parents[2] / "scripts" / "min_turnover_calibration_check.py",
)
assert _SPEC and _SPEC.loader
calib = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(calib)


def test_percentile_nearest_rank() -> None:
    vals = [float(i) for i in range(1, 101)]  # 1..100
    assert calib.percentile(vals, 0.0) == 1.0
    assert calib.percentile(vals, 1.0) == 100.0
    # nearest-rank p80 of 1..100 → index round(0.8*99)=79 → value 80
    assert calib.percentile(vals, 0.80) == 80.0


def test_percentile_empty_raises() -> None:
    with pytest.raises(ValueError):
        calib.percentile([], 0.5)


def test_round_nice_picks_clean_threshold() -> None:
    assert calib.round_nice(2_920_000) == 3_000_000
    assert calib.round_nice(1_100_000) == 1_000_000
    assert calib.round_nice(4_600_000) == 5_000_000
    assert calib.round_nice(0) == 0.0


def test_assess_floor_in_band_is_ok_no_change() -> None:
    # The live 3M floor inside today's observed band → no recommendation.
    v = calib.assess_floor(1_750_000, 4_880_000, 3_000_000)
    assert v["in_band"] is True
    assert v["status"] == "ok"
    assert v["recommended_floor_usd"] is None


def test_assess_floor_too_strict_recommends_recenter() -> None:
    # Floor above p90 → too strict → drift, recommend rounded geo-mid of band.
    v = calib.assess_floor(1_750_000, 4_880_000, 20_000_000)
    assert v["in_band"] is False
    assert v["status"] == "drift"
    assert v["recommended_floor_usd"] == 3_000_000


def test_assess_floor_too_permissive_recommends_recenter() -> None:
    # Floor below p80 → too permissive → drift.
    v = calib.assess_floor(1_750_000, 4_880_000, 500_000)
    assert v["in_band"] is False
    assert v["status"] == "drift"
    assert v["recommended_floor_usd"] == 3_000_000


def test_assess_floor_at_band_edges_is_in_band() -> None:
    assert calib.assess_floor(1_750_000, 4_880_000, 1_750_000)["in_band"] is True
    assert calib.assess_floor(1_750_000, 4_880_000, 4_880_000)["in_band"] is True
