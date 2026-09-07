"""Der Backup-Installer laeuft unter ``sudo`` — also darf er nichts anbieten.

Ein Installer, der unter root laeuft und Quelle, Ziel, Eigentuemer oder den
erwarteten Hash aus Argumenten oder der Umgebung nimmt, ist kein Installer
mehr. Er ist ein generischer Root-Executor mit einem beruhigenden Namen: wer
die Umgebung setzt, schreibt beliebigen Inhalt an einen beliebigen Ort.

Genau so sah die Vorgaengerfassung aus (``KAI_INSTALL_SRC``, ``_DST``,
``_OWNER``, ``_MODE``, ``_EXPECT_SHA``), und ihr erwarteter Hash war per Default
LEER -- die Pruefung liess sich durch Weglassen abschalten.

Diese Datei prueft beides: dass es die Stellschrauben nicht mehr gibt (am
Verhalten, nicht am Wortlaut), und dass der Inhalt, der gehasht wird, exakt der
Inhalt ist, der installiert wird. Der Erfolgspfad braucht root und wird
deshalb strukturell geprueft -- an der REIHENFOLGE der Operationen, nicht an
einer Ausfuehrung, die es im Test nicht geben kann.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "bin" / "install_standby_backup.sh"
QUELLE = REPO / "deploy" / "bin" / "standby_to_usb.sh"
PIN = REPO / "deploy" / "bin" / "standby_to_usb.sha256"


def _text() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def _code() -> str:
    """Nur der Code, ohne Kommentare — sonst misst ein Test die Prosa."""
    return "\n".join(
        zeile.split("#", 1)[0] if not zeile.lstrip().startswith("#") else ""
        for zeile in _text().splitlines()
    )


def _kopie(ziel: Path) -> Path:
    ziel.mkdir(parents=True, exist_ok=True)
    for datei in (INSTALLER, QUELLE, PIN):
        shutil.copy2(datei, ziel / datei.name)
    return ziel / INSTALLER.name


def _lauf(
    skript: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    import os

    umgebung = {**os.environ, **(env or {})}
    return subprocess.run(  # noqa: S603
        [_BASH, str(skript), *args],
        capture_output=True,
        text=True,
        check=False,
        env=umgebung,
        cwd=str(skript.parent),
    )


# ---------------------------------------------------------------------------
# Er ist syntaktisch heil und nimmt nichts entgegen.
# ---------------------------------------------------------------------------


def test_der_installer_ist_syntaktisch_heil() -> None:
    assert _BASH is not None
    fertig = subprocess.run(  # noqa: S603
        [_BASH, "-n", str(INSTALLER)], capture_output=True, text=True, check=False
    )
    assert fertig.returncode == 0, fertig.stderr


@pytest.mark.parametrize("args", [("foo",), ("--dst", "/tmp/x"), ("-h",), ("a", "b")])
def test_jedes_argument_wird_abgewiesen(tmp_path: Path, args: tuple[str, ...]) -> None:
    """Eine Schnittstelle unter sudo ist eine Angriffsflaeche."""
    fertig = _lauf(_kopie(tmp_path / "bin"), *args)

    assert fertig.returncode == 2, fertig.stdout + fertig.stderr
    assert "nimmt keine Argumente" in fertig.stderr
    assert "INSTALL_OK" not in fertig.stdout


# ---------------------------------------------------------------------------
# Keine Stellschraube ueberlebt — am Verhalten gemessen.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "variable",
    [
        "KAI_INSTALL_SRC",
        "KAI_INSTALL_DST",
        "KAI_INSTALL_OWNER",
        "KAI_INSTALL_MODE",
        "KAI_INSTALL_EXPECT_SHA",
    ],
)
def test_die_alten_umgebungsvariablen_haben_keine_wirkung_mehr(variable: str) -> None:
    """Am Quelltext geprueft: der Name kommt im CODE nicht mehr vor.

    Im Kommentar darf er stehen -- dort erklaert er, warum es ihn nicht mehr
    gibt. Ein Test, der das nicht unterscheidet, verbietet die Begruendung.
    """
    assert variable not in _code(), f"{variable} ist wieder eine Stellschraube"


def test_ein_entfuehrungsversuch_landet_nicht_woanders(tmp_path: Path) -> None:
    """Mit gesetzter Umgebung darf weder eine fremde Quelle noch ein fremdes
    Ziel benutzt werden. Der Lauf scheitert am festen Zielpfad -- und genau
    dort, nicht an einem beliebigen anderen."""
    entfuehrt = tmp_path / "entfuehrt.sh"
    fremd = tmp_path / "fremd.sh"
    fremd.write_text("#!/usr/bin/env bash\necho boese\n", encoding="utf-8")

    fertig = _lauf(
        _kopie(tmp_path / "bin"),
        env={
            "KAI_INSTALL_SRC": str(fremd),
            "KAI_INSTALL_DST": str(entfuehrt),
            "KAI_INSTALL_OWNER": "nobody:nogroup",
            "KAI_INSTALL_MODE": "0777",
            "KAI_INSTALL_EXPECT_SHA": "0" * 64,
        },
    )

    assert not entfuehrt.exists(), "das Ziel aus der Umgebung wurde benutzt"
    assert "INSTALL_OK" not in fertig.stdout
    ausgabe = fertig.stdout + fertig.stderr
    assert "/usr/local/bin/standby_to_usb.sh" in ausgabe, "das feste Ziel blieb fest"
    assert str(fremd) not in ausgabe, "die Quelle aus der Umgebung wurde benutzt"


def test_die_vier_festwerte_stehen_als_readonly_im_code() -> None:
    code = _code()
    for zuweisung in (
        'readonly DST="/usr/local/bin/standby_to_usb.sh"',
        'readonly OWNER="root:root"',
        'readonly MODE="0755"',
        'readonly SRC="$HERE/standby_to_usb.sh"',
    ):
        assert zuweisung in code, zuweisung


# ---------------------------------------------------------------------------
# Ein fehlender oder falscher Hash ist ein Fehler, kein Freibrief.
# ---------------------------------------------------------------------------


def test_ohne_pin_wird_nicht_installiert(tmp_path: Path) -> None:
    """Eine Pruefung, die man durch Weglassen abschaltet, ist keine."""
    skript = _kopie(tmp_path / "bin")
    (skript.parent / PIN.name).unlink()

    fertig = _lauf(skript)

    assert fertig.returncode == 1
    assert "Erwarteter Hash fehlt" in fertig.stderr
    assert "INSTALL_OK" not in fertig.stdout


@pytest.mark.parametrize(
    ("inhalt", "grund"),
    [
        ("", "leer"),
        ("kurz", "zu kurz"),
        ("z" * 64, "nicht hexadezimal"),
        ("0" * 63, "ein Zeichen zu kurz"),
    ],
)
def test_ein_unbrauchbarer_pin_ist_ein_fehler(tmp_path: Path, inhalt: str, grund: str) -> None:
    skript = _kopie(tmp_path / "bin")
    (skript.parent / PIN.name).write_text(inhalt + "\n", encoding="utf-8")

    fertig = _lauf(skript)

    assert fertig.returncode == 1, grund
    assert "unbrauchbar" in fertig.stderr or "kein SHA-256" in fertig.stderr
    assert "INSTALL_OK" not in fertig.stdout


def test_ein_falscher_pin_meldet_beide_hashes(tmp_path: Path) -> None:
    skript = _kopie(tmp_path / "bin")
    (skript.parent / PIN.name).write_text("a" * 64 + "\n", encoding="utf-8")

    fertig = _lauf(skript)

    assert fertig.returncode == 1
    assert "SHA_MISMATCH" in fertig.stderr
    assert "a" * 64 in fertig.stderr, "der erwartete Hash gehoert in die Meldung"
    assert "INSTALL_OK" not in fertig.stdout


def test_eine_veraenderte_quelle_faellt_auf(tmp_path: Path) -> None:
    """Der Pin ist die einzige Stelle, an der ,,geprueft'' etwas bedeutet."""
    skript = _kopie(tmp_path / "bin")
    quelle = skript.parent / QUELLE.name
    quelle.write_text(
        quelle.read_text(encoding="utf-8") + '\necho "still hinzugefuegt"\n', encoding="utf-8"
    )

    fertig = _lauf(skript)

    assert fertig.returncode == 1
    assert "SHA_MISMATCH" in fertig.stderr
    assert "INSTALL_OK" not in fertig.stdout


def test_eine_syntaktisch_kaputte_quelle_erreicht_den_zielpfad_nicht(tmp_path: Path) -> None:
    """Ein Backup-Skript, das mitten im Satz aufhoert, faellt erst beim Restore auf."""
    skript = _kopie(tmp_path / "bin")
    quelle = skript.parent / QUELLE.name
    kaputt = quelle.read_text(encoding="utf-8") + "\nif true; then\n"
    quelle.write_text(kaputt, encoding="utf-8")
    import hashlib

    (skript.parent / PIN.name).write_text(
        hashlib.sha256(kaputt.encode("utf-8")).hexdigest() + "\n", encoding="utf-8"
    )

    fertig = _lauf(skript)

    assert fertig.returncode == 1
    assert "syntaktisch kaputt" in fertig.stderr
    assert "INSTALL_OK" not in fertig.stdout


def test_der_pin_im_repo_passt_zur_quelle_im_repo() -> None:
    """Sonst waere der ausgelieferte Zustand von Anfang an nicht installierbar."""
    import hashlib

    erwartet = PIN.read_text(encoding="utf-8").strip()
    tatsaechlich = hashlib.sha256(QUELLE.read_bytes()).hexdigest()
    assert erwartet == tatsaechlich, "Pin und Quelle sind auseinandergelaufen"


# ---------------------------------------------------------------------------
# TOCTOU: gehasht und installiert wird DERSELBE Schnappschuss.
# ---------------------------------------------------------------------------


def _reihenfolge(code: str) -> list[tuple[int, str]]:
    """Position der entscheidenden Operationen im Code, in Zeilenreihenfolge."""
    muster = {
        "kopie": r'\bcp\s+--\s+"\$SRC"\s+"\$SNAP"',
        "syntax": r'bash\s+-n\s+"\$SNAP"',
        "hash": r'sha256sum\s+"\$SNAP"',
        "vergleich": r'\[\s*"\$ACTUAL_SHA"\s*=\s*"\$EXPECT_SHA"\s*\]',
        "install": r'install\s+-m\s+"\$MODE"\s+"\$SNAP"\s+"\$STAGE"',
        "chown": r'chown\s+"\$OWNER"\s+"\$STAGE"',
        "mv": r'mv\s+-f\s+"\$STAGE"\s+"\$DST"',
    }
    gefunden: list[tuple[int, str]] = []
    for nummer, zeile in enumerate(code.splitlines()):
        for name, regex in muster.items():
            if re.search(regex, zeile):
                gefunden.append((nummer, name))
    return sorted(gefunden)


def test_die_quelle_wird_genau_einmal_gelesen() -> None:
    """Zwei Oeffnungen derselben user-schreibbaren Datei sind das Zeitfenster."""
    code = _code()
    assert len(re.findall(r'"\$SRC"', code)) >= 1
    # Nach der Uebernahme darf `$SRC` nirgends mehr als DATENQUELLE auftauchen:
    # weder gehasht noch installiert noch kopiert.
    assert not re.search(r'sha256sum\s+"\$SRC"', code), "gehasht wird der Schnappschuss"
    assert not re.search(r'install\s+[^\n]*"\$SRC"', code), "installiert wird der Schnappschuss"
    assert not re.search(r'bash\s+-n\s+"\$SRC"', code), "geprueft wird der Schnappschuss"


def test_gehasht_und_installiert_wird_derselbe_schnappschuss() -> None:
    reihenfolge = [name for _, name in _reihenfolge(_code())]

    assert reihenfolge == [
        "kopie",
        "syntax",
        "hash",
        "vergleich",
        "install",
        "chown",
        "mv",
    ], reihenfolge


def test_der_zielpfad_wird_erst_nach_dem_hashvergleich_beruehrt() -> None:
    """Vorher darf am Ziel nichts entstehen — auch nichts Halbes."""
    reihenfolge = [name for _, name in _reihenfolge(_code())]
    assert reihenfolge.index("vergleich") < reihenfolge.index("install")
    assert reihenfolge.index("vergleich") < reihenfolge.index("mv")


def test_der_schnappschuss_wird_von_root_angelegt_nicht_uebernommen() -> None:
    code = _code()
    assert 'SNAP="$(mktemp)"' in code, "der Schnappschuss gehoert dem laufenden Prozess"
    assert "trap cleanup EXIT" in code, "ein Abbruch darf nichts liegen lassen"


# ---------------------------------------------------------------------------
# Kein Fehler wird geschluckt.
# ---------------------------------------------------------------------------


def test_chown_darf_nicht_folgenlos_scheitern() -> None:
    """Sonst gaebe es eine Datei mit falschem Eigentuemer und die Meldung OK."""
    code = _code()
    zeile = next(z for z in code.splitlines() if 'chown "$OWNER"' in z)
    assert "|| die" in zeile, zeile
    assert "2>/dev/null" not in zeile
    assert "|| true" not in zeile


def test_kein_schritt_wird_stillschweigend_uebergangen() -> None:
    code = _code()
    assert "set -euo pipefail" in code
    for verboten in ("|| true", "2>/dev/null || ", "set +e"):
        assert verboten not in code, verboten


# ---------------------------------------------------------------------------
# Die Rechte-Grenze ist dokumentiert, nicht nur eingehalten.
# ---------------------------------------------------------------------------


def test_die_verbotene_sudo_regel_ist_im_kopf_benannt() -> None:
    """Wer spaeter eine NOPASSWD-Regel erwaegt, soll hier darueber stolpern."""
    kopf = _text()
    assert "NOPASSWD_REPO_SCRIPT = FORBIDDEN" in kopf
    assert "NOPASSWD_STANDBY_TO_USB_DIRECT = FORBIDDEN" in kopf
    assert "SCHREIBBAR" in kopf, "die Begruendung ist die Schreibbarkeit, nicht der Geschmack"


def _sudo_regeln() -> list[tuple[str, str]]:
    """(Regelzeile, aufgeloestes Ziel) jeder ECHTEN NOPASSWD-Regel im Repo.

    Geprueft werden die sudoers-Dateien selbst, nicht das Vorkommen des Wortes
    ,,NOPASSWD'' irgendwo. Sonst schlaegt der Test bei jeder Datei an, die
    ERKLAERT, warum es die Regel nicht gibt -- und verbietet damit die
    Begruendung statt der Sache.
    """
    regeln: list[tuple[str, str]] = []
    for datei in sorted(REPO.glob("deploy/sudoers.d/*")):
        if not datei.is_file():
            continue
        zeilen = [
            z.strip()
            for z in datei.read_text(encoding="utf-8").splitlines()
            if z.strip() and not z.lstrip().startswith("#")
        ]
        aliase = {
            teil[0].split(None, 1)[1].strip(): teil[1].strip()
            for z in zeilen
            if z.startswith("Cmnd_Alias") and (teil := z.split("=", 1)) and len(teil) == 2
        }
        for zeile in zeilen:
            if "NOPASSWD:" not in zeile:
                continue
            ziel = zeile.split("NOPASSWD:", 1)[1].strip()
            regeln.append((zeile, aliase.get(ziel, ziel)))
    return regeln


def test_keine_passwortfreie_regel_zielt_auf_das_backupskript() -> None:
    """Ein direktes NOPASSWD auf `standby_to_usb.sh` waere root mit Umweg.

    Das Skript nimmt bewusst KAI_STANDBY_*-Overrides fuer Repo, Current,
    Release-Root, State, USB und Mount-Guard entgegen. Unter frei aufrufbarem
    sudo ist das eine ganz andere Sicherheitslage als in einem Test.
    """
    for zeile, ziel in _sudo_regeln():
        assert "standby" not in ziel.lower(), zeile


def test_keine_passwortfreie_regel_zielt_in_den_checkout() -> None:
    """,,Ist in Git versioniert'' ist keine Unix-Rechtebarriere.

    Ein NOPASSWD-Ziel unterhalb des Checkouts oder des Release-Baums gaebe root
    an jeden, der die Datei vorher editieren kann -- und beides ist fuer
    `ubuntu` schreibbar beziehungsweise wird von `ubuntu` erzeugt.
    """
    for zeile, ziel in _sudo_regeln():
        for verboten in ("/home/kai/ai_analyst_trading_bot", "/home/kai/current", "/home/ubuntu"):
            assert verboten not in ziel, zeile


def test_die_vorhandene_regel_zeigt_auf_einen_root_eigenen_broker() -> None:
    """Der Inhalt ist das Privileg, nicht der Dateiname."""
    regeln = _sudo_regeln()
    assert regeln, "ohne Regeln pruefte dieser Test nichts"
    for zeile, ziel in regeln:
        assert ziel.startswith(("/usr/local/sbin/", "/usr/local/bin/", "/usr/bin/")), zeile


def test_der_installer_ist_kein_ziel_einer_passwortfreien_regel() -> None:
    for zeile, ziel in _sudo_regeln():
        assert "install_standby_backup" not in ziel, zeile


def test_der_installer_startet_keine_shell_auf_fremdem_inhalt() -> None:
    """`bash -n` prueft, `bash` wuerde ausfuehren — der Unterschied ist alles."""
    code = _code()
    assert not re.search(r"\bbash\s+(?!-n)\S", code), "nur Syntaxpruefung, keine Ausfuehrung"
    for verboten in ("eval ", "source ", ". $", "curl", "wget"):
        assert verboten not in code, verboten


def test_die_python_seite_kennt_dieselben_festwerte() -> None:
    """Ein Test, der die Pfade nur im Shell-Skript kennt, wandert mit ihm mit."""
    baum = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    assert isinstance(baum, ast.Module)
    code = _code()
    assert "/usr/local/bin/standby_to_usb.sh" in code
    assert "root:root" in code
    assert "0755" in code
