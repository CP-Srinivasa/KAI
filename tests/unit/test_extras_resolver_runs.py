"""Der Extra-Resolver wird AUSGEFUEHRT, nicht gelesen.

Am 2026-09-07 stand in `pi_make_release.sh` ein eingebettetes Python-Schnipsel,
dessen Zeichenkette einen echten Zeilenumbruch enthielt:

    print("
".join(specs))

In der Shell ist das gueltig -- direkt darueber steht dieselbe Konstruktion in
`printf` und `tr` und funktioniert. In Python ist es ein SyntaxError. Der erste
echte Aufruf von `--extra litellm` brach mit

    SyntaxError: unterminated string literal (detected at line 12)
    Extra-Aufloesung gescheitert

sofort ab, bevor irgendetwas gebaut wurde.

Neun CI-Checks waren gruen. Die Tests des einfuehrenden PRs pruefen den
Skript-TEXT -- ob Flag, Feldname und Pfadaufbau vorkommen. Das ist eine Aussage
ueber die Datei, keine ueber das Verhalten: das Schnipsel wurde nie
ausgefuehrt. "Der Code steht da" ist nicht dasselbe wie "er laeuft" -- dieselbe
Familie wie "das Feld steht da" gegen "es stimmt".

Diese Datei fuehrt den Resolver deshalb wirklich aus, mit einer echten
`pyproject.toml`, und prueft das Ergebnis. Sie braucht kein venv und keinen
Release -- nur `python3` und den Skripttext.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pi_make_release.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="Der Resolver ist in ein Shell-Skript eingebettet"
)

PYPROJECT = """
[project]
name = "probe"
version = "0.1.0"

[project.optional-dependencies]
litellm = ["litellm[proxy]==1.99.0"]
mehrere = ["alpha==1.0", "beta==2.0"]
"""


def _resolver_quelltext() -> str:
    """Das eingebettete Schnipsel AUS dem Skript, nicht nachgebaut."""
    text = SKRIPT.read_text(encoding="utf-8")
    treffer = re.search(r"EXTRA_SPECS=\"\$\(python3 -c '\n(.*?)\n' ", text, re.DOTALL)
    assert treffer, "Extra-Resolver im Builder nicht gefunden"
    return treffer.group(1)


def _aufloesen(tmp: Path, *extras: str) -> subprocess.CompletedProcess[str]:
    projekt = tmp / "pyproject.toml"
    projekt.write_text(PYPROJECT, encoding="utf-8")
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", _resolver_quelltext(), str(projekt), *extras],
        capture_output=True,
        text=True,
        check=False,
    )


def test_der_resolver_ist_ueberhaupt_ausfuehrbar(tmp_path: Path) -> None:
    """Die Pruefung, die gefehlt hat: kein SyntaxError beim Start."""
    ergebnis = _aufloesen(tmp_path, "litellm")

    assert "SyntaxError" not in ergebnis.stderr, ergebnis.stderr
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_ein_extra_liefert_seine_spec(tmp_path: Path) -> None:
    ergebnis = _aufloesen(tmp_path, "litellm")

    assert ergebnis.stdout.split() == ["litellm[proxy]==1.99.0"]


def test_mehrere_specs_stehen_je_auf_einer_zeile(tmp_path: Path) -> None:
    """Der Aufrufer wortsplittet die Ausgabe -- eine Zeile je Spec ist der Vertrag."""
    ergebnis = _aufloesen(tmp_path, "mehrere")

    assert ergebnis.stdout.splitlines() == ["alpha==1.0", "beta==2.0"]


def test_ein_unbekanntes_extra_scheitert_benannt(tmp_path: Path) -> None:
    """Die Gegenprobe: der Resolver darf nicht alles durchwinken."""
    ergebnis = _aufloesen(tmp_path, "gibtesnicht")

    assert ergebnis.returncode != 0
    assert "UNBEKANNTES_EXTRA" in ergebnis.stderr
    assert "litellm" in ergebnis.stderr, "die verfuegbaren Extras werden nicht genannt"
