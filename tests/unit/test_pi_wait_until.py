"""scripts/lib/pi_wait_until.sh — Warten mit Frist statt fester Pause.

Anlass (17.09.2026): ``pi_release_deploy.sh`` schlief nach den Restarts fest
20 s und pruefte dann EINMAL. Beim Deploy ff93050c antwortete /health da noch
nicht -> DEPLOY_NOT_VERIFIED/Exit 1, obwohl der Deploy gesund war; beim Deploy
d27e708d reichte es nur knapp (uptime_s 3,86). Ein Deploy-Skript, das gesunde
Deploys als gescheitert meldet, lehrt, seine Meldung zu ignorieren.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "scripts" / "lib" / "pi_wait_until.sh"
DEPLOY = REPO / "scripts" / "pi_release_deploy.sh"
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")


def _bash(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_BASH or "bash", "-c", script], capture_output=True, text=True, encoding="utf-8"
    )


def _lib() -> str:
    return LIB.as_posix()


def test_returns_as_soon_as_the_check_passes(tmp_path: Path) -> None:
    counter = (tmp_path / "n").as_posix()
    started = time.monotonic()
    res = _bash(
        f'. "{_lib()}"; echo 0 > "{counter}"; '
        f'check() {{ n=$(( $(cat "{counter}") + 1 )); echo $n > "{counter}"; [ "$n" -ge 3 ]; }}; '
        f'pi_wait_until 30 0.2 check; echo rc=$?; cat "{counter}"'
    )
    assert "rc=0" in res.stdout, res.stdout + res.stderr
    assert res.stdout.strip().splitlines()[-1] == "3"
    assert time.monotonic() - started < 10


def test_gives_up_after_the_deadline() -> None:
    started = time.monotonic()
    res = _bash(f'. "{_lib()}"; pi_wait_until 1 0.2 false; echo rc=$?')
    elapsed = time.monotonic() - started
    assert "rc=1" in res.stdout, res.stdout + res.stderr
    assert 0.9 <= elapsed < 8


def test_passes_arguments_through() -> None:
    res = _bash(f'. "{_lib()}"; pi_wait_until 5 0.2 test "a" = "a"; echo rc=$?')
    assert "rc=0" in res.stdout, res.stdout + res.stderr


def test_rejects_a_missing_command() -> None:
    res = _bash(f'. "{_lib()}"; pi_wait_until 5 0.2; echo rc=$?')
    assert "rc=2" in res.stdout, res.stdout + res.stderr


def test_deploy_waits_for_verification_instead_of_a_fixed_sleep() -> None:
    text = DEPLOY.read_text("utf-8")
    code = "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))
    assert "sleep 20" not in code
    assert "pi_wait_until.sh" in code
    assert "pi_wait_until" in code.split("== Verifikation")[0]
    # Die Frist ist einstellbar, der Default traegt einen langsamen Kaltstart.
    assert "KAI_PI_VERIFY_TIMEOUT_S" in code


def test_deploy_script_still_parses() -> None:
    res = subprocess.run([_BASH or "bash", "-n", str(DEPLOY)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
