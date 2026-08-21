r"""Vom eingefrorenen Input zum Verdikt — und was nach einem Absturz gilt.

Ohne festgehaltene Zustandsfolge ist nach einem Absturz nicht entscheidbar, ob
eine Auswertung bereits gelaufen ist. Der Wiederanlauf zieht dann neue Daten,
rechnet einen neuen Stichtag, bekommt einen neuen Hash — und niemand kann
hinterher sagen, welcher Lauf das Verdikt getragen hat. Genau dort entsteht
optional stopping, ohne dass es jemand beabsichtigt haette.

Die Kette lautet:

    EVALUATION_INPUT_FROZEN
        -> CHECKPOINT_DECIDED(EVALUATE, evaluation_input_sha256)
        -> EVALUATION_RUNNING
        -> VERDICT_RECORDED
        -> CLOSED

``EXTEND_TO_T2`` und ``INCONCLUSIVE_NOT_MATURE`` sind ebenfalls Entscheidungen,
duerfen aber nie in einen Auswertungslauf muenden: an ihnen entsteht kein
Performance-Artefakt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.research.evaluation_state import (
    STATE_CHECKPOINT_DECIDED,
    STATE_CLOSED,
    STATE_EVALUATION_RUNNING,
    STATE_INPUT_FROZEN,
    STATE_VERDICT_RECORDED,
    EvaluationStateError,
    VerdictRecord,
    current_state,
    record_checkpoint_decision,
    record_input_frozen,
    record_running,
    record_verdict,
    resume_target,
    verdict_of,
)

_ACT = "a" * 64
_INPUT = "b" * 64
_DATASET = "c" * 64
_EVALUATOR = "d" * 64
_CP = "T1"
_NOW = "2026-11-30T00:05:00+00:00"


def _journal(tmp_path: Path) -> Path:
    return tmp_path / "evaluation_state.jsonl"


def _verdict(**overrides) -> VerdictRecord:
    kwargs = {
        "activation_sha256": _ACT,
        "checkpoint": _CP,
        "evaluation_input_sha256": _INPUT,
        "dataset_sha256": _DATASET,
        "evaluator_sha256": _EVALUATOR,
        "verdict": "NOT_MET",
        "n_valid": 120,
        "n_clusters": 61,
        "mean_net_bps": 3.25,
        "standard_error": 1.5,
        "t_stat": 2.16,
        "degrees_of_freedom": 60,
        "p_value": 0.0348,
        "alpha": 0.05,
        "economic_floor_bps": 5.0,
        "recorded_at_utc": _NOW,
    }
    kwargs.update(overrides)
    return VerdictRecord(**kwargs)


def _walk_to_running(path: Path) -> None:
    record_input_frozen(
        path,
        activation_sha256=_ACT,
        checkpoint=_CP,
        evaluation_input_sha256=_INPUT,
        recorded_at_utc=_NOW,
    )
    record_checkpoint_decision(
        path,
        activation_sha256=_ACT,
        checkpoint=_CP,
        action="EVALUATE",
        evaluation_input_sha256=_INPUT,
        recorded_at_utc=_NOW,
    )
    record_running(
        path,
        activation_sha256=_ACT,
        checkpoint=_CP,
        evaluation_input_sha256=_INPUT,
        recorded_at_utc=_NOW,
    )


# ── Die Kette ───────────────────────────────────────────────────────────────


def test_the_happy_path_walks_the_whole_chain(tmp_path: Path) -> None:
    path = _journal(tmp_path)

    _walk_to_running(path)
    record_verdict(path, _verdict())

    assert current_state(path, activation_sha256=_ACT, checkpoint=_CP) == STATE_VERDICT_RECORDED
    assert verdict_of(path, activation_sha256=_ACT, checkpoint=_CP).verdict == "NOT_MET"


def test_an_empty_journal_has_no_state(tmp_path: Path) -> None:
    assert current_state(_journal(tmp_path), activation_sha256=_ACT, checkpoint=_CP) is None


def test_running_without_a_decision_is_refused(tmp_path: Path) -> None:
    """Ein Auswertungslauf ohne festgehaltene Entscheidung ist genau der Lauf,
    den hinterher niemand mehr einordnen kann."""
    path = _journal(tmp_path)
    record_input_frozen(
        path,
        activation_sha256=_ACT,
        checkpoint=_CP,
        evaluation_input_sha256=_INPUT,
        recorded_at_utc=_NOW,
    )

    with pytest.raises(EvaluationStateError, match=STATE_CHECKPOINT_DECIDED):
        record_running(
            path,
            activation_sha256=_ACT,
            checkpoint=_CP,
            evaluation_input_sha256=_INPUT,
            recorded_at_utc=_NOW,
        )


def test_a_verdict_without_a_running_evaluation_is_refused(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    record_input_frozen(
        path,
        activation_sha256=_ACT,
        checkpoint=_CP,
        evaluation_input_sha256=_INPUT,
        recorded_at_utc=_NOW,
    )
    record_checkpoint_decision(
        path,
        activation_sha256=_ACT,
        checkpoint=_CP,
        action="EVALUATE",
        evaluation_input_sha256=_INPUT,
        recorded_at_utc=_NOW,
    )

    with pytest.raises(EvaluationStateError, match=STATE_EVALUATION_RUNNING):
        record_verdict(path, _verdict())


def test_a_second_verdict_for_the_same_checkpoint_is_refused(tmp_path: Path) -> None:
    """Ein Checkpoint hat genau ein Verdikt. Ein zweites waere ein Nachschlag."""
    path = _journal(tmp_path)
    _walk_to_running(path)
    record_verdict(path, _verdict())

    with pytest.raises(EvaluationStateError):
        record_verdict(path, _verdict(verdict="MET"))


def test_closing_requires_a_recorded_verdict(tmp_path: Path) -> None:
    from app.research.evaluation_state import record_closed

    path = _journal(tmp_path)
    _walk_to_running(path)

    with pytest.raises(EvaluationStateError, match=STATE_VERDICT_RECORDED):
        record_closed(path, activation_sha256=_ACT, checkpoint=_CP, recorded_at_utc=_NOW)

    record_verdict(path, _verdict())
    record_closed(path, activation_sha256=_ACT, checkpoint=_CP, recorded_at_utc=_NOW)

    assert current_state(path, activation_sha256=_ACT, checkpoint=_CP) == STATE_CLOSED


# ── Kein Performance-Artefakt ohne EVALUATE ─────────────────────────────────


@pytest.mark.parametrize("action", ["EXTEND_TO_T2", "INCONCLUSIVE_NOT_MATURE"])
def test_a_non_evaluate_decision_may_not_carry_an_evaluation_input(
    tmp_path: Path, action: str
) -> None:
    """Wer verlaengert, hat nichts gemessen — und darf nichts einfrieren."""
    path = _journal(tmp_path)
    record_input_frozen(
        path,
        activation_sha256=_ACT,
        checkpoint=_CP,
        evaluation_input_sha256=_INPUT,
        recorded_at_utc=_NOW,
    )

    with pytest.raises(EvaluationStateError, match="EVALUATE"):
        record_checkpoint_decision(
            path,
            activation_sha256=_ACT,
            checkpoint=_CP,
            action=action,
            evaluation_input_sha256=_INPUT,
            recorded_at_utc=_NOW,
        )


@pytest.mark.parametrize("action", ["EXTEND_TO_T2", "INCONCLUSIVE_NOT_MATURE"])
def test_a_non_evaluate_decision_cannot_be_followed_by_a_run(tmp_path: Path, action: str) -> None:
    path = _journal(tmp_path)
    record_input_frozen(
        path,
        activation_sha256=_ACT,
        checkpoint=_CP,
        evaluation_input_sha256=_INPUT,
        recorded_at_utc=_NOW,
    )
    record_checkpoint_decision(
        path,
        activation_sha256=_ACT,
        checkpoint=_CP,
        action=action,
        evaluation_input_sha256=None,
        recorded_at_utc=_NOW,
    )

    with pytest.raises(EvaluationStateError):
        record_running(
            path,
            activation_sha256=_ACT,
            checkpoint=_CP,
            evaluation_input_sha256=_INPUT,
            recorded_at_utc=_NOW,
        )


# ── Wiederanlauf nach Absturz ───────────────────────────────────────────────


def test_a_crashed_run_resumes_on_exactly_the_same_input(tmp_path: Path) -> None:
    """Der Kern der Regel: derselbe Hash, keine neuen Daten, kein neuer Stichtag."""
    path = _journal(tmp_path)
    _walk_to_running(path)

    assert resume_target(path, activation_sha256=_ACT, checkpoint=_CP) == _INPUT


def test_a_decided_but_unstarted_evaluation_also_resumes(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    record_input_frozen(
        path,
        activation_sha256=_ACT,
        checkpoint=_CP,
        evaluation_input_sha256=_INPUT,
        recorded_at_utc=_NOW,
    )
    record_checkpoint_decision(
        path,
        activation_sha256=_ACT,
        checkpoint=_CP,
        action="EVALUATE",
        evaluation_input_sha256=_INPUT,
        recorded_at_utc=_NOW,
    )

    assert resume_target(path, activation_sha256=_ACT, checkpoint=_CP) == _INPUT


def test_nothing_to_resume_once_the_verdict_is_recorded(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    _walk_to_running(path)
    record_verdict(path, _verdict())

    assert resume_target(path, activation_sha256=_ACT, checkpoint=_CP) is None


def test_resuming_with_a_different_input_is_refused(tmp_path: Path) -> None:
    """Nach dem Absturz einen frisch gebauten Input einzusetzen ist die
    unauffaelligste Art, das Ergebnis zu wechseln."""
    path = _journal(tmp_path)
    _walk_to_running(path)

    with pytest.raises(EvaluationStateError, match="evaluation_input_sha256"):
        record_running(
            path,
            activation_sha256=_ACT,
            checkpoint=_CP,
            evaluation_input_sha256="e" * 64,
            recorded_at_utc=_NOW,
        )


def test_a_verdict_for_a_different_input_than_the_running_one_is_refused(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    _walk_to_running(path)

    with pytest.raises(EvaluationStateError, match="evaluation_input_sha256"):
        record_verdict(path, _verdict(evaluation_input_sha256="e" * 64))


# ── Was das Verdikt bindet ──────────────────────────────────────────────────


def test_the_verdict_binds_the_whole_chain_and_its_own_hash(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    _walk_to_running(path)
    record_verdict(path, _verdict())

    stored = verdict_of(path, activation_sha256=_ACT, checkpoint=_CP)

    assert stored.activation_sha256 == _ACT
    assert stored.evaluation_input_sha256 == _INPUT
    assert stored.dataset_sha256 == _DATASET
    assert stored.evaluator_sha256 == _EVALUATOR
    assert (stored.n_valid, stored.n_clusters) == (120, 61)
    assert (stored.mean_net_bps, stored.standard_error) == (3.25, 1.5)
    assert (stored.t_stat, stored.degrees_of_freedom, stored.p_value) == (2.16, 60, 0.0348)
    assert (stored.alpha, stored.economic_floor_bps) == (0.05, 5.0)
    assert len(stored.result_sha256) == 64


def test_two_different_verdicts_have_different_result_hashes() -> None:
    assert _verdict().result_sha256 != _verdict(verdict="MET").result_sha256


def test_the_result_hash_ignores_the_recording_time() -> None:
    """Ein spaeterer Wiederholungsversuch ist dasselbe Ergebnis, nicht ein neues."""
    later = _verdict(recorded_at_utc="2026-12-01T09:00:00+00:00")

    assert _verdict().result_sha256 == later.result_sha256


# ── Nebenlaeufige Checkpoints ───────────────────────────────────────────────


def test_t1_and_t2_are_independent_chains(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    _walk_to_running(path)
    record_verdict(path, _verdict())

    record_input_frozen(
        path,
        activation_sha256=_ACT,
        checkpoint="T2",
        evaluation_input_sha256="f" * 64,
        recorded_at_utc=_NOW,
    )

    assert current_state(path, activation_sha256=_ACT, checkpoint="T2") == STATE_INPUT_FROZEN
    assert current_state(path, activation_sha256=_ACT, checkpoint=_CP) == STATE_VERDICT_RECORDED
