from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "deploy" / "systemd"
BACKUP_SCRIPT = ROOT / "scripts" / "kai_backup_artifacts.sh"
DRILL_SCRIPT = ROOT / "scripts" / "kai_backup_restore_drill.sh"
PASSPHRASE = "correct horse battery staple for kai backups 2026"


def _directives(path: Path) -> dict[str, dict[str, str]]:
    section = ""
    parsed: dict[str, dict[str, str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            parsed.setdefault(section, {})
            continue
        key, sep, value = line.partition("=")
        assert sep, f"nicht parsebare Unit-Zeile in {path.name}: {raw!r}"
        parsed.setdefault(section, {})[key] = value
    return parsed


def test_backup_artifacts_timer_contract() -> None:
    timer = _directives(SYSTEMD / "kai-backup-artifacts.timer")

    # Der Timer existierte schon (03:47 UTC, RandomizedDelay, kein Requires= — #414);
    # er war auf der Pi nur nie ENABLED. Deshalb bleibt die Datei unveraendert.
    assert timer["Timer"]["OnCalendar"] == "*-*-* 03:47:00"
    assert timer["Timer"]["Persistent"] == "true"
    assert timer["Timer"]["AccuracySec"] == "10min"
    assert timer["Timer"]["RandomizedDelaySec"] == "15min"
    assert "Requires" not in timer.get("Unit", {}), "Timer-Requires-Kaskade (#414)"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_restore_drill_units_contract() -> None:
    service = _directives(SYSTEMD / "kai-backup-restore-drill.service")
    timer = _directives(SYSTEMD / "kai-backup-restore-drill.timer")

    assert service["Service"]["Type"] == "oneshot"
    assert service["Service"]["User"] == "ubuntu"
    assert service["Service"]["WorkingDirectory"] == "/home/ubuntu/ai_analyst_trading_bot"
    assert service["Unit"]["OnFailure"] == "kai-unit-failure-notify@%n.service"
    assert service["Service"]["TimeoutStartSec"] == "20min"
    assert "scripts/kai_backup_restore_drill.sh" in service["Service"]["ExecStart"]
    read_write = service["Service"]["ReadWritePaths"]
    assert "/home/ubuntu/ai_analyst_trading_bot/artifacts" in read_write
    assert "/tmp" in read_write

    assert timer["Timer"]["OnCalendar"] == "*-*-01 04:10:00"
    assert timer["Timer"]["Persistent"] == "true"
    assert timer["Timer"]["Unit"] == "kai-backup-restore-drill.service"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_restore_drill_script_static_contract() -> None:
    text = DRILL_SCRIPT.read_text(encoding="utf-8")

    assert "set -uo pipefail" in text
    assert "trap cleanup EXIT" in text
    assert "backup_restore_drill/v1" in text
    assert "KAI_BACKUP_PASSPHRASE" in text
    assert "kai_artifacts_*.tar.gz.enc" in text


def _require_backup_tools() -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash nicht installiert")
    probe = subprocess.run(
        ["bash", "-lc", "command -v openssl >/dev/null && command -v tar >/dev/null"],
        text=True,
        capture_output=True,
        timeout=10,
    )
    if probe.returncode != 0:
        pytest.skip("openssl oder tar nicht in bash-PATH")


def _copy_drill_fixture(root: Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir()
    shutil.copy2(BACKUP_SCRIPT, scripts / "kai_backup_artifacts.sh")
    shutil.copy2(DRILL_SCRIPT, scripts / "kai_backup_restore_drill.sh")


def _write_fixture_sources(root: Path) -> None:
    files = {
        "artifacts/research/prereg_ledger.jsonl": '{"id":"pre","ok":true}\n',
        "artifacts/truth/attestation_ledger.jsonl": '{"id":"att","ok":true}\n',
        "artifacts/research/hypothesis_ledger.jsonl": '{"id":"hyp","n":1}\n',
        "artifacts/research/falsification_verdicts.jsonl": '{"id":"verdict","pass":true}\n',
        "artifacts/research/forecaster_panel/panel.json": '{"panel":"alpha"}\n',
        "DECISION_LOG.md": "# decisions\n\n- fixture\n",
    }
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _run_bash(
    root: Path, command: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env or {})
    return subprocess.run(
        ["bash", "-lc", command],
        cwd=root,
        env=merged,
        text=True,
        capture_output=True,
        timeout=30,
    )


def _make_backup(root: Path) -> Path:
    result = _run_bash(
        root,
        "env "
        f"KAI_BACKUP_PASSPHRASE={shlex.quote(PASSPHRASE)} "
        "KAI_BACKUP_KEEP_DAYS=0 "
        "bash scripts/kai_backup_artifacts.sh",
    )
    assert result.returncode == 0, result.stderr + result.stdout
    archives = sorted((root / "artifacts" / "backups").rglob("kai_artifacts_*.tar.gz.enc"))
    assert len(archives) == 1
    return archives[0]


def _run_drill(
    root: Path, archive: Path | None, passphrase: str | None = PASSPHRASE
) -> subprocess.CompletedProcess[str]:
    (root / "tmp").mkdir(exist_ok=True)
    env_prefix = "env TMPDIR=$PWD/tmp "
    if passphrase is not None:
        env_prefix += f"KAI_BACKUP_PASSPHRASE={shlex.quote(passphrase)} "
    archive_arg = ""
    if archive is not None:
        archive_arg = " --archive " + shlex.quote(archive.relative_to(root).as_posix())
    return _run_bash(root, env_prefix + "bash scripts/kai_backup_restore_drill.sh" + archive_arg)


def _latest_proof(root: Path) -> dict[str, object]:
    proofs = sorted((root / "artifacts" / "ops" / "backup_drill").glob("*.json"))
    assert proofs
    return cast(dict[str, object], json.loads(proofs[-1].read_text(encoding="utf-8")))


def test_restore_drill_passes_and_cleans_tmp(tmp_path: Path) -> None:
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    result = _run_drill(tmp_path, archive)

    assert result.returncode == 0, result.stderr + result.stdout
    proof = _latest_proof(tmp_path)
    assert proof["schema"] == "backup_restore_drill/v1"
    assert proof["status"] == "PASS"
    assert proof["archive_sha256"]
    assert proof["files_restored"] == proof["files_expected"]
    files_expected = cast(list[str], proof["files_expected"])
    assert "artifacts/research/prereg_ledger.jsonl" in files_expected
    assert "artifacts/research/forecaster_panel/panel.json" in files_expected
    assert proof["files_missing"] == []
    assert proof["sha256_mismatch"] == []
    assert list((tmp_path / "tmp").glob("kai-backup-restore-drill.*")) == []


def test_restore_drill_detects_a_corrupted_archive(tmp_path: Path) -> None:
    """Korruption IM ARCHIV ist ein Befund — das ist die Frage eines Restore-Drills.

    Vorher pruefte dieser Test etwas anderes: er aenderte die LEBENDE Quelldatei
    und erwartete FAIL. Das kodierte die falsche Frage („entspricht das Archiv
    dem aktuellen Systemzustand?") statt der richtigen („laesst sich aus diesem
    Archiv wiederherstellen?"). Live auf der Pi (2026-08-27) fuehrte genau das
    zum Fehlalarm: ``trading_loop_audit.jsonl`` war zwischen Backup und Drill um
    5.044 Bytes gewachsen, das Archiv war bit-genau ein Praefix der Live-Datei —
    also einwandfrei — und der Drill meldete trotzdem ``content mismatch``.
    Ein Waechter, der bei jedem Lauf schlaegt, wird abgeschaltet.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    # Das verschluesselte Archiv beschaedigen (ein Byte in der Mitte kippen).
    blob = bytearray(archive.read_bytes())
    mid = len(blob) // 2
    blob[mid] ^= 0xFF
    archive.write_bytes(bytes(blob))

    result = _run_drill(tmp_path, archive)

    assert result.returncode != 0, "ein beschaedigtes Archiv darf nie PASS melden"
    proof = _latest_proof(tmp_path)
    assert proof["status"] == "FAIL"
    assert list((tmp_path / "tmp").glob("kai-backup-restore-drill.*")) == []


def test_drill_passes_when_a_source_grew_after_the_backup(tmp_path: Path) -> None:
    """Der Regressionstest zum Vorfall: append-only-Wachstum ist kein Befund.

    Der Trading-Loop haengt im Minutentakt an ``trading_loop_audit.jsonl`` an.
    Ein Drill, der gegen die lebende Datei vergleicht, wird dadurch ab dem
    ersten Timer-Lauf JEDEN Monat rot — ohne dass am Backup etwas falsch waere.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)
    assert archive.exists()

    grown = tmp_path / "artifacts" / "research" / "hypothesis_ledger.jsonl"
    with grown.open("a", encoding="utf-8") as fh:
        fh.write('{"id":"hyp","n":2}\n')

    result = _run_drill(tmp_path, archive)
    proof = _latest_proof(tmp_path)

    assert proof["status"] == "PASS", (
        f"append-only-Wachstum darf kein FAIL sein: {proof.get('reason')} "
        f"/ {proof.get('sha256_mismatch')}"
    )
    assert proof["sha256_mismatch"] == []
    assert result.returncode == 0


def test_backup_manifest_describes_the_archive_content(tmp_path: Path) -> None:
    """Das Manifest beschreibt den ARCHIVINHALT und aendert sich danach nie.

    Genau diese Eigenschaft macht den Drill unabhaengig vom Live-Zustand: er
    prueft, ob das Wiederhergestellte dem entspricht, was eingepackt wurde.
    """
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    _make_backup(tmp_path)

    audit = tmp_path / "artifacts" / "backup_audit.jsonl"
    entry = json.loads(audit.read_text(encoding="utf-8").strip().splitlines()[-1])
    hashes = entry.get("file_sha256")
    assert isinstance(hashes, dict) and hashes, "Audit-Eintrag ohne file_sha256"
    assert "artifacts/research/prereg_ledger.jsonl" in hashes

    for rel, digest in hashes.items():
        source = tmp_path / rel
        if source.is_file():
            assert digest == hashlib.sha256(source.read_bytes()).hexdigest(), rel

    before = dict(hashes)
    ledger = tmp_path / "artifacts" / "research" / "hypothesis_ledger.jsonl"
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write('{"id":"hyp","n":3}\n')
    again = json.loads(audit.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert again["file_sha256"] == before, "Manifest darf sich nachtraeglich nicht aendern"


def test_restore_drill_missing_passphrase_writes_fail_proof(tmp_path: Path) -> None:
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    result = _run_drill(tmp_path, archive, passphrase=None)

    assert result.returncode == 2
    proof = _latest_proof(tmp_path)
    assert proof["status"] == "FAIL"
    assert proof["reason"] == "passphrase missing"
    assert cast(str, proof["archive"]).endswith(".tar.gz.enc")


def test_restore_drill_no_archive_writes_fail_proof(tmp_path: Path) -> None:
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    (tmp_path / "artifacts" / "backups").mkdir(parents=True)

    result = _run_drill(tmp_path, archive=None)

    assert result.returncode == 3
    proof = _latest_proof(tmp_path)
    assert proof["status"] == "FAIL"
    assert proof["reason"] == "archive missing"
    assert proof["archive"] == ""


def test_restore_drill_wrong_passphrase_writes_fail_proof(tmp_path: Path) -> None:
    _require_backup_tools()
    _copy_drill_fixture(tmp_path)
    _write_fixture_sources(tmp_path)
    archive = _make_backup(tmp_path)

    result = _run_drill(tmp_path, archive, passphrase="wrong passphrase but still present")

    assert result.returncode == 4
    proof = _latest_proof(tmp_path)
    assert proof["status"] == "FAIL"
    assert proof["reason"] == "decrypt failed"
