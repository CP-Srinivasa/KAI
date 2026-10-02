"""Der Backup-Vertrag aus dem Cutover-Runbook, als ausführbare Prüfung.

Das Gate, gegen das diese Datei steht: seit dem Release-Modell (#848) laufen
zwei produktive Code-Welten nebeneinander — der Quell-Checkout und
``current -> releases/<SHA>``. Ein System-Tier, das weiterhin nur den Checkout
sichert, ist unvollständig, und das Gefährliche daran ist nicht die Lücke,
sondern dass so ein Lauf **grün meldet**.

Deshalb prüft jeder Negativtest hier dasselbe: dass der Lauf **fehlschlägt**,
statt zu überspringen. ``FALSE_GREEN_ON_MISSING_ACTIVE_RELEASE = IMPOSSIBLE``
ist die eigentliche Anforderung; ein ``[ -d "$X" ] || continue`` erfüllte jede
COVERED-Zeile und verletzte den Vertrag trotzdem.

Läuft überall, wo eine POSIX-Shell mit ``tar`` verfügbar ist — auf dem
Linux-CI-Runner also immer.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

SKRIPT = Path(__file__).resolve().parents[2] / "deploy" / "bin" / "standby_to_usb.sh"
SHA = "a" * 40
#: Mindestens 32 Zeichen wie in Produktion; GEHEIM steht in .env und Artefakten.
PASSPHRASE = "standby-test-passphrase-" + "x" * 16
GEHEIM = "GEHEIMNIS-aus-der-env-4711"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("tar") is None or shutil.which("openssl") is None,
    reason="Der Backup-Vertrag ist ein POSIX-Shell-Skript (laeuft in CI auf Linux)",
)


def _welt(tmp: Path, *, release_sha: str = SHA, marker_sha: str | None = None) -> dict[str, Path]:
    """Ein vollständiger, gültiger Ausgangszustand — Checkout, Release, Marker."""
    repo = tmp / "ai_analyst_trading_bot"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "main.py").write_text("print('checkout')\n", encoding="utf-8")
    (repo / "artifacts" / "runtime").mkdir(parents=True)
    (repo / ".env").write_text(
        f"KAI_BACKUP_PASSPHRASE={PASSPHRASE}\nAPI_TOKEN={GEHEIM}\n", encoding="utf-8"
    )
    (repo / "data").mkdir()
    (repo / "data" / "x.jsonl").write_text("{}\n", encoding="utf-8")
    (repo / "artifacts" / "api_request_audit.jsonl").write_text(
        json.dumps({"client_ip": GEHEIM}) + "\n", encoding="utf-8"
    )

    releases = tmp / "releases"
    release = releases / release_sha
    (release / "app").mkdir(parents=True)
    (release / "app" / "main.py").write_text("print('release')\n", encoding="utf-8")
    (release / ".venv" / "bin").mkdir(parents=True)
    (release / ".venv" / "bin" / "python3").write_text("#!/bin/sh\n", encoding="utf-8")
    (release / "requirements.lock").write_text("pkg==1.0\n", encoding="utf-8")
    (release / "release.json").write_text(
        json.dumps(
            {
                "schema": "kai_release/v1",
                "repo_sha": release_sha,
                "release_path": str(release),
                "release_tree_sha256": "t" * 64,
                "requirements_lock_sha256": "l" * 64,
                "python_version": "3.12.0",
                "created_at_utc": "2026-09-04T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    (repo / "artifacts" / "runtime" / "deployment_marker.json").write_text(
        json.dumps(
            {
                "schema": "deployment_marker/v1",
                "repo_sha": marker_sha if marker_sha is not None else release_sha,
                "release_path": str(release),
                "release_tree_sha256": "t" * 64,
                "requirements_lock_sha256": "l" * 64,
                "deployed_at_utc": "2026-09-04T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    current = tmp / "current"
    try:
        current.symlink_to(release, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink-Recht fehlt — der Release-Pfad ist so nicht nachstellbar")

    usb = tmp / "usb"
    usb.mkdir()
    return {"repo": repo, "releases": releases, "release": release, "current": current, "usb": usb}


def _lauf(welt: dict[str, Path], *, modus: str = "system") -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(SKRIPT), modus],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "KAI_STANDBY_REPO": str(welt["repo"]),
            "KAI_STANDBY_CURRENT": str(welt["current"]),
            "KAI_STANDBY_RELEASES_ROOT": str(welt["releases"]),
            "KAI_STANDBY_USB": str(welt["usb"]),
            "KAI_STANDBY_MOUNT_GUARD": "",  # kein USB im Test
        },
        check=False,
    )


def _archiv(usb: Path, praefix: str) -> Path:
    treffer = sorted(usb.glob(f"{praefix}_*.tar.gz.enc"))
    assert treffer, f"kein verschluesseltes {praefix}-Archiv erzeugt"
    return treffer[-1]


def _entschluesseln(pfad: Path, passphrase: str = PASSPHRASE) -> bytes:
    """Unabhaengig von openssl: genau das dokumentierte Format.

    ``openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000``: ``Salted__`` + 8 Byte
    Salz, Schluessel und IV per PBKDF2-HMAC-SHA256 (48 Byte), PKCS#7. Dasselbe
    Format wie Vault und Pi-Tagesarchive -- mit derselben Passphrase lesbar.
    """
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    roh = pfad.read_bytes()
    assert roh[:8] == b"Salted__", f"{pfad.name}: kein openssl-enc-Format"
    material = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), roh[8:16], 200_000, dklen=48)
    entschl = Cipher(algorithms.AES(material[:32]), modes.CBC(material[32:])).decryptor()
    gepolstert = entschl.update(roh[16:]) + entschl.finalize()
    entpolster = padding.PKCS7(128).unpadder()
    return entpolster.update(gepolstert) + entpolster.finalize()


def _namen(pfad: Path) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(_entschluesseln(pfad)), mode="r:gz") as tar:
        return tar.getnames()


# --------------------------------------------------------------------------
# Positivkontrolle — sonst prüfen die Negativtests nur, dass etwas kaputt ist.
# --------------------------------------------------------------------------


def test_gueltiger_zustand_sichert_checkout_release_venv_und_marker(tmp_path: Path) -> None:
    welt = _welt(tmp_path)
    ergebnis = _lauf(welt)
    assert ergebnis.returncode == 0, ergebnis.stderr

    inhalt = _namen(_archiv(welt["usb"], "release"))
    assert any(n.endswith("release.json") for n in inhalt), "RELEASE_JSON_COVERED"
    assert any("/.venv/" in n or n.endswith("/.venv") for n in inhalt), "RELEASE_VENV_COVERED"
    assert any("/app/" in n or n.endswith("/app") for n in inhalt), "ACTIVE_RELEASE_COVERED"

    marker = _namen(_archiv(welt["usb"], "deploymarker"))
    assert any(n.endswith("deployment_marker.json") for n in marker), "DEPLOYMENT_MARKER_COVERED"

    # Der Checkout bleibt erhalten — nicht ersetzt, sondern zusätzlich.
    system = _namen(_archiv(welt["usb"], "system"))
    assert any(n.endswith("app/main.py") for n in system), "CHECKOUT_COVERED"


def test_der_datentier_bleibt_unveraendert(tmp_path: Path) -> None:
    """Die Härtung darf den bestehenden 6-Stunden-Lauf nicht anfassen."""
    welt = _welt(tmp_path)
    ergebnis = _lauf(welt, modus="data")
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert any(n.endswith("data/x.jsonl") for n in _namen(_archiv(welt["usb"], "data")))


# --------------------------------------------------------------------------
# Negativkontrollen — jede MUSS fehlschlagen, nicht überspringen.
# --------------------------------------------------------------------------


def _erwarte_fail(ergebnis: subprocess.CompletedProcess[str], grund: str) -> None:
    assert ergebnis.returncode != 0, f"grüner Lauf trotz {grund} — genau das ist das falsche Grün"
    assert grund in ergebnis.stderr, ergebnis.stderr


def test_fehlendes_current_ist_backup_fail(tmp_path: Path) -> None:
    welt = _welt(tmp_path)
    welt["current"].unlink()
    _erwarte_fail(_lauf(welt), "ACTIVE_RELEASE_MISSING")


def test_dangling_current_ist_backup_fail(tmp_path: Path) -> None:
    welt = _welt(tmp_path)
    shutil.rmtree(welt["release"])
    _erwarte_fail(_lauf(welt), "ACTIVE_RELEASE_DANGLING")


def test_current_ausserhalb_des_release_roots_ist_backup_fail(tmp_path: Path) -> None:
    """Sonst hielte das Backup irgendein Verzeichnis für den laufenden Code."""
    welt = _welt(tmp_path)
    fremd = tmp_path / "woanders"
    (fremd / "app").mkdir(parents=True)
    (fremd / "release.json").write_text('{"repo_sha": "' + SHA + '"}', encoding="utf-8")
    (fremd / ".venv").mkdir()
    welt["current"].unlink()
    welt["current"].symlink_to(fremd, target_is_directory=True)
    _erwarte_fail(_lauf(welt), "ACTIVE_RELEASE_OUTSIDE_ROOT")


def test_fehlende_release_json_ist_backup_fail(tmp_path: Path) -> None:
    welt = _welt(tmp_path)
    (welt["release"] / "release.json").unlink()
    _erwarte_fail(_lauf(welt), "RELEASE_JSON_MISSING")


def test_fehlendes_venv_ist_backup_fail(tmp_path: Path) -> None:
    """Ohne .venv ist der Baum Quelltext, kein lauffähiger Stand."""
    welt = _welt(tmp_path)
    shutil.rmtree(welt["release"] / ".venv")
    _erwarte_fail(_lauf(welt), "VENV_MISSING")


def test_fehlender_deployment_marker_ist_backup_fail(tmp_path: Path) -> None:
    welt = _welt(tmp_path)
    (welt["repo"] / "artifacts" / "runtime" / "deployment_marker.json").unlink()
    _erwarte_fail(_lauf(welt), "DEPLOYMENT_MARKER_MISSING")


def test_marker_zeigt_auf_ein_anderes_release_als_current(tmp_path: Path) -> None:
    """Deploy-Marker und aktives Release müssen dieselbe Revision meinen."""
    welt = _welt(tmp_path, marker_sha="b" * 40)
    _erwarte_fail(_lauf(welt), "MARKER_RELEASE_MISMATCH")


def test_ein_archiv_ohne_release_inhalt_ist_backup_fail(tmp_path: Path) -> None:
    """Die Kernvariante des falschen Grüns: tar lief, packte aber nicht ein.

    Nachgestellt über ein `app/`-loses Release — das Inventar muss anschlagen,
    obwohl `tar` selbst mit 0 zurückkommt.
    """
    welt = _welt(tmp_path)
    shutil.rmtree(welt["release"] / "app")
    _erwarte_fail(_lauf(welt), "ARCHIVE_MISSING_REQUIRED_RELEASE_CONTENT")


def test_ein_unlesbarer_marker_ist_backup_fail(tmp_path: Path) -> None:
    welt = _welt(tmp_path)
    (welt["repo"] / "artifacts" / "runtime" / "deployment_marker.json").write_text(
        "{}", encoding="utf-8"
    )
    _erwarte_fail(_lauf(welt), "DEPLOYMENT_MARKER_UNREADABLE")


# --------------------------------------------------------------------------
# Die Quelle gehört ins Repo, nicht auf den Pi.
# --------------------------------------------------------------------------


def test_der_mount_guard_laesst_sich_nur_ausdruecklich_abschalten() -> None:
    """`${VAR-default}`, nicht `${VAR:-default}` — der Unterschied ist der Fehler.

    Mit `:-` greift der Vorgabewert auch bei einem AUSDRUECKLICH leer gesetzten
    Wert. Der Guard liesse sich dann gar nicht abschalten, und ein Test, der ihn
    abschalten will, liefe gegen `/mnt/kai-data`: auf dem CI-Runner ist das
    nicht gemountet und alles schlaegt fehl, auf dem Pi IST es gemountet und der
    Guard besteht aus dem falschen Grund. Genau so ist es passiert.
    """
    text = SKRIPT.read_text(encoding="utf-8")
    assert "${KAI_STANDBY_MOUNT_GUARD-" in text
    assert "${KAI_STANDBY_MOUNT_GUARD:-" not in text


def test_ein_nicht_gemounteter_guard_pfad_bricht_ab(tmp_path: Path) -> None:
    """Die Gegenprobe: gesetzt und kein Mountpoint => Abbruch, kein Backup."""
    welt = _welt(tmp_path)
    ergebnis = subprocess.run(  # noqa: S603
        ["bash", str(SKRIPT), "system"],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "KAI_STANDBY_REPO": str(welt["repo"]),
            "KAI_STANDBY_CURRENT": str(welt["current"]),
            "KAI_STANDBY_RELEASES_ROOT": str(welt["releases"]),
            "KAI_STANDBY_USB": str(welt["usb"]),
            "KAI_STANDBY_MOUNT_GUARD": str(tmp_path / "kein-mountpoint"),
        },
        check=False,
    )
    assert ergebnis.returncode != 0
    assert "not mounted" in ergebnis.stderr
    assert not list(welt["usb"].glob("*.tar.gz*")), "kein Archiv bei blockiertem Guard"


def test_die_kanonische_fassung_liegt_im_repository() -> None:
    assert SKRIPT.is_file()
    text = SKRIPT.read_text(encoding="utf-8")
    assert "FALSE_GREEN" in text, "der Vertrag muss im Skript benannt sein"
    assert "kanonische Fassung" in text


def test_der_installationspfad_ist_eng_gefasst() -> None:
    """Kein generischer Editor, keine Shell, kein `cp *` — genau eine Datei."""
    installer = SKRIPT.parent / "install_standby_backup.sh"
    assert installer.is_file()
    text = installer.read_text(encoding="utf-8")
    assert "/usr/local/bin/standby_to_usb.sh" in text
    assert "sha256sum" in text, "der Installer muss den erwarteten Hash pruefen koennen"
    assert "bash -n" in text, "eine syntaktisch kaputte Quelle darf nicht installiert werden"


def test_beide_skripte_sind_syntaktisch_gueltig() -> None:
    for pfad in (SKRIPT, SKRIPT.parent / "install_standby_backup.sh"):
        ergebnis = subprocess.run(  # noqa: S603
            ["bash", "-n", str(pfad)], capture_output=True, text=True, check=False
        )
        assert ergebnis.returncode == 0, f"{pfad.name}: {ergebnis.stderr}"


# ---------------------------------------------------------------------------
# Der Fall, den diese Datei bis 2026-09-07 NICHT sehen konnte.
#
# Alle Fixtures oben bauen Mini-Archive. Darin ist ``tar`` fertig, bevor
# ``grep -q`` beim ersten Treffer aussteigt -- die Pipe wird nie unter ``tar``
# geschlossen, es gibt kein SIGPIPE, und die Pruefung sah gesund aus.
#
# Auf kai-pi5 mit 24.817 Eintraegen sah sie es anders: ``grep -q`` stieg nach
# wenigen Zeilen aus, ``tar`` bekam SIGPIPE und endete mit 141, und
# ``set -o pipefail`` machte daraus den Status der Pipeline. Ein GEFUNDENER
# Eintrag las sich als fehlender:
#
#     BACKUP_FAIL: ARCHIVE_MISSING_REQUIRED_RELEASE_CONTENT (release.json)
#
# Seit dem 2026-08-31 entstand deshalb kein System-Backup mehr.
#
# Der Test erzwingt den Fall deterministisch statt ihn nachzustellen: viele
# Dateien, und der gesuchte Eintrag liegt vorne. Je frueher der Treffer, desto
# sicherer schlaegt die kaputte Fassung fehl.
# ---------------------------------------------------------------------------

#: Genug Eintraege, dass `tar` beim Aussteigen von `grep` noch schreibt. 4000
#: reichen auf jedem Runner; das echte Archiv hatte 24.817.
_VIELE = 4000


def test_ein_grosses_release_archiv_wird_nicht_faelschlich_als_leer_gemeldet(
    tmp_path: Path,
) -> None:
    """Die Inventar-Pruefung darf nicht davon abhaengen, WANN der Treffer kommt."""
    welt = _welt(tmp_path)
    fueller = welt["release"] / ".venv" / "lib"
    fueller.mkdir(parents=True, exist_ok=True)
    for i in range(_VIELE):
        (fueller / f"m{i:05d}.py").write_text("x\n", encoding="utf-8")

    ergebnis = _lauf(welt)

    assert ergebnis.returncode == 0, (
        "ein vollstaendiges, nur grosses Archiv wurde als unvollstaendig gemeldet:\n"
        f"{ergebnis.stdout}\n{ergebnis.stderr}"
    )
    assert "ARCHIVE_MISSING_REQUIRED_RELEASE_CONTENT" not in ergebnis.stderr

    # Und der Beweis, dass die Pruefung nicht einfach uebersprungen wurde:
    # das Archiv enthaelt wirklich, was sie behauptet.
    namen = _namen(_archiv(welt["usb"], "release"))
    assert any(n.endswith("release.json") for n in namen)
    assert sum(1 for n in namen if "/.venv/" in n or n.startswith("./.venv")) > _VIELE


def test_die_pruefung_meldet_einen_echt_fehlenden_eintrag_auch_im_grossen_archiv(
    tmp_path: Path,
) -> None:
    """Die Gegenprobe: gross UND unvollstaendig muss weiterhin durchfallen.

    Ohne sie koennte man den Fehler oben auch dadurch 'beheben', dass die
    Pruefung gar nichts mehr prueft.
    """
    welt = _welt(tmp_path)
    fueller = welt["release"] / ".venv" / "lib"
    fueller.mkdir(parents=True, exist_ok=True)
    for i in range(_VIELE):
        (fueller / f"m{i:05d}.py").write_text("x\n", encoding="utf-8")
    (welt["release"] / "release.json").unlink()

    ergebnis = _lauf(welt)

    assert ergebnis.returncode != 0
    assert "RELEASE_JSON_MISSING" in ergebnis.stderr or (
        "ARCHIVE_MISSING_REQUIRED_RELEASE_CONTENT" in ergebnis.stderr
    )


# ---------------------------------------------------------------------------
# Verschluesselung (Operator 2026-10-02): auf dem Stick liegt kein Klartext mehr.
#
# Vorher lagen .env, Telegram-Sitzung und IP-haltige Zugriffsprotokolle offen
# auf dem USB-Stick, die Datenschutzseite sagt "verschluesselte Sicherungen".
# ---------------------------------------------------------------------------


def _beide_stufen(welt: dict[str, Path]) -> None:
    for modus in ("system", "data"):
        ergebnis = _lauf(welt, modus=modus)
        assert ergebnis.returncode == 0, f"{modus}: {ergebnis.stderr}"


def test_auf_dem_stick_liegt_kein_klartext(tmp_path: Path) -> None:
    welt = _welt(tmp_path)
    _beide_stufen(welt)

    dateien = [p for p in welt["usb"].iterdir() if p.is_file()]
    assert not [p.name for p in dateien if p.name.endswith((".tar.gz", ".part"))]
    for datei in dateien:
        inhalt = datei.read_bytes()
        assert GEHEIM.encode() not in inhalt, f"{datei.name} traegt ein Geheimnis im Klartext"
        assert PASSPHRASE.encode() not in inhalt, f"{datei.name} traegt die Passphrase"

    # Gesichert ist es trotzdem -- verschluesselt, mit der Passphrase lesbar.
    roh = _entschluesseln(_archiv(welt["usb"], "system"))
    with tarfile.open(fileobj=io.BytesIO(roh), mode="r:gz") as tar:
        env = tar.extractfile("./.env")
        assert env is not None and GEHEIM.encode() in env.read()
    roh = _entschluesseln(_archiv(welt["usb"], "data"))
    with tarfile.open(fileobj=io.BytesIO(roh), mode="r:gz") as tar:
        audit = tar.extractfile("artifacts/api_request_audit.jsonl")
        assert audit is not None and GEHEIM.encode() in audit.read()


@pytest.mark.parametrize("env", ["ANDERES=1\n", "KAI_BACKUP_PASSPHRASE=zu-kurz\n", None])
def test_ohne_passphrase_wird_nichts_geschrieben(tmp_path: Path, env: str | None) -> None:
    welt = _welt(tmp_path)
    if env is None:
        (welt["repo"] / ".env").unlink()
    else:
        (welt["repo"] / ".env").write_text(env, encoding="utf-8")
    for modus in ("system", "data"):
        _erwarte_fail(_lauf(welt, modus=modus), "PASSPHRASE_MISSING")
    assert not [p.name for p in welt["usb"].iterdir() if ".tar.gz" in p.name]


def test_die_env_wird_gelesen_nicht_ausgefuehrt(tmp_path: Path) -> None:
    """Das Skript laeuft als root, die .env gehoert ``ubuntu``: sourcen = Root-Codeausfuehrung."""
    welt = _welt(tmp_path)
    falle = tmp_path / "ausgefuehrt"
    (welt["repo"] / ".env").write_text(
        f"X=$(touch {falle})\nY=`touch {falle}`\nKAI_BACKUP_PASSPHRASE={PASSPHRASE}\n",
        encoding="utf-8",
    )
    ergebnis = _lauf(welt, modus="data")
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert not falle.exists(), "eine Zeile der .env wurde als Shell ausgefuehrt"


@pytest.mark.parametrize(
    "zeile",
    [
        f'KAI_BACKUP_PASSPHRASE="{PASSPHRASE}"',
        f"KAI_BACKUP_PASSPHRASE='{PASSPHRASE}'",
        f"export KAI_BACKUP_PASSPHRASE={PASSPHRASE}",
        f"KAI_BACKUP_PASSPHRASE={PASSPHRASE}\r",
    ],
)
def test_die_passphrase_gilt_wie_beim_vault(tmp_path: Path, zeile: str) -> None:
    """Anfuehrungszeichen, ``export`` und CRLF aendern die Passphrase nicht."""
    welt = _welt(tmp_path)
    (welt["repo"] / ".env").write_text(zeile + "\n", encoding="utf-8", newline="")
    ergebnis = _lauf(welt, modus="data")
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert any(n.endswith("data/x.jsonl") for n in _namen(_archiv(welt["usb"], "data")))


def test_jeder_satz_hat_eine_passende_pruefsumme(tmp_path: Path) -> None:
    welt = _welt(tmp_path)
    _beide_stufen(welt)
    saetze = sorted(welt["usb"].glob("*.tar.gz.enc"))
    assert {p.name.split("_")[0] for p in saetze} >= {"system", "release", "deploymarker", "data"}
    for satz in saetze:
        zeile = (satz.parent / f"{satz.name}.sha256").read_text(encoding="utf-8").split()
        assert zeile == [hashlib.sha256(satz.read_bytes()).hexdigest(), satz.name]


def test_alte_klartext_saetze_verschwinden_erst_nach_erfolg(tmp_path: Path) -> None:
    welt = _welt(tmp_path)
    alt = welt["usb"] / "data_20260101T000000Z.tar.gz"
    halb = welt["usb"] / "data_20260101T060000Z.tar.gz.part"
    fremd = welt["usb"] / "system_20260101T000000Z.tar.gz"
    for datei in (alt, halb, fremd):
        datei.write_bytes(b"klartext")
    env = welt["repo"] / ".env"
    richtig = env.read_text(encoding="utf-8")

    env.write_text("NICHTS=1\n", encoding="utf-8")
    assert _lauf(welt, modus="data").returncode != 0
    assert alt.exists() and halb.exists(), "ohne neuen verschluesselten Satz bleibt der alte"

    env.write_text(richtig, encoding="utf-8")
    assert _lauf(welt, modus="data").returncode == 0
    assert not alt.exists() and not halb.exists()
    assert fremd.exists(), "die Datenstufe raeumt nur ihre eigenen Saetze"


def test_die_datenstufe_behaelt_28_verschluesselte_saetze(tmp_path: Path) -> None:
    welt = _welt(tmp_path)
    for i in range(30):
        alt = welt["usb"] / f"data_202601{i + 1:02d}T000000Z.tar.gz.enc"
        alt.write_bytes(b"x")
        (welt["usb"] / f"{alt.name}.sha256").write_text(f"x  {alt.name}\n", encoding="utf-8")
        os.utime(alt, (1_700_000_000 + i, 1_700_000_000 + i))
    assert _lauf(welt, modus="data").returncode == 0
    assert len(list(welt["usb"].glob("data_*.tar.gz.enc"))) == 28
    assert len(list(welt["usb"].glob("data_*.tar.gz.enc.sha256"))) == 28
    assert not (welt["usb"] / "data_20260101T000000Z.tar.gz.enc").exists(), "aeltester weg"
    assert (welt["usb"] / "data_20260130T000000Z.tar.gz.enc").exists()


def test_die_anleitung_kommt_bei_jedem_lauf_auf_den_stick(tmp_path: Path) -> None:
    welt = _welt(tmp_path)
    ordner = welt["repo"] / "deploy" / "standby"
    ordner.mkdir(parents=True)
    (ordner / "RESTORE_FROM_USB.md").write_text("# Anleitung\n", encoding="utf-8")
    assert _lauf(welt, modus="data").returncode == 0
    assert (welt["usb"] / "RESTORE_FROM_USB.md").read_text(encoding="utf-8") == "# Anleitung\n"


def test_die_anleitung_prueft_entschluesselt_und_loescht_vor_dem_start() -> None:
    """Wiederherstellung: erst Pruefsummen, dann entschluesseln, Loeschregeln VOR dem Start."""
    text = (SKRIPT.parents[1] / "standby" / "RESTORE_FROM_USB.md").read_text(encoding="utf-8")
    assert "openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000" in text
    pruefen = text.index("sha256sum -c")
    loeschen = text.index("scripts/audit_rotate.py --apply")
    starten = text.index("systemctl enable --now")
    assert pruefen < loeschen < starten
