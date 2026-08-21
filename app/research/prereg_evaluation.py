"""Der Zustandsautomat, der aus einer Absicht eine einmalige Auswertung macht.

::

    CHECKPOINT_DECIDED  -> EVALUATION_INPUT_FROZEN -> EVALUATION_RUNNING
                        -> VERDICT_RECORDED        -> CLOSED

**Die Reihenfolge ist der Sicherheitsgewinn, nicht die Zustandsnamen.** Das
Artefakt wird geschrieben, BEVOR ``EVALUATE`` im Journal steht::

    1. exakten Datenschnitt bestimmen (in-memory)
    2. blinde Reifezahlen daraus
    3. EXTEND / INCONCLUSIVE  -> nur die Entscheidung journalisieren
    4. EVALUATE              -> FrozenEvaluationInput bauen, hashen,
                                unveraenderlich schreiben (fsync + rename)
    5. DANACH                 -> CHECKPOINT_DECIDED(EVALUATE) mit dem Hash
    6. Evaluator startet ausschliesslich aus dem Artefakt

Andersherum waere die gefaehrliche Variante: stuende ``EVALUATE`` im Journal und
das Artefakt fehlte, waere der Entschluss erhalten, seine Datengrundlage aber
nicht — und beim Neustart wuerde neu geladen. Kaeme dann eine minimal andere
Historie zurueck, waere die Wiederholung eine zweite Auswertung.

So herum gilt der starke Satz::

    Journal sagt EVALUATE  =>  das referenzierte Artefakt EXISTIERT bereits

Ein Artefakt ohne Journaleintrag ist nur eine Waise. Es hat keine Autoritaet und
schadet nichts.

**Wiederaufnahme ist nicht Wiederholung.** ``EVALUATE`` ohne ``VERDICT_RECORDED``
bedeutet: exakt dieses Artefakt erneut auswerten. Keine neuen Daten, kein neuer
Hash, kein zweiter Blick. ``EVALUATION_RUNNING`` ist nach einem Absturz niemals
terminal — nur ``VERDICT_RECORDED`` schliesst.

**Nichts ist frei uebergebbar.** Hypothese, Universum, Horizont, Kosten, Alpha
und die oekonomische Huerde werden aus dem gehashten Candidate aufgeloest. Es
gibt in diesem Modul keinen Parameter, mit dem man sie ueberschreiben koennte.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.analysis.features.feature_matrix import FeatureRow
from app.market_data.kline_windows import interval_to_ms
from app.research.frozen_dataset import (
    FrozenEvaluationDataset,
    FrozenRow,
    build_frozen_dataset,
    canonical_bytes,
    dataset_sha256,
)
from app.research.frozen_input import (
    FrozenInputError,
    build_frozen_input,
    evaluation_input_sha256,
    read_frozen_artifact,
    write_frozen_artifact,
)
from app.research.prereg_candidate import PreRegActivation, PreRegCandidate, activation_sha256
from app.research.prereg_window import (
    ACTION_EVALUATE,
    ACTION_RESUME_EVALUATION,
    CHECKPOINT_T1,
    CHECKPOINT_T2,
    MaturityCounts,
    WindowDecision,
    decide_window_action,
)
from app.research.prereg_window_state import (
    RECORDABLE_ACTIONS,
    CheckpointJournalError,
    CheckpointRecord,
    record_checkpoint,
    resolve_t1_outcome,
)
from app.research.primary_confirmatory import (
    PrimaryConfirmatoryResult,
    SymbolPanel,
    evaluate_primary,
)
from app.research.runner import PRIMARY_CONFIRMATORY_NAME, rsi_reentry_volume_confirmed
from app.research.samples import Decider

VERDICT_SCHEMA_VERSION = "kai/prereg-verdict/v1"

STATE_CHECKPOINT_DECIDED = "CHECKPOINT_DECIDED"
STATE_INPUT_FROZEN = "EVALUATION_INPUT_FROZEN"
STATE_RUNNING = "EVALUATION_RUNNING"
STATE_VERDICT_RECORDED = "VERDICT_RECORDED"
STATE_CLOSED = "CLOSED"

# Die Hypothese wird NICHT uebergeben, sondern ueber ihren versiegelten Namen
# aufgeloest. Ein freier Decider-Parameter waere ein Weg, am Candidate vorbei
# etwas anderes zu messen, als versiegelt wurde.
_HYPOTHESIS_REGISTRY: dict[str, Decider] = {
    PRIMARY_CONFIRMATORY_NAME: rsi_reentry_volume_confirmed,
}


class SealedEvaluationError(RuntimeError):
    """Der aktivierungsgebundene Pfad verweigert — fail-closed."""


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
    # ``None``, wenn die Statistik entartet ist. Bei Streuung null liefert der
    # Sandwich ein unendliches t — das ist kein JSON und wuerde als
    # ``Infinity`` geschrieben, also als etwas, das beim Zurueckladen wie eine
    # Zahl aussieht. Der p-Wert traegt dieselbe Information.
    t_statistic: float | None
    df: int
    p_value: float
    alpha: float
    economic_floor_bps: float
    recorded_at_utc: str
    # Kein Aggregat ohne Zerlegung (Direktive 2026-08-08). Der p-Wert darf
    # nicht zitierbar sein, ohne dass danebensteht, WER ihn traegt:
    # Gruppentabelle, Konzentration und beide Auslass-Proben. Alles
    # NON_GATING — es aendert das Verdikt nicht, aber es kann es einordnen.
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
    Datenschnitts erzeugt denselben ``result_sha256``. Genau das macht die
    Wiederaufnahme nach einem Absturz pruefbar — ein abweichender Hash hiesse,
    dass etwas anderes gemessen wurde.
    """
    payload = {k: v for k, v in asdict(record).items() if k != "recorded_at_utc"}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


# --- Verdikt-Journal (append-only, dieselbe Haltbarkeit wie die Checkpoints) --


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
        stored = payload.pop("result_sha256", None)
        try:
            record = VerdictRecord(**payload)
        except TypeError as exc:
            raise CheckpointJournalError(f"{where}: Felder passen nicht zum Schema") from exc
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


# --- Adapter zwischen lebenden Zeilen und eingefrorenen -----------------------


def freeze_rows(
    rows: list[FeatureRow],
    labels: list[float | None],
    label_exit_utc: list[str],
    decide: Decider,
) -> list[FrozenRow]:
    """Nur die Zeilen, auf denen die Regel FEUERT — inklusive der ohne Label.

    Eine Feuerung ohne Label ist DATA_UNAVAILABLE und bleibt sichtbar; sie
    zaehlt zu ``raw_fires``, nicht zu ``n_valid``. Nicht feuernde Zeilen sind
    fuer das Verdikt bedeutungslos und wuerden das Artefakt nur aufblaehen.
    """
    if not (len(rows) == len(labels) == len(label_exit_utc)):
        raise SealedEvaluationError("rows, labels und label_exit_utc haben ungleiche Laenge")
    frozen: list[FrozenRow] = []
    for row, label, exit_at in zip(rows, labels, label_exit_utc, strict=True):
        if decide(row) == 0:
            continue
        frozen.append(
            FrozenRow(
                signal_timestamp_utc=row.timestamp_utc,
                label_exit_utc=exit_at,
                features={k: v for k, v in asdict(row).items() if k != "timestamp_utc"},
                label_bps=label,
            )
        )
    return frozen


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
    dataset: FrozenEvaluationDataset,
    *,
    horizon: int,
    timeframe_ms: int,
) -> MaturityCounts:
    """Blinde Reifezahlen aus dem eingefrorenen Schnitt. Kein Mittelwert, kein p."""
    from app.research.primary_confirmatory import maturity_counts

    return maturity_counts(
        panels_from_frozen(dataset),
        _resolve_decider_or_fail(PRIMARY_CONFIRMATORY_NAME),
        round_trip_cost_bps=0.0,
        timeframe_ms=timeframe_ms,
        horizon=horizon,
    )


def _resolve_decider_or_fail(name: str) -> Decider:
    decider = _HYPOTHESIS_REGISTRY.get(name)
    if decider is None:
        raise SealedEvaluationError(
            f"Hypothese {name!r} ist nicht registriert. Der Decider wird ueber den "
            "versiegelten Namen aufgeloest, nicht uebergeben."
        )
    return decider


# --- Schritt 1-5: entscheiden, einfrieren, journalisieren ---------------------


def decide_and_freeze(
    *,
    now_utc: str,
    candidate: PreRegCandidate,
    activation: PreRegActivation,
    sealed_symbols: tuple[str, ...],
    sealed_universe_sha256: str,
    rows_by_symbol: dict[str, list[FrozenRow]],
    checkpoint_journal: Path,
    verdict_journal: Path,
    artifact_dir: Path,
) -> tuple[WindowDecision, str]:
    """Entscheide den Checkpoint und friere bei ``EVALUATE`` die Eingabe ein.

    Returns:
        (Entscheidung, ``evaluation_input_sha256`` oder "").

    Die Reihenfolge ist die Zusicherung: bei ``EVALUATE`` liegt das Artefakt auf
    der Platte, bevor der Journaleintrag geschrieben wird.
    """
    timeframe_ms = interval_to_ms(candidate.timeframe)
    checkpoint = _checkpoint_for(now_utc, activation)
    cutoff = activation.t1_utc if checkpoint == "T1" else activation.t2_utc

    dataset = build_frozen_dataset(
        checkpoint=checkpoint,
        t0_utc=activation.t0_utc,
        cutoff_utc=cutoff,
        sealed_symbols=sealed_symbols,
        rows_by_symbol=rows_by_symbol,
    )
    counts = maturity_from_dataset(dataset, horizon=candidate.horizon, timeframe_ms=timeframe_ms)

    act_sha = activation_sha256(activation)
    verdicts = load_verdicts(verdict_journal, activation_sha256_value=act_sha)

    # BEWUSST NICHT ``resolve_window``: das journalisiert die Entscheidung
    # sofort. Bei EVALUATE stuende sie damit im Journal, BEVOR das Artefakt
    # existiert — genau die gefaehrliche Reihenfolge. Hier wird erst rein
    # entschieden, dann eingefroren, dann journalisiert.
    decision = decide_window_action(
        now_utc=now_utc,
        t1_utc=activation.t1_utc,
        t2_utc=activation.t2_utc,
        counts=counts,
        n_valid_min=candidate.n_valid_min,
        cluster_min=candidate.cluster_min,
        t1_outcome=resolve_t1_outcome(checkpoint_journal, activation_sha256=act_sha),
        verdict_recorded=bool(verdicts),
    )

    if decision.action != ACTION_EVALUATE:
        # EXTEND / INCONCLUSIVE: Entscheidung festhalten, keine Performance,
        # kein Artefakt. WAIT / RESUME / CLOSED sind Zustaende, keine
        # Entscheidungen — sie gehoeren nicht ins Journal.
        if decision.action in RECORDABLE_ACTIONS and decision.checkpoint in (
            CHECKPOINT_T1,
            CHECKPOINT_T2,
        ):
            record_checkpoint(
                checkpoint_journal,
                CheckpointRecord(
                    activation_sha256=act_sha,
                    checkpoint=decision.checkpoint,
                    action=decision.action,
                    mature=decision.mature,
                    recorded_at_utc=now_utc,
                    counts=_counts_dict(counts),
                ),
            )
        return decision, ""

    frozen = build_frozen_input(
        dataset=dataset,
        candidate=candidate,
        activation=activation,
        sealed_universe_sha256=sealed_universe_sha256,
        sealed_symbols=sealed_symbols,
        maturity_counts=counts,
    )
    digest = evaluation_input_sha256(frozen)
    # ZUERST das Artefakt — ein Journaleintrag ohne Datengrundlage waere die
    # gefaehrliche Reihenfolge.
    write_frozen_artifact(artifact_dir, frozen, dataset)
    record_checkpoint(
        checkpoint_journal,
        CheckpointRecord(
            activation_sha256=act_sha,
            checkpoint=checkpoint,
            action=ACTION_EVALUATE,
            mature=decision.mature,
            recorded_at_utc=now_utc,
            counts=_counts_dict(counts),
            evaluation_input_sha256=digest,
        ),
    )
    return decision, digest


def _counts_dict(counts: MaturityCounts) -> dict[str, int]:
    from app.research.prereg_window_state import counts_to_dict

    return counts_to_dict(counts)


def _checkpoint_for(now_utc: str, activation: PreRegActivation) -> str:
    from datetime import datetime

    now = datetime.fromisoformat(now_utc)
    return "T2" if now >= datetime.fromisoformat(activation.t2_utc) else "T1"


# --- Schritt 6: werten, ausschliesslich aus dem Artefakt ----------------------


def run_sealed_evaluation(
    *,
    decision: WindowDecision,
    evaluation_input_sha256_value: str,
    candidate: PreRegCandidate,
    activation: PreRegActivation,
    artifact_dir: Path,
    verdict_journal: Path,
    now_utc: str,
) -> PrimaryConfirmatoryResult:
    """Werte den eingefrorenen Datenschnitt. Nichts daran ist uebergebbar.

    Es gibt hier keinen Parameter fuer Hypothese, Universum, Horizont, Kosten,
    Alpha oder die oekonomische Huerde — alle stammen aus dem Artefakt, das
    seinerseits an den gehashten Candidate gebunden ist.
    """
    if not decision.may_evaluate:
        raise SealedEvaluationError(
            f"action={decision.action} — an diesem Punkt darf nicht gewertet werden."
        )

    payload = read_frozen_artifact(artifact_dir, evaluation_input_sha256_value)
    body = payload["input"]
    act_sha = activation_sha256(activation)
    if body["activation_sha256"] != act_sha:
        raise SealedEvaluationError("das Artefakt gehoert zu einer anderen Aktivierung")
    if body["candidate_sha256"] != activation.candidate_sha256:
        raise SealedEvaluationError("das Artefakt gehoert zu einem anderen Candidate")

    contract = body["resolved_contract"]
    dataset = _dataset_from_payload(payload["dataset"])
    if dataset_sha256(dataset) != body["dataset_sha256"]:  # pragma: no cover - Invariante
        raise SealedEvaluationError("dataset_sha256 passt nicht zu den geladenen Daten")

    result = evaluate_primary(
        panels_from_frozen(dataset),
        _resolve_decider_or_fail(contract["hypothesis"]),
        hypothesis=contract["hypothesis"],
        universe_sha256=body["universe_sha256"],
        round_trip_cost_bps=contract["round_trip_cost_bps"],
        timeframe_ms=interval_to_ms(contract["timeframe"]),
        horizon=contract["horizon"],
        n_min=contract["n_valid_min"],
        cluster_min=contract["cluster_min"],
        alpha=contract["alpha"],
        economic_floor_bps=contract["economic_floor_bps"],
        sensitivity_cost_bps=tuple(candidate.sensitivity_cost_bps),
    )

    record_verdict(
        verdict_journal,
        VerdictRecord(
            schema_version=VERDICT_SCHEMA_VERSION,
            activation_sha256=act_sha,
            checkpoint=body["checkpoint"],
            evaluation_input_sha256=evaluation_input_sha256_value,
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
    """Wer traegt das Ergebnis — ein Prozess oder eine Stunde und ein Asset?

    Alles hier ist ``DIAGNOSTIC_NON_GATING``. Es steht im Verdikt-Artefakt,
    damit der p-Wert gar nicht erst ohne seine Zerlegung zitierbar ist.
    """
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
    from app.research.frozen_dataset import FrozenSymbolPanel

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


def resume_evaluation_input_sha256(
    checkpoint_journal: Path, *, activation_sha256_value: str, checkpoint: str
) -> str:
    """Der Hash, auf dem eine Wiederaufnahme werten MUSS.

    Steht ``EVALUATE`` im Journal, aber ohne Hash, ist der Zustand nicht
    wiederherstellbar — dann lieber ein Abbruch als eine frische Ladung Daten.
    """
    from app.research.prereg_window_state import load_checkpoints

    for record in load_checkpoints(checkpoint_journal, activation_sha256=activation_sha256_value):
        if record.checkpoint == checkpoint and record.action == ACTION_EVALUATE:
            if not record.evaluation_input_sha256:
                raise SealedEvaluationError(
                    f"{checkpoint}: EVALUATE steht im Journal, aber ohne "
                    "evaluation_input_sha256 — der Datenschnitt ist nicht "
                    "wiederherstellbar."
                )
            return record.evaluation_input_sha256
    raise SealedEvaluationError(f"{checkpoint}: kein EVALUATE im Journal")


__all__ = [
    "ACTION_RESUME_EVALUATION",
    "FrozenInputError",
    "SealedEvaluationError",
    "STATE_CHECKPOINT_DECIDED",
    "STATE_CLOSED",
    "STATE_INPUT_FROZEN",
    "STATE_RUNNING",
    "STATE_VERDICT_RECORDED",
    "VerdictRecord",
    "decide_and_freeze",
    "freeze_rows",
    "load_verdicts",
    "panels_from_frozen",
    "record_verdict",
    "resume_evaluation_input_sha256",
    "result_sha256",
    "run_sealed_evaluation",
]
