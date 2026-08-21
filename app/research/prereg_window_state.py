"""Der T1-Ausgang ist ein Ereignis — er muss ueberleben, nicht neu abgeleitet werden.

``decide_window_action`` ist bewusst zustandslos und bekommt ``t1_outcome``
mitgegeben. Ohne Persistenz waere das ein Loch: nach einem Neustart wuesste
niemand mehr, dass an T1 bereits verlaengert wurde, und der T1-Checkpoint waere
ein zweites Mal offen. Genau das ist optional stopping — nur mit einem Neustart
als Ausrede.

Deshalb ein **append-only** Journal, gebunden an den Hash der Aktivierung:

    {"activation_sha256": "...", "checkpoint": "T1", "action": "EXTEND_TO_T2", ...}

Drei Eigenschaften, die es haelt:

**Ein Checkpoint wird genau einmal entschieden.** Ein zweiter T1-Eintrag mit
einer ANDEREN Aktion wird abgewiesen. Ein identischer Eintrag ist ein No-Op —
ein Absturz zwischen Schreiben und Weiterarbeiten darf nicht dazu fuehren, dass
gar nichts geht.

**Fail-closed bei Beschaedigung.** Ist das Journal unlesbar, wird NICHT "kein
T1-Ausgang" angenommen. Diese Annahme wuerde den T1-Checkpoint wieder oeffnen —
also genau den Schaden anrichten, gegen den das Journal gebaut ist. Stattdessen
ein Abbruch.

**Fremde Eintraege sind ein Fehler, kein Rauschen.** Ein Journal mit Eintraegen
einer anderen Aktivierung heisst, dass die falsche Datei benutzt wird. Still zu
ignorieren waere die schlimmere Variante.

EHRLICHE GRENZE: hier wird der ENTSCHLUSS festgehalten, nicht das Verdikt.
Stuerzt der Prozess zwischen einem aufgezeichneten ``EVALUATE`` und dem
fertigen p-Wert ab, steht der Checkpoint auf ``CLOSED`` und das Ergebnis ist
verloren. Die Auswertung auf demselben Datenschnitt ist deterministisch und
darf wiederholt werden — aber das Verdikt gehoert dann ebenfalls in dieses
Journal. Das ist noch nicht gebaut.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

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

# Nur diese Aktionen sind Checkpoint-ENTSCHEIDUNGEN. ``WAIT`` und ``CLOSED``
# sind Zustandsbeschreibungen und werden nicht journalisiert — sonst waere das
# Journal ein Log und keine Entscheidungskette.
RECORDABLE_ACTIONS: frozenset[str] = frozenset(
    {ACTION_EVALUATE, ACTION_EXTEND_TO_T2, ACTION_INCONCLUSIVE}
)


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


def load_checkpoints(path: Path, *, activation_sha256: str) -> tuple[CheckpointRecord, ...]:
    """Alle Eintraege dieser Aktivierung. Fehlende Datei = leer, kaputte = Abbruch.

    Raises:
        CheckpointJournalError: unlesbare Zeile, fehlendes Feld oder ein Eintrag
            einer anderen Aktivierung.
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
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CheckpointJournalError(
                f"{path}:{number} ist kein gueltiges JSON — der T1-Ausgang waere "
                "damit unbekannt, und ein unbekannter T1-Ausgang oeffnet den "
                "Checkpoint ein zweites Mal. Abbruch statt Annahme."
            ) from exc
        missing = {"activation_sha256", "checkpoint", "action", "mature"} - set(payload)
        if missing:
            raise CheckpointJournalError(f"{path}:{number} fehlen Felder: {sorted(missing)}")
        if payload["activation_sha256"] != activation_sha256:
            raise CheckpointJournalError(
                f"{path}:{number} gehoert zu Aktivierung "
                f"{payload['activation_sha256'][:12]}…, erwartet "
                f"{activation_sha256[:12]}… — falsches Journal."
            )
        records.append(
            CheckpointRecord(
                activation_sha256=payload["activation_sha256"],
                checkpoint=payload["checkpoint"],
                action=payload["action"],
                mature=bool(payload["mature"]),
                recorded_at_utc=payload.get("recorded_at_utc", ""),
                counts=dict(payload.get("counts", {})),
            )
        )
    return tuple(records)


def record_checkpoint(path: Path, record: CheckpointRecord) -> bool:
    """Haenge eine Checkpoint-Entscheidung an. Idempotent, aber nicht ueberschreibbar.

    Returns:
        True, wenn geschrieben wurde; False, wenn derselbe Eintrag schon stand.

    Raises:
        CheckpointConflictError: derselbe Checkpoint, andere Aktion.
    """
    existing = load_checkpoints(path, activation_sha256=record.activation_sha256)
    for previous in existing:
        if previous.checkpoint != record.checkpoint:
            continue
        if previous.action == record.action:
            return False  # Absturz-sicher: derselbe Entschluss noch einmal ist harmlos
        raise CheckpointConflictError(
            f"{record.checkpoint} steht bereits auf {previous.action!r}; "
            f"{record.action!r} waere eine zweite Entscheidung desselben "
            "Checkpoints. Ein Checkpoint wird genau einmal entschieden."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(record), sort_keys=True, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return True


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
    )

    if decision.action in RECORDABLE_ACTIONS and decision.checkpoint in (
        CHECKPOINT_T1,
        CHECKPOINT_T2,
    ):
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
