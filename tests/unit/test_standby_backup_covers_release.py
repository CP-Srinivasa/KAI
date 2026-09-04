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

import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

SKRIPT = Path(__file__).resolve().parents[2] / "deploy" / "bin" / "standby_to_usb.sh"
SHA = "a" * 40

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("tar") is None,
    reason="Der Backup-Vertrag ist ein POSIX-Shell-Skript (laeuft in CI auf Linux)",
)


def _welt(tmp: Path, *, release_sha: str = SHA, marker_sha: str | None = None) -> dict[str, Path]:
    """Ein vollständiger, gültiger Ausgangszustand — Checkout, Release, Marker."""
    repo = tmp / "ai_analyst_trading_bot"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "main.py").write_text("print('checkout')\n", encoding="utf-8")
    (repo / "artifacts" / "runtime").mkdir(parents=True)

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
    treffer = sorted(usb.glob(f"{praefix}_*.tar.gz"))
    assert treffer, f"kein {praefix}-Archiv erzeugt"
    return treffer[-1]


# --------------------------------------------------------------------------
# Positivkontrolle — sonst prüfen die Negativtests nur, dass etwas kaputt ist.
# --------------------------------------------------------------------------


def test_gueltiger_zustand_sichert_checkout_release_venv_und_marker(tmp_path: Path) -> None:
    welt = _welt(tmp_path)
    ergebnis = _lauf(welt)
    assert ergebnis.returncode == 0, ergebnis.stderr

    inhalt = tarfile.open(_archiv(welt["usb"], "release")).getnames()
    assert any(n.endswith("release.json") for n in inhalt), "RELEASE_JSON_COVERED"
    assert any("/.venv/" in n or n.endswith("/.venv") for n in inhalt), "RELEASE_VENV_COVERED"
    assert any("/app/" in n or n.endswith("/app") for n in inhalt), "ACTIVE_RELEASE_COVERED"

    marker = tarfile.open(_archiv(welt["usb"], "deploymarker")).getnames()
    assert any(n.endswith("deployment_marker.json") for n in marker), "DEPLOYMENT_MARKER_COVERED"

    # Der Checkout bleibt erhalten — nicht ersetzt, sondern zusätzlich.
    system = tarfile.open(_archiv(welt["usb"], "system")).getnames()
    assert any(n.endswith("app/main.py") for n in system), "CHECKOUT_COVERED"


def test_der_datentier_bleibt_unveraendert(tmp_path: Path) -> None:
    """Die Härtung darf den bestehenden 6-Stunden-Lauf nicht anfassen."""
    welt = _welt(tmp_path)
    (welt["repo"] / "data").mkdir()
    (welt["repo"] / "data" / "x.jsonl").write_text("{}\n", encoding="utf-8")
    ergebnis = _lauf(welt, modus="data")
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert any(
        n.endswith("data/x.jsonl") for n in tarfile.open(_archiv(welt["usb"], "data")).getnames()
    )


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
    assert not list(welt["usb"].glob("*.tar.gz")), "kein Archiv bei blockiertem Guard"


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
