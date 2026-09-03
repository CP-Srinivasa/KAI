"""Eine release-gebundene Unit darf keinen Pfad des alten Checkout-Modells fuehren.

Der Defekt, gegen den diese Datei steht (gemessen auf 37cc57f2):
``kai-liquidation-stream.service`` war halb umgestellt. ``WorkingDirectory`` und
der Wrapper zeigten auf ``/home/kai/current``, aber ``PYTHONPATH``, die
``EnvironmentFile`` und — entscheidend — das Kommando NACH ``--`` standen
weiterhin auf ``/home/ubuntu/ai_analyst_trading_bot``.

``bind_argv_to_release`` schreibt nur Pfade um, die mit dem ``--repo``-Wert
beginnen. ``/home/ubuntu/...`` beginnt nicht damit und blieb stehen. Der Dienst
haette also das unveraenderliche Release bezeugt und seinen Interpreter samt
Modulen aus dem beweglichen Checkout geladen — exakt der Capture-zu-Load-Race,
den das ganze Release-Modell schliessen soll, wieder da als
Konfigurationsfehler statt als Codepfad. Kein Test konnte ihn sehen, weil kein
Test die Unit-DATEIEN gelesen hat.

Die Menge pflegt sich selbst: geprueft wird jede Unit, die ``runtime-exec``
fuehrt — dieselbe Quelle, aus der ``expected_attesting_units`` seine Sollmenge
zieht. Eine handgefuehrte Liste waere die naechste Wachliste, die von ihrer
Quelle abweicht.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UNITS_DIR = Path(__file__).resolve().parents[2] / "deploy" / "systemd"

#: Ein Interpreter- oder Modulpfad. Genau die Sorte Pfad, die nach dem Binden
#: nicht mehr am beweglichen Symlink haengen darf.
_PY_PATH = re.compile(r"(/\S*?/\.venv/bin/python[\d.]*)")


def _release_bound_units() -> list[Path]:
    return sorted(p for p in UNITS_DIR.glob("*.service") if "runtime-exec" in p.read_text("utf-8"))


def _repo_arg(text: str) -> str:
    m = re.search(r"--repo\s+(\S+)", text)
    return m.group(1) if m else ""


def test_es_gibt_ueberhaupt_release_gebundene_units() -> None:
    """Ohne diese Zusicherung waere jeder Test darunter leer und damit gruen."""
    units = _release_bound_units()
    assert len(units) >= 5, [p.name for p in units]


@pytest.mark.parametrize("unit", _release_bound_units(), ids=lambda p: p.name)
def test_jeder_interpreterpfad_haengt_am_release(unit: Path) -> None:
    text = unit.read_text(encoding="utf-8")
    repo = _repo_arg(text)
    assert repo, f"{unit.name}: runtime-exec ohne --repo"
    fremd = [p for p in _PY_PATH.findall(text) if not p.startswith(repo)]
    assert fremd == [], (
        f"{unit.name}: Interpreterpfad ausserhalb des gebundenen Release {repo}: {fremd}. "
        "bind_argv_to_release schreibt nur Pfade um, die mit --repo beginnen — "
        "dieser Dienst wuerde das Release bezeugen und anderen Code laden."
    )


@pytest.mark.parametrize("unit", _release_bound_units(), ids=lambda p: p.name)
def test_working_directory_ist_der_gebundene_release_pfad(unit: Path) -> None:
    text = unit.read_text(encoding="utf-8")
    wd = re.search(r"^WorkingDirectory=(\S+)", text, re.MULTILINE)
    assert wd, f"{unit.name}: kein WorkingDirectory"
    assert wd.group(1) == _repo_arg(text), (
        f"{unit.name}: WorkingDirectory {wd.group(1)} != --repo {_repo_arg(text)} — "
        "Import-Root und bezeugtes Release muessen derselbe Baum sein."
    )


@pytest.mark.parametrize("unit", _release_bound_units(), ids=lambda p: p.name)
def test_kein_statischer_pythonpath(unit: Path) -> None:
    """Ein fester PYTHONPATH hebt die cwd-Bindung wieder auf.

    Der Wrapper wechselt vor dem ``execv`` in das aufgeloeste Release; ein
    statischer Import-Root in der Unit zoege die Module trotzdem woanders her.
    """
    text = unit.read_text(encoding="utf-8")
    assert not re.search(r"^Environment=PYTHONPATH=", text, re.MULTILINE), (
        f"{unit.name}: statischer PYTHONPATH in einer release-gebundenen Unit"
    )


@pytest.mark.parametrize("unit", _release_bound_units(), ids=lambda p: p.name)
def test_zustandspfade_liegen_nicht_im_release(unit: Path) -> None:
    """Logs und ``.env`` gehoeren in den stabilen Zustandspfad, nicht ins Release.

    Der Release-Baum ist unter ``ProtectSystem=strict`` schreibgeschuetzt; ein
    Logpfad darin wuerde den Dienst beim Start scheitern lassen. Und ein ``.env``
    im Release verlore bei jedem Deploy seinen Inhalt.
    """
    text = unit.read_text(encoding="utf-8")
    repo = _repo_arg(text)
    for key in ("EnvironmentFile", "StandardOutput", "StandardError"):
        for m in re.finditer(rf"^{key}=(\S+)", text, re.MULTILINE):
            wert = m.group(1).lstrip("-").removeprefix("append:")
            if wert in {"journal", "null", "inherit"}:
                continue
            assert not wert.startswith(repo), (
                f"{unit.name}: {key} zeigt mit {wert} in den unveraenderlichen "
                "Release-Baum — Zustand gehoert in den stabilen Zustandspfad."
            )
