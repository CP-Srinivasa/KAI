"""Der T1-Ausgang ist ein Ereignis — er muss ueberleben, nicht neu abgeleitet werden.

``decide_window_action`` ist bewusst zustandslos und bekommt ``t1_outcome``
mitgegeben. Ohne Persistenz waere das ein Loch mit einer bequemen Ausrede: nach
einem Neustart wuesste niemand mehr, dass an T1 bereits verlaengert wurde, der
Checkpoint stuende wieder offen — und wer dann auswertet, hat zweimal
hingesehen. Das ist optional stopping, nur mit Stromausfall davor.

Deshalb ein **append-only** Journal, gebunden an den Hash der Aktivierung::

    {"activation_sha256": "...", "checkpoint": "T1", "action": "EXTEND_TO_T2",
     "decision_fingerprint": "...", ...}

Weil dieses Journal die einzige Quelle fuer einen sehr teuren Zustand ist, ist
sein Vertrag haerter als gewoehnliches JSONL:

**Der Fingerabdruck entscheidet ueber Idempotenz, nicht die Aktion.** Ein Retry
mit derselben Aktion, aber anderen Reifezahlen ist NICHT derselbe Entschluss —
er wurde auf anderer Grundlage gefasst. ``decision_fingerprint`` deckt
``activation_sha256``, ``checkpoint``, ``action``, ``mature`` und ``counts`` ab.
Bewusst NICHT die Uhrzeit: ein Wiederholungsversuch geschieht spaeter, ist aber
derselbe Entschluss.

**Geschrieben heisst auf der Platte.** ``write`` allein landet im Page-Cache;
faellt der Strom danach aus, ist genau die letzte Entscheidung weg — und der
Neustart sieht wieder "kein T1-Ausgang". Also ``flush`` + ``os.fsync``, und beim
erstmaligen Anlegen zusaetzlich ein ``fsync`` des Verzeichnisses, damit auch der
Verzeichniseintrag selbst haltbar ist.

**Fail-closed heisst semantisch, nicht nur syntaktisch.** ``{"mature": "false"}``
ist gueltiges JSON, und ``bool("false")`` ist ``True`` — eine unreife
Entscheidung waere damit als reif eingelesen worden. Jedes Feld wird deshalb auf
Typ UND Wertebereich geprueft, und der gespeicherte Fingerabdruck muss zum Rest
der Zeile passen.

EHRLICHE GRENZE: hier wird der ENTSCHLUSS festgehalten, nicht das Verdikt.
Stuerzt der Prozess zwischen einem aufgezeichneten ``EVALUATE`` und dem fertigen
p-Wert ab, steht der Checkpoint auf ``CLOSED`` und das Ergebnis ist verloren.
Der Ausweg ist ein dritter Zustand (``VERDICT_RECORDED``) plus ein eingefrorener
Datenschnitt — er gehoert in das Activation-Record und ist noch nicht gebaut.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.research.exclusive_lock import exclusive_lock
from app.research.prereg_window import (
    ACTION_EVALUATE,
    ACTION_EXTEND_TO_T2,
    ACTION_INCONCLUSIVE,
    CHECKPOINT_T1,
    CHECKPOINT_T2,
    MaturityCounts,
    WindowDecision,
    decide_window_action,
)

JOURNAL_SPEC_VERSION = "kai/prereg-checkpoints/v1"

# Nur diese Aktionen sind Checkpoint-ENTSCHEIDUNGEN. ``WAIT`` und ``CLOSED``
# sind Zustandsbeschreibungen und werden nicht journalisiert — sonst waere das
# Journal ein Log und keine Entscheidungskette.
RECORDABLE_ACTIONS: frozenset[str] = frozenset(
    {ACTION_EVALUATE, ACTION_EXTEND_TO_T2, ACTION_INCONCLUSIVE}
)
RECORDABLE_CHECKPOINTS: frozenset[str] = frozenset({CHECKPOINT_T1, CHECKPOINT_T2})

_REQUIRED_FIELDS = (
    "activation_sha256",
    "checkpoint",
    "action",
    "mature",
    "recorded_at_utc",
    "counts",
    "decision_fingerprint",
)
_HEX64 = 64


class CheckpointJournalError(RuntimeError):
    """Das Journal ist unlesbar, fremd oder widersprueclich — fail-closed."""


class CheckpointConflictError(CheckpointJournalError):
    """Ein Checkpoint sollte ein zweites Mal ANDERS entschieden werden."""


@dataclass(frozen=True)
class CheckpointRecord:
    """Eine Entscheidung an einem Checkpoint. Unveraenderlich, append-only."""

    activation_sha256: str
    checkpoint: str
    action: str
    mature: bool
    recorded_at_utc: str
    counts: dict[str, int] = field(default_factory=dict)
    # Nur bei EVALUATE gesetzt: der Hash des eingefrorenen Datenschnitts, auf
    # dem gewertet werden DARF. Steht er im Journal, MUSS das Artefakt bereits
    # existieren — es wird vor diesem Eintrag geschrieben.
    evaluation_input_sha256: str = ""

    @property
    def fingerprint(self) -> str:
        return decision_fingerprint(self)


def counts_to_dict(counts: MaturityCounts) -> dict[str, int]:
    """Nur blinde Zahlen wandern ins Journal — es gibt hier keine Performance."""
    return {
        "n_valid": counts.n_valid,
        "n_clusters": counts.n_clusters,
        "raw_fires": counts.raw_fires,
        "label_capable_fires": counts.label_capable_fires,
        "data_unavailable_count": counts.data_unavailable_count,
        "symbols_with_valid_signals": counts.symbols_with_valid_signals,
    }


def decision_fingerprint(record: CheckpointRecord) -> str:
    """Was den Entschluss ausmacht — Aktion allein genuegt nicht.

    Dieselbe Aktion bei anderen Reifezahlen ist ein ANDERER Entschluss: er wurde
    auf anderer Grundlage gefasst. Wuerde man ihn als idempotenten Retry
    durchwinken, stuende im Journal am Ende eine Entscheidung mit einer
    Begruendung, die nie zu ihr gehoerte.

    ``recorded_at_utc`` gehoert ausdruecklich NICHT dazu: ein
    Wiederholungsversuch geschieht spaeter und ist trotzdem derselbe Entschluss.
    """
    payload = {
        "spec": JOURNAL_SPEC_VERSION,
        "activation_sha256": record.activation_sha256,
        "checkpoint": record.checkpoint,
        "action": record.action,
        "mature": record.mature,
        "counts": dict(sorted(record.counts.items())),
        "evaluation_input_sha256": record.evaluation_input_sha256,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HEX64
        and all(c in "0123456789abcdef" for c in value)
    )


def _validate_counts(value: object, where: str) -> dict[str, int]:
    """Ganzzahlen, und ``bool`` ausdruecklich NICHT.

    ``bool`` ist in Python eine Unterklasse von ``int``; ohne diese Pruefung
    ginge ``{"n_valid": true}`` als ``n_valid = 1`` durch.
    """
    if not isinstance(value, dict):
        raise CheckpointJournalError(f"{where}: 'counts' ist kein Objekt")
    out: dict[str, int] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            raise CheckpointJournalError(f"{where}: 'counts' hat einen nicht-textuellen Schluessel")
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise CheckpointJournalError(
                f"{where}: counts[{key!r}] ist {type(raw).__name__}, erwartet int"
            )
        if raw < 0:
            raise CheckpointJournalError(f"{where}: counts[{key!r}] ist negativ")
        out[key] = raw
    return out


def _validate_optional_hash(value: object, where: str) -> str:
    """Leer oder ein SHA-256 — nichts dazwischen.

    Der Wert liegt im Fingerabdruck; ein nachtraegliches Entfernen faellt
    deshalb ohnehin auf. Die Formpruefung fuegt hinzu, dass auch ein
    *vorhandener* Wert kein Zufallstext sein kann.
    """
    if value == "":
        return ""
    if not _is_hex64(value):
        raise CheckpointJournalError(
            f"{where}: 'evaluation_input_sha256' ist weder leer noch ein SHA-256"
        )
    return str(value)


def _parse_record(payload: Any, where: str, activation_sha256: str) -> CheckpointRecord:
    """Streng. Ein beschaedigtes Journal muss auffallen, nicht plausibel wirken."""
    if not isinstance(payload, dict):
        raise CheckpointJournalError(f"{where}: Zeile ist kein Objekt")

    missing = [name for name in _REQUIRED_FIELDS if name not in payload]
    if missing:
        raise CheckpointJournalError(f"{where} fehlen Felder: {missing}")

    if not _is_hex64(payload["activation_sha256"]):
        raise CheckpointJournalError(f"{where}: 'activation_sha256' ist kein SHA-256")
    if payload["activation_sha256"] != activation_sha256:
        raise CheckpointJournalError(
            f"{where} gehoert zu Aktivierung {payload['activation_sha256'][:12]}…, "
            f"erwartet {activation_sha256[:12]}… — falsches Journal."
        )

    if payload["checkpoint"] not in RECORDABLE_CHECKPOINTS:
        raise CheckpointJournalError(
            f"{where}: 'checkpoint' ist {payload['checkpoint']!r}, "
            f"erlaubt {sorted(RECORDABLE_CHECKPOINTS)}"
        )
    if payload["action"] not in RECORDABLE_ACTIONS:
        raise CheckpointJournalError(
            f"{where}: 'action' ist {payload['action']!r}, erlaubt {sorted(RECORDABLE_ACTIONS)}"
        )

    # Kein bool(): '{"mature": "false"}' ist gueltiges JSON, und bool("false")
    # ist True — eine unreife Entscheidung waere als reif eingelesen worden.
    if not isinstance(payload["mature"], bool):
        raise CheckpointJournalError(
            f"{where}: 'mature' ist {type(payload['mature']).__name__}, erwartet bool"
        )

    recorded_at = payload["recorded_at_utc"]
    if not isinstance(recorded_at, str):
        raise CheckpointJournalError(f"{where}: 'recorded_at_utc' ist kein Text")
    try:
        parsed_at = datetime.fromisoformat(recorded_at)
    except ValueError as exc:
        raise CheckpointJournalError(f"{where}: 'recorded_at_utc' ist kein ISO-8601") from exc
    # Zeitzonenlos ging bisher durch — und ein Offset ebenfalls. Beides ist in
    # einer Wahrheitsschicht falsch: zwei Schreibweisen desselben Augenblicks
    # ergeben zwei verschiedene Bytes und damit zwei verschiedene Hashes.
    if parsed_at.tzinfo is None:
        raise CheckpointJournalError(
            f"{where}: 'recorded_at_utc' ist zeitzonenlos — UTC wird nicht geraten"
        )
    if parsed_at.utcoffset() != timedelta(0):
        raise CheckpointJournalError(
            f"{where}: 'recorded_at_utc' traegt Offset {parsed_at.utcoffset()}, "
            "erwartet +00:00. Ein Feld auf _utc ist UTC."
        )

    record = CheckpointRecord(
        activation_sha256=payload["activation_sha256"],
        checkpoint=payload["checkpoint"],
        action=payload["action"],
        mature=payload["mature"],
        recorded_at_utc=recorded_at,
        counts=_validate_counts(payload["counts"], where),
        evaluation_input_sha256=_validate_optional_hash(
            payload.get("evaluation_input_sha256", ""), where
        ),
    )

    stored = payload["decision_fingerprint"]
    if not _is_hex64(stored):
        raise CheckpointJournalError(f"{where}: 'decision_fingerprint' ist kein SHA-256")
    if stored != record.fingerprint:
        raise CheckpointJournalError(
            f"{where}: 'decision_fingerprint' passt nicht zum Inhalt der Zeile — "
            "das Journal wurde nachtraeglich veraendert oder ist beschaedigt."
        )
    return record


def load_checkpoints(path: Path, *, activation_sha256: str) -> tuple[CheckpointRecord, ...]:
    """Alle Eintraege dieser Aktivierung. Fehlende Datei = leer, kaputte = Abbruch.

    Raises:
        CheckpointJournalError: unlesbare, semantisch ungueltige oder fremde Zeile.
    """
    if not path.exists():
        return ()

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensiv
        raise CheckpointJournalError(f"Journal {path} nicht lesbar: {exc}") from exc

    records: list[CheckpointRecord] = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{path}:{number}"
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CheckpointJournalError(
                f"{where} ist kein gueltiges JSON — der T1-Ausgang waere damit "
                "unbekannt, und ein unbekannter T1-Ausgang oeffnet den Checkpoint "
                "ein zweites Mal. Abbruch statt Annahme."
            ) from exc
        records.append(_parse_record(payload, where, activation_sha256))
    return tuple(records)


def _fsync_directory(directory: Path) -> None:
    """Verzeichniseintrag haltbar machen. Nur POSIX — Windows kennt das nicht."""
    if os.name != "posix":  # pragma: no cover - plattformabhaengig
        return
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def record_checkpoint(path: Path, record: CheckpointRecord) -> bool:
    """Haenge eine Checkpoint-Entscheidung an. Idempotent, aber nicht ueberschreibbar.

    Idempotenz haengt am **Fingerabdruck**, nicht an der Aktion: derselbe
    Entschluss noch einmal ist harmlos, dieselbe Aktion auf anderer Grundlage
    nicht.

    Drei Zusicherungen:

    **Streng VOR dem Schreiben.** Vorher pruefte nur der Leser; ein direkt
    gebauter Datensatz mit einem ungueltigen Feld liess sich anhaengen und machte
    das Journal beim naechsten Lesen dauerhaft rot.

    **Lesen, pruefen und Anhaengen unter EINEM Lock.** Sonst koennen zwei
    Prozesse beide "kein Eintrag vorhanden" sehen und beide schreiben — bei
    Checkpoints waeren das zwei konkurrierende Entscheidungen.

    **Geschrieben heisst auf der Platte** (``flush`` + ``fsync``, beim Anlegen
    zusaetzlich das Verzeichnis).

    Returns:
        True, wenn geschrieben wurde; False bei identischem Retry.

    Raises:
        CheckpointConflictError: derselbe Checkpoint, anderer Fingerabdruck.
    """
    if record.checkpoint not in RECORDABLE_CHECKPOINTS:
        raise CheckpointJournalError(f"{record.checkpoint!r} ist kein Entscheidungs-Checkpoint")
    if record.action not in RECORDABLE_ACTIONS:
        raise CheckpointJournalError(f"{record.action!r} ist keine Checkpoint-Entscheidung")

    fingerprint = record.fingerprint
    payload = asdict(record)
    payload["decision_fingerprint"] = fingerprint
    # Der Schreiber normiert auf UTC, der Leser besteht darauf.
    payload["recorded_at_utc"] = _canonical_utc(record.recorded_at_utc)
    # Streng gegen dasselbe Schema pruefen, das der Leser anlegt — bevor eine
    # Zeile im append-only Journal steht, die niemand mehr entfernen kann.
    _parse_record(payload, f"{path} (Schreibvorgang)", record.activation_sha256)

    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(path.parent / f".{path.name}.lock", what="Checkpoint-Schreibvorgang"):
        for previous in load_checkpoints(path, activation_sha256=record.activation_sha256):
            if previous.checkpoint != record.checkpoint:
                continue
            if previous.fingerprint == fingerprint:
                return False  # identischer Retry — absturzsicher, kein zweiter Eintrag
            raise CheckpointConflictError(
                f"{record.checkpoint} steht bereits auf {previous.action!r} "
                f"(mature={previous.mature}, counts={previous.counts}); "
                f"{record.action!r} (mature={record.mature}, counts={record.counts}) "
                "waere eine zweite Entscheidung desselben Checkpoints. "
                "Ein Checkpoint wird genau einmal entschieden."
            )

        existed = path.exists()
        line = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if not existed:
            _fsync_directory(path.parent)
    return True


def _canonical_utc(timestamp_utc: str) -> str:
    """Auf UTC normieren. Zeitzonenlos wird abgelehnt, nicht geraten."""
    try:
        parsed = datetime.fromisoformat(timestamp_utc)
    except (TypeError, ValueError) as exc:
        raise CheckpointJournalError(f"{timestamp_utc!r} ist kein ISO-8601-Zeitstempel") from exc
    if parsed.tzinfo is None:
        raise CheckpointJournalError(f"{timestamp_utc!r} ist zeitzonenlos — UTC wird nicht geraten")
    return parsed.astimezone(UTC).isoformat()


def resolve_t1_outcome(path: Path, *, activation_sha256: str) -> str | None:
    """Was an T1 entschieden wurde — oder None, wenn T1 noch nicht erreicht war."""
    for record in load_checkpoints(path, activation_sha256=activation_sha256):
        if record.checkpoint == CHECKPOINT_T1:
            return record.action
    return None


def resolve_window(
    *,
    now_utc: str,
    t1_utc: str,
    t2_utc: str,
    counts: MaturityCounts,
    n_valid_min: int,
    cluster_min: int,
    activation_sha256: str,
    state_path: Path,
    verdict_recorded: bool = False,
) -> WindowDecision:
    """Entscheiden UND die Entscheidung festhalten — in dieser Reihenfolge.

    Das Aufzeichnen passiert **vor** der Rueckgabe und damit vor jeder
    Auswertung. Ein Absturz danach laesst den Checkpoint entschieden zurueck,
    nicht offen — offen waere die Einladung, ihn noch einmal zu entscheiden.
    """
    t1_outcome = resolve_t1_outcome(state_path, activation_sha256=activation_sha256)
    decision = decide_window_action(
        now_utc=now_utc,
        t1_utc=t1_utc,
        t2_utc=t2_utc,
        counts=counts,
        n_valid_min=n_valid_min,
        cluster_min=cluster_min,
        t1_outcome=t1_outcome,
        verdict_recorded=verdict_recorded,
    )

    if decision.action in RECORDABLE_ACTIONS and decision.checkpoint in RECORDABLE_CHECKPOINTS:
        record_checkpoint(
            state_path,
            CheckpointRecord(
                activation_sha256=activation_sha256,
                checkpoint=decision.checkpoint,
                action=decision.action,
                mature=decision.mature,
                recorded_at_utc=now_utc,
                counts=counts_to_dict(counts),
            ),
        )
    return decision
