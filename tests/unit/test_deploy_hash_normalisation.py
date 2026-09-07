"""Der Hash-Vergleich haelt seine Zusage unter JEDER Dateinamens-Form.

CONTEXT IS PART OF THE CONTRACT. Ein Test beweist nicht nur "Funktion X liefert
Y", sondern bei systemnahen Pfaden auch: unter welchen Shell-, OS-, Pfad- und
Encoding-Bedingungen gilt Y? Genau diese Frage war hier unbeantwortet.

Der Vorfall: `sha256sum` stellt der AUSGABE ein Escape-Zeichen voran, sobald der
DATEINAME einen Backslash enthaelt. Unter Git-Bash auf Windows ist der Pfad des
Tarballs genau so einer, und `awk '{print $1}'` nahm das Zeichen mit --
`pi_deploy_web.sh` brach mit "sha256 mismatch after scp" ab, obwohl die
Uebertragung fehlerfrei war. Die Fachlogik (Hash vor und nach der Uebertragung
vergleichen) war richtig; falsch war die unbeachtete Betriebssemantik darunter.

Deshalb prueft diese Datei nicht nur, dass der Vergleich funktioniert, sondern
variiert die KULISSE, die er fuer selbstverstaendlich hielt:

    POSIX-Dateiname       -> PASS
    Windows-Backslashes   -> PASS
    echter anderer Hash   -> FAIL

Erst die dritte Zeile macht aus den ersten beiden eine Aussage: ohne sie waere
"beide normalisiert gleich" auch dann erfuellt, wenn die Funktion schlicht alles
gleichmacht.

Geprueft wird die AUSGELIEFERTE Funktion, aus dem Skript extrahiert -- eine
Kopie im Test wuerde nur beweisen, dass die Kopie tut, was die Kopie tut.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SKRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pi_deploy_web.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("sha256sum") is None,
    reason="Shell-Semantik braucht bash und coreutils",
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def _funktionsdefinition() -> str:
    """Die Zeile mit `entschaerfe_hash` AUS dem Skript, nicht nachgebaut."""
    for zeile in SKRIPT.read_text(encoding="utf-8").splitlines():
        if zeile.startswith("entschaerfe_hash()"):
            return zeile
    raise AssertionError("entschaerfe_hash() nicht im Skript gefunden")


def _normalisiere(roh: str) -> str:
    ergebnis = subprocess.run(  # noqa: S603
        [
            "bash",
            "-c",
            f"{_funktionsdefinition()}\nprintf '%s' \"$1\" | entschaerfe_hash",
            "_",
            roh,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return ergebnis.stdout.strip()


@pytest.mark.parametrize(
    ("kulisse", "roh"),
    [
        ("POSIX-Dateiname", HASH_A),
        ("Windows-Backslashes", chr(92) + HASH_A),
    ],
)
def test_derselbe_hash_bleibt_derselbe(kulisse: str, roh: str) -> None:
    """Die Form des Dateinamens darf den Hash nicht veraendern."""
    assert _normalisiere(roh) == HASH_A, f"{kulisse}: Normalisierung liefert nicht den Hash"


def test_zwei_verschiedene_hashes_bleiben_verschieden() -> None:
    """Die Gegenprobe -- ohne sie waere 'alles gleichmachen' auch bestanden."""
    assert _normalisiere(HASH_A) != _normalisiere(chr(92) + HASH_B)


def test_sha256sum_verhaelt_sich_wirklich_so(tmp_path: Path) -> None:
    """Die Annahme selbst messen, nicht glauben.

    Wenn coreutils dieses Verhalten eines Tages aendert, soll das hier
    auffallen und nicht erst beim naechsten Deploy vom Laptop.
    """
    datei = tmp_path / ("mit" + chr(92) + "backslash.bin")
    try:
        datei.write_bytes(b"x")
    except OSError:
        pytest.skip("Dateiname mit Backslash auf diesem Dateisystem nicht anlegbar")

    roh = subprocess.run(  # noqa: S603
        ["sha256sum", str(datei)], capture_output=True, text=True, check=True
    ).stdout
    assert roh.startswith(chr(92)), (
        "coreutils stellt kein Escape-Zeichen mehr voran — die Normalisierung "
        "in pi_deploy_web.sh ist dann unnoetig, aber nicht schaedlich"
    )
    feld = roh.split()[0]
    assert _normalisiere(feld) == _normalisiere(feld.lstrip(chr(92)))
