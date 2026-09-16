"""``scripts/env_backup.sh``: eine .env-Sicherung anlegen, nie mehr als drei behalten.

Befund 2026-09-16: auf dem Pi lagen 79 Klartext-Kopien der ``.env`` (Mai bis
September), die meisten mit noch gueltigen Secrets. Jede Sitzung legte vor einem
Eingriff eine Sicherung an, keine raeumte auf. Der Helfer macht beides in einem
Schritt.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "env_backup.sh"


def _require_bash() -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash nicht installiert")


def _run(root: Path, *args: str, keep: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "KAI_ENV_BACKUP_ROOT": str(root)}
    if keep is not None:
        env["KAI_ENV_BACKUP_KEEP"] = keep
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
    )


def _fixture(root: Path, n_old: int) -> list[Path]:
    (root / ".env").write_text("OPENAI_API_KEY=sk-live-value\n", encoding="utf-8")
    (root / ".env.example").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    old = []
    for i in range(n_old):
        p = root / f".env.bak-old{i}"
        p.write_text(f"OPENAI_API_KEY=sk-old-{i}\n", encoding="utf-8")
        ts = 1_700_000_000 + i * 3600
        os.utime(p, (ts, ts))
        old.append(p)
    return old


def _backups(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.name.startswith((".env.bak", ".env.backup")))


def test_backup_is_created_and_only_the_newest_three_remain(tmp_path: Path) -> None:
    _require_bash()
    old = _fixture(tmp_path, n_old=5)

    proc = _run(tmp_path, "prearm")

    assert proc.returncode == 0, proc.stderr
    kept = _backups(tmp_path)
    assert len(kept) == 3
    new = [p for p in kept if p.name.endswith("-prearm")]
    assert len(new) == 1
    assert new[0].read_text(encoding="utf-8") == "OPENAI_API_KEY=sk-live-value\n"
    # die zwei juengsten Altkopien bleiben, die drei aeltesten sind weg
    assert {p.name for p in kept} == {new[0].name, old[4].name, old[3].name}
    assert (tmp_path / ".env").exists()
    assert (tmp_path / ".env.example").exists()


def test_output_never_contains_secret_values(tmp_path: Path) -> None:
    _require_bash()
    _fixture(tmp_path, n_old=5)

    proc = _run(tmp_path, "prearm")

    assert "sk-" not in proc.stdout + proc.stderr


def test_prune_only_creates_nothing(tmp_path: Path) -> None:
    _require_bash()
    old = _fixture(tmp_path, n_old=4)

    proc = _run(tmp_path, "--prune-only")

    assert proc.returncode == 0, proc.stderr
    assert {p.name for p in _backups(tmp_path)} == {old[3].name, old[2].name, old[1].name}


def test_keep_is_configurable(tmp_path: Path) -> None:
    _require_bash()
    old = _fixture(tmp_path, n_old=4)

    proc = _run(tmp_path, "--prune-only", keep="1")

    assert proc.returncode == 0, proc.stderr
    assert [p.name for p in _backups(tmp_path)] == [old[3].name]


def test_keep_below_one_is_refused_and_deletes_nothing(tmp_path: Path) -> None:
    _require_bash()
    _fixture(tmp_path, n_old=4)

    proc = _run(tmp_path, "--prune-only", keep="0")

    assert proc.returncode == 2
    assert len(_backups(tmp_path)) == 4


def test_invalid_label_is_refused(tmp_path: Path) -> None:
    _require_bash()
    _fixture(tmp_path, n_old=4)

    proc = _run(tmp_path, "../evil")

    assert proc.returncode == 2
    assert len(_backups(tmp_path)) == 4


def test_missing_env_aborts_without_pruning(tmp_path: Path) -> None:
    _require_bash()
    _fixture(tmp_path, n_old=4)
    (tmp_path / ".env").unlink()

    proc = _run(tmp_path, "prearm")

    assert proc.returncode == 3
    assert len(_backups(tmp_path)) == 4


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-Dateirechte")
def test_backup_is_owner_only(tmp_path: Path) -> None:
    _require_bash()
    _fixture(tmp_path, n_old=0)

    proc = _run(tmp_path, "prearm")

    assert proc.returncode == 0, proc.stderr
    (new,) = _backups(tmp_path)
    assert new.stat().st_mode & 0o777 == 0o600
