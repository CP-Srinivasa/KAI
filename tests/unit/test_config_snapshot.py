"""Unit tests for the YAML config-snapshot pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.learning.approval import ApprovalService
from app.learning.config_snapshot import (
    ConfigSnapshot,
    read_snapshot,
    remove_snapshot,
    snapshot_path,
    write_snapshot,
)
from app.learning.parameter_version import ParameterVersionStore

# ============================================================================
# Pure helpers
# ============================================================================


def test_snapshot_path_translates_dotted_namespace_to_filename(tmp_path: Path):
    out = snapshot_path("bayes.calibrator.global", tmp_path)
    assert out == tmp_path / "bayes.calibrator.global.yaml"


def test_snapshot_path_sanitizes_unsafe_characters(tmp_path: Path):
    out = snapshot_path("foo/bar baz", tmp_path)
    # both '/' and ' ' get replaced with '_'
    assert out.name == "foo_bar_baz.yaml"


def test_snapshot_path_rejects_blank_path(tmp_path: Path):
    with pytest.raises(ValueError, match="non-empty"):
        snapshot_path("", tmp_path)
    with pytest.raises(ValueError, match="non-empty"):
        snapshot_path("   ", tmp_path)


# ============================================================================
# write_snapshot / read_snapshot
# ============================================================================


def test_write_then_read_round_trip(tmp_path: Path):
    target = write_snapshot(
        parameter_path="bayes.calibrator.global",
        parameter_set={"intercept": 0.05, "slope": 0.92, "n_fitted": 80},
        version_id="pv_abc123def456",
        activated_at_utc="2026-05-09T15:40:00+00:00",
        activated_by="sascha",
        snapshot_dir=tmp_path,
    )
    assert target.exists()
    snap = read_snapshot("bayes.calibrator.global", tmp_path)
    assert snap is not None
    assert snap.parameter_path == "bayes.calibrator.global"
    assert snap.version_id == "pv_abc123def456"
    assert snap.activated_at_utc == "2026-05-09T15:40:00+00:00"
    assert snap.activated_by == "sascha"
    assert snap.parameter_set == {"intercept": 0.05, "slope": 0.92, "n_fitted": 80}


def test_read_snapshot_returns_none_when_file_missing(tmp_path: Path):
    assert read_snapshot("nonexistent.path", tmp_path) is None


def test_read_snapshot_handles_malformed_yaml_gracefully(tmp_path: Path):
    """A corrupt YAML file must not crash the boot path — return None."""
    target = snapshot_path("broken.path", tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("::: not yaml :::\n[unclosed", encoding="utf-8")
    assert read_snapshot("broken.path", tmp_path) is None


def test_read_snapshot_rejects_non_mapping_root(tmp_path: Path):
    target = snapshot_path("listy.path", tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("- a\n- b\n- c\n", encoding="utf-8")
    assert read_snapshot("listy.path", tmp_path) is None


def test_read_snapshot_rejects_missing_required_fields(tmp_path: Path):
    target = snapshot_path("partial.path", tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump({"parameter_path": "partial.path"}), encoding="utf-8")
    assert read_snapshot("partial.path", tmp_path) is None


def test_write_overwrites_previous_snapshot(tmp_path: Path):
    write_snapshot(
        parameter_path="p",
        parameter_set={"v": 1},
        version_id="pv_first",
        activated_at_utc="2026-01-01T00:00:00+00:00",
        activated_by="op",
        snapshot_dir=tmp_path,
    )
    write_snapshot(
        parameter_path="p",
        parameter_set={"v": 2},
        version_id="pv_second",
        activated_at_utc="2026-02-02T00:00:00+00:00",
        activated_by="op",
        snapshot_dir=tmp_path,
    )
    snap = read_snapshot("p", tmp_path)
    assert snap is not None
    assert snap.version_id == "pv_second"
    assert snap.parameter_set == {"v": 2}


def test_snapshot_carries_audit_header(tmp_path: Path):
    target = write_snapshot(
        parameter_path="p",
        parameter_set={"v": 1},
        version_id="pv_x",
        activated_at_utc="2026-01-01T00:00:00+00:00",
        activated_by="op",
        snapshot_dir=tmp_path,
    )
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# Auto-generated")
    assert "DO NOT EDIT MANUALLY" in text
    assert "parameter_journal.jsonl" in text


def test_remove_snapshot(tmp_path: Path):
    write_snapshot(
        parameter_path="p",
        parameter_set={"v": 1},
        version_id="pv_x",
        activated_at_utc="2026-01-01T00:00:00+00:00",
        activated_by="op",
        snapshot_dir=tmp_path,
    )
    assert remove_snapshot("p", tmp_path) is True
    assert remove_snapshot("p", tmp_path) is False  # already gone
    assert read_snapshot("p", tmp_path) is None


# ============================================================================
# ApprovalService integration
# ============================================================================


def test_approval_writes_snapshot_when_snapshot_dir_configured(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    snap_dir = tmp_path / "snaps"
    svc = ApprovalService(ParameterVersionStore(journal), snapshot_dir=snap_dir)
    proposal = svc.store.propose_version(
        parameter_path="bayes.calibrator.global",
        parameter_set={"intercept": 0.05, "slope": 0.92, "n_fitted": 80},
        evidence={"brier_before": 0.21, "brier_after": 0.18},
    )
    svc.approve(
        parameter_path="bayes.calibrator.global",
        version_id=proposal.version_id,
        operator_id="sascha",
        notes="approved after walk-forward + counterfactual",
    )
    snap = read_snapshot("bayes.calibrator.global", snap_dir)
    assert snap is not None
    assert snap.version_id == proposal.version_id
    assert snap.activated_by == "sascha"
    assert snap.parameter_set == {"intercept": 0.05, "slope": 0.92, "n_fitted": 80}


def test_approval_does_not_write_snapshot_when_dir_is_none(tmp_path: Path):
    """Backward compat: a service without snapshot_dir must not touch disk."""
    journal = tmp_path / "journal.jsonl"
    svc = ApprovalService(ParameterVersionStore(journal))  # no snapshot_dir
    proposal = svc.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    svc.approve(parameter_path="p", version_id=proposal.version_id, operator_id="op")
    # no config dir created anywhere under tmp_path beyond the journal itself
    config_dir = tmp_path / "config"
    assert not config_dir.exists()


def test_rollback_refreshes_snapshot_to_earlier_version(tmp_path: Path):
    journal = tmp_path / "journal.jsonl"
    snap_dir = tmp_path / "snaps"
    svc = ApprovalService(ParameterVersionStore(journal), snapshot_dir=snap_dir)
    p1 = svc.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    p2 = svc.store.propose_version(parameter_path="p", parameter_set={"v": 2})
    svc.approve(parameter_path="p", version_id=p1.version_id, operator_id="op")
    svc.approve(parameter_path="p", version_id=p2.version_id, operator_id="op")
    snap_after_approve = read_snapshot("p", snap_dir)
    assert snap_after_approve.parameter_set == {"v": 2}

    svc.rollback(
        parameter_path="p",
        version_id=p1.version_id,
        operator_id="op",
        notes="regression detected",
    )
    snap_after_rollback = read_snapshot("p", snap_dir)
    assert snap_after_rollback is not None
    assert snap_after_rollback.version_id == p1.version_id
    assert snap_after_rollback.parameter_set == {"v": 1}


def test_reject_does_not_touch_active_snapshot(tmp_path: Path):
    """Rejecting a *different* (pending) proposal must not overwrite the
    snapshot of the currently-active one."""
    journal = tmp_path / "journal.jsonl"
    snap_dir = tmp_path / "snaps"
    svc = ApprovalService(ParameterVersionStore(journal), snapshot_dir=snap_dir)
    p1 = svc.store.propose_version(parameter_path="p", parameter_set={"v": 1})
    svc.approve(parameter_path="p", version_id=p1.version_id, operator_id="op")
    p2 = svc.store.propose_version(parameter_path="p", parameter_set={"v": 2})
    svc.reject(
        parameter_path="p",
        version_id=p2.version_id,
        operator_id="op",
        reason="OoS performance regression",
    )
    snap = read_snapshot("p", snap_dir)
    assert snap.parameter_set == {"v": 1}


def test_snapshot_persists_regime_bundle_round_trip(tmp_path: Path):
    """Realistic shape: a regime calibrator bundle → JSONL → YAML snapshot."""
    from app.learning.calibration import OutcomePair
    from app.learning.regime_calibration import fit_regime_calibrators

    rng_pairs: list[OutcomePair] = []
    import random

    rng = random.Random(11)
    for regime in ("low_vol", "high_vol"):
        for i in range(60):
            p = rng.uniform(0.70, 0.95)
            win = rng.random() < (0.85 if regime == "low_vol" else 0.30)
            rng_pairs.append(
                OutcomePair(
                    decision_id=f"{regime}_{i}",
                    predicted_probability=p,
                    actual_outcome=1 if win else 0,
                    regime=regime,
                )
            )
    bundle = fit_regime_calibrators(rng_pairs, min_pairs_per_regime=30)
    payload = bundle.to_parameter_set()

    journal = tmp_path / "journal.jsonl"
    snap_dir = tmp_path / "snaps"
    svc = ApprovalService(ParameterVersionStore(journal), snapshot_dir=snap_dir)
    proposal = svc.store.propose_version(
        parameter_path="bayes.calibrator.regime_bundle",
        parameter_set=payload,
    )
    svc.approve(
        parameter_path="bayes.calibrator.regime_bundle",
        version_id=proposal.version_id,
        operator_id="sascha",
    )
    snap = read_snapshot("bayes.calibrator.regime_bundle", snap_dir)
    assert snap is not None
    assert "regimes" in snap.parameter_set
    assert "global" in snap.parameter_set


def test_config_snapshot_dataclass_is_frozen():
    """frozen dataclass → assigning a field raises FrozenInstanceError."""
    import dataclasses

    snap = ConfigSnapshot(
        parameter_path="p",
        version_id="pv_x",
        activated_at_utc="2026-01-01T00:00:00+00:00",
        activated_by="op",
        parameter_set={},
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.parameter_set = {"v": 999}  # type: ignore[misc]
