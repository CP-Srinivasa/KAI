r"""Ein Neustart darf den T1-Checkpoint nicht wieder oeffnen.

``decide_window_action`` ist zustandslos und bekommt ``t1_outcome`` mitgegeben.
Ohne Persistenz waere das ein Loch mit einer bequemen Ausrede: nach einem
Neustart wuesste niemand mehr, dass an T1 bereits verlaengert wurde, der
Checkpoint stuende wieder offen — und wer dann auswertet, hat zweimal
hingesehen. Das ist optional stopping, nur mit Stromausfall davor.

Drei Eigenschaften werden hier gehalten:

* **genau einmal entschieden** — ein zweiter T1-Eintrag mit anderer Aktion wird
  abgewiesen, ein identischer ist ein No-Op (Absturz-Sicherheit)
* **fail-closed** — ein beschaedigtes Journal fuehrt zum Abbruch, NICHT zu der
  Annahme "T1 war noch nicht"
* **kein fremdes Journal** — Eintraege einer anderen Aktivierung sind ein
  Fehler, kein Rauschen
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.research.prereg_window import (
    ACTION_CLOSED,
    ACTION_EVALUATE,
    ACTION_EXTEND_TO_T2,
    ACTION_INCONCLUSIVE,
    ACTION_RESUME_EVALUATION,
    ACTION_WAIT,
    CHECKPOINT_T1,
    MaturityCounts,
)
from app.research.prereg_window_state import (
    CheckpointConflictError,
    CheckpointJournalError,
    CheckpointRecord,
    load_checkpoints,
    record_checkpoint,
    resolve_t1_outcome,
    resolve_window,
)

_ACT = "a" * 64
_OTHER_ACT = "b" * 64
_T1 = "2026-11-30T00:00:00+00:00"
_T2 = "2027-02-28T00:00:00+00:00"
_BEFORE_T1 = "2026-11-01T00:00:00+00:00"
_BETWEEN = "2026-12-20T00:00:00+00:00"


def _counts(n_valid: int, n_clusters: int) -> MaturityCounts:
    return MaturityCounts(n_valid=n_valid, n_clusters=n_clusters)


def _resolve(path: Path, now: str, counts: MaturityCounts):
    return resolve_window(
        now_utc=now,
        t1_utc=_T1,
        t2_utc=_T2,
        counts=counts,
        n_valid_min=100,
        cluster_min=50,
        activation_sha256=_ACT,
        state_path=path,
    )


def _record(checkpoint: str, action: str) -> CheckpointRecord:
    return CheckpointRecord(
        activation_sha256=_ACT,
        checkpoint=checkpoint,
        action=action,
        mature=action == ACTION_EVALUATE,
        recorded_at_utc=_T1,
    )


# ── Journal-Grundlagen ──────────────────────────────────────────────────────


def test_a_missing_journal_is_empty_not_an_error(tmp_path: Path) -> None:
    """Vor dem ersten Checkpoint gibt es nichts — das ist kein Defekt."""
    assert load_checkpoints(tmp_path / "nope.jsonl", activation_sha256=_ACT) == ()
    assert resolve_t1_outcome(tmp_path / "nope.jsonl", activation_sha256=_ACT) is None


def test_a_recorded_outcome_survives_a_restart(tmp_path: Path) -> None:
    """Der Kern: was an T1 entschieden wurde, muss danach lesbar sein."""
    path = tmp_path / "checkpoints.jsonl"

    assert record_checkpoint(path, _record(CHECKPOINT_T1, ACTION_EXTEND_TO_T2)) is True

    assert resolve_t1_outcome(path, activation_sha256=_ACT) == ACTION_EXTEND_TO_T2


def test_recording_the_same_decision_twice_is_a_no_op(tmp_path: Path) -> None:
    """Absturz zwischen Schreiben und Weiterarbeiten darf nichts blockieren."""
    path = tmp_path / "checkpoints.jsonl"
    record_checkpoint(path, _record(CHECKPOINT_T1, ACTION_EXTEND_TO_T2))

    assert record_checkpoint(path, _record(CHECKPOINT_T1, ACTION_EXTEND_TO_T2)) is False
    assert len(load_checkpoints(path, activation_sha256=_ACT)) == 1


def test_deciding_the_same_checkpoint_differently_is_refused(tmp_path: Path) -> None:
    """Ein Checkpoint wird genau einmal entschieden — sonst waere er keiner."""
    path = tmp_path / "checkpoints.jsonl"
    record_checkpoint(path, _record(CHECKPOINT_T1, ACTION_EXTEND_TO_T2))

    with pytest.raises(CheckpointConflictError, match="genau einmal"):
        record_checkpoint(path, _record(CHECKPOINT_T1, ACTION_EVALUATE))


def test_the_journal_is_append_only(tmp_path: Path) -> None:
    """Bestehende Zeilen werden nie umgeschrieben."""
    path = tmp_path / "checkpoints.jsonl"
    record_checkpoint(path, _record(CHECKPOINT_T1, ACTION_EXTEND_TO_T2))
    first_line = path.read_text(encoding="utf-8").splitlines()[0]

    record_checkpoint(path, _record("T2", ACTION_INCONCLUSIVE))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == first_line
    assert len(lines) == 2


# ── Fail-closed ─────────────────────────────────────────────────────────────


def test_a_corrupt_journal_aborts_instead_of_assuming(tmp_path: Path) -> None:
    """ "Unbekannter T1-Ausgang" wuerde den Checkpoint ein zweites Mal oeffnen.

    Genau deshalb ein Abbruch statt einer Annahme — die bequeme Annahme ist hier
    die gefaehrliche.
    """
    path = tmp_path / "checkpoints.jsonl"
    path.write_text("{kaputt\n", encoding="utf-8")

    with pytest.raises(CheckpointJournalError, match="zweites Mal"):
        resolve_t1_outcome(path, activation_sha256=_ACT)


def test_a_record_missing_fields_aborts(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints.jsonl"
    path.write_text('{"activation_sha256": "' + _ACT + '"}\n', encoding="utf-8")

    with pytest.raises(CheckpointJournalError, match="fehlen Felder"):
        load_checkpoints(path, activation_sha256=_ACT)


def test_a_journal_from_another_activation_aborts(tmp_path: Path) -> None:
    """Falsche Datei ist ein Fehler, kein Rauschen."""
    path = tmp_path / "checkpoints.jsonl"
    record_checkpoint(
        path,
        CheckpointRecord(
            activation_sha256=_OTHER_ACT,
            checkpoint=CHECKPOINT_T1,
            action=ACTION_EVALUATE,
            mature=True,
            recorded_at_utc=_T1,
        ),
    )

    with pytest.raises(CheckpointJournalError, match="falsches Journal"):
        load_checkpoints(path, activation_sha256=_ACT)


def test_blank_lines_are_tolerated(tmp_path: Path) -> None:
    """Ein abschliessender Zeilenumbruch darf kein Abbruchgrund sein."""
    path = tmp_path / "checkpoints.jsonl"
    record_checkpoint(path, _record(CHECKPOINT_T1, ACTION_EXTEND_TO_T2))
    path.write_text(path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")

    assert resolve_t1_outcome(path, activation_sha256=_ACT) == ACTION_EXTEND_TO_T2


# ── Zusammenspiel mit der Fensterregel ──────────────────────────────────────


def test_waiting_before_t1_writes_nothing(tmp_path: Path) -> None:
    """``WAIT`` ist ein Zustand, keine Entscheidung — es gehoert nicht ins Journal."""
    path = tmp_path / "checkpoints.jsonl"

    decision = _resolve(path, _BEFORE_T1, _counts(146, 75))

    assert decision.action == ACTION_WAIT
    assert not path.exists()


def test_an_extension_at_t1_survives_and_holds_until_t2(tmp_path: Path) -> None:
    """Der eigentliche Zweck dieser Datei, Ende zu Ende.

    Ohne Journal saehe der zweite Aufruf einen offenen T1-Checkpoint — und
    wuerde bei inzwischen erreichter Reife auswerten. Zweimal hingesehen.
    """
    path = tmp_path / "checkpoints.jsonl"

    first = _resolve(path, _T1, _counts(80, 41))
    assert first.action == ACTION_EXTEND_TO_T2

    # Neustart: neuer Prozess, nur das Journal ist geblieben — und die Reife ist
    # inzwischen erreicht.
    second = _resolve(path, _BETWEEN, _counts(200, 90))

    assert second.action == ACTION_WAIT
    assert not second.may_evaluate


def test_an_evaluate_without_a_verdict_resumes_instead_of_closing(tmp_path: Path) -> None:
    """Ein Absturz zwischen Entschluss und p-Wert darf das Ergebnis nicht verschlucken.

    Frueher galt hier ``CLOSED`` — der Entschluss stand, das Verdikt fehlte, und
    das Experiment war zu Ende, ohne je ein Ergebnis gehabt zu haben. Jetzt wird
    wiederaufgenommen, aber ausschliesslich auf dem eingefrorenen Datenschnitt.
    """
    path = tmp_path / "checkpoints.jsonl"

    first = _resolve(path, _T1, _counts(146, 75))
    assert first.action == ACTION_EVALUATE

    second = _resolve(path, _T2, _counts(300, 150))

    assert second.action == ACTION_RESUME_EVALUATION
    assert second.may_evaluate
    assert second.must_use_frozen_input, "keine aktuellen Daten, nur das Artefakt"


def test_only_a_recorded_verdict_closes_the_experiment(tmp_path: Path) -> None:
    """Erst ``VERDICT_RECORDED`` schliesst — nicht schon der Entschluss."""
    path = tmp_path / "checkpoints.jsonl"
    _resolve(path, _T1, _counts(146, 75))

    closed = resolve_window(
        now_utc=_T2,
        t1_utc=_T1,
        t2_utc=_T2,
        counts=_counts(300, 150),
        n_valid_min=100,
        cluster_min=50,
        activation_sha256=_ACT,
        state_path=path,
        verdict_recorded=True,
    )

    assert closed.action == ACTION_CLOSED
    assert not closed.may_evaluate


def test_the_decision_is_recorded_before_it_is_returned(tmp_path: Path) -> None:
    """Ein Absturz NACH der Rueckgabe laesst den Checkpoint entschieden zurueck.

    Offen waere die Einladung, ihn noch einmal zu entscheiden.
    """
    path = tmp_path / "checkpoints.jsonl"

    decision = _resolve(path, _T1, _counts(80, 41))

    assert path.exists(), "das Journal muss stehen, bevor der Aufrufer weitermacht"
    assert resolve_t1_outcome(path, activation_sha256=_ACT) == decision.action


def test_the_journal_carries_the_blind_counts_only(tmp_path: Path) -> None:
    """Auch hier keine Performance — sonst waere die Trennung nur halb."""
    path = tmp_path / "checkpoints.jsonl"
    _resolve(path, _T1, _counts(80, 41))

    stored = load_checkpoints(path, activation_sha256=_ACT)[0].counts

    assert stored["n_valid"] == 80
    assert stored["n_clusters"] == 41
    for forbidden in ("mean_bps", "p_value", "hit_rate"):
        assert forbidden not in stored


def test_an_immature_t2_is_recorded_as_inconclusive(tmp_path: Path) -> None:
    path = tmp_path / "checkpoints.jsonl"
    _resolve(path, _T1, _counts(80, 41))

    decision = _resolve(path, _T2, _counts(88, 44))

    assert decision.action == ACTION_INCONCLUSIVE
    actions = {r.checkpoint: r.action for r in load_checkpoints(path, activation_sha256=_ACT)}
    assert actions == {CHECKPOINT_T1: ACTION_EXTEND_TO_T2, "T2": ACTION_INCONCLUSIVE}


def test_resolving_twice_at_t2_is_idempotent(tmp_path: Path) -> None:
    """Wiederholtes Aufrufen darf weder scheitern noch das Journal aufblaehen."""
    path = tmp_path / "checkpoints.jsonl"
    _resolve(path, _T1, _counts(80, 41))
    _resolve(path, _T2, _counts(88, 44))

    _resolve(path, _T2, _counts(88, 44))

    assert len(load_checkpoints(path, activation_sha256=_ACT)) == 2
