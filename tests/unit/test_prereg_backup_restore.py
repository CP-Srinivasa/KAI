r"""Ein Backup ist erst ein Rueckweg, wenn das Zurueckgeholte auch haelt.

Der Laufzeit-Zustand der Praeregistrierung liegt bewusst **nicht** in Git: ein
T1-/T2-Ereignis darf keinen Commit brauchen, denn an dem haengt wiederum
``research_code_sha``. Die Folge ist unbequem und muss ausgesprochen werden —
**das Backup ist der einzige Rueckweg**. Geht es verloren, ist nicht das Ergebnis
weg, sondern der Beweis, unter welchen Daten es entstanden ist.

Deshalb prueft dieser Test nicht "die Dateien sind im Archiv", sondern die ganze
Kette nach der Wiederherstellung::

    entschluesseln -> auspacken -> activation_sha256 -> decision_fingerprint
                   -> result_sha256 -> evaluation_input_sha256 -> dataset_sha256

Der Entschluesselungsbefehl MUSS die Iterationszahl des Skripts spiegeln —
ohne ``-iter 200000`` leitet openssl einen anderen Schluessel ab und meldet
"bad decrypt", was wie eine falsche Passphrase aussieht::

    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000
        -in <archiv>.enc -pass env:KAI_BACKUP_PASSPHRASE
        | tar -xzf - -C <ziel>

**Zur Passphrase:** hier laeuft eine Test-Passphrase gegen ein temporaeres
Verzeichnis. Das beweist den MECHANISMUS und ist bewusst unabhaengig von
``KAI_BACKUP_PASSPHRASE`` — die ist ein Betriebsproblem (sie darf nicht nur auf
dem Pi liegen, sonst ist das Archiv beim SD-Tod unentschluesselbar) und kein
Code-Problem. Ein gruener Test hier sagt nichts darueber, ob das
PRODUKTIONS-Backup entschluesselbar ist.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from app.research.frozen_dataset import FrozenRow, build_frozen_dataset
from app.research.frozen_input import build_frozen_input, write_frozen_artifact
from app.research.prereg_candidate import activate, build_rsi_reentry_volume_candidate
from app.research.prereg_evaluation import VerdictRecord, record_verdict
from app.research.prereg_storage import (
    PreRegStorageError,
    checkpoint_journal_path,
    frozen_dir,
    initialise_activation,
    read_active,
    verdict_journal_path,
    verify_prereg_tree,
)
from app.research.prereg_window import MaturityCounts
from app.research.prereg_window_state import CheckpointRecord, record_checkpoint

REPO = Path(__file__).resolve().parents[2]
_HOUR_MS = 3_600_000
BACKUP_SCRIPT = REPO / "scripts" / "kai_backup_artifacts.sh"
_UNIVERSE = json.loads(
    (REPO / "docs" / "research" / "universe_rsi_reentry_v1.json").read_text(encoding="utf-8")
)
_SYMBOLS = tuple(_UNIVERSE["canonical_universe"])
_UNIVERSE_SHA = _UNIVERSE["universe_sha256"]

_BASH = shutil.which("bash")
_OPENSSL = shutil.which("openssl")
_TEST_PASSPHRASE = "kai-test-passphrase-nicht-fuer-produktion-0123456789"

pytestmark = pytest.mark.skipif(
    _BASH is None or _OPENSSL is None, reason="bash/openssl not available"
)


def _feature_row(hour: int) -> dict[str, float | None]:
    from app.analysis.features.feature_matrix import FeatureRow

    row = FeatureRow(
        timestamp_utc=f"2026-10-01T{hour:02d}:00:00+00:00",
        close=100.0,
        log_return=None,
        rsi_14=31.0,
        adx_14=None,
        plus_di_14=None,
        minus_di_14=None,
        realized_vol_24=None,
        ema_12=None,
        ema_26=None,
        macd=None,
        bollinger_z_20=None,
        rsi_14_prev=28.0,
        volume_z_20=3.0,
    )
    return {k: v for k, v in asdict(row).items() if k != "timestamp_utc"}


def _populated_tree(root: Path) -> tuple[str, str]:
    """Ein Baum wie nach einem echten T1: Journal, Artefakt und Verdikt."""
    candidate = build_rsi_reentry_volume_candidate(_UNIVERSE_SHA, len(_SYMBOLS))
    activation = activate(
        candidate,
        t0_utc="2026-09-01T00:00:00+00:00",
        research_code_sha="c" * 40,
        evaluator_sha256="e" * 64,
        operator_approved=True,
    )
    initialise_activation(root, activation)
    sha = read_active(root)

    rows = {
        _SYMBOLS[0]: [
            FrozenRow(
                signal_timestamp_utc=f"2026-10-01T{hour:02d}:00:00+00:00",
                label_exit_utc=f"2026-10-01T{hour + 4:02d}:00:00+00:00",
                features=_feature_row(hour),
                label_bps=50.0 + hour,
            )
            for hour in (1, 6, 11)
        ]
    }
    dataset = build_frozen_dataset(
        checkpoint="T1",
        t0_utc=activation.t0_utc,
        cutoff_utc=activation.t1_utc,
        sealed_symbols=_SYMBOLS,
        rows_by_symbol=rows,
        timeframe_ms=_HOUR_MS,
        horizon=4,
        # Diese Tests pruefen nicht die Abdeckung — sie haben eigene.
        min_coverage=0.0,
    )
    counts = MaturityCounts(n_valid=3, n_clusters=3, raw_fires=3, label_capable_fires=3)
    frozen = build_frozen_input(
        dataset=dataset,
        candidate=candidate,
        activation=activation,
        sealed_universe_sha256=_UNIVERSE_SHA,
        sealed_symbols=_SYMBOLS,
        maturity_counts=counts,
    )
    from app.research.frozen_input import evaluation_input_sha256

    digest = evaluation_input_sha256(frozen)
    write_frozen_artifact(frozen_dir(root, sha, "T1"), frozen, dataset)

    record_checkpoint(
        checkpoint_journal_path(root, sha),
        CheckpointRecord(
            activation_sha256=sha,
            checkpoint="T1",
            action="EVALUATE",
            mature=True,
            recorded_at_utc=activation.t1_utc,
            counts={"n_valid": 3, "n_clusters": 3},
            evaluation_input_sha256=digest,
        ),
    )
    record_verdict(
        verdict_journal_path(root, sha),
        VerdictRecord(
            schema_version="kai/prereg-verdict/v1",
            activation_sha256=sha,
            checkpoint="T1",
            evaluation_input_sha256=digest,
            dataset_sha256=frozen.dataset_sha256,
            evaluator_sha256="e" * 64,
            verdict="NOT_MET",
            n_valid=3,
            n_clusters=3,
            estimate_mean_net_bps=32.0,
            standard_error=1.5,
            t_statistic=21.3,
            df=2,
            p_value=0.03,
            alpha=0.05,
            economic_floor_bps=5.0,
            recorded_at_utc=activation.t1_utc,
        ),
    )
    return sha, digest


# ── Die Kette selbst ────────────────────────────────────────────────────────


def test_a_healthy_tree_verifies(tmp_path: Path) -> None:
    """Gegenprobe zuerst — sonst waere die Pruefung nur ein Verhinderer."""
    root = tmp_path / "prereg"
    sha, _ = _populated_tree(root)

    report = verify_prereg_tree(root, sha)

    assert report == {
        "checkpoints": 1,
        "verdicts": 1,
        "verified_artifacts": 1,
        "orphan_artifacts": 0,
    }


def test_a_lost_frozen_artifact_breaks_the_chain(tmp_path: Path) -> None:
    """Das Journal sagt EVALUATE — dann MUSS das Artefakt da sein."""
    root = tmp_path / "prereg"
    sha, digest = _populated_tree(root)
    (frozen_dir(root, sha, "T1") / f"evaluation_input_{digest}.json").unlink()

    with pytest.raises(PreRegStorageError, match="haelt nicht"):
        verify_prereg_tree(root, sha)


def test_a_tampered_artifact_breaks_the_chain(tmp_path: Path) -> None:
    root = tmp_path / "prereg"
    sha, digest = _populated_tree(root)
    path = frozen_dir(root, sha, "T1") / f"evaluation_input_{digest}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["input"]["n_symbols"] = 7
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PreRegStorageError, match="haelt nicht"):
        verify_prereg_tree(root, sha)


def test_an_orphan_artifact_is_counted_not_condemned(tmp_path: Path) -> None:
    """Eine Waise entsteht bei einem Absturz zwischen Schreiben und Journal.

    Autoritaet hat nur, was im Journal steht — die Waise schadet nichts.
    """
    root = tmp_path / "prereg"
    sha, _ = _populated_tree(root)
    orphan = frozen_dir(root, sha, "T2") / f"evaluation_input_{'a' * 64}.json"
    orphan.write_text("{}", encoding="utf-8")

    assert verify_prereg_tree(root, sha)["orphan_artifacts"] == 1


def test_a_verdict_without_a_journal_entry_is_refused(tmp_path: Path) -> None:
    """Ein Ergebnis ohne den Entschluss, das es hervorbrachte, ist kein Beweis."""
    root = tmp_path / "prereg"
    sha, _ = _populated_tree(root)
    journal = checkpoint_journal_path(root, sha)
    journal.write_text("", encoding="utf-8")

    with pytest.raises(PreRegStorageError, match="kein Journaleintrag nennt"):
        verify_prereg_tree(root, sha)


# ── Backup und Wiederherstellung ────────────────────────────────────────────


def _run(script: str, cwd: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    import os

    assert _BASH is not None
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(  # noqa: S603
        [_BASH, "-c", script], capture_output=True, text=True, check=False, cwd=str(cwd), env=env
    )


def test_the_prereg_tree_survives_backup_and_restore(tmp_path: Path) -> None:
    """Verschluesseln, entschluesseln, auspacken — und die Kette haelt noch.

    Der eigentliche Beweis ist nicht "die Datei ist im Archiv", sondern dass
    ``verify_prereg_tree`` auf dem WIEDERHERGESTELLTEN Baum durchlaeuft.
    """
    home = tmp_path / "kai"
    (home / "scripts").mkdir(parents=True)
    shutil.copy(BACKUP_SCRIPT, home / "scripts" / BACKUP_SCRIPT.name)
    # Die Wahrheits-Schicht-Pflichtdateien; ohne sie bricht das Skript ab (zu Recht).
    for required in (
        "artifacts/research/prereg_ledger.jsonl",
        "artifacts/truth/attestation_ledger.jsonl",
    ):
        path = home / required
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    root = home / "artifacts" / "research" / "prereg"
    sha, digest = _populated_tree(root)

    backup = _run(
        f'bash scripts/{BACKUP_SCRIPT.name}; echo "RC=$?"',
        home,
        {"KAI_BACKUP_PASSPHRASE": _TEST_PASSPHRASE},
    )
    assert "RC=0" in backup.stdout, backup.stdout + backup.stderr

    archives = sorted((home / "artifacts" / "backups").rglob("*.enc"))
    assert archives, f"kein verschluesseltes Archiv: {backup.stdout}"

    restored = tmp_path / "restored"
    restored.mkdir()
    extract = _run(
        f'openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "{archives[-1].as_posix()}" '
        f'-pass env:KAI_BACKUP_PASSPHRASE | tar -xzf - -C "{restored.as_posix()}"; echo "RC=$?"',
        home,
        {"KAI_BACKUP_PASSPHRASE": _TEST_PASSPHRASE},
    )
    assert "RC=0" in extract.stdout, extract.stdout + extract.stderr

    candidates = list(restored.rglob("artifacts/research/prereg"))
    assert candidates, (
        f"der prereg-Baum fehlt im Archiv: {sorted(p.name for p in restored.rglob('*'))[:20]}"
    )

    report = verify_prereg_tree(candidates[0], sha)

    assert report["verified_artifacts"] == 1
    assert report["verdicts"] == 1
    assert (candidates[0] / sha / "frozen" / "T1" / f"evaluation_input_{digest}.json").exists()


def test_a_wrong_passphrase_cannot_restore(tmp_path: Path) -> None:
    """Gegenprobe zur Verschluesselung — sonst waere sie nur Dekoration."""
    home = tmp_path / "kai"
    (home / "scripts").mkdir(parents=True)
    shutil.copy(BACKUP_SCRIPT, home / "scripts" / BACKUP_SCRIPT.name)
    for required in (
        "artifacts/research/prereg_ledger.jsonl",
        "artifacts/truth/attestation_ledger.jsonl",
    ):
        path = home / required
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    _populated_tree(home / "artifacts" / "research" / "prereg")

    _run(f"bash scripts/{BACKUP_SCRIPT.name}", home, {"KAI_BACKUP_PASSPHRASE": _TEST_PASSPHRASE})
    archive = sorted((home / "artifacts" / "backups").rglob("*.enc"))[-1]

    wrong = _run(
        f'openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "{archive.as_posix()}" '
        f'-pass env:KAI_BACKUP_PASSPHRASE > /dev/null; echo "RC=$?"',
        home,
        {"KAI_BACKUP_PASSPHRASE": "eine-voellig-andere-passphrase-1234567890"},
    )

    assert "RC=0" not in wrong.stdout
