"""End-to-end pipeline test for a non-calibrator parameter class.

Verifies that the whole adaptive-learning pipeline accommodates a *new*
parameter type (`signal.thresholds.min_bayes_confidence`) without any
core-pipeline changes — i.e. the same `ParameterVersionStore`,
`ApprovalService`, `write_snapshot`/`read_snapshot`, and runtime
`ActiveThreshold` loader all work with a non-calibrator payload.

Pipeline:
  Optimizer → Propose → Approve → Snapshot → Active loader → Apply.
"""

from __future__ import annotations

import random
from pathlib import Path

from app.learning.active_threshold import (
    DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
    ActiveThreshold,
)
from app.learning.approval import ApprovalService, ProposalStatus
from app.learning.parameter_version import ParameterVersionStore
from app.learning.threshold_optimizer import (
    ThresholdObservation,
    optimize_threshold,
)


def _build_observations(n: int = 120, seed: int = 99) -> list[ThresholdObservation]:
    """High-conf trades win, low-conf lose — optimizer should lift threshold."""
    rng = random.Random(seed)
    out: list[ThresholdObservation] = []
    for i in range(n):
        score = rng.uniform(0.50, 0.95)
        win = rng.random() < (0.85 if score >= 0.75 else 0.30)
        pnl = rng.uniform(50, 150) if win else -rng.uniform(50, 150)
        out.append(
            ThresholdObservation(
                observation_id=f"o_{i}", score=score, realized_pnl_usd=pnl
            )
        )
    return out


def test_full_pipeline_threshold_class_round_trip(tmp_path: Path):
    """Run the whole pipeline end-to-end on threshold parameter."""
    journal = tmp_path / "journal.jsonl"
    snap_dir = tmp_path / "snaps"

    # ── Step 1: optimize ──────────────────────────────────────────────────
    observations = _build_observations(n=120, seed=99)
    report = optimize_threshold(
        observations=observations, baseline_threshold=0.50
    )
    assert report.decision == "approve", report.decision_reasons
    new_threshold = report.best_threshold
    assert new_threshold is not None
    assert new_threshold > 0.50

    # ── Step 2: propose into hash-chained journal ────────────────────────
    svc = ApprovalService(
        ParameterVersionStore(journal), snapshot_dir=snap_dir
    )
    proposal = svc.store.propose_version(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        parameter_set={"value": new_threshold, "default": 0.50},
        evidence={
            "n_observations": report.n_observations,
            "baseline_pnl_usd": report.baseline_pnl_usd,
            "best_pnl_usd": report.best_pnl_usd,
            "improvement_usd": report.pnl_improvement_usd,
        },
    )

    # Verify it shows up as pending
    pending = svc.list_pending(parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH)
    assert len(pending) == 1
    assert isinstance(pending[0], ProposalStatus)
    assert pending[0].proposal.version_id == proposal.version_id

    # ── Step 3: operator approves ────────────────────────────────────────
    svc.approve(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        version_id=proposal.version_id,
        operator_id="sascha",
        notes="threshold optimizer e2e smoketest",
    )

    # Hash-chain remains valid after the write
    ok, err = svc.verify_chain()
    assert ok, err

    # ── Step 4: YAML snapshot was written ────────────────────────────────
    expected_yaml = snap_dir / f"{DEFAULT_MIN_BAYES_CONFIDENCE_PATH}.yaml"
    assert expected_yaml.exists()
    text = expected_yaml.read_text(encoding="utf-8")
    assert "DO NOT EDIT MANUALLY" in text
    assert proposal.version_id in text

    # ── Step 5: ActiveThreshold loads it ─────────────────────────────────
    th = ActiveThreshold.load(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        default_value=0.30,
        snapshot_dir=snap_dir,
    )
    assert th.is_active
    assert th.value == new_threshold
    assert th.version_id == proposal.version_id

    # ── Step 6: Rollback → snapshot reflects rollback ────────────────────
    # Add a second proposal + activate it, then roll back to the first
    proposal_v2 = svc.store.propose_version(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        parameter_set={"value": 0.60, "default": 0.50},
    )
    svc.approve(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        version_id=proposal_v2.version_id,
        operator_id="sascha",
    )
    th_v2 = ActiveThreshold.load(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        default_value=0.30,
        snapshot_dir=snap_dir,
    )
    assert th_v2.value == 0.60

    svc.rollback(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        version_id=proposal.version_id,
        operator_id="sascha",
        notes="rollback to optimizer pick",
    )
    th_after_rollback = ActiveThreshold.load(
        parameter_path=DEFAULT_MIN_BAYES_CONFIDENCE_PATH,
        default_value=0.30,
        snapshot_dir=snap_dir,
    )
    assert th_after_rollback.value == new_threshold
    assert th_after_rollback.version_id == proposal.version_id

    # Hash-chain still valid after the rollback
    ok, err = svc.verify_chain()
    assert ok, err
