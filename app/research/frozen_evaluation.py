"""Eingefrorene Auswertungs-Eingabe: Bytes mit Hash statt Verweis auf damals.

Ein praeregistriertes Verdikt ist nur so viel wert wie die Antwort auf die
Frage, WORAUF es gefallen ist. Solange die Auswertung ihre Daten beim Start
frisch zieht und ihre Parameter vom Aufrufer entgegennimmt, gibt es diese
Antwort nicht: zwei Laeufe koennen dasselbe behaupten und Verschiedenes gemessen
haben, und nach einem Absturz misst der Wiederanlauf etwas Drittes.

Dieses Modul stellt die Bindung her.

* ``FrozenEvaluationDataset`` — genau das versiegelte Universum, in kanonischer
  Reihenfolge, mit ausgerichteten Labels, als kanonische Bytes mit
  ``dataset_sha256``.
* ``FrozenEvaluationInput`` — dieses Datenset plus die aufgeloesten
  Vertragswerte und die Hash-Kette der Praeregistrierung, zusammengefasst zu
  ``evaluation_input_sha256``.
* ``publish_frozen_input`` — dauerhaft und unveraenderlich veroeffentlichen:
  gleiche Bytes sind idempotent, andere Bytes fuer dieselbe Identitaet sind ein
  Konflikt.

Der aufgeloeste Vertrag kommt NIE vom Aufrufer. Er wird aus Candidate und
Activation geladen, die Hashes werden nachgerechnet und verglichen; jede
Abweichung ist ein Abbruch. Ein Auswertungslauf, der seine eigenen Kosten,
seinen eigenen Horizont oder sein eigenes Alpha mitbringen darf, ist keine
Konfirmation, sondern eine Suche.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.analysis.features.feature_matrix import FeatureRow
from app.research.prereg_candidate import (
    PreRegActivation,
    PreRegCandidate,
    candidate_sha256,
)
from app.research.prereg_candidate import activation_sha256 as _activation_sha
from app.research.primary_confirmatory import SymbolPanel, evaluate_primary

SPEC_VERSION = "kai/frozen-evaluation/v1"

CHECKPOINT_T1 = "T1"
CHECKPOINT_T2 = "T2"
_CHECKPOINTS = frozenset({CHECKPOINT_T1, CHECKPOINT_T2})


class FrozenEvaluationError(RuntimeError):
    """Die eingefrorene Eingabe ist nicht das, was sie behauptet — fail-closed."""


class PublishConflictError(FrozenEvaluationError):
    """Dieselbe Auswertungs-Identitaet, andere Bytes."""


# ── Zeit ────────────────────────────────────────────────────────────────────


def _aware(value: str, where: str) -> datetime:
    """Operator-Eingaben muessen eine Zone tragen.

    Bei T0 und beim Checkpoint-Cutoff ist eine stillschweigende UTC-Annahme
    gefaehrlich: sie verschiebt die Epochengrenze um Stunden, und ein Signal
    knapp daneben wechselt die Seite, ohne dass es jemand sieht.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{where} must carry a timezone, got {value!r}")
    return parsed.astimezone(UTC)


def _normalised(value: str, where: str) -> datetime:
    """Maschinen-Zeitstempel: naiv gilt als UTC — wie ueberall sonst im Repo.

    Der Unterschied zu ``_aware`` ist Absicht. T0 kommt vom Operator und darf
    nicht geraten werden; Zeilen-Zeitstempel stammen aus der eigenen
    Feature-Pipeline und folgen deren Konvention. Kanonisiert wird in beiden
    Faellen — in den Bytes steht am Ende UTC.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where} is not an ISO-8601 timestamp: {value!r}") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _finite(value: float | None, where: str) -> float | None:
    """``None`` bleibt ``None``; ``NaN``/``Inf`` sind ein Abbruch.

    ``None`` heisst DATA_UNAVAILABLE und ist eine Aussage. ``NaN`` ist keine —
    es ist ein Rechenfehler, und es still zu ``null`` zu kanonisieren wuerde
    genau diesen Fehler als Datenlage ausgeben.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} must be a real number or None, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{where} must be finite, got {value!r}")
    return number


# ── Datenset ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FrozenSymbolPanel:
    """Ein Symbol: kausale Feature-Zeilen, ausgerichtete Labels, Label-Ende."""

    symbol: str
    rows: tuple[FeatureRow, ...]
    labels: tuple[float | None, ...]
    label_exit_utc: tuple[str | None, ...]


@dataclass(frozen=True)
class FrozenEvaluationDataset:
    """Das Datenset, ueber das ein Verdikt faellt — inklusive seiner Bytes."""

    universe_sha256: str
    canonical_symbols: tuple[str, ...]
    panels: tuple[FrozenSymbolPanel, ...]
    t0_utc: str
    checkpoint: str
    checkpoint_cutoff_utc: str
    canonical_bytes: bytes
    dataset_sha256: str


def dataset_sha256(canonical_bytes: bytes) -> str:
    """Hash ueber exakt die veroeffentlichten Bytes — nicht ueber ein Objekt."""
    return hashlib.sha256(canonical_bytes).hexdigest()


def canonical_bytes_of(payload: dict[str, Any]) -> bytes:
    """Stabile Serialisierung mit Versionspraefix.

    ``allow_nan=False`` ist der zweite Riegel gegen ``NaN``: selbst wenn ein
    Wert an der Pruefung vorbeikaeme, entstuende hier kein Dokument, das
    ``NaN`` als JSON-Literal traegt und nur von Python wieder gelesen werden
    kann.
    """
    body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return f"{SPEC_VERSION}\n{body}\n".encode()


def _row_payload(row: FeatureRow, where: str) -> dict[str, Any]:
    payload = asdict(row)
    payload["timestamp_utc"] = _normalised(row.timestamp_utc, f"{where}.timestamp_utc").isoformat()
    for key, value in list(payload.items()):
        if key == "timestamp_utc":
            continue
        payload[key] = _finite(value, f"{where}.{key}")
    return payload


def _panel_payload(
    panel: FrozenSymbolPanel,
    *,
    t0: datetime,
    cutoff: datetime,
    checkpoint: str,
) -> dict[str, Any]:
    where = f"panel[{panel.symbol}]"
    if not (len(panel.rows) == len(panel.labels) == len(panel.label_exit_utc)):
        raise ValueError(
            f"{where}: rows, labels and label_exit_utc must be aligned "
            f"({len(panel.rows)}/{len(panel.labels)}/{len(panel.label_exit_utc)})"
        )

    rows: list[dict[str, Any]] = []
    labels: list[float | None] = []
    exits: list[str | None] = []
    for index, (row, label, exit_utc) in enumerate(
        zip(panel.rows, panel.labels, panel.label_exit_utc, strict=True)
    ):
        cell = f"{where}[{index}]"
        signal_at = _normalised(row.timestamp_utc, f"{cell}.timestamp_utc")
        if signal_at < t0:
            raise ValueError(
                f"{cell}: signal at {signal_at.isoformat()} lies before T0 "
                f"({t0.isoformat()}) and would be in-sample"
            )
        rows.append(_row_payload(row, cell))

        value = _finite(label, f"{cell}.label")
        labels.append(value)
        if value is None:
            # Kein Label heisst: es gibt nichts zu reifen. Ein Ende darf hier
            # fehlen — behauptet wird damit nichts.
            exits.append(None if exit_utc is None else _normalised(exit_utc, cell).isoformat())
            continue
        if exit_utc is None:
            raise ValueError(f"{cell}: a present label needs label_exit_utc to prove maturity")
        exit_at = _normalised(exit_utc, f"{cell}.label_exit_utc")
        if exit_at > cutoff:
            raise ValueError(
                f"{cell}: label is not mature at {checkpoint} — it exits "
                f"{exit_at.isoformat()} after the cutoff {cutoff.isoformat()}"
            )
        exits.append(exit_at.isoformat())

    return {
        "symbol": panel.symbol,
        "rows": rows,
        "labels": labels,
        "label_exit_utc": exits,
    }


def build_frozen_dataset(
    *,
    canonical_symbols: tuple[str, ...],
    panels: tuple[FrozenSymbolPanel, ...],
    universe_sha256: str,
    t0_utc: str,
    checkpoint: str,
    checkpoint_cutoff_utc: str,
) -> FrozenEvaluationDataset:
    """Pruefe, kanonisiere, hashe — oder brich ab.

    Es gibt hier bewusst keinen Reparaturmodus: kein Nachsortieren, kein
    Auffuellen fehlender Symbole, kein Abschneiden unreifer Labels. Jede dieser
    Freundlichkeiten wuerde aus einem Fehler ein anderes Datenset machen, das
    trotzdem plausibel aussieht.
    """
    if checkpoint not in _CHECKPOINTS:
        raise ValueError(f"checkpoint must be one of {sorted(_CHECKPOINTS)}, got {checkpoint!r}")
    if len(set(canonical_symbols)) != len(canonical_symbols):
        raise ValueError("canonical_symbols contains duplicates")

    panel_symbols = tuple(p.symbol for p in panels)
    if set(panel_symbols) != set(canonical_symbols):
        missing = sorted(set(canonical_symbols) - set(panel_symbols))
        foreign = sorted(set(panel_symbols) - set(canonical_symbols))
        raise ValueError(
            "panels do not cover the sealed universe exactly "
            f"(missing={missing}, foreign={foreign})"
        )
    if panel_symbols != tuple(canonical_symbols):
        raise ValueError(
            "panels are not in canonical universe order — order is part of the identity"
        )

    t0 = _aware(t0_utc, "t0_utc")
    cutoff = _aware(checkpoint_cutoff_utc, "checkpoint_cutoff_utc")
    if cutoff < t0:
        raise ValueError("checkpoint_cutoff_utc lies before t0_utc")

    payload = {
        "spec": SPEC_VERSION,
        "universe_sha256": universe_sha256,
        "t0_utc": t0.isoformat(),
        "checkpoint": checkpoint,
        "checkpoint_cutoff_utc": cutoff.isoformat(),
        "canonical_symbols": list(canonical_symbols),
        "panels": [
            _panel_payload(panel, t0=t0, cutoff=cutoff, checkpoint=checkpoint) for panel in panels
        ],
    }
    canonical = canonical_bytes_of(payload)
    return FrozenEvaluationDataset(
        universe_sha256=universe_sha256,
        canonical_symbols=tuple(canonical_symbols),
        panels=tuple(panels),
        t0_utc=t0.isoformat(),
        checkpoint=checkpoint,
        checkpoint_cutoff_utc=cutoff.isoformat(),
        canonical_bytes=canonical,
        dataset_sha256=dataset_sha256(canonical),
    )


# ── Aufgeloester Vertrag ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolvedContract:
    """Die Forschungsparameter, wie sie versiegelt wurden — nicht wie uebergeben.

    Jedes Feld hier hat heute ein Gegenstueck als freies Schluesselwortargument
    in ``evaluate_primary``. Genau deshalb existiert diese Klasse: der Weg vom
    Candidate zum Test soll keine Stelle mehr haben, an der jemand einen
    anderen Wert einsetzen kann, ohne dass der Hash es zeigt.
    """

    hypothesis: str
    timeframe: str
    horizon: int
    n_valid_min: int
    cluster_min: int
    alpha: float
    round_trip_cost_bps: float
    economic_floor_bps: float
    primary_estimand: str
    inference: str
    execution_convention: str


def resolve_contract(
    candidate: PreRegCandidate,
    activation: PreRegActivation,
) -> ResolvedContract:
    """Vertrag aus der Praeregistrierung laden — und die Kette nachrechnen.

    Der Candidate wird neu gehasht und gegen den Verweis in der Activation
    gehalten. Weicht auch nur eine Zahl ab, ist das kein Detail: dann wurde ein
    anderer Forschungsplan aktiviert als der, der gleich ausgewertet werden
    soll, und das Verdikt wuerde einer Praeregistrierung zugeschrieben, die es
    nie gedeckt hat.
    """
    recomputed = candidate_sha256(candidate)
    if recomputed != activation.candidate_sha256:
        raise ValueError(
            "candidate_sha256 mismatch: the activation refers to "
            f"{activation.candidate_sha256!r}, the given candidate hashes to {recomputed!r}"
        )
    if candidate.universe_sha256 != activation.universe_sha256:
        raise ValueError(
            "universe_sha256 mismatch between candidate and activation: "
            f"{candidate.universe_sha256!r} != {activation.universe_sha256!r}"
        )
    return ResolvedContract(
        hypothesis=candidate.hypothesis,
        timeframe=candidate.timeframe,
        horizon=candidate.horizon,
        n_valid_min=candidate.n_valid_min,
        cluster_min=candidate.cluster_min,
        alpha=candidate.alpha,
        round_trip_cost_bps=candidate.round_trip_cost_bps,
        economic_floor_bps=candidate.economic_floor_bps,
        primary_estimand=candidate.primary_estimand,
        inference=candidate.inference,
        execution_convention=candidate.execution_convention,
    )


# ── Eingefrorene Eingabe ────────────────────────────────────────────────────


@dataclass(frozen=True)
class FrozenEvaluationInput:
    """Alles, was ein Verdikt spaeter nachpruefbar macht — als ein Hash."""

    dataset_sha256: str
    activation_sha256: str
    candidate_sha256: str
    universe_sha256: str
    research_code_sha: str
    evaluator_sha256: str
    checkpoint: str
    checkpoint_cutoff_utc: str
    t0_utc: str
    t1_utc: str
    t2_utc: str
    n_symbols: int
    contract: ResolvedContract
    maturity_counts: dict[str, int]
    canonical_bytes: bytes
    evaluation_input_sha256: str


def _counts_payload(counts: Any) -> dict[str, int]:
    """Nur blinde Zahlen. Ein Mittelwert hat hier nichts verloren.

    Die Reifezahlen gehoeren in den Hash, weil dieselbe Auswertung auf anderer
    Reifegrundlage eine andere Auswertung ist. Performance gehoert NICHT hinein
    — sie entsteht erst nach dem Einfrieren, und wer sie vorher kennt, hat den
    Checkpoint bereits verletzt.
    """
    forbidden = {"mean_bps", "mean_net_bps", "p_value", "t_stat"}
    payload = {
        "n_valid": int(counts.n_valid),
        "n_clusters": int(counts.n_clusters),
        "raw_fires": int(getattr(counts, "raw_fires", 0)),
        "label_capable_fires": int(getattr(counts, "label_capable_fires", 0)),
        "data_unavailable_count": int(getattr(counts, "data_unavailable_count", 0)),
        "symbols_with_valid_signals": int(getattr(counts, "symbols_with_valid_signals", 0)),
    }
    leaked = sorted(forbidden.intersection(vars(counts))) if hasattr(counts, "__dict__") else []
    if leaked:
        raise ValueError(f"maturity counts must stay blind, found performance fields: {leaked}")
    return payload


def freeze_evaluation_input(
    *,
    candidate: PreRegCandidate,
    activation: PreRegActivation,
    dataset: FrozenEvaluationDataset,
    counts: Any,
) -> FrozenEvaluationInput:
    """Binde Datenset, Vertrag und Hash-Kette zu EINER Identitaet.

    Der Checkpoint-Stichtag wird nicht entgegengenommen, sondern gegen T1 bzw.
    T2 der Activation geprueft. Ein frei gewaehlter Stichtag waere ein frei
    gewaehltes Ergebnis: man verschiebt ihn, bis die Zahlen gefallen.
    """
    contract = resolve_contract(candidate, activation)

    if dataset.universe_sha256 != activation.universe_sha256:
        raise ValueError(
            "dataset universe_sha256 does not match the activation: "
            f"{dataset.universe_sha256!r} != {activation.universe_sha256!r}"
        )
    if _aware(dataset.t0_utc, "dataset.t0_utc") != _aware(activation.t0_utc, "activation.t0_utc"):
        raise ValueError(
            f"dataset t0_utc {dataset.t0_utc!r} does not match activation {activation.t0_utc!r}"
        )
    if len(dataset.canonical_symbols) != candidate.n_symbols:
        raise ValueError(
            f"dataset carries {len(dataset.canonical_symbols)} symbols, "
            f"candidate n_symbols is {candidate.n_symbols}"
        )

    expected_cutoff = (
        activation.t1_utc if dataset.checkpoint == CHECKPOINT_T1 else activation.t2_utc
    )
    given_cutoff = _aware(dataset.checkpoint_cutoff_utc, "cutoff")
    if given_cutoff != _aware(expected_cutoff, "expected cutoff"):
        raise ValueError(
            f"checkpoint cutoff {dataset.checkpoint_cutoff_utc!r} is not the sealed "
            f"{dataset.checkpoint} boundary {expected_cutoff!r}"
        )

    counts_payload = _counts_payload(counts)
    activation_hash = _activation_sha(activation)
    payload = {
        "spec": SPEC_VERSION,
        "dataset_sha256": dataset.dataset_sha256,
        "activation_sha256": activation_hash,
        "candidate_sha256": activation.candidate_sha256,
        "universe_sha256": activation.universe_sha256,
        "research_code_sha": activation.research_code_sha,
        "evaluator_sha256": activation.evaluator_sha256,
        "checkpoint": dataset.checkpoint,
        "checkpoint_cutoff_utc": dataset.checkpoint_cutoff_utc,
        "t0_utc": activation.t0_utc,
        "t1_utc": activation.t1_utc,
        "t2_utc": activation.t2_utc,
        "n_symbols": candidate.n_symbols,
        "contract": asdict(contract),
        "maturity_counts": counts_payload,
    }
    canonical = canonical_bytes_of(payload)
    return FrozenEvaluationInput(
        dataset_sha256=dataset.dataset_sha256,
        activation_sha256=activation_hash,
        candidate_sha256=activation.candidate_sha256,
        universe_sha256=activation.universe_sha256,
        research_code_sha=activation.research_code_sha,
        evaluator_sha256=activation.evaluator_sha256,
        checkpoint=dataset.checkpoint,
        checkpoint_cutoff_utc=dataset.checkpoint_cutoff_utc,
        t0_utc=activation.t0_utc,
        t1_utc=activation.t1_utc,
        t2_utc=activation.t2_utc,
        n_symbols=candidate.n_symbols,
        contract=contract,
        maturity_counts=counts_payload,
        canonical_bytes=canonical,
        evaluation_input_sha256=dataset_sha256(canonical),
    )


# ── Dauerhafte, unveraenderliche Veroeffentlichung ──────────────────────────


@dataclass(frozen=True)
class PublishResult:
    """Wo die Eingabe liegt — und ob dieser Lauf sie geschrieben hat."""

    path: Path
    evaluation_input_sha256: str
    created: bool


def evaluation_identity(activation_sha256: str, checkpoint: str) -> str:
    """Eine Auswertung ist durch Aktivierung UND Checkpoint bestimmt.

    Bewusst nicht durch ``evaluation_input_sha256``: waere der Hash Teil des
    Dateinamens, koennte eine zweite, abweichende Eingabe einfach danebenliegen
    — und niemand haette einen Konflikt gesehen.
    """
    return f"{activation_sha256}_{checkpoint}"


def _fsync_directory(directory: Path) -> None:
    """Der Rename ist erst dauerhaft, wenn das Verzeichnis selbst gesynct ist."""
    # Windows erlaubt schon das OEFFNEN eines Verzeichnisses nicht; dort wirft
    # bereits ``os.open`` und nicht erst ``os.fsync``. Der Rename bleibt auch
    # dann atomar — nur die Haltbarkeit des Verzeichniseintrags liegt beim
    # Dateisystem. Auf der Pi (ext4) laeuft der Sync wie vorgesehen.
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def publish_frozen_input(directory: Path, frozen: FrozenEvaluationInput) -> PublishResult:
    """Atomar schreiben, nie ueberschreiben.

    Gleiche Bytes fuer dieselbe Identitaet sind ein Wiederanlauf und damit in
    Ordnung. Andere Bytes sind der Moment, in dem eine Auswertung heimlich
    ausgetauscht wuerde — daher ein Abbruch statt eines neuen Standes.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{evaluation_identity(frozen.activation_sha256, frozen.checkpoint)}.json"

    if target.exists():
        existing = target.read_bytes()
        if existing == frozen.canonical_bytes:
            return PublishResult(target, frozen.evaluation_input_sha256, created=False)
        raise PublishConflictError(
            f"conflict: {target.name} already holds a different evaluation input "
            f"({dataset_sha256(existing)} != {frozen.evaluation_input_sha256})"
        )

    handle = tempfile.NamedTemporaryFile(
        mode="wb", dir=str(directory), prefix=".tmp-", suffix=".json", delete=False
    )
    try:
        with handle as stream:
            stream.write(frozen.canonical_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    _fsync_directory(directory)
    return PublishResult(target, frozen.evaluation_input_sha256, created=True)


def load_frozen_input(
    directory: Path,
    *,
    activation_sha256: str,
    checkpoint: str,
) -> bytes | None:
    """Exakt die veroeffentlichten Bytes zurueckgeben — oder ``None``.

    Kein Neuaufbau, kein erneuter Abruf: nach einem Absturz muss dieselbe
    Eingabe wieder in die Auswertung gehen, nicht eine frisch erzeugte, die ihr
    aehnelt.
    """
    target = Path(directory) / f"{evaluation_identity(activation_sha256, checkpoint)}.json"
    if not target.exists():
        return None
    return target.read_bytes()


# ── Gebundener Einstieg in den Konfirmationstest ────────────────────────────

_TIMEFRAME_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def timeframe_to_ms(timeframe: str) -> int:
    """Takt in Millisekunden — bekannte Werte oder Abbruch.

    Ein geratener Takt verschiebt die Cluster-Grenzen und damit die
    Freiheitsgrade des Tests. Das faellt in keinem Ergebnis auf.
    """
    try:
        return _TIMEFRAME_MS[timeframe.strip()]
    except KeyError:
        raise ValueError(
            f"unknown timeframe {timeframe!r}; known: {sorted(_TIMEFRAME_MS)}"
        ) from None


def evaluate_frozen(
    *,
    frozen_input: FrozenEvaluationInput,
    dataset: FrozenEvaluationDataset,
    decide: Any,
) -> Any:
    """Den Primaertest ausschliesslich aus der eingefrorenen Eingabe speisen.

    Diese Funktion hat bewusst kein ``**kwargs`` und keinen einzigen
    Vertragsparameter in der Signatur. Alles — Kosten, Horizont, Alpha, die
    oekonomische Schranke, die Reifeschranken, der Universe-Hash und die Panels
    — stammt aus ``frozen_input`` und ``dataset``, und beide tragen einen Hash.
    Ein Aufrufer, der etwas anderes messen will, muss dafuer die
    Praeregistrierung aendern; dann aendert sich der Hash und es faellt auf.
    """
    if dataset.dataset_sha256 != frozen_input.dataset_sha256:
        raise ValueError(
            "dataset_sha256 does not match the frozen input: "
            f"{dataset.dataset_sha256!r} != {frozen_input.dataset_sha256!r}"
        )

    contract = frozen_input.contract
    panels = [
        SymbolPanel(symbol=panel.symbol, rows=list(panel.rows), labels=list(panel.labels))
        for panel in dataset.panels
    ]
    return evaluate_primary(
        panels,
        decide,
        hypothesis=contract.hypothesis,
        universe_sha256=frozen_input.universe_sha256,
        round_trip_cost_bps=contract.round_trip_cost_bps,
        timeframe_ms=timeframe_to_ms(contract.timeframe),
        horizon=contract.horizon,
        n_min=contract.n_valid_min,
        cluster_min=contract.cluster_min,
        alpha=contract.alpha,
        economic_floor_bps=contract.economic_floor_bps,
    )
