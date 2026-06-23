"""Unit tests for the replace-only-when-ready graduation engine (Phase 3 safety core).

These pin the irreversible-action invariants that protect the active source set:
- 1-in-1-out (the active set never grows from discovery);
- strictly-better (a candidate replaces a rotation source only if it scores higher);
- evidence gate (probation runs + deliveries);
- no orphan archival (a rotation source is only archived as a graduation partner).
"""

from __future__ import annotations

from app.learning.source_graduation import (
    ProbationCandidate,
    RotationCandidate,
    decide_graduation,
)


def _cand(source: str, score: float, *, runs: int = 5, deliveries: int = 10) -> ProbationCandidate:
    return ProbationCandidate(source=source, score=score, deliveries=deliveries, runs=runs)


def test_strong_candidate_replaces_weakest_rotation_source() -> None:
    plan = decide_graduation(
        [_cand("fresh", 0.70)],
        [RotationCandidate("weak", 0.20), RotationCandidate("midd", 0.40)],
    )
    assert len(plan.swaps) == 1
    swap = plan.swaps[0]
    assert swap.promote == "fresh"
    assert swap.archive == "weak"  # the WEAKEST rotation target


def test_no_graduation_without_rotation_target() -> None:
    """Replace-only-when-ready: nothing to replace → no graduation, set never grows."""
    plan = decide_graduation([_cand("fresh", 0.99)], [])
    assert plan.swaps == []
    assert ("fresh", "no_rotation_target") in plan.skipped


def test_candidate_not_strictly_better_is_skipped() -> None:
    plan = decide_graduation(
        [_cand("equal", 0.40)],
        [RotationCandidate("weak", 0.40)],  # tie, not strictly greater
    )
    assert plan.swaps == []
    assert ("equal", "not_strictly_better_than_weakest_rotation") in plan.skipped


def test_evidence_gate_blocks_thin_candidates() -> None:
    plan = decide_graduation(
        [
            _cand("too_few_runs", 0.9, runs=1),
            _cand("too_few_deliv", 0.9, deliveries=1),
        ],
        [RotationCandidate("weak", 0.1)],
    )
    assert plan.swaps == []
    reasons = dict(plan.skipped)
    assert reasons["too_few_runs"].startswith("probation_runs")
    assert reasons["too_few_deliv"].startswith("deliveries")


def test_one_in_one_out_each_rotation_source_used_once() -> None:
    plan = decide_graduation(
        [_cand("a", 0.80), _cand("b", 0.75), _cand("c", 0.72)],
        [RotationCandidate("x", 0.10), RotationCandidate("y", 0.20)],  # only two targets
    )
    # Three eligible candidates, two targets → at most two swaps.
    assert len(plan.swaps) == 2
    archived = {s.archive for s in plan.swaps}
    assert archived == {"x", "y"}  # each target consumed exactly once
    promoted = {s.promote for s in plan.swaps}
    assert promoted == {"a", "b"}  # best two candidates
    assert ("c", "no_rotation_target") in plan.skipped


def test_best_candidate_pairs_with_weakest_target_deterministic() -> None:
    plan = decide_graduation(
        [_cand("hi", 0.90), _cand("lo", 0.55)],
        [RotationCandidate("w", 0.10), RotationCandidate("s", 0.50)],
    )
    pairs = {(s.promote, s.archive) for s in plan.swaps}
    # best (hi) → weakest (w); next (lo) → next-weakest (s) only if strictly better.
    assert ("hi", "w") in pairs
    assert ("lo", "s") in pairs  # 0.55 > 0.50


def test_active_set_never_expands() -> None:
    """The number of promotions always equals the number of archivals."""
    plan = decide_graduation(
        [_cand("a", 0.9), _cand("b", 0.8)],
        [RotationCandidate("x", 0.1)],
    )
    assert len({s.promote for s in plan.swaps}) == len({s.archive for s in plan.swaps})
