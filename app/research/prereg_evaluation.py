"""Der Zustandsautomat, der aus einer Absicht eine einmalige Auswertung macht.

::

    CHECKPOINT_DECIDED -> EVALUATION_INPUT_FROZEN -> EVALUATION_RUNNING
                       -> VERDICT_RECORDED        -> CLOSED

**Die Journale werden ZUERST gelesen.** Erst wenn ein Checkpoint wirklich
unentschieden ist, wird ueberhaupt ein Datensatz beschafft. Das ist keine
Optimierung, sondern die Zusicherung selbst: solange der Plan aus aktuellen
Daten gebaut wuerde, waere "eine Wiederaufnahme benutzt keine neuen Daten" nur
eine Aussage ueber den Ausgang, nicht ueber den Weg. Deshalb bekommt
``decide_and_freeze`` einen ``rows_loader``, der auf den Pfaden CLOSED, WAIT und
RESUME **gar nicht aufgerufen** wird.

Die vollstaendige Tabelle, ausschliesslich aus dem Journal::

    T2 EVALUATE + Verdikt               -> CLOSED
    T2 EVALUATE ohne Verdikt            -> RESUME T2 auf exakt seinem SHA
    T1 EVALUATE + Verdikt               -> CLOSED
    T1 EVALUATE ohne Verdikt            -> RESUME T1 auf exakt seinem SHA
    T2 INCONCLUSIVE                     -> CLOSED (terminal)
    T1 EXTEND, jetzt < T2               -> WAIT
    T1 EXTEND, jetzt >= T2              -> UNDECIDED(T2)
    nichts, jetzt < T1                  -> WAIT
    nichts, jetzt >= T1                 -> UNDECIDED(T1)

**Die Reihenfolge beim Einfrieren ist der zweite Sicherheitsgewinn.** Das
Artefakt liegt auf der Platte, BEVOR ``EVALUATE`` journalisiert wird. Damit
gilt: *Journal sagt EVALUATE ⇒ das Artefakt existiert.* Ein Artefakt ohne
Journaleintrag ist nur eine Waise ohne Autoritaet.

**Die Frozen-Grenze liegt VOR der Signalauswahl.** Eingefroren wird der
vollstaendige OOS-Schnitt; der versiegelte Decider laeuft danach AUS dem
Artefakt. Andernfalls koennte das Artefakt zwar beweisen, welche Feuerungen
gewertet wurden, aber nicht, ob aus dem urspruenglichen Schnitt die richtigen
ausgewaehlt worden waren — die Stichprobenbildung laege ausserhalb der
Beweisgrenze.

**Nichts ist frei uebergebbar.** Hypothese, Universum, Horizont, Kosten, Alpha,
Schwellen und die Sensitivitaets-Achse stammen aus dem gehashten Candidate bzw.
aus dem Artefakt. Zusaetzlich wird vor jeder Performance-Rechnung bewiesen, dass
der laufende Code der versiegelte ist.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.analysis.features.feature_matrix import FeatureRow
from app.market_data.kline_windows import interval_to_ms
from app.research.frozen_dataset import (
    FrozenEvaluationDataset,
    FrozenRow,
    FrozenSymbolPanel,
    build_frozen_dataset,
    canonical_bytes,
    dataset_sha256,
)
from app.research.frozen_input import (
    build_frozen_input,
    evaluation_input_sha256,
    load_sealed_universe,
    read_frozen_artifact,
    write_frozen_artifact,
)
from app.research.prereg_candidate import PreRegActivation, PreRegCandidate, activation_sha256
from app.research.prereg_storage import (
    checkpoint_journal_path,
    frozen_dir,
    verdict_journal_path,
)
from app.research.prereg_window import (
    ACTION_EVALUATE,
    ACTION_EXTEND_TO_T2,
    ACTION_INCONCLUSIVE,
    CHECKPOINT_T1,
    CHECKPOINT_T2,
    MaturityCounts,
)
from app.research.prereg_window_state import (
    CheckpointJournalError,
    CheckpointRecord,
    counts_to_dict,
    load_checkpoints,
    record_checkpoint,
)
from app.research.primary_confirmatory import (
    PrimaryConfirmatoryResult,
    SymbolPanel,
    evaluate_primary,
    maturity_counts,
)
from app.research.samples import Decider
from app.research.sealed_hypothesis import (
    PRIMARY_CONFIRMATORY_NAME,
    rsi_reentry_volume_confirmed,
)

VERDICT_SCHEMA_VERSION = "kai/prereg-verdict/v1"

STATE_CHECKPOINT_DECIDED = "CHECKPOINT_DECIDED"
STATE_INPUT_FROZEN = "EVALUATION_INPUT_FROZEN"
STATE_RUNNING = "EVALUATION_RUNNING"
STATE_VERDICT_RECORDED = "VERDICT_RECORDED"
STATE_CLOSED = "CLOSED"

PLAN_CLOSED = "CLOSED"
PLAN_WAIT = "WAIT"
PLAN_RESUME = "RESUME"
PLAN_UNDECIDED = "UNDECIDED"

ALLOWED_VERDICTS = frozenset({"PASS", "NOT_MET", "INCONCLUSIVE_NOT_MATURE"})
_SHA256_LEN = 64

# Die Hypothese wird NICHT uebergeben, sondern ueber ihren versiegelten Namen
# aufgeloest. Ein freier Decider-Parameter waere ein Weg, am Candidate vorbei
# etwas anderes zu messen, als versiegelt wurde.
_HYPOTHESIS_REGISTRY: dict[str, Decider] = {
    PRIMARY_CONFIRMATORY_NAME: rsi_reentry_volume_confirmed,
}


class SealedEvaluationError(RuntimeError):
    """Der aktivierungsgebundene Pfad verweigert — fail-closed."""


def resolve_decider(name: str) -> Decider:
    decider = _HYPOTHESIS_REGISTRY.get(name)
    if decider is None:
        raise SealedEvaluationError(
            f"Hypothese {name!r} ist nicht registriert. Der Decider wird ueber den "
            "versiegelten Namen aufgeloest, nicht uebergeben."
        )
    return decider


# --- Verdikt-Datensatz -------------------------------------------------------


@dataclass(frozen=True)
class VerdictRecord:
    """Das Ergebnis, verkettet mit allem, was es hervorgebracht hat."""

    schema_version: str
    activation_sha256: str
    checkpoint: str
    evaluation_input_sha256: str
    dataset_sha256: str
    evaluator_sha256: str
    verdict: str
    n_valid: int
    n_clusters: int
    estimate_mean_net_bps: float
    standard_error: float
    # ``None`` bei entarteter Statistik: bei Streuung null liefert der Sandwich
    # ein unendliches t. Das ist kein JSON und wuerde als ``Infinity``
    # geschrieben — beim Zurueckladen etwas, das wie eine Zahl aussieht. Der
    # p-Wert traegt dieselbe Information.
    t_statistic: float | None
    df: int
    p_value: float
    alpha: float
    economic_floor_bps: float
    recorded_at_utc: str
    # Kein Aggregat ohne Zerlegung (Direktive 2026-08-08): der p-Wert soll gar
    # nicht erst ohne Gruppentabelle, Konzentration und Auslass-Proben zitierbar
    # sein. Alles NON_GATING.
    decomposition: dict[str, Any] = field(default_factory=dict)

    @property
    def result_sha256(self) -> str:
        return result_sha256(self)


def finite_or_none(value: float) -> float | None:
    """Unendlich und NaN gehoeren nicht in ein Beweisartefakt."""
    return value if math.isfinite(value) else None


def result_sha256(record: VerdictRecord) -> str:
    """Hash ueber das Ergebnis — OHNE ``recorded_at_utc``.

    Absicht: eine deterministische Wiederauswertung desselben eingefrorenen
    Datenschnitts erzeugt denselben Hash. Genau das macht die Wiederaufnahme
    nach einem Absturz pruefbar — ein abweichender Hash hiesse, dass etwas
    anderes gemessen wurde.
    """
    payload = {k: v for k, v in asdict(record).items() if k != "recorded_at_utc"}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


# --- Verdikt-Journal: dieselbe strikte Disziplin wie die Checkpoints ---------


def _hex64(value: object, where: str, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LEN
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise CheckpointJournalError(f"{where}: {field_name!r} ist kein SHA-256")
    return value


def _strict_int(value: object, where: str, field_name: str) -> int:
    """``bool`` ist eine Unterklasse von ``int`` und hat hier nichts zu suchen."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise CheckpointJournalError(
            f"{where}: {field_name!r} ist {type(value).__name__}, erwartet int"
        )
    if value < 0:
        raise CheckpointJournalError(f"{where}: {field_name!r} ist negativ")
    return value


def _strict_float(value: object, where: str, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CheckpointJournalError(
            f"{where}: {field_name!r} ist {type(value).__name__}, erwartet Zahl"
        )
    number = float(value)
    if not math.isfinite(number):
        raise CheckpointJournalError(f"{where}: {field_name!r} ist nicht endlich")
    return number


def _validate_verdict_payload(payload: Any, where: str) -> VerdictRecord:
    """Typ UND Wertebereich. An der letzten Wahrheitsschicht keine Python-Semantik."""
    if not isinstance(payload, dict):
        raise CheckpointJournalError(f"{where}: Zeile ist kein Objekt")
    if payload.get("schema_version") != VERDICT_SCHEMA_VERSION:
        raise CheckpointJournalError(
            f"{where}: schema_version ist {payload.get('schema_version')!r}, "
            f"erwartet {VERDICT_SCHEMA_VERSION!r}"
        )
    if payload.get("checkpoint") not in (CHECKPOINT_T1, CHECKPOINT_T2):
        raise CheckpointJournalError(f"{where}: checkpoint {payload.get('checkpoint')!r}")
    if payload.get("verdict") not in ALLOWED_VERDICTS:
        raise CheckpointJournalError(
            f"{where}: verdict {payload.get('verdict')!r}, erlaubt {sorted(ALLOWED_VERDICTS)}"
        )

    for name in (
        "activation_sha256",
        "evaluation_input_sha256",
        "dataset_sha256",
        "evaluator_sha256",
    ):
        _hex64(payload.get(name), where, name)

    p_value = _strict_float(payload.get("p_value"), where, "p_value")
    if not 0.0 <= p_value <= 1.0:
        raise CheckpointJournalError(f"{where}: p_value={p_value} liegt ausserhalb [0, 1]")
    alpha = _strict_float(payload.get("alpha"), where, "alpha")
    if not 0.0 < alpha < 1.0:
        raise CheckpointJournalError(f"{where}: alpha={alpha} liegt ausserhalb (0, 1)")

    recorded_at = payload.get("recorded_at_utc")
    if not isinstance(recorded_at, str):
        raise CheckpointJournalError(f"{where}: recorded_at_utc ist kein Text")
    try:
        parsed = datetime.fromisoformat(recorded_at)
    except ValueError as exc:
        raise CheckpointJournalError(f"{where}: recorded_at_utc ist kein ISO-8601") from exc
    if parsed.tzinfo is None:
        raise CheckpointJournalError(
            f"{where}: recorded_at_utc ist zeitzonenlos — UTC wird nicht geraten"
        )

    t_stat = payload.get("t_statistic")
    if t_stat is not None:
        _strict_float(t_stat, where, "t_statistic")
    decomposition = payload.get("decomposition", {})
    if not isinstance(decomposition, dict):
        raise CheckpointJournalError(f"{where}: decomposition ist kein Objekt")

    return VerdictRecord(
        schema_version=VERDICT_SCHEMA_VERSION,
        activation_sha256=str(payload["activation_sha256"]),
        checkpoint=str(payload["checkpoint"]),
        evaluation_input_sha256=str(payload["evaluation_input_sha256"]),
        dataset_sha256=str(payload["dataset_sha256"]),
        evaluator_sha256=str(payload["evaluator_sha256"]),
        verdict=str(payload["verdict"]),
        n_valid=_strict_int(payload.get("n_valid"), where, "n_valid"),
        n_clusters=_strict_int(payload.get("n_clusters"), where, "n_clusters"),
        estimate_mean_net_bps=_strict_float(
            payload.get("estimate_mean_net_bps"), where, "estimate_mean_net_bps"
        ),
        standard_error=_strict_float(payload.get("standard_error"), where, "standard_error"),
        t_statistic=None if t_stat is None else float(t_stat),
        df=_strict_int(payload.get("df"), where, "df"),
        p_value=p_value,
        alpha=alpha,
        economic_floor_bps=_strict_float(
            payload.get("economic_floor_bps"), where, "economic_floor_bps"
        ),
        recorded_at_utc=recorded_at,
        decomposition=decomposition,
    )


def load_verdicts(path: Path, *, activation_sha256_value: str) -> tuple[VerdictRecord, ...]:
    """Alle Verdikte dieser Aktivierung. Fehlende Datei = leer, kaputte = Abbruch."""
    if not path.exists():
        return ()
    out: list[VerdictRecord] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{path}:{number}"
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CheckpointJournalError(f"{where} ist kein gueltiges JSON") from exc
        stored = payload.pop("result_sha256", None) if isinstance(payload, dict) else None
        record = _validate_verdict_payload(payload, where)
        if record.activation_sha256 != activation_sha256_value:
            raise CheckpointJournalError(f"{where} gehoert zu einer anderen Aktivierung")
        if stored != record.result_sha256:
            raise CheckpointJournalError(
                f"{where}: result_sha256 passt nicht zum Inhalt — nachtraeglich veraendert."
            )
        out.append(record)
    return tuple(out)


def record_verdict(path: Path, record: VerdictRecord) -> bool:
    """Genau ein Verdikt je Checkpoint. Identisch = No-Op, abweichend = Abbruch."""
    for previous in load_verdicts(path, activation_sha256_value=record.activation_sha256):
        if previous.checkpoint != record.checkpoint:
            continue
        if previous.result_sha256 == record.result_sha256:
            return False
        raise CheckpointJournalError(
            f"{record.checkpoint}: es steht bereits ein ANDERES Verdikt "
            f"({previous.verdict}, p={previous.p_value}). Ein Checkpoint wird "
            "genau einmal gewertet."
        )
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(record)
    payload["result_sha256"] = record.result_sha256
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    if not existed and os.name == "posix":  # pragma: no cover - plattformabhaengig
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return True


# --- Der Plan: ausschliesslich aus den Journalen ------------------------------


@dataclass(frozen=True)
class CheckpointPlan:
    """Was zu tun ist — ermittelt OHNE einen einzigen Datenzugriff."""

    action: str
    checkpoint: str | None = None
    evaluation_input_sha256: str = ""
    reasons: tuple[str, ...] = ()

    @property
    def needs_data(self) -> bool:
        return self.action == PLAN_UNDECIDED

    @property
    def may_evaluate(self) -> bool:
        return self.action in (PLAN_RESUME, PLAN_UNDECIDED)

    @property
    def must_use_frozen_input(self) -> bool:
        return self.action == PLAN_RESUME


def _parse_utc(timestamp_utc: str, field_name: str) -> datetime:
    parsed = datetime.fromisoformat(timestamp_utc)
    if parsed.tzinfo is None:
        raise SealedEvaluationError(f"{field_name}: zeitzonenlos — UTC wird nicht geraten")
    return parsed.astimezone(UTC)


def plan_checkpoint(*, now_utc: str, activation: PreRegActivation, root: Path) -> CheckpointPlan:
    """Die vollstaendige Tabelle. Liest NUR Journale, niemals Marktdaten."""
    sha = activation_sha256(activation)
    checkpoints = load_checkpoints(checkpoint_journal_path(root, sha), activation_sha256=sha)
    verdicts = load_verdicts(verdict_journal_path(root, sha), activation_sha256_value=sha)

    decided = {record.checkpoint: record for record in checkpoints}
    judged = {record.checkpoint for record in verdicts}
    now = _parse_utc(now_utc, "now_utc")

    t1 = decided.get(CHECKPOINT_T1)
    t2 = decided.get(CHECKPOINT_T2)

    if t1 is not None and t1.action == ACTION_EVALUATE and t2 is not None:
        raise SealedEvaluationError(
            "T1 wurde gewertet UND T2 entschieden — das Journal ist widerspruechlich."
        )

    # T2 zuerst: ist dort etwas entschieden, ist T1 zwangslaeufig verlaengert
    # worden, und der spaetere Checkpoint bestimmt den Zustand.
    for record in (t2, t1):
        if record is None or record.action != ACTION_EVALUATE:
            continue
        if record.checkpoint in judged:
            return CheckpointPlan(
                action=PLAN_CLOSED,
                checkpoint=record.checkpoint,
                reasons=(f"{record.checkpoint}: Verdikt liegt vor — genau einmal.",),
            )
        if not record.evaluation_input_sha256:
            raise SealedEvaluationError(
                f"{record.checkpoint}: EVALUATE steht im Journal, aber ohne "
                "evaluation_input_sha256 — der Datenschnitt ist nicht wiederherstellbar."
            )
        return CheckpointPlan(
            action=PLAN_RESUME,
            checkpoint=record.checkpoint,
            evaluation_input_sha256=record.evaluation_input_sha256,
            reasons=(
                f"{record.checkpoint}: EVALUATE ohne Verdikt — exakt den "
                "eingefrorenen Datenschnitt erneut auswerten, keine neuen Daten.",
            ),
        )

    if t2 is not None and t2.action == ACTION_INCONCLUSIVE:
        return CheckpointPlan(
            action=PLAN_CLOSED,
            checkpoint=CHECKPOINT_T2,
            reasons=("T2 endete unreif — INCONCLUSIVE ist terminal, kein drittes Fenster.",),
        )

    if t1 is not None and t1.action == ACTION_EXTEND_TO_T2:
        if now < _parse_utc(activation.t2_utc, "t2_utc"):
            return CheckpointPlan(
                action=PLAN_WAIT,
                checkpoint=CHECKPOINT_T2,
                reasons=("an T1 verlaengert — naechster Entscheidungszeitpunkt ist T2",),
            )
        return CheckpointPlan(action=PLAN_UNDECIDED, checkpoint=CHECKPOINT_T2)

    if now < _parse_utc(activation.t1_utc, "t1_utc"):
        return CheckpointPlan(
            action=PLAN_WAIT,
            checkpoint=CHECKPOINT_T1,
            reasons=("T1 ist der erste Entscheidungszeitpunkt — auch bei erreichter Reife.",),
        )
    return CheckpointPlan(action=PLAN_UNDECIDED, checkpoint=CHECKPOINT_T1)


# --- Adapter zwischen lebenden Zeilen und eingefrorenen ----------------------


def frozen_rows_from_panel(
    rows: Sequence[FeatureRow],
    labels: Sequence[float | None],
    label_exit_utc: Sequence[str],
) -> list[FrozenRow]:
    """Der VOLLSTAENDIGE OOS-Schnitt — ohne den Decider zu fragen.

    Frueher wurden hier nur feuernde Zeilen eingefroren. Damit konnte das
    Artefakt beweisen, WELCHE Feuerungen gewertet wurden, aber nicht, ob aus dem
    urspruenglichen Schnitt die richtigen ausgewaehlt worden waren — die
    Stichprobenbildung lag ausserhalb der Beweisgrenze.
    """
    if not (len(rows) == len(labels) == len(label_exit_utc)):
        raise SealedEvaluationError("rows, labels und label_exit_utc haben ungleiche Laenge")
    return [
        FrozenRow(
            signal_timestamp_utc=row.timestamp_utc,
            label_exit_utc=exit_at,
            features={k: v for k, v in asdict(row).items() if k != "timestamp_utc"},
            label_bps=label,
        )
        for row, label, exit_at in zip(rows, labels, label_exit_utc, strict=True)
    ]


def panels_from_frozen(dataset: FrozenEvaluationDataset) -> list[SymbolPanel]:
    """Baue die Auswertungs-Panels AUS DEM ARTEFAKT — nie aus aktuellen Daten."""
    panels: list[SymbolPanel] = []
    for panel in dataset.panels:
        rows: list[FeatureRow] = []
        labels: list[float | None] = []
        for row in panel.rows:
            # ``FeatureRow`` erwartet konkrete Typen; die eingefrorenen Werte
            # sind per Konstruktion endliche floats oder None.
            rows.append(
                FeatureRow(timestamp_utc=row.signal_timestamp_utc, **row.features)  # type: ignore[arg-type]
            )
            labels.append(row.label_bps)
        panels.append(SymbolPanel(symbol=panel.symbol, rows=rows, labels=labels))
    return panels


def maturity_from_dataset(
    dataset: FrozenEvaluationDataset, *, hypothesis: str, horizon: int, timeframe_ms: int
) -> MaturityCounts:
    """Blinde Reifezahlen aus dem eingefrorenen Schnitt. Kein Mittelwert, kein p."""
    return maturity_counts(
        panels_from_frozen(dataset),
        resolve_decider(hypothesis),
        round_trip_cost_bps=0.0,
        timeframe_ms=timeframe_ms,
        horizon=horizon,
    )


# --- Entscheiden und einfrieren ----------------------------------------------


def decide_and_freeze(
    *,
    now_utc: str,
    candidate: PreRegCandidate,
    activation: PreRegActivation,
    root: Path,
    repo_root: Path,
    rows_loader: Callable[[], Mapping[str, Sequence[FrozenRow]]],
) -> tuple[CheckpointPlan, MaturityCounts | None]:
    """Plan aus den Journalen; Daten NUR, wenn wirklich unentschieden.

    ``rows_loader`` wird auf den Pfaden CLOSED, WAIT und RESUME nicht aufgerufen.
    Das macht "eine Wiederaufnahme benutzt keine neuen Daten" zu einer Aussage
    ueber den Weg statt ueber den Ausgang.
    """
    plan = plan_checkpoint(now_utc=now_utc, activation=activation, root=root)
    if plan.action != PLAN_UNDECIDED:
        return plan, None

    checkpoint = plan.checkpoint or CHECKPOINT_T1
    sha = activation_sha256(activation)
    universe_sha, symbols = load_sealed_universe(
        repo_root, expected_sha256=candidate.universe_sha256
    )
    timeframe_ms = interval_to_ms(candidate.timeframe)
    cutoff = activation.t1_utc if checkpoint == CHECKPOINT_T1 else activation.t2_utc

    dataset = build_frozen_dataset(
        checkpoint=checkpoint,
        t0_utc=activation.t0_utc,
        cutoff_utc=cutoff,
        sealed_symbols=symbols,
        rows_by_symbol=rows_loader(),
    )
    counts = maturity_from_dataset(
        dataset,
        hypothesis=candidate.hypothesis,
        horizon=candidate.horizon,
        timeframe_ms=timeframe_ms,
    )

    mature = counts.n_valid >= candidate.n_valid_min and counts.n_clusters >= candidate.cluster_min
    journal = checkpoint_journal_path(root, sha)

    if not mature:
        action = ACTION_EXTEND_TO_T2 if checkpoint == CHECKPOINT_T1 else ACTION_INCONCLUSIVE
        record_checkpoint(
            journal,
            CheckpointRecord(
                activation_sha256=sha,
                checkpoint=checkpoint,
                action=action,
                mature=False,
                recorded_at_utc=now_utc,
                counts=counts_to_dict(counts),
            ),
        )
        return (
            CheckpointPlan(
                action=PLAN_CLOSED if action == ACTION_INCONCLUSIVE else PLAN_WAIT,
                checkpoint=checkpoint,
                reasons=(
                    f"n_valid={counts.n_valid}/{candidate.n_valid_min}, "
                    f"clusters={counts.n_clusters}/{candidate.cluster_min} — "
                    "KEINE Performance ansehen.",
                ),
            ),
            counts,
        )

    frozen = build_frozen_input(
        dataset=dataset,
        candidate=candidate,
        activation=activation,
        sealed_universe_sha256=universe_sha,
        sealed_symbols=symbols,
        maturity_counts=counts,
    )
    digest = evaluation_input_sha256(frozen)
    # ZUERST das Artefakt — ein Journaleintrag ohne Datengrundlage waere die
    # gefaehrliche Reihenfolge.
    write_frozen_artifact(frozen_dir(root, sha, checkpoint), frozen, dataset)
    record_checkpoint(
        journal,
        CheckpointRecord(
            activation_sha256=sha,
            checkpoint=checkpoint,
            action=ACTION_EVALUATE,
            mature=True,
            recorded_at_utc=now_utc,
            counts=counts_to_dict(counts),
            evaluation_input_sha256=digest,
        ),
    )
    return (
        CheckpointPlan(
            action=PLAN_UNDECIDED, checkpoint=checkpoint, evaluation_input_sha256=digest
        ),
        counts,
    )


# --- Werten, ausschliesslich aus dem Artefakt --------------------------------


def run_sealed_evaluation(
    *,
    plan: CheckpointPlan,
    activation: PreRegActivation,
    root: Path,
    repo_root: Path,
    now_utc: str,
) -> PrimaryConfirmatoryResult:
    """Werte den eingefrorenen Datenschnitt. Nichts daran ist uebergebbar.

    Es gibt hier keinen Parameter fuer Hypothese, Universum, Horizont, Kosten,
    Alpha, Schwellen oder die Sensitivitaets-Achse — alle stammen aus dem
    Artefakt, das seinerseits an den gehashten Candidate gebunden ist. Nicht
    einmal der Candidate wird noch entgegengenommen.
    """
    if not plan.may_evaluate or not plan.evaluation_input_sha256:
        raise SealedEvaluationError(
            f"action={plan.action} — an diesem Punkt darf nicht gewertet werden."
        )

    sha = activation_sha256(activation)
    checkpoint = plan.checkpoint or CHECKPOINT_T1
    payload = read_frozen_artifact(frozen_dir(root, sha, checkpoint), plan.evaluation_input_sha256)
    body = payload["input"]
    if body["activation_sha256"] != sha:
        raise SealedEvaluationError("das Artefakt gehoert zu einer anderen Aktivierung")
    if body["candidate_sha256"] != activation.candidate_sha256:
        raise SealedEvaluationError("das Artefakt gehoert zu einem anderen Candidate")
    if body["evaluator_sha256"] != activation.evaluator_sha256:
        raise SealedEvaluationError("das Artefakt nennt einen anderen Evaluator")

    contract = body["resolved_contract"]

    # Der laufende Code muss der versiegelte sein — sonst ist es ein anderes
    # Experiment, kein schwaecheres Ergebnis.
    from app.research.evaluator_identity import assert_runtime_matches

    assert_runtime_matches(
        repo_root=repo_root,
        research_code_sha=body["research_code_sha"],
        evaluator_sha256=body["evaluator_sha256"],
        decider_name=contract["hypothesis"],
    )

    dataset = _dataset_from_payload(payload["dataset"])
    if dataset_sha256(dataset) != body["dataset_sha256"]:  # pragma: no cover - Invariante
        raise SealedEvaluationError("dataset_sha256 passt nicht zu den geladenen Daten")

    result = evaluate_primary(
        panels_from_frozen(dataset),
        resolve_decider(contract["hypothesis"]),
        hypothesis=contract["hypothesis"],
        universe_sha256=body["universe_sha256"],
        round_trip_cost_bps=contract["round_trip_cost_bps"],
        timeframe_ms=interval_to_ms(contract["timeframe"]),
        horizon=contract["horizon"],
        n_min=contract["n_valid_min"],
        cluster_min=contract["cluster_min"],
        alpha=contract["alpha"],
        economic_floor_bps=contract["economic_floor_bps"],
        sensitivity_cost_bps=tuple(contract["sensitivity_cost_bps"]),
    )

    record_verdict(
        verdict_journal_path(root, sha),
        VerdictRecord(
            schema_version=VERDICT_SCHEMA_VERSION,
            activation_sha256=sha,
            checkpoint=body["checkpoint"],
            evaluation_input_sha256=plan.evaluation_input_sha256,
            dataset_sha256=body["dataset_sha256"],
            evaluator_sha256=body["evaluator_sha256"],
            verdict=result.verdict,
            n_valid=result.summary.n,
            n_clusters=result.summary.n_clusters,
            estimate_mean_net_bps=result.summary.mean_bps,
            standard_error=result.summary.se_bps,
            t_statistic=finite_or_none(result.summary.t_stat),
            df=result.summary.dof,
            p_value=result.summary.p_value,
            alpha=contract["alpha"],
            economic_floor_bps=contract["economic_floor_bps"],
            recorded_at_utc=now_utc,
            decomposition=_decomposition(result),
        ),
    )
    return result


def _decomposition(result: PrimaryConfirmatoryResult) -> dict[str, Any]:
    """Wer traegt das Ergebnis — ein Prozess oder eine Stunde und ein Asset?"""
    clusters = result.clusters
    return {
        "per_symbol_signals": dict(sorted(clusters.per_symbol_signals.items())),
        "top_symbol_share": clusters.top_symbol_share,
        "top_cluster_share": clusters.top_cluster_share,
        "leave_one_out_top_symbol": {
            "symbol": clusters.leave_one_out_top_symbol.symbol,
            "n_signals": clusters.leave_one_out_top_symbol.n_signals,
            "n_clusters": clusters.leave_one_out_top_symbol.n_clusters,
        },
        "robustness": [
            {
                "label": diagnostic.label,
                "without_unit": diagnostic.without_unit,
                "n": diagnostic.n,
                "n_clusters": diagnostic.n_clusters,
                "mean_bps": diagnostic.mean_bps,
                "p_value": diagnostic.p_value,
            }
            for diagnostic in result.robustness
        ],
        "status": "DIAGNOSTIC_NON_GATING",
    }


def _dataset_from_payload(payload: dict[str, Any]) -> FrozenEvaluationDataset:
    return FrozenEvaluationDataset(
        schema_version=payload["schema_version"],
        checkpoint=payload["checkpoint"],
        t0_utc=payload["t0_utc"],
        cutoff_utc=payload["cutoff_utc"],
        symbols=tuple(payload["symbols"]),
        panels=tuple(
            FrozenSymbolPanel(
                symbol=panel["symbol"],
                rows=tuple(
                    FrozenRow(
                        signal_timestamp_utc=row["signal_timestamp_utc"],
                        label_exit_utc=row["label_exit_utc"],
                        features=row["features"],
                        label_bps=row["label_bps"],
                    )
                    for row in panel["rows"]
                ),
            )
            for panel in payload["panels"]
        ),
    )


__all__ = [
    "ALLOWED_VERDICTS",
    "PLAN_CLOSED",
    "PLAN_RESUME",
    "PLAN_UNDECIDED",
    "PLAN_WAIT",
    "STATE_CHECKPOINT_DECIDED",
    "STATE_CLOSED",
    "STATE_INPUT_FROZEN",
    "STATE_RUNNING",
    "STATE_VERDICT_RECORDED",
    "CheckpointPlan",
    "SealedEvaluationError",
    "VerdictRecord",
    "decide_and_freeze",
    "frozen_rows_from_panel",
    "load_verdicts",
    "panels_from_frozen",
    "plan_checkpoint",
    "record_verdict",
    "resolve_decider",
    "result_sha256",
    "run_sealed_evaluation",
]
