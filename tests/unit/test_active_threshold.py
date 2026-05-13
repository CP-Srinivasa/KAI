"""Unit tests for the boot-time threshold loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.learning.active_threshold import (
    DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
    ActiveThreshold,
)
from app.learning.config_snapshot import write_snapshot

# --------------------------------------------------------------------- helpers


def _write_snapshot(
    tmp_path: Path,
    *,
    value: float,
    parameter_path: str = DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
    version_id: str = "pv_threshold_test",
) -> None:
    write_snapshot(
        parameter_path=parameter_path,
        parameter_set={"value": value, "default": 0.30},
        version_id=version_id,
        activated_at_utc="2026-05-09T16:30:00+00:00",
        activated_by="test",
        snapshot_dir=tmp_path,
    )


# ============================================================================
# Loading
# ============================================================================


def test_load_returns_default_when_no_snapshot(tmp_path: Path):
    th = ActiveThreshold.load(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        default_value=0.30,
        snapshot_dir=tmp_path,
    )
    assert not th.is_active
    assert th.value == 0.30
    assert th.default_value == 0.30
    assert th.version_id is None


def test_load_picks_up_snapshot_value(tmp_path: Path):
    _write_snapshot(tmp_path, value=0.45)
    th = ActiveThreshold.load(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        default_value=0.30,
        snapshot_dir=tmp_path,
    )
    assert th.is_active
    assert th.value == 0.45
    assert th.version_id == "pv_threshold_test"


def test_load_falls_back_to_default_when_value_missing(tmp_path: Path):
    write_snapshot(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        parameter_set={"unrelated": "field"},
        version_id="pv_broken",
        activated_at_utc="2026-05-09T16:35:00+00:00",
        activated_by="test",
        snapshot_dir=tmp_path,
    )
    th = ActiveThreshold.load(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        default_value=0.30,
        snapshot_dir=tmp_path,
    )
    assert not th.is_active  # malformed payload → treated as inactive
    assert th.value == 0.30


def test_load_falls_back_when_value_is_not_a_number(tmp_path: Path):
    write_snapshot(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        parameter_set={"value": "not-a-number"},
        version_id="pv_bad_value",
        activated_at_utc="2026-05-09T16:40:00+00:00",
        activated_by="test",
        snapshot_dir=tmp_path,
    )
    th = ActiveThreshold.load(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        default_value=0.30,
        snapshot_dir=tmp_path,
    )
    assert not th.is_active
    assert th.value == 0.30


def test_fixed_constructor_skips_snapshot_lookup():
    th = ActiveThreshold.fixed(parameter_path="anything", value=0.42)
    assert th.value == 0.42
    assert not th.is_active
    assert th.version_id is None


# ============================================================================
# State exposure
# ============================================================================


def test_state_exposes_audit_metadata(tmp_path: Path):
    _write_snapshot(tmp_path, value=0.40)
    th = ActiveThreshold.load(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        default_value=0.30,
        snapshot_dir=tmp_path,
    )
    assert th.state.activated_at_utc == "2026-05-09T16:30:00+00:00"
    assert th.parameter_path == DEFAULT_MIN_BAYES_CONFIDENCE_PATH


def test_threshold_is_immutable_after_load(tmp_path: Path):
    """ActiveThresholdState is a frozen dataclass — no live re-load."""
    _write_snapshot(tmp_path, value=0.40)
    th = ActiveThreshold.load(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        default_value=0.30,
        snapshot_dir=tmp_path,
    )
    # Overwrite snapshot file behind our back
    _write_snapshot(tmp_path, value=0.99, version_id="pv_drift")
    # Old instance still reflects original value
    assert th.value == 0.40
    # A fresh load picks up the new value
    th2 = ActiveThreshold.load(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        default_value=0.30,
        snapshot_dir=tmp_path,
    )
    assert th2.value == 0.99


def test_state_dataclass_is_frozen():
    import dataclasses

    th = ActiveThreshold.fixed(parameter_path="p", value=0.5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        th.state.value = 0.99  # type: ignore[misc]
