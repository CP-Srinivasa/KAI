"""Zustandsfolge einer praeregistrierten Auswertung — append-only und pruefbar.

Ohne festgehaltene Folge ist nach einem Absturz nicht entscheidbar, ob eine
Auswertung schon gelaufen ist. Der Wiederanlauf zieht dann neue Daten, rechnet
einen neuen Stichtag und bekommt einen neuen Hash; hinterher laesst sich nicht
mehr sagen, welcher Lauf das Verdikt getragen hat. Das ist optional stopping,
ohne dass es jemand beabsichtigt haette — und es ist von aussen unsichtbar.

Die Kette:

    EVALUATION_INPUT_FROZEN
        -> CHECKPOINT_DECIDED(EVALUATE, evaluation_input_sha256)
        -> EVALUATION_RUNNING
        -> VERDICT_RECORDED
        -> CLOSED

``EXTEND_TO_T2`` und ``INCONCLUSIVE_NOT_MATURE`` sind ebenfalls Entscheidungen,
muenden aber nie in einen Lauf: an ihnen entsteht kein Performance-Artefakt.

Jeder Checkpoint einer Aktivierung fuehrt seine eigene Kette; T1 und T2 stehen
nebeneinander im selben Journal und stoeren sich nicht.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

JOURNAL_SPEC_VERSION = "kai/evaluation-state/v1"

STATE_INPUT_FROZEN = "EVALUATION_INPUT_FROZEN"
STATE_CHECKPOINT_DECIDED = "CHECKPOINT_DECIDED"
STATE_EVALUATION_RUNNING = "EVALUATION_RUNNING"
STATE_VERDICT_RECORDED = "VERDICT_RECORDED"
STATE_CLOSED = "CLOSED"

ACTION_EVALUATE = "EVALUATE"

# Was auf was folgen darf. Ein Zustand, der hier nicht steht, ist kein Zustand.
_ALLOWED_NEXT: dict[str | None, frozenset[str]] = {
    None: frozenset({STATE_INPUT_FROZEN}),
    STATE_INPUT_FROZEN: frozenset({STATE_CHECKPOINT_DECIDED}),
    STATE_CHECKPOINT_DECIDED: frozenset({STATE_EVALUATION_RUNNING}),
    # RUNNING -> RUNNING ist der Wiederanlauf nach einem Absturz. Er ist
    # ausdruecklich erlaubt und ausdruecklich der Ort, an dem der eingefrorene
    # Hash geprueft wird: ein neu gebauter Input waere die unauffaelligste Art,
    # das Ergebnis zu wechseln.
    STATE_EVALUATION_RUNNING: frozenset({STATE_VERDICT_RECORDED, STATE_EVALUATION_RUNNING}),
    STATE_VERDICT_RECORDED: frozenset({STATE_CLOSED}),
    STATE_CLOSED: frozenset(),
}


class EvaluationStateError(RuntimeError):
    """Der verlangte Schritt passt nicht zur festgehaltenen Kette — fail-closed."""


@dataclass(frozen=True)
class VerdictRecord:
    """Das Verdikt mit allem, was es nachpruefbar macht.

    Bewusst vollstaendig: wer spaeter nur ``verdict`` und ``p_value`` haette,
    koennte nicht mehr feststellen, auf welchen Daten, mit welchem Evaluator und
    gegen welche Schranken entschieden wurde — und genau das ist die Frage, die
    eine Praeregistrierung beantworten koennen muss.
    """

    activation_sha256: str
    checkpoint: str
    evaluation_input_sha256: str
    dataset_sha256: str
    evaluator_sha256: str
    verdict: str
    n_valid: int
    n_clusters: int
    mean_net_bps: float
    standard_error: float
    t_stat: float
    degrees_of_freedom: int
    p_value: float
    alpha: float
    economic_floor_bps: float
    recorded_at_utc: str = ""

    @property
    def result_sha256(self) -> str:
        """Hash ueber das Ergebnis — ohne den Zeitpunkt der Niederschrift.

        Ein Wiederholungsversuch geschieht spaeter und ist trotzdem dasselbe
        Ergebnis. Waere ``recorded_at_utc`` Teil des Hashes, saehe jeder Retry
        wie ein neues Verdikt aus.
        """
        payload = {k: v for k, v in asdict(self).items() if k != "recorded_at_utc"}
        payload["spec"] = JOURNAL_SPEC_VERSION
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StateEvent:
    """Eine Zeile im Journal."""

    state: str
    activation_sha256: str
    checkpoint: str
    recorded_at_utc: str
    evaluation_input_sha256: str | None = None
    action: str | None = None
    verdict: dict[str, Any] = field(default_factory=dict)


# ── Lesen ───────────────────────────────────────────────────────────────────


def _read_events(path: Path) -> list[StateEvent]:
    target = Path(path)
    if not target.exists():
        return []
    events: list[StateEvent] = []
    for number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise EvaluationStateError(f"{target}:{number} is not valid JSON") from exc
        if payload.get("spec") != JOURNAL_SPEC_VERSION:
            raise EvaluationStateError(f"{target}:{number} carries a foreign spec version")
        events.append(
            StateEvent(
                state=str(payload.get("state", "")),
                activation_sha256=str(payload.get("activation_sha256", "")),
                checkpoint=str(payload.get("checkpoint", "")),
                recorded_at_utc=str(payload.get("recorded_at_utc", "")),
                evaluation_input_sha256=payload.get("evaluation_input_sha256"),
                action=payload.get("action"),
                verdict=payload.get("verdict") or {},
            )
        )
    return events


def _chain(path: Path, activation_sha256: str, checkpoint: str) -> list[StateEvent]:
    return [
        event
        for event in _read_events(path)
        if event.activation_sha256 == activation_sha256 and event.checkpoint == checkpoint
    ]


def current_state(path: Path, *, activation_sha256: str, checkpoint: str) -> str | None:
    """Der zuletzt festgehaltene Zustand dieser Auswertung — oder ``None``."""
    chain = _chain(path, activation_sha256, checkpoint)
    return chain[-1].state if chain else None


def _last(path: Path, activation_sha256: str, checkpoint: str) -> StateEvent | None:
    chain = _chain(path, activation_sha256, checkpoint)
    return chain[-1] if chain else None


def verdict_of(path: Path, *, activation_sha256: str, checkpoint: str) -> VerdictRecord | None:
    """Das festgehaltene Verdikt — oder ``None``, wenn es keines gibt."""
    for event in reversed(_chain(path, activation_sha256, checkpoint)):
        if event.state == STATE_VERDICT_RECORDED and event.verdict:
            return VerdictRecord(**event.verdict)
    return None


def resume_target(path: Path, *, activation_sha256: str, checkpoint: str) -> str | None:
    """Welcher Input nach einem Absturz weiterlaufen MUSS.

    Liegt eine EVALUATE-Entscheidung vor und fehlt das Verdikt, dann ist die
    Antwort genau der damals eingefrorene Hash — keine neuen Panels, kein neuer
    Abruf, kein neuer Stichtag. Gibt es nichts Offenes, ist die Antwort
    ``None``; dann darf auch nichts nachgeholt werden.
    """
    chain = _chain(path, activation_sha256, checkpoint)
    if any(event.state in (STATE_VERDICT_RECORDED, STATE_CLOSED) for event in chain):
        return None
    for event in reversed(chain):
        if event.state == STATE_CHECKPOINT_DECIDED and event.action == ACTION_EVALUATE:
            return event.evaluation_input_sha256
    return None


# ── Schreiben ───────────────────────────────────────────────────────────────


def _append(path: Path, payload: dict[str, Any]) -> None:
    """Anhaengen und syncen — ein Journal, das den Absturz nicht ueberlebt,
    beantwortet genau die Frage nicht, fuer die es existiert."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _guard(
    path: Path, activation_sha256: str, checkpoint: str, next_state: str
) -> StateEvent | None:
    previous = _last(path, activation_sha256, checkpoint)
    state = previous.state if previous else None
    allowed = _ALLOWED_NEXT.get(state, frozenset())
    if next_state not in allowed:
        expected = ", ".join(sorted(allowed)) or "nothing (the chain is closed)"
        raise EvaluationStateError(
            f"cannot move to {next_state} from {state or 'an empty chain'}; expected: {expected}"
        )
    return previous


def record_input_frozen(
    path: Path,
    *,
    activation_sha256: str,
    checkpoint: str,
    evaluation_input_sha256: str,
    recorded_at_utc: str,
) -> None:
    _guard(path, activation_sha256, checkpoint, STATE_INPUT_FROZEN)
    _append(
        path,
        {
            "spec": JOURNAL_SPEC_VERSION,
            "state": STATE_INPUT_FROZEN,
            "activation_sha256": activation_sha256,
            "checkpoint": checkpoint,
            "evaluation_input_sha256": evaluation_input_sha256,
            "recorded_at_utc": recorded_at_utc,
        },
    )


def record_checkpoint_decision(
    path: Path,
    *,
    activation_sha256: str,
    checkpoint: str,
    action: str,
    evaluation_input_sha256: str | None,
    recorded_at_utc: str,
) -> None:
    """Die Entscheidung festhalten — mit Input nur bei ``EVALUATE``.

    Wer verlaengert oder fuer unreif erklaert, hat nichts gemessen. Traegt eine
    solche Entscheidung trotzdem einen eingefrorenen Input, dann existiert ein
    Auswertungsartefakt zu einem Zeitpunkt, an dem es keines geben darf.
    """
    _guard(path, activation_sha256, checkpoint, STATE_CHECKPOINT_DECIDED)
    if action != ACTION_EVALUATE and evaluation_input_sha256 is not None:
        raise EvaluationStateError(
            f"a {action} decision must not carry an evaluation input — only EVALUATE may"
        )
    if action == ACTION_EVALUATE and not evaluation_input_sha256:
        raise EvaluationStateError("an EVALUATE decision requires the frozen evaluation input")
    _append(
        path,
        {
            "spec": JOURNAL_SPEC_VERSION,
            "state": STATE_CHECKPOINT_DECIDED,
            "activation_sha256": activation_sha256,
            "checkpoint": checkpoint,
            "action": action,
            "evaluation_input_sha256": evaluation_input_sha256,
            "recorded_at_utc": recorded_at_utc,
        },
    )


def record_running(
    path: Path,
    *,
    activation_sha256: str,
    checkpoint: str,
    evaluation_input_sha256: str,
    recorded_at_utc: str,
) -> None:
    _guard(path, activation_sha256, checkpoint, STATE_EVALUATION_RUNNING)
    decided = next(
        (
            event
            for event in reversed(_chain(path, activation_sha256, checkpoint))
            if event.state == STATE_CHECKPOINT_DECIDED
        ),
        None,
    )
    if decided is None or decided.action != ACTION_EVALUATE:
        raise EvaluationStateError("only an EVALUATE decision may be followed by a run")
    # Gegen die ENTSCHEIDUNG geprueft, nicht gegen den Vorgaenger: nach einem
    # Absturz ist der Vorgaenger der abgebrochene Lauf selbst, und die Frage
    # bleibt dieselbe — ist das noch derselbe eingefrorene Input?
    if decided.evaluation_input_sha256 != evaluation_input_sha256:
        raise EvaluationStateError(
            "evaluation_input_sha256 differs from the decided one: "
            f"{evaluation_input_sha256!r} != {decided.evaluation_input_sha256!r}"
        )
    _append(
        path,
        {
            "spec": JOURNAL_SPEC_VERSION,
            "state": STATE_EVALUATION_RUNNING,
            "activation_sha256": activation_sha256,
            "checkpoint": checkpoint,
            "evaluation_input_sha256": evaluation_input_sha256,
            "recorded_at_utc": recorded_at_utc,
        },
    )


def record_verdict(path: Path, verdict: VerdictRecord) -> None:
    previous = _guard(path, verdict.activation_sha256, verdict.checkpoint, STATE_VERDICT_RECORDED)
    if previous is None or previous.evaluation_input_sha256 != verdict.evaluation_input_sha256:
        expected = previous.evaluation_input_sha256 if previous else None
        raise EvaluationStateError(
            "evaluation_input_sha256 does not match the running evaluation: "
            f"{verdict.evaluation_input_sha256!r} != {expected!r}"
        )
    payload = asdict(verdict)
    _append(
        path,
        {
            "spec": JOURNAL_SPEC_VERSION,
            "state": STATE_VERDICT_RECORDED,
            "activation_sha256": verdict.activation_sha256,
            "checkpoint": verdict.checkpoint,
            "evaluation_input_sha256": verdict.evaluation_input_sha256,
            "recorded_at_utc": verdict.recorded_at_utc,
            "result_sha256": verdict.result_sha256,
            "verdict": payload,
        },
    )


def record_closed(
    path: Path,
    *,
    activation_sha256: str,
    checkpoint: str,
    recorded_at_utc: str,
) -> None:
    _guard(path, activation_sha256, checkpoint, STATE_CLOSED)
    _append(
        path,
        {
            "spec": JOURNAL_SPEC_VERSION,
            "state": STATE_CLOSED,
            "activation_sha256": activation_sha256,
            "checkpoint": checkpoint,
            "recorded_at_utc": recorded_at_utc,
        },
    )
