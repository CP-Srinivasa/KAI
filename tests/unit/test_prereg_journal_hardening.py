r"""Das Journal ist SSOT fuer einen teuren Zustand — sein Vertrag muss haerter sein.

Drei Befunde am ersten Entwurf (Operator 2026-08-21), alle am Code nachgestellt:

**1. "Idempotent" war zu grosszuegig.** ``record_checkpoint`` akzeptierte
denselben ``checkpoint`` + ``action`` als No-Op, auch wenn ``mature`` oder die
Reifezahlen abwichen. Das ist nicht derselbe Entschluss — er waere auf anderer
Grundlage gefasst worden, und im Journal stuende am Ende eine Entscheidung mit
einer Begruendung, die nie zu ihr gehoerte. Jetzt entscheidet ein
``decision_fingerprint`` ueber ``activation_sha256 + checkpoint + action +
mature + counts``.

**2. Es war nicht crash-durable.** ``open("a")`` + ``write`` landet im
Page-Cache. Faellt danach der Strom aus, ist genau die zuletzt gefasste
Entscheidung weg — und der Neustart sieht wieder "kein T1-Ausgang", also exakt
der Schaden, gegen den das Journal gebaut ist. Jetzt ``flush`` + ``os.fsync``,
beim Anlegen zusaetzlich ein ``fsync`` des Verzeichnisses.

**3. Fail-closed war nur syntaktisch.** ``{"mature": "false"}`` ist gueltiges
JSON, und ``bool("false")`` ist ``True`` — eine unreife Entscheidung waere als
reif eingelesen worden. Jetzt wird jedes Feld auf Typ UND Wertebereich geprueft,
und der gespeicherte Fingerabdruck muss zum Rest der Zeile passen.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.research.prereg_window import (
    ACTION_EVALUATE,
    ACTION_EXTEND_TO_T2,
    ACTION_WAIT,
    CHECKPOINT_T1,
)
from app.research.prereg_window_state import (
    CheckpointConflictError,
    CheckpointJournalError,
    CheckpointRecord,
    decision_fingerprint,
    load_checkpoints,
    record_checkpoint,
)

_ACT = "a" * 64
_WHEN = "2026-11-30T00:00:00+00:00"


def _record(
    *,
    action: str = ACTION_EXTEND_TO_T2,
    mature: bool = False,
    counts: dict[str, int] | None = None,
    when: str = _WHEN,
) -> CheckpointRecord:
    return CheckpointRecord(
        activation_sha256=_ACT,
        checkpoint=CHECKPOINT_T1,
        action=action,
        mature=mature,
        recorded_at_utc=when,
        counts=counts if counts is not None else {"n_valid": 80, "n_clusters": 41},
    )


def _write_raw(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _valid_payload(**overrides) -> dict:
    record = _record()
    payload = {
        "activation_sha256": record.activation_sha256,
        "checkpoint": record.checkpoint,
        "action": record.action,
        "mature": record.mature,
        "recorded_at_utc": record.recorded_at_utc,
        "counts": record.counts,
        "decision_fingerprint": record.fingerprint,
    }
    payload.update(overrides)
    return payload


# ── 1. Fingerabdruck statt blosser Aktion ───────────────────────────────────


def test_the_same_decision_retried_is_idempotent(tmp_path: Path) -> None:
    """Absturz zwischen Schreiben und Weiterarbeiten darf nichts blockieren."""
    path = tmp_path / "j.jsonl"
    record_checkpoint(path, _record())

    assert record_checkpoint(path, _record()) is False
    assert len(load_checkpoints(path, activation_sha256=_ACT)) == 1


def test_a_retry_at_a_later_clock_time_is_still_the_same_decision(tmp_path: Path) -> None:
    """``recorded_at_utc`` gehoert bewusst NICHT in den Fingerabdruck."""
    path = tmp_path / "j.jsonl"
    record_checkpoint(path, _record(when=_WHEN))

    assert record_checkpoint(path, _record(when="2026-11-30T00:05:00+00:00")) is False


def test_same_action_but_different_maturity_is_a_conflict(tmp_path: Path) -> None:
    """Der Befund: gleiche Aktion, andere Grundlage — das ist kein Retry."""
    path = tmp_path / "j.jsonl"
    record_checkpoint(path, _record(mature=False))

    with pytest.raises(CheckpointConflictError, match="genau einmal"):
        record_checkpoint(path, _record(mature=True))


def test_same_action_but_different_counts_is_a_conflict(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    record_checkpoint(path, _record(counts={"n_valid": 80, "n_clusters": 41}))

    with pytest.raises(CheckpointConflictError, match="genau einmal"):
        record_checkpoint(path, _record(counts={"n_valid": 81, "n_clusters": 41}))


def test_the_conflict_message_names_both_grounds(tmp_path: Path) -> None:
    """Wer das liest, soll sehen, WORIN sich die beiden Entschluesse unterscheiden."""
    path = tmp_path / "j.jsonl"
    record_checkpoint(path, _record(mature=False))

    with pytest.raises(CheckpointConflictError) as excinfo:
        record_checkpoint(path, _record(mature=True))

    assert "mature=False" in str(excinfo.value)
    assert "mature=True" in str(excinfo.value)


def test_fingerprint_covers_every_deciding_field() -> None:
    base = _record()

    assert decision_fingerprint(base) == decision_fingerprint(_record())
    assert decision_fingerprint(base) != decision_fingerprint(_record(mature=True))
    assert decision_fingerprint(base) != decision_fingerprint(_record(action=ACTION_EVALUATE))
    assert decision_fingerprint(base) != decision_fingerprint(_record(counts={"n_valid": 1}))


def test_count_order_does_not_change_the_fingerprint() -> None:
    """Sonst waere ein Retry je nach Dict-Reihenfolge mal Retry, mal Konflikt."""
    a = _record(counts={"n_valid": 80, "n_clusters": 41})
    b = _record(counts={"n_clusters": 41, "n_valid": 80})

    assert decision_fingerprint(a) == decision_fingerprint(b)


# ── 2. Crash-Durability ─────────────────────────────────────────────────────


def test_the_write_is_fsynced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``write`` allein landet im Page-Cache — ein Stromausfall fraesse die Entscheidung."""
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])

    record_checkpoint(tmp_path / "j.jsonl", _record())

    assert synced, "os.fsync wurde nicht aufgerufen"


def test_the_directory_is_fsynced_when_the_journal_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auch der Verzeichniseintrag muss haltbar sein — sonst fehlt die Datei danach.

    Nur POSIX; Windows kennt kein Verzeichnis-fsync und der Aufruf entfaellt
    dort bewusst.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        "app.research.prereg_window_state._fsync_directory",
        lambda directory: calls.append(str(directory)),
    )
    path = tmp_path / "j.jsonl"

    record_checkpoint(path, _record())
    record_checkpoint(
        path,
        CheckpointRecord(
            activation_sha256=_ACT,
            checkpoint="T2",
            action=ACTION_EVALUATE,
            mature=True,
            recorded_at_utc=_WHEN,
            counts={"n_valid": 146, "n_clusters": 75},
        ),
    )

    assert calls == [str(tmp_path)], "nur beim Anlegen, nicht bei jedem Anhaengen"


def test_directory_fsync_does_not_raise(tmp_path: Path) -> None:
    """Gegenprobe auf der echten Plattform — auf Windows ein No-Op."""
    from app.research.prereg_window_state import _fsync_directory

    _fsync_directory(tmp_path)


# ── 3. Fail-closed, semantisch ──────────────────────────────────────────────


def test_a_healthy_line_still_loads(tmp_path: Path) -> None:
    """Gegenprobe zuerst — sonst waere die strenge Pruefung nur ein Verhinderer."""
    path = tmp_path / "j.jsonl"
    _write_raw(path, _valid_payload())

    records = load_checkpoints(path, activation_sha256=_ACT)

    assert len(records) == 1
    assert records[0].action == ACTION_EXTEND_TO_T2


def test_a_string_false_is_not_maturity(tmp_path: Path) -> None:
    """DER Befund: ``bool("false")`` ist ``True``.

    Eine unreife Entscheidung waere als reif eingelesen worden — und damit
    saehe der naechste Checkpoint eine Reife, die es nie gab.
    """
    path = tmp_path / "j.jsonl"
    _write_raw(path, _valid_payload(mature="false"))

    with pytest.raises(CheckpointJournalError, match="'mature' ist str"):
        load_checkpoints(path, activation_sha256=_ACT)


def test_an_integer_is_not_maturity(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    _write_raw(path, _valid_payload(mature=1))

    with pytest.raises(CheckpointJournalError, match="'mature' ist int"):
        load_checkpoints(path, activation_sha256=_ACT)


@pytest.mark.parametrize(
    ("field_name", "value", "pattern"),
    [
        ("checkpoint", "PRE_T1", "'checkpoint' ist"),
        ("checkpoint", "T3", "'checkpoint' ist"),
        ("action", ACTION_WAIT, "'action' ist"),
        ("action", "PASS", "'action' ist"),
        ("activation_sha256", "kurz", "kein SHA-256"),
        ("recorded_at_utc", "gestern", "kein ISO-8601"),
        ("recorded_at_utc", 12345, "kein Text"),
        ("counts", [1, 2], "kein Objekt"),
        ("counts", {"n_valid": "80"}, "erwartet int"),
        ("counts", {"n_valid": True}, "erwartet int"),
        ("counts", {"n_valid": -1}, "negativ"),
        ("decision_fingerprint", "0" * 64, "passt nicht zum Inhalt"),
        ("decision_fingerprint", "zu kurz", "kein SHA-256"),
    ],
)
def test_every_semantically_invalid_field_aborts(
    tmp_path: Path, field_name: str, value: object, pattern: str
) -> None:
    """Ein beschaedigtes Journal muss auffallen, nicht plausibel wirken."""
    path = tmp_path / "j.jsonl"
    # Der Fingerabdruck bleibt der des gesunden Datensatzes. Das ist Absicht:
    # `_parse_record` prueft die Felder VOR dem Fingerabdruck, also faellt hier
    # wirklich das getestete Feld auf und nicht ersatzweise der Hash.
    _write_raw(path, _valid_payload(**{field_name: value}))

    with pytest.raises(CheckpointJournalError, match=pattern):
        load_checkpoints(path, activation_sha256=_ACT)


def test_tampering_with_the_content_is_detected(tmp_path: Path) -> None:
    """Wer die Zahlen aendert, muesste auch den Fingerabdruck faelschen."""
    path = tmp_path / "j.jsonl"
    record_checkpoint(path, _record())
    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    payload["counts"]["n_valid"] = 999  # Fingerabdruck bleibt der alte
    _write_raw(path, payload)

    with pytest.raises(CheckpointJournalError, match="nachtraeglich veraendert"):
        load_checkpoints(path, activation_sha256=_ACT)


def test_a_line_that_is_not_an_object_aborts(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    path.write_text("[1, 2, 3]\n", encoding="utf-8")

    with pytest.raises(CheckpointJournalError, match="kein Objekt"):
        load_checkpoints(path, activation_sha256=_ACT)


# ── Der Schreibpfad selbst ──────────────────────────────────────────────────


def test_only_real_checkpoints_may_be_recorded(tmp_path: Path) -> None:
    """``WAIT`` ist ein Zustand, keine Entscheidung — er gehoert nicht ins Journal."""
    path = tmp_path / "j.jsonl"

    with pytest.raises(CheckpointJournalError, match="keine Checkpoint-Entscheidung"):
        record_checkpoint(path, _record(action=ACTION_WAIT))


def test_a_non_decision_checkpoint_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    bad = CheckpointRecord(
        activation_sha256=_ACT,
        checkpoint="PRE_T1",
        action=ACTION_EXTEND_TO_T2,
        mature=False,
        recorded_at_utc=_WHEN,
    )

    with pytest.raises(CheckpointJournalError, match="kein Entscheidungs-Checkpoint"):
        record_checkpoint(path, bad)


def test_the_stored_line_carries_the_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    record = _record()

    record_checkpoint(path, record)

    stored = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert stored["decision_fingerprint"] == record.fingerprint
