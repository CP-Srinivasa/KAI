"""Der Backup-Installer soll unter sudo stehen — also darf er nichts anderes können.

Ein Skript, das später eine NOPASSWD-Regel bekommt, ist keine Bequemlichkeit
mehr, sondern eine Rechteerweiterung: **alles, was es kann, kann jeder, der es
aufrufen darf.** Die Vorgängerfassung nahm Quelle, Ziel, Owner und Modus aus der
Umgebung entgegen. Damit hätte

    KAI_INSTALL_SRC=/tmp/meins.sh KAI_INSTALL_DST=/usr/local/bin/sudo \\
    KAI_INSTALL_MODE=4755 sudo ./install_standby_backup.sh

eine beliebige Datei mit beliebigem Modus an einen beliebigen Pfad geschrieben —
ein generischer Root-Executor, nur umständlicher aufgeschrieben. Eine sudo-Regel
darauf hätte root verschenkt.

Diese Datei hält fest, dass genau das nicht mehr geht.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[2] / "deploy" / "bin"
INSTALLER = BIN / "install_standby_backup.sh"
QUELLE = BIN / "standby_to_usb.sh"
HASHDATEI = BIN / "standby_to_usb.sha256"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="Der Installer ist ein POSIX-Shell-Skript (laeuft in CI auf Linux)",
)


def _lauf(*, env: dict[str, str] | None = None, args: list[str] | None = None, cwd: Path = BIN):
    return subprocess.run(  # noqa: S603
        ["bash", str(INSTALLER), *(args or [])],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", **(env or {})},
        check=False,
    )


# --------------------------------------------------------------------------
# Keine Umgebungsvariable darf noch etwas steuern.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["KAI_INSTALL_SRC", "KAI_INSTALL_DST", "KAI_INSTALL_OWNER", "KAI_INSTALL_MODE"],
)
def test_keine_install_variable_wird_noch_ausgewertet(name: str) -> None:
    """Statisch geprüft: die Variable wird nirgends mehr GELESEN.

    Geprüft wird die Parametererweiterung, nicht das blosse Vorkommen — der
    Kopfkommentar nennt diese Namen absichtlich, weil er den Angriff erklärt,
    den sie ermöglicht haben. Ein Test, der schon am Wort scheitert, zwänge
    dazu, die Begründung zu löschen.
    """
    text = INSTALLER.read_text(encoding="utf-8")
    assert f"${{{name}" not in text, f"{name} wird noch ausgewertet"
    assert f"${name}" not in text, f"{name} wird noch ausgewertet"


def test_ziel_owner_und_modus_sind_readonly_literale() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'readonly DST="/usr/local/bin/standby_to_usb.sh"' in text
    assert 'readonly OWNER="root:root"' in text
    assert 'readonly MODE="0755"' in text
    assert 'readonly SRC="$HERE/standby_to_usb.sh"' in text


def test_eine_gesetzte_umgebung_aendert_das_ziel_nicht(tmp_path: Path) -> None:
    """Der Angriff aus dem Docstring — er darf nirgends ankommen.

    Geprüft wird nicht nur der Rückgabewert, sondern dass der untergeschobene
    Pfad gar nicht erst auftaucht: der Lauf scheitert am Hash der echten Quelle
    oder an fehlenden Rechten, aber niemals an `/tmp/meins.sh`.
    """
    boese = tmp_path / "meins.sh"
    boese.write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
    ziel = tmp_path / "gekapert"
    ergebnis = _lauf(
        env={
            "KAI_INSTALL_SRC": str(boese),
            "KAI_INSTALL_DST": str(ziel),
            "KAI_INSTALL_OWNER": "nobody:nogroup",
            "KAI_INSTALL_MODE": "4755",
        }
    )
    assert not ziel.exists(), "der untergeschobene Zielpfad wurde geschrieben"
    assert str(boese) not in ergebnis.stdout + ergebnis.stderr


def test_argumente_werden_abgewiesen() -> None:
    """Eine Schnittstelle unter sudo ist eine Angriffsfläche."""
    ergebnis = _lauf(args=["/usr/local/bin/sudo"])
    assert ergebnis.returncode == 2
    assert "keine Argumente" in ergebnis.stderr


# --------------------------------------------------------------------------
# Hash-Pflicht — ein Installer ohne Hashprüfung installiert, was dasteht.
# --------------------------------------------------------------------------


def test_die_gepinnte_pruefsumme_passt_zur_quelle() -> None:
    """Sonst rottet der Pin still vor sich hin und schützt nichts mehr."""
    erwartet = HASHDATEI.read_text(encoding="utf-8").split()[0]
    assert erwartet == hashlib.sha256(QUELLE.read_bytes()).hexdigest()


def _kopie(tmp_path: Path) -> Path:
    """Ein isoliertes deploy/bin, in dem sich Quelle und Hash verbiegen lassen."""
    ziel = tmp_path / "bin"
    ziel.mkdir()
    for name in ("install_standby_backup.sh", "standby_to_usb.sh", "standby_to_usb.sha256"):
        shutil.copy2(BIN / name, ziel / name)
    return ziel


def _lauf_in(ordner: Path):
    return subprocess.run(  # noqa: S603
        ["bash", str(ordner / "install_standby_backup.sh")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
        check=False,
    )


def test_ein_falscher_hash_verhindert_die_installation(tmp_path: Path) -> None:
    ordner = _kopie(tmp_path)
    (ordner / "standby_to_usb.sha256").write_text("b" * 64 + "  standby_to_usb.sh\n", "utf-8")
    ergebnis = _lauf_in(ordner)
    assert ergebnis.returncode == 1
    assert "SHA_MISMATCH" in ergebnis.stderr


def test_eine_veraenderte_quelle_faellt_ueber_den_hash_auf(tmp_path: Path) -> None:
    ordner = _kopie(tmp_path)
    (ordner / "standby_to_usb.sh").write_text("#!/bin/bash\necho harmlos\n", "utf-8")
    ergebnis = _lauf_in(ordner)
    assert ergebnis.returncode == 1
    assert "SHA_MISMATCH" in ergebnis.stderr


def test_eine_fehlende_hashdatei_ist_ein_fehlschlag(tmp_path: Path) -> None:
    ordner = _kopie(tmp_path)
    (ordner / "standby_to_usb.sha256").unlink()
    ergebnis = _lauf_in(ordner)
    assert ergebnis.returncode == 1
    assert "Erwarteter Hash fehlt" in ergebnis.stderr


def test_eine_unbrauchbare_hashdatei_ist_ein_fehlschlag(tmp_path: Path) -> None:
    ordner = _kopie(tmp_path)
    (ordner / "standby_to_usb.sha256").write_text("zu kurz\n", "utf-8")
    ergebnis = _lauf_in(ordner)
    assert ergebnis.returncode == 1
    assert "unbrauchbar" in ergebnis.stderr


def test_eine_fehlende_quelle_ist_ein_fehlschlag(tmp_path: Path) -> None:
    ordner = _kopie(tmp_path)
    (ordner / "standby_to_usb.sh").unlink()
    ergebnis = _lauf_in(ordner)
    assert ergebnis.returncode == 1
    assert "Quelle fehlt" in ergebnis.stderr


def test_eine_syntaktisch_kaputte_quelle_wird_nicht_installiert(tmp_path: Path) -> None:
    """Ein Backup-Skript, das mitten im Satz aufhört, fällt erst beim Restore auf."""
    ordner = _kopie(tmp_path)
    kaputt = "#!/usr/bin/env bash\nif [ 1 -eq 1 ]; then\n  echo unvollstaendig\n"
    (ordner / "standby_to_usb.sh").write_text(kaputt, encoding="utf-8")
    (ordner / "standby_to_usb.sha256").write_text(
        hashlib.sha256(kaputt.encode()).hexdigest() + "  standby_to_usb.sh\n", encoding="utf-8"
    )
    ergebnis = _lauf_in(ordner)
    assert ergebnis.returncode == 1
    assert "syntaktisch kaputt" in ergebnis.stderr


# --------------------------------------------------------------------------
# Positivkontrolle — so weit sie ohne root gehen kann.
# --------------------------------------------------------------------------


def test_ein_gueltiges_artefakt_kommt_bis_zum_privilegierten_schritt() -> None:
    """Alle Prüfungen bestehen; erst root fehlt.

    Weiter kann ein Test nicht kommen, ohne selbst nach /usr/local/bin zu
    schreiben. Entscheidend ist, dass der Lauf NICHT an einer Validierung
    scheitert — sonst wäre jeder Negativtest oben wertlos, weil das Skript
    ohnehin immer fehlschlägt.
    """
    ergebnis = _lauf()
    ausgabe = ergebnis.stdout + ergebnis.stderr
    for validierung in ("SHA_MISMATCH", "Quelle fehlt", "Erwarteter Hash", "syntaktisch kaputt"):
        assert validierung not in ausgabe, ausgabe
    if ergebnis.returncode == 0:
        assert "INSTALL_OK" in ergebnis.stdout
    else:
        # Gescheitert ist er dann am PRIVILEGIERTEN Schritt — je nach System
        # fehlen Rechte oder /usr/local/bin gibt es gar nicht. Beides ist derselbe
        # Punkt in der Kette; die Fehlermeldung nennt ihn, nicht das Betriebssystem.
        assert any(w in ausgabe for w in ("install nach", "chown", "mv nach")), ausgabe


def test_beide_skripte_sind_syntaktisch_gueltig() -> None:
    for pfad in (INSTALLER, QUELLE):
        ergebnis = subprocess.run(  # noqa: S603
            ["bash", "-n", str(pfad)], capture_output=True, text=True, check=False
        )
        assert ergebnis.returncode == 0, f"{pfad.name}: {ergebnis.stderr}"
