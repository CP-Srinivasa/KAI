"""Die vollstaendige, eingefrorene Evaluationsidentitaet.

``dataset_sha256`` beantwortet *welche Daten*. Das genuegt nicht: dieselben Daten
unter einer anderen Kostenannahme sind eine andere Auswertung. Deshalb bindet
``evaluation_input_sha256`` die ganze Kette::

    WELCHE DATEN?              dataset_sha256
    UNTER WELCHEM VERTRAG?     candidate_sha256
    WELCHE POPULATION?         universe_sha256
    WANN ERLAUBT?              activation_sha256 + checkpoint + cutoff
    WELCHER CODE?              research_code_sha + evaluator_sha256

**Die Parameter werden gelesen, nicht entgegengenommen.** Es gibt keinen Weg,
``horizon``, ``round_trip_cost_bps``, ``alpha`` oder ``economic_floor_bps``
hereinzureichen — sie werden aus dem gehashten Candidate aufgeloest. Damit sind
sie gleichzeitig im Klartext sichtbar UND kryptographisch gebunden. Die
Redundanz ist Absicht: weicht der aufgeloeste Vertrag vom Candidate ab, bricht
der Builder ab, bevor irgendetwas gehasht wird.

**Universe-Hash allein genuegt nicht.** Er beweist, WELCHES Universum versiegelt
wurde — nicht, dass die uebergebenen Daten genau dieses abdecken. Ohne die
zusaetzliche Mengenpruefung koennte ein korrekter Hash neben 33 Symbolen stehen.
Genau diese Luecke hatte ``run_confirmatory``: es nahm beliebige Panels entgegen
und schrieb den mitgelieferten ``universe_sha256`` unbesehen ins Ergebnis.

Ein Symbol mit null gueltigen Signalen bleibt Mitglied. ``DATA_UNAVAILABLE`` ist
nicht ``asset removed``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.research.frozen_dataset import (
    FrozenEvaluationDataset,
    canonical_bytes,
    dataset_sha256,
    dataset_to_dict,
)
from app.research.prereg_candidate import (
    PreRegActivation,
    PreRegCandidate,
    activation_sha256,
    candidate_sha256,
)
from app.research.prereg_window import MaturityCounts

INPUT_SCHEMA_VERSION = "kai/prereg-evaluation-input/v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class FrozenInputError(ValueError):
    """Die Evaluationsidentitaet ist nicht schluessig — Abbruch vor dem Hashen."""


@dataclass(frozen=True)
class FrozenEvaluationInput:
    """Alles, was eine Auswertung eindeutig macht. Ohne ein einziges Ergebnis."""

    schema_version: str
    activation_sha256: str
    candidate_sha256: str
    universe_sha256: str
    research_code_sha: str
    evaluator_sha256: str
    checkpoint: str
    t0_utc: str
    cutoff_utc: str
    dataset_sha256: str
    n_symbols: int
    symbols: tuple[str, ...]
    resolved_contract: dict[str, Any]
    maturity_counts: dict[str, int]


def resolve_contract(candidate: PreRegCandidate) -> dict[str, Any]:
    """Der Forschungsvertrag, ausschliesslich aus dem versiegelten Candidate."""
    return {
        "hypothesis": candidate.hypothesis,
        "timeframe": candidate.timeframe,
        "horizon": candidate.horizon,
        "n_valid_min": candidate.n_valid_min,
        "cluster_min": candidate.cluster_min,
        "alpha": candidate.alpha,
        "round_trip_cost_bps": candidate.round_trip_cost_bps,
        "economic_floor_bps": candidate.economic_floor_bps,
        "primary_estimand": candidate.primary_estimand,
        "inference": candidate.inference,
        "execution_convention": candidate.execution_convention,
        # NON_GATING, aber ebenfalls versiegelt: der Anspruch lautet
        # "ausschliesslich aus dem Frozen Input", und der soll buchstaeblich
        # stimmen. Sonst kaeme die Sensitivitaets-Achse zur Verdikt-Zeit aus
        # einem Candidate-Objekt, das jemand daneben gereicht hat.
        "sensitivity_cost_bps": list(candidate.sensitivity_cost_bps),
    }


def _verify_resolved_contract(contract: dict[str, Any], candidate: PreRegCandidate) -> None:
    """Selbstkontrolle gegen eine zweite Quelle, die es heute nicht gibt.

    Heute baut ``resolve_contract`` den Vertrag allein aus dem Candidate — es
    KANN nicht abweichen. Der Test bleibt trotzdem: er faengt den Tag, an dem
    jemand einen Wert "der Bequemlichkeit halber" von aussen durchreicht.
    """
    expected = resolve_contract(candidate)
    drift = [key for key, value in expected.items() if contract.get(key) != value]
    if drift or set(contract) != set(expected):
        raise FrozenInputError(
            f"resolved_contract weicht vom versiegelten Candidate ab: {sorted(drift)} "
            "— die Parameter duerfen NUR aus dem Candidate stammen."
        )


def _verify_symbols(dataset: FrozenEvaluationDataset, sealed_symbols: tuple[str, ...]) -> None:
    """Menge, Anzahl und Reihenfolge — nicht nur der Hash daneben."""
    if tuple(dataset.symbols) != sealed_symbols:
        missing = sorted(set(sealed_symbols) - set(dataset.symbols))
        extra = sorted(set(dataset.symbols) - set(sealed_symbols))
        raise FrozenInputError(
            "die Symbole des Datenschnitts sind nicht das versiegelte Universum "
            f"(fehlt: {missing}, zusaetzlich: {extra}, "
            f"n={len(dataset.symbols)} statt {len(sealed_symbols)}). "
            "Ein korrekter universe_sha256 daneben beweist das NICHT."
        )
    panel_symbols = tuple(panel.symbol for panel in dataset.panels)
    if panel_symbols != sealed_symbols:
        raise FrozenInputError(
            "die Panel-Reihenfolge entspricht nicht der kanonischen Universumsordnung"
        )


def build_frozen_input(
    *,
    dataset: FrozenEvaluationDataset,
    candidate: PreRegCandidate,
    activation: PreRegActivation,
    sealed_universe_sha256: str,
    sealed_symbols: tuple[str, ...],
    maturity_counts: MaturityCounts,
) -> FrozenEvaluationInput:
    """Baue die Evaluationsidentitaet — nach Pruefung jeder Bindung.

    Reihenfolge ist hier die halbe Sicherheit: erst wird jede Kette verifiziert,
    dann erst entsteht ein Hash. Ein Hash ueber ungeprueften Inhalt waere ein
    Siegel auf einem unbekannten Dokument.
    """
    computed_candidate = candidate_sha256(candidate)
    if activation.candidate_sha256 != computed_candidate:
        raise FrozenInputError(
            f"Activation verweist auf Candidate {activation.candidate_sha256[:12]}…, "
            f"vorliegend ist {computed_candidate[:12]}…"
        )
    if candidate.universe_sha256 != sealed_universe_sha256:
        raise FrozenInputError(
            f"Candidate verweist auf Universum {candidate.universe_sha256[:12]}…, "
            f"geladen wurde {sealed_universe_sha256[:12]}…"
        )
    if activation.universe_sha256 != sealed_universe_sha256:
        raise FrozenInputError("Activation und Universum passen nicht zusammen")
    if candidate.n_symbols != len(sealed_symbols):
        raise FrozenInputError(
            f"Candidate erwartet {candidate.n_symbols} Symbole, "
            f"das Universum hat {len(sealed_symbols)}"
        )

    if dataset.checkpoint == "T1":
        expected_cutoff = activation.t1_utc
    elif dataset.checkpoint == "T2":
        expected_cutoff = activation.t2_utc
    else:  # pragma: no cover - build_frozen_dataset laesst nichts anderes zu
        raise FrozenInputError(f"unbekannter Checkpoint {dataset.checkpoint!r}")
    if not _same_instant(dataset.cutoff_utc, expected_cutoff):
        raise FrozenInputError(
            f"cutoff {dataset.cutoff_utc} ist nicht der versiegelte "
            f"{dataset.checkpoint}-Zeitpunkt {expected_cutoff}"
        )
    if not _same_instant(dataset.t0_utc, activation.t0_utc):
        raise FrozenInputError(
            f"t0 {dataset.t0_utc} ist nicht das versiegelte T0 {activation.t0_utc}"
        )

    _verify_symbols(dataset, sealed_symbols)

    contract = resolve_contract(candidate)
    _verify_resolved_contract(contract, candidate)

    return FrozenEvaluationInput(
        schema_version=INPUT_SCHEMA_VERSION,
        activation_sha256=activation_sha256(activation),
        candidate_sha256=computed_candidate,
        universe_sha256=sealed_universe_sha256,
        research_code_sha=activation.research_code_sha,
        evaluator_sha256=activation.evaluator_sha256,
        checkpoint=dataset.checkpoint,
        t0_utc=dataset.t0_utc,
        cutoff_utc=dataset.cutoff_utc,
        dataset_sha256=dataset_sha256(dataset),
        n_symbols=len(sealed_symbols),
        symbols=sealed_symbols,
        resolved_contract=contract,
        maturity_counts={
            "n_valid": maturity_counts.n_valid,
            "n_clusters": maturity_counts.n_clusters,
            "raw_fires": maturity_counts.raw_fires,
            "label_capable_fires": maturity_counts.label_capable_fires,
            "data_unavailable_count": maturity_counts.data_unavailable_count,
            "symbols_with_valid_signals": maturity_counts.symbols_with_valid_signals,
        },
    )


def _same_instant(a: str, b: str) -> bool:
    from datetime import datetime

    return datetime.fromisoformat(a) == datetime.fromisoformat(b)


def input_to_dict(frozen: FrozenEvaluationInput) -> dict[str, Any]:
    return {
        "schema_version": frozen.schema_version,
        "activation_sha256": frozen.activation_sha256,
        "candidate_sha256": frozen.candidate_sha256,
        "universe_sha256": frozen.universe_sha256,
        "research_code_sha": frozen.research_code_sha,
        "evaluator_sha256": frozen.evaluator_sha256,
        "checkpoint": frozen.checkpoint,
        "t0_utc": frozen.t0_utc,
        "cutoff_utc": frozen.cutoff_utc,
        "dataset_sha256": frozen.dataset_sha256,
        "n_symbols": frozen.n_symbols,
        "symbols": list(frozen.symbols),
        "resolved_contract": dict(sorted(frozen.resolved_contract.items())),
        "maturity_counts": dict(sorted(frozen.maturity_counts.items())),
    }


def evaluation_input_sha256(frozen: FrozenEvaluationInput) -> str:
    return hashlib.sha256(canonical_bytes(input_to_dict(frozen))).hexdigest()


# --- Artefakt: unveraenderlich, atomar geschrieben ---------------------------


def write_frozen_artifact(
    directory: Path,
    frozen: FrozenEvaluationInput,
    dataset: FrozenEvaluationDataset,
) -> Path:
    """Schreibe Eingabe + Daten als EIN unveraenderliches Artefakt.

    Atomar: erst in eine temporaere Datei, ``flush``, ``fsync``, dann ``rename``,
    dann ``fsync`` des Verzeichnisses. Ein halb geschriebenes Artefakt darf es
    nicht geben — ein Absturz mittendrin muss aussehen, als waere gar nichts
    passiert.

    Der Dateiname traegt den Hash. Ein zweiter Aufruf mit demselben Inhalt
    erzeugt denselben Namen und ist damit von sich aus idempotent.
    """
    digest = evaluation_input_sha256(frozen)
    payload = {
        "evaluation_input_sha256": digest,
        "input": input_to_dict(frozen),
        "dataset": dataset_to_dict(dataset),
    }
    dataset_payload = payload["dataset"]
    if not isinstance(dataset_payload, dict) or dataset_payload.get("symbols") != list(
        frozen.symbols
    ):  # pragma: no cover - Invariante
        raise FrozenInputError("Datensatz und Eingabe beschreiben verschiedene Universen")

    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"evaluation_input_{digest}.json"
    if target.exists():
        # Der Dateiname ist KEIN Beweis. Idempotent ist nur, was byte-identisch
        # ist; alles andere ist eine Beschaedigung und darf nicht als Erfolg
        # durchgehen — gerade weil danach EVALUATE journalisiert wird.
        existing = read_frozen_artifact(directory, digest)
        if canonical_bytes(existing) != canonical_bytes(payload):
            raise FrozenInputError(
                f"{target} existiert bereits mit ABWEICHENDEM Inhalt bei gleichem "
                "Hash — das Artefakt ist beschaedigt. Kein EVALUATE darauf."
            )
        return target

    tmp = directory / f".{digest}.tmp"
    with tmp.open("wb") as handle:
        handle.write(canonical_bytes(payload))
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(target)
    _fsync_directory(directory)
    return target


def _fsync_directory(directory: Path) -> None:
    """Nur POSIX; Windows kennt kein Verzeichnis-fsync."""
    if os.name != "posix":  # pragma: no cover - plattformabhaengig
        return
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def read_frozen_artifact(directory: Path, expected_sha256: str) -> dict[str, Any]:
    """Lade das Artefakt und PRUEFE seinen Hash gegen den Inhalt.

    Der Dateiname ist ein Hinweis, kein Beweis: er laesst sich umbenennen. Der
    Hash wird deshalb aus dem Inhalt neu berechnet.
    """
    if not _SHA256_RE.match(expected_sha256):
        raise FrozenInputError(f"{expected_sha256!r} ist kein SHA-256")
    path = directory / f"evaluation_input_{expected_sha256}.json"
    if not path.exists():
        raise FrozenInputError(
            f"das Journal verweist auf {expected_sha256[:12]}…, das Artefakt fehlt: {path}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FrozenInputError(f"{path} ist kein gueltiges JSON") from exc

    stored = payload.get("evaluation_input_sha256")
    body = payload.get("input")
    if not isinstance(body, dict):
        raise FrozenInputError(f"{path}: 'input' fehlt")
    recomputed = hashlib.sha256(canonical_bytes(body)).hexdigest()
    if stored != recomputed or recomputed != expected_sha256:
        raise FrozenInputError(
            f"{path}: Hash passt nicht zum Inhalt "
            f"(erwartet {expected_sha256[:12]}…, berechnet {recomputed[:12]}…)"
        )
    dataset = payload.get("dataset")
    if not isinstance(dataset, dict):
        raise FrozenInputError(f"{path}: 'dataset' fehlt")
    if hashlib.sha256(canonical_bytes(dataset)).hexdigest() != body.get("dataset_sha256"):
        raise FrozenInputError(f"{path}: dataset_sha256 passt nicht zu den enthaltenen Daten")
    result: dict[str, Any] = payload
    return result


def validate_git_sha(value: str, field: str) -> str:
    if not isinstance(value, str) or not _GIT_SHA_RE.match(value):
        raise FrozenInputError(
            f"{field}: {value!r} ist kein vollstaendiger Git-SHA-1 (40 Hex, klein)"
        )
    return value


def validate_sha256(value: str, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise FrozenInputError(f"{field}: {value!r} ist kein SHA-256 (64 Hex, klein)")
    return value


# --- Das kanonische Universum: geladen, nachgerechnet, nicht uebergeben ------

SEALED_UNIVERSE_RELPATH = "docs/research/universe_rsi_reentry_v1.json"


def load_sealed_universe(repo_root: Path, *, expected_sha256: str) -> tuple[str, tuple[str, ...]]:
    """Das Universum aus dem Repo-Artefakt — Hash aus dem INHALT nachgerechnet.

    Der Aufrufer darf weder die Symbolliste noch ihren Hash mitbringen. Sonst
    bliebe der staerkere Angriff offen: irgendeine andere Liste mit 34 Symbolen
    neben dem korrekten offiziellen Hash als getrennt uebergebenem String.

    Args:
        repo_root: Wurzel des Checkouts.
        expected_sha256: der im Candidate versiegelte Universums-Hash.

    Returns:
        (Hash, kanonische Symbolliste in ihrer Reihenfolge).

    Raises:
        FrozenInputError: Artefakt fehlt, ist unlesbar, blockiert oder sein
            nachgerechneter Hash weicht ab.
    """
    from app.research.universe_integrity import universe_sha256

    path = repo_root / SEALED_UNIVERSE_RELPATH
    if not path.is_file():
        raise FrozenInputError(f"das versiegelte Universum fehlt: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FrozenInputError(f"{path} ist kein gueltiges JSON") from exc

    symbols = payload.get("canonical_universe")
    if not isinstance(symbols, list) or not all(isinstance(s, str) for s in symbols):
        raise FrozenInputError(f"{path}: 'canonical_universe' fehlt oder ist keine Liste")
    if payload.get("ok") is not True:
        raise FrozenInputError(f"{path}: ok=false — ein blockiertes Universum wird nicht benutzt")

    recomputed = universe_sha256(symbols)
    if payload.get("universe_sha256") != recomputed:
        raise FrozenInputError(
            f"{path}: universe_sha256 passt nicht zur Liste "
            f"(steht {str(payload.get('universe_sha256'))[:12]}…, berechnet {recomputed[:12]}…)"
        )
    if recomputed != expected_sha256:
        raise FrozenInputError(
            f"das Repo-Universum ist {recomputed[:12]}…, der Candidate verlangt "
            f"{expected_sha256[:12]}… — verschiedene Populationen."
        )
    return recomputed, tuple(symbols)
