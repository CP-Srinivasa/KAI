"""Ein Drop-in, das über den geladenen Code entscheidet, darf nicht unsichtbar sein.

Der Deploy-Abgleich vergleicht Unit-Dateien Byte für Byte. Ein
``/etc/systemd/system/<unit>.d/*.conf`` steht daneben, wird von systemd aber
**nach** der Unit ausgewertet — ein zweites ``ExecStart=`` ersetzt den Befehl,
und der Abgleich meldete trotzdem „angewendet und bewiesen". Deshalb bricht er
fail-closed ab, sobald er ein solches Drop-in findet.

Diese Datei hält zwei Dinge fest.

Erstens die Wirkung: fremde Drop-ins blockieren weiterhin, und zwar bevor
irgendetwas geschrieben wird.

Zweitens die **Ehrlichkeit der Meldung**. Sie riet bis 2026-09-09, das Drop-in
„ins Repo aufzunehmen (dann trägt es der Abgleich mit)". Das tut er nicht: die
Quellschleife nimmt nur ``*.service|*.timer|*.socket|*.target|*.path`` und
kopiert einzelne Dateien. Wer dem Rat folgte, legte Dateien an, lief erneut und
bekam denselben Abbruch. Ein Test auf die Meldung ist hier kein Prosa-Test — die
Meldung IST die Schnittstelle zum Operator, und sie war falsch.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "scripts" / "pi_apply_systemd_units.sh"
UNIT = REPO / "deploy" / "systemd" / "kai-paper-trading.service"


def _lauf(src: Path, dst: Path) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    return subprocess.run(  # noqa: S603
        [_BASH, str(INSTALLER), "--dry-run", "--src", str(src), "--dst", str(dst)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
    )


def _quelle(tmp_path: Path) -> Path:
    src = tmp_path / "repo-units"
    src.mkdir()
    (src / "kai-beispiel.service").write_text(
        "[Service]\nExecStart=/bin/true\n", encoding="utf-8", newline="\n"
    )
    return src


# ---------------------------------------------------------------------------
# Die Wirkung: fremde Drop-ins blockieren, bevor etwas geschrieben wird.
# ---------------------------------------------------------------------------


def test_ein_fremdes_dropin_stoppt_den_lauf(tmp_path: Path) -> None:
    """Fail-closed, und zwar bevor irgendetwas angefasst wird."""
    dst = tmp_path / "etc"
    (dst / "kai-beispiel.service.d").mkdir(parents=True)
    (dst / "kai-beispiel.service.d" / "fremd.conf").write_text(
        "[Service]\nExecStart=\nExecStart=/bin/false\n", encoding="utf-8", newline="\n"
    )

    fertig = _lauf(_quelle(tmp_path), dst)

    assert fertig.returncode == 4, fertig.stdout + fertig.stderr
    assert "DROPIN_OVERRIDE" in fertig.stderr
    assert not (dst / "kai-beispiel.service").exists(), "es wurde geschrieben"


def test_ohne_dropin_laeuft_der_abgleich_weiter(tmp_path: Path) -> None:
    """Die Gegenprobe: eine Wache, die immer blockiert, ist keine."""
    dst = tmp_path / "etc"
    dst.mkdir()

    fertig = _lauf(_quelle(tmp_path), dst)

    assert fertig.returncode != 4, fertig.stderr
    assert "DROPIN_OVERRIDE" not in fertig.stderr


def test_ein_harmloses_dropin_ist_kein_grund_zum_abbruch(tmp_path: Path) -> None:
    """Nur Direktiven, die über den geladenen Code entscheiden, zählen.

    ``TimeoutStopSec`` ändert nichts daran, welche Bytes laufen — ein Abbruch
    dafür wäre eine Wache, die den Betrieb behindert, ohne etwas zu schützen.
    """
    dst = tmp_path / "etc"
    (dst / "kai-beispiel.service.d").mkdir(parents=True)
    (dst / "kai-beispiel.service.d" / "harmlos.conf").write_text(
        "[Service]\nTimeoutStopSec=20s\n", encoding="utf-8", newline="\n"
    )

    fertig = _lauf(_quelle(tmp_path), dst)

    assert fertig.returncode != 4, fertig.stderr


# ---------------------------------------------------------------------------
# Die Ehrlichkeit der Meldung.
# ---------------------------------------------------------------------------


def test_die_meldung_verspricht_keinen_repo_dropin_weg(tmp_path: Path) -> None:
    """Sie riet zu etwas, das der Abgleich nicht kann.

    „Ins Repo aufnehmen (dann trägt es der Abgleich mit)" — die Quellschleife
    nimmt aber nur einzelne Unit-Dateien. Wer dem folgte, bekam denselben
    Abbruch und keinen Hinweis, warum.
    """
    dst = tmp_path / "etc"
    (dst / "kai-beispiel.service.d").mkdir(parents=True)
    (dst / "kai-beispiel.service.d" / "fremd.conf").write_text(
        "[Service]\nExecStart=/bin/false\n", encoding="utf-8", newline="\n"
    )

    fertig = _lauf(_quelle(tmp_path), dst)

    assert "traegt es der Abgleich mit" not in fertig.stderr
    assert "hilft NICHT" in fertig.stderr, "der Irrweg muss ausdrücklich benannt sein"


def test_die_meldung_nennt_die_reihenfolge_erst_beweisen_dann_entfernen(
    tmp_path: Path,
) -> None:
    """Zuerst den Inhalt kanonisch ausrollen, dann das Drop-in entfernen.

    Andersherum verschwindet ein Verhalten, dessen Gleichwertigkeit niemand
    gezeigt hat — und niemand merkt es, weil die Unit weiterhin startet.
    """
    dst = tmp_path / "etc"
    (dst / "kai-beispiel.service.d").mkdir(parents=True)
    (dst / "kai-beispiel.service.d" / "fremd.conf").write_text(
        "[Service]\nEnvironment=X=1\n", encoding="utf-8", newline="\n"
    )

    fehler = _lauf(_quelle(tmp_path), dst).stderr

    assert "DANACH" in fehler
    assert "kanonische Unit" in fehler


def test_die_meldung_nennt_unit_datei_und_direktive(tmp_path: Path) -> None:
    """Ohne die Direktive müsste der Operator raten, was das Drop-in tut."""
    dst = tmp_path / "etc"
    (dst / "kai-beispiel.service.d").mkdir(parents=True)
    (dst / "kai-beispiel.service.d" / "fremd.conf").write_text(
        "[Service]\nExecStart=/bin/false\n", encoding="utf-8", newline="\n"
    )

    fehler = _lauf(_quelle(tmp_path), dst).stderr

    assert "kai-beispiel.service" in fehler
    assert "fremd.conf" in fehler
    assert "ExecStart" in fehler


# ---------------------------------------------------------------------------
# Der Canary-Wert gehört in die Unit, nicht daneben.
# ---------------------------------------------------------------------------


def test_das_canary_profil_steht_kanonisch_in_der_unit() -> None:
    """Operator-Entscheidung vom 2026-06-03, bis heute nur in einem Drop-in.

    Damit entschied eine Datei über das Verhalten, die in keinem Byte-Beweis
    vorkam — und der Abgleich verweigerte zu Recht jede weitere
    Unit-Installation, solange sie dort lag.
    """
    text = UNIT.read_text(encoding="utf-8")

    assert "Environment=PAPER_CRON_PROFILE=conservative" in text
    assert "2026-06-03" in text, "die Herkunft der Entscheidung gehört dazu"


def test_die_unit_setzt_das_profil_genau_einmal() -> None:
    """Zwei ``Environment=``-Zeilen für denselben Schlüssel wären eine stille
    Vorrangfrage — und die letzte gewönne, ohne dass es jemand sieht."""
    zeilen = [
        z
        for z in UNIT.read_text(encoding="utf-8").splitlines()
        if z.startswith("Environment=PAPER_CRON_PROFILE=")
    ]

    assert len(zeilen) == 1, zeilen
