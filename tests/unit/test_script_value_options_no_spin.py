"""Eine Option mit Wert, die als LETZTES Argument ohne Wert kommt, darf nicht haengen.

Der Defekt (26./27.09.2026): ``--sha) SHA="${2:-}"; shift 2 ;;`` — steht ``--sha``
am Ende, scheitert ``shift 2`` und verschiebt nichts; ``while [ $# -gt 0 ]`` dreht
dann endlos. Ein in PowerShell beim Einfuegen zerbrochener Aufruf
(``... pi_release_deploy.sh --sha`` / naechste Zeile der SHA) lief so 26 h mit
100 % CPU auf der Pi. Jetzt bricht jede solche Option sofort mit Meldung ab.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")

#: Skript -> erwarteter Exit-Code bei fehlendem Wert.
SCRIPTS = {
    "scripts/pi_release_deploy.sh": 3,
    "scripts/pi_deploy_step.sh": 2,
    "scripts/pi_apply_systemd_units.sh": 2,
}


def _value_options(script: str) -> list[str]:
    text = (REPO / script).read_text(encoding="utf-8")
    return re.findall(r"^\s*(--[a-z-]+)\) \[ \$# -ge 2 \]", text, flags=re.MULTILINE)


CASES = [(script, opt) for script in SCRIPTS for opt in _value_options(script)]


def test_every_script_is_covered() -> None:
    assert {script for script, _ in CASES} == set(SCRIPTS)
    assert len(CASES) == 11


def test_no_value_option_uses_the_spinning_pattern_anymore() -> None:
    for script in SCRIPTS:
        text = (REPO / script).read_text(encoding="utf-8")
        assert not re.search(r'\$\{2:-\}"?; shift 2', text), script


@pytest.mark.parametrize(("script", "option"), CASES)
def test_a_trailing_option_without_value_aborts_instead_of_spinning(
    script: str, option: str
) -> None:
    result = subprocess.run(
        [_BASH or "bash", str(REPO / script), option],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO,
        timeout=20,  # der alte Code haengt hier -> TimeoutExpired
    )
    assert result.returncode == SCRIPTS[script]
    assert f"{option} braucht einen Wert" in result.stderr
