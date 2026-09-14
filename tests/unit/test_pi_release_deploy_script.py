"""scripts/pi_release_deploy.sh: die Unit-Liste kommt aus der Quelle, nicht von Hand.

Der Defekt (11.–14.09.2026): ein Deploy-Skript in /tmp startete vier Units neu,
das Repo kennt sechs release-gebundene. Zwei Dienste lasen drei Deploys lang
alte Unit-Dateien, und /health -- das nur kai-server kennt -- sah nichts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "pi_release_deploy.sh"
UNITS_DIR = REPO / "deploy" / "systemd"
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")


def _runtime_exec_units() -> set[str]:
    return {p.stem for p in UNITS_DIR.glob("*.service") if "runtime-exec" in p.read_text("utf-8")}


def _lauf(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_BASH or "bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO,
        env={**os.environ, "KAI_PI_REPO": str(REPO)},
    )


def test_skript_ist_syntaktisch_gueltig() -> None:
    ergebnis = subprocess.run([_BASH or "bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_keine_handgefuehrte_unit_liste() -> None:
    """Jede Unit-Nennung im Skript muss aus `pi_release_bound_units` kommen."""
    text = SCRIPT.read_text("utf-8")
    assert "pi_release_bound_units" in text
    code = "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))
    for unit in _runtime_exec_units():
        assert unit not in code, f"{unit} steht als Literal im Skript"


def test_dry_run_nennt_alle_release_gebundenen_units() -> None:
    ergebnis = _lauf("--sha", "a" * 40, "--dry-run")

    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    zeile = next(z for z in ergebnis.stdout.splitlines() if z.startswith("release_bound_units"))
    genannt = set(zeile.split("=", 1)[1].split())
    assert genannt == _runtime_exec_units()
    assert len(genannt) >= 6


@pytest.mark.parametrize("sha", ["", "abc123", "e25eab08", "g" * 40])
def test_kurzer_oder_ungueltiger_sha_bricht_vor_jeder_aktion_ab(sha: str) -> None:
    """Der Vorfall vom 14.09.: ein Kurz-SHA scheiterte erst NACH dem ff-Pull."""
    ergebnis = _lauf("--sha", sha, "--dry-run")

    assert ergebnis.returncode == 3
    assert "ABBRUCH" in ergebnis.stderr


def test_ff_only_und_kein_force() -> None:
    text = SCRIPT.read_text("utf-8")
    assert "--ff-only" in text
    assert "--force" not in text and "reset --hard" not in text
