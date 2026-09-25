"""Hub-Installer (K7): detached HEAD ist zulaessig, Aufloesung vor jedem Schreiben.

Befund 24.09.2026: aus einem Worktree mit detached HEAD brach
``scripts/install_kai_dev_hub.ps1`` an ``(git branch --show-current).Trim()``
ab -- NACHDEM die Hub-Dateien schon nach ``app\\v<Version>`` kopiert waren.
Zurueck blieb ein halber Versionsordner ohne ``install.json``.

Die Tests fahren den ECHTEN Installer gegen ein Fixture-Repo im tmp-Verzeichnis,
mit ``-InstallRoot`` und ohne Shortcut/Scheduled Task. Der Desktop und die
Aufgabenplanung des Rechners bleiben unberuehrt.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install_kai_dev_hub.ps1"
HUB = ROOT / "scripts" / "kai_dev_hub.py"
WORKFLOW = ROOT / "scripts" / "kai_dev_workflow.py"

_SHELL = shutil.which("pwsh") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or _SHELL is None or shutil.which("git") is None,
    reason="Der Hub-Installer ist ein Windows-Werkzeug (PowerShell + pythonw)",
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def kai_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    (repo / "docs" / "AI_HANDOFF.md").write_text("# handoff\n", encoding="utf-8")
    (repo / "opencode.json").write_text("{}\n", encoding="utf-8")
    for quelle in (INSTALLER, HUB, WORKFLOW):
        shutil.copy2(quelle, repo / "scripts" / quelle.name)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "add", "-A")
    _git(
        repo,
        "-c",
        "user.email=t@example.invalid",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    return repo


def _install(repo: Path, root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [
            _SHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(repo / "scripts" / "install_kai_dev_hub.ps1"),
            "-Repository",
            str(repo),
            "-InstallRoot",
            str(root),
            "-SkipScheduledTask",
            "-SkipShortcut",
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
    )


def _version() -> str:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(HUB), "--version"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _app_dirs(root: Path) -> list[str]:
    app = root / "app"
    return sorted(p.name for p in app.iterdir()) if app.exists() else []


def test_detached_head_installiert_mit_source_head_als_herkunft(
    kai_fixture: Path, tmp_path: Path
) -> None:
    head = _git(kai_fixture, "rev-parse", "HEAD")
    _git(kai_fixture, "checkout", "-q", "--detach")
    root = tmp_path / "hub"

    ergebnis = _install(kai_fixture, root)

    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    ziel = root / "app" / f"v{_version()}"
    manifest = json.loads((ziel / "install.json").read_text(encoding="utf-8"))
    assert manifest["source_head"] == head
    assert manifest["source_branch"] is None
    assert manifest["source_detached"] is True
    assert manifest["hub_sha256"] == _sha256(ziel / "kai_dev_hub.py")
    assert manifest["workflow_sha256"] == _sha256(ziel / "kai_dev_workflow.py")
    assert _sha256(ziel / "kai_dev_hub.py") == _sha256(HUB)
    assert _app_dirs(root) == [ziel.name], "kein Staging-Rest neben der Version"


def test_install_json_ist_utf8_ohne_bom(kai_fixture: Path, tmp_path: Path) -> None:
    root = tmp_path / "hub"

    assert _install(kai_fixture, root).returncode == 0

    roh = (root / "app" / f"v{_version()}" / "install.json").read_bytes()
    assert not roh.startswith(b"\xef\xbb\xbf")
    json.loads(roh.decode("utf-8"))


def test_auf_einem_branch_steht_der_branchname(kai_fixture: Path, tmp_path: Path) -> None:
    root = tmp_path / "hub"

    ergebnis = _install(kai_fixture, root)

    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    manifest = json.loads(
        (root / "app" / f"v{_version()}" / "install.json").read_text(encoding="utf-8")
    )
    assert manifest["source_branch"] == "main"
    assert manifest["source_detached"] is False


def test_scheiternde_aufloesung_hinterlaesst_keinen_versionsordner(
    kai_fixture: Path, tmp_path: Path
) -> None:
    root = tmp_path / "hub"

    ergebnis = _install(kai_fixture, root, "-PythonExe", str(tmp_path / "gibt-es-nicht.exe"))

    assert ergebnis.returncode != 0
    assert not [n for n in _app_dirs(root) if n.startswith(("v", ".staging"))]


def test_gebrochene_version_hinterlaesst_keinen_versionsordner(
    kai_fixture: Path, tmp_path: Path
) -> None:
    hub = kai_fixture / "scripts" / "kai_dev_hub.py"
    hub.write_text(
        hub.read_text(encoding="utf-8").replace('HUB_VERSION = "', 'HUB_VERSION = "kaputt-', 1),
        encoding="utf-8",
    )
    root = tmp_path / "hub"

    ergebnis = _install(kai_fixture, root)

    assert ergebnis.returncode != 0
    # Nur der ASCII-Teil: die Konsolen-Codepage verbiegt das "ü" je nach Shell.
    assert "ltige Hub-Version: kaputt-" in ergebnis.stdout + ergebnis.stderr
    assert not [n for n in _app_dirs(root) if n.startswith(("v", ".staging"))]


def test_neuinstallation_ersetzt_die_version_vollstaendig(
    kai_fixture: Path, tmp_path: Path
) -> None:
    root = tmp_path / "hub"
    assert _install(kai_fixture, root).returncode == 0
    ziel = root / "app" / f"v{_version()}"
    (ziel / "fremd.txt").write_text("rest einer alten installation\n", encoding="utf-8")

    ergebnis = _install(kai_fixture, root)

    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert not (ziel / "fremd.txt").exists(), "ersetzt, nicht darueberkopiert"
    assert (ziel / "install.json").exists()
    assert _app_dirs(root) == [ziel.name]
