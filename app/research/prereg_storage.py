"""Wo der Laufzeit-Zustand einer Praeregistrierung liegt — und warum nicht in Git.

``docs/research/`` traegt die versionierte SPEZIFIKATION: Candidate, Universum,
Methodik, spaeter einen Abschlussbericht. Ein T1-/T2-Ereignis darf dagegen
**keinen Git-Commit brauchen**. Sonst veraenderte ein operatives
Evaluationsereignis den Repository-Stand — und genau daran haengt
``research_code_sha``. Der Laufzeit-Zustand liegt deshalb unter::

    artifacts/research/prereg/
    ├── ACTIVE                      <- die volle activation_sha256, sonst nichts
    └── <activation_sha256>/
        ├── activation.json         <- bei T0 einmal, danach nie wieder
        ├── checkpoints.jsonl       <- bei T0 leer und haltbar angelegt
        ├── verdicts.jsonl          <- desgleichen
        └── frozen/
            ├── T1/evaluation_input_<sha>.json
            └── T2/evaluation_input_<sha>.json

Nach ``activate`` steht die vollstaendige Struktur. Ein fehlendes Verzeichnis
zur Checkpoint-Zeit waere sonst ein Fehlerfall in genau dem Moment, in dem man
ihn am wenigsten gebrauchen kann.

``ACTIVE`` ist ein **operativer Zeiger**, keine wissenschaftliche Identitaet.
Die kommt aus ``activation_sha256`` — der Zeiger sagt nur, welche Aktivierung
dieser Host gerade fuehrt.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app.research.prereg_candidate import (
    PreRegActivation,
    activation_sha256,
    activation_to_dict,
)

PREREG_ROOT = Path("artifacts/research/prereg")
ACTIVE_POINTER = "ACTIVE"
ACTIVATION_FILE = "activation.json"
CHECKPOINT_JOURNAL = "checkpoints.jsonl"
VERDICT_JOURNAL = "verdicts.jsonl"
FROZEN_DIR = "frozen"
CHECKPOINTS = ("T1", "T2")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PreRegStorageError(RuntimeError):
    """Die Ablage ist unvollstaendig oder widersprueclich — fail-closed."""


def activation_dir(root: Path, activation_sha256_value: str) -> Path:
    if not _SHA256_RE.match(activation_sha256_value):
        raise PreRegStorageError(f"{activation_sha256_value!r} ist kein SHA-256")
    return root / activation_sha256_value


def checkpoint_journal_path(root: Path, sha: str) -> Path:
    return activation_dir(root, sha) / CHECKPOINT_JOURNAL


def verdict_journal_path(root: Path, sha: str) -> Path:
    return activation_dir(root, sha) / VERDICT_JOURNAL


def frozen_dir(root: Path, sha: str, checkpoint: str) -> Path:
    if checkpoint not in CHECKPOINTS:
        raise PreRegStorageError(f"{checkpoint!r} ist kein Entscheidungszeitpunkt")
    return activation_dir(root, sha) / FROZEN_DIR / checkpoint


def _fsync_dir(directory: Path) -> None:
    if os.name != "posix":  # pragma: no cover - Windows kennt kein Verzeichnis-fsync
        return
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def _write_durable(path: Path, content: str) -> None:
    """Atomar und haltbar: tmp -> fsync -> rename -> Verzeichnis-fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
    _fsync_dir(path.parent)


def initialise_activation(root: Path, activation: PreRegActivation) -> Path:
    """Lege die vollstaendige Struktur an. Einmal, bei T0.

    ``activation.json`` ist danach unveraenderlich: ein zweiter Aufruf mit
    ABWEICHENDEM Inhalt bricht ab. Ein zweiter Aufruf mit identischem Inhalt ist
    ein No-Op — ein Absturz nach dem Schreiben darf nicht blockieren.

    Die Journale entstehen **leer und haltbar** mit. Erst danach ist die Ablage
    vollstaendig, und ein fehlendes Verzeichnis kann zur Checkpoint-Zeit nicht
    mehr ueberraschen.
    """
    sha = activation_sha256(activation)
    directory = activation_dir(root, sha)
    payload = json.dumps(
        {"activation_sha256": sha, "activation": activation_to_dict(activation)},
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )

    target = directory / ACTIVATION_FILE
    if target.exists():
        if target.read_text(encoding="utf-8") != payload:
            raise PreRegStorageError(
                f"{target} existiert bereits mit ANDEREM Inhalt — eine Aktivierung "
                "wird nicht ueberschrieben."
            )
    else:
        _write_durable(target, payload)

    for name in (CHECKPOINT_JOURNAL, VERDICT_JOURNAL):
        journal = directory / name
        if not journal.exists():
            _write_durable(journal, "")

    for checkpoint in CHECKPOINTS:
        frozen = frozen_dir(root, sha, checkpoint)
        frozen.mkdir(parents=True, exist_ok=True)
        _fsync_dir(frozen)

    _write_durable(root / ACTIVE_POINTER, sha + "\n")
    return directory


def read_active(root: Path) -> str:
    """Die aktive Aktivierung — oder ein Abbruch. Nie ein Ratespiel.

    Fail-closed heisst hier: ein Zeiger auf eine unvollstaendige Ablage ist
    schlimmer als gar keiner, weil er Vollstaendigkeit suggeriert.
    """
    pointer = root / ACTIVE_POINTER
    if not pointer.exists():
        raise PreRegStorageError(f"{pointer} fehlt — keine aktive Praeregistrierung")
    sha = pointer.read_text(encoding="utf-8").strip()
    if not _SHA256_RE.match(sha):
        raise PreRegStorageError(f"{pointer} enthaelt {sha!r}, erwartet 64 Hex")
    validate_layout(root, sha)
    return sha


def validate_layout(root: Path, sha: str) -> None:
    """Vollstaendigkeit der Ablage, mechanisch geprueft."""
    directory = activation_dir(root, sha)
    missing = [
        str(path)
        for path in (
            directory / ACTIVATION_FILE,
            directory / CHECKPOINT_JOURNAL,
            directory / VERDICT_JOURNAL,
        )
        if not path.exists()
    ]
    missing += [
        str(frozen_dir(root, sha, checkpoint))
        for checkpoint in CHECKPOINTS
        if not frozen_dir(root, sha, checkpoint).is_dir()
    ]
    if missing:
        raise PreRegStorageError(
            "die Ablage der aktiven Praeregistrierung ist unvollstaendig: " + ", ".join(missing)
        )


def load_activation(root: Path, sha: str) -> dict[str, object]:
    """Lade ``activation.json`` und PRUEFE seinen Hash gegen den Inhalt."""
    path = activation_dir(root, sha) / ACTIVATION_FILE
    if not path.exists():
        raise PreRegStorageError(f"{path} fehlt")
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = payload.get("activation")
    if not isinstance(body, dict):
        raise PreRegStorageError(f"{path}: 'activation' fehlt")
    from app.research.prereg_candidate import PreRegActivation as _Activation

    try:
        rebuilt = _Activation(**body)
    except TypeError as exc:
        raise PreRegStorageError(f"{path}: Felder passen nicht zum Schema") from exc
    recomputed = activation_sha256(rebuilt)
    if payload.get("activation_sha256") != recomputed or recomputed != sha:
        raise PreRegStorageError(
            f"{path}: activation_sha256 passt nicht zum Inhalt "
            f"(erwartet {sha[:12]}…, berechnet {recomputed[:12]}…)"
        )
    return body


def verify_prereg_tree(root: Path, sha: str) -> dict[str, int]:
    """Die ganze Beweiskette eines wiederhergestellten Baums nachrechnen.

    Ein Backup ist erst dann ein Rueckweg, wenn das Zurueckgeholte auch haelt.
    Geprueft wird deshalb nicht "die Dateien sind da", sondern::

        activation.json      -> activation_sha256 aus dem Inhalt
        checkpoints.jsonl    -> decision_fingerprint je Zeile
        verdicts.jsonl       -> result_sha256 je Zeile
        frozen/<T>/…         -> evaluation_input_sha256 und dataset_sha256

    Ein Artefakt OHNE Journaleintrag ist kein Fehler, sondern eine Waise: es
    entsteht, wenn ein Absturz zwischen Schreiben und Journalisieren liegt. Es
    wird gezaehlt und gemeldet, nicht beanstandet — Autoritaet hat nur, was im
    Journal steht.

    Returns:
        Zaehlungen (Checkpoints, Verdikte, gepruefte Artefakte, Waisen).

    Raises:
        PreRegStorageError: irgendein Glied der Kette haelt nicht.
    """
    from app.research.frozen_input import FrozenInputError, read_frozen_artifact
    from app.research.prereg_evaluation import load_verdicts
    from app.research.prereg_window_state import CheckpointJournalError, load_checkpoints

    validate_layout(root, sha)
    load_activation(root, sha)

    try:
        checkpoints = load_checkpoints(checkpoint_journal_path(root, sha), activation_sha256=sha)
        verdicts = load_verdicts(verdict_journal_path(root, sha), activation_sha256_value=sha)
    except CheckpointJournalError as exc:
        raise PreRegStorageError(f"Journal haelt nicht: {exc}") from exc

    referenced: set[tuple[str, str]] = set()
    for record in checkpoints:
        if not record.evaluation_input_sha256:
            continue
        referenced.add((record.checkpoint, record.evaluation_input_sha256))
        try:
            read_frozen_artifact(
                frozen_dir(root, sha, record.checkpoint), record.evaluation_input_sha256
            )
        except FrozenInputError as exc:
            raise PreRegStorageError(
                f"{record.checkpoint}: das journalisierte Artefakt haelt nicht — {exc}"
            ) from exc

    for verdict in verdicts:
        key = (verdict.checkpoint, verdict.evaluation_input_sha256)
        if key not in referenced:
            raise PreRegStorageError(
                f"{verdict.checkpoint}: das Verdikt verweist auf "
                f"{verdict.evaluation_input_sha256[:12]}…, das kein Journaleintrag nennt."
            )

    orphans = 0
    for checkpoint in CHECKPOINTS:
        for path in sorted(frozen_dir(root, sha, checkpoint).glob("evaluation_input_*.json")):
            digest = path.stem.removeprefix("evaluation_input_")
            if (checkpoint, digest) not in referenced:
                orphans += 1

    return {
        "checkpoints": len(checkpoints),
        "verdicts": len(verdicts),
        "verified_artifacts": len(referenced),
        "orphan_artifacts": orphans,
    }
