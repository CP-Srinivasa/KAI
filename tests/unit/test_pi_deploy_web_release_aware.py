"""Ein HTTP-200 beweist nicht, dass die uebertragene SPA ausgeliefert wird.

Gemessen am 2026-09-04 auf kai-pi5: ``pi_deploy_web.sh`` hat ``web/dist`` im
Checkout aktualisiert, kai-server neu gestartet und gemeldet

    /dashboard/ HTTP 200 -- SPA serving
    Deploy complete.

waehrend weiterhin ``assets/index-DkDllgvZ.js`` ausgeliefert wurde -- ein
Bundle, das zwei Wochen aelter war als der Server, der es auslieferte. Unter dem
Release-Modell laedt kai-server die SPA aus ``current``, nicht aus dem Checkout;
der Restart war folgenlos, und der Statuscode sagte nichts ueber den Inhalt.

Zwei Eigenschaften folgen daraus, und beide werden hier geprueft:

1. Regiert ein Release, wird KEIN Restart ausgeloest und KEIN Erfolg gemeldet.
   Der Lauf endet mit 3 und ``SPA_TRANSFERRED_NOT_ACTIVATED``.
2. Ohne Release wird neu gestartet -- und der Smoke vergleicht das AUSGELIEFERTE
   Bundle mit dem uebertragenen. Weichen sie ab, ist das ein Fehlschlag.

``ssh`` und ``scp`` werden ueber PATH gestubbt: der Test soll die Entscheidungen
des Skripts pruefen, nicht ein Netzwerk. Der Stub protokolliert jeden Aufruf,
damit sich BEWEISEN laesst, dass im Release-Fall kein Restart passiert -- die
Abwesenheit einer Handlung ist hier die eigentliche Zusage.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

NL = chr(10)

SKRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pi_deploy_web.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("tar") is None,
    reason="Shell-Skript-Vertrag (laeuft in CI auf Linux)",
)

BUNDLE_NEU = "assets/index-NEU00000.js"
BUNDLE_ALT = "assets/index-ALT00000.js"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    pfad = bin_dir / name
    pfad.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    pfad.chmod(0o755)


def _welt(tmp: Path, *, release_aktiv: bool, serviert: str) -> tuple[Path, Path, Path]:
    """Repo mit gebautem dist, plus ssh/scp-Stubs mit Aufruf-Protokoll."""
    repo = tmp / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "web" / "dist").mkdir(parents=True)
    (repo / "web" / "dist" / "index.html").write_text(
        f'<script src="/dashboard/{BUNDLE_NEU}"></script>\n', encoding="utf-8"
    )
    shutil.copy2(SKRIPT, repo / "scripts" / "pi_deploy_web.sh")

    log = tmp / "ssh_calls.log"
    bin_dir = tmp / "bin"
    bin_dir.mkdir()

    # Der scp-Stub legt das Tarball wirklich ab: das Skript vergleicht die
    # sha256 vor und nach der Uebertragung, und dieser Vergleich soll echt
    # bleiben -- ihn wegzustubben hiesse, eine Sicherung zu entfernen.
    _stub(
        bin_dir,
        "scp",
        'echo "scp $*" >> "$SSH_LOG"'
        + NL
        + 'for a in "$@"; do [ -f "$a" ] && cp "$a" "$SSH_LOG.tarball"; done'
        + NL
        + "exit 0"
        + NL,
    )
    _stub(
        bin_dir,
        "ssh",
        f'''cmd="$*"
echo "ssh $cmd" >> "$SSH_LOG"
case "$cmd" in
  *"echo ok"*)            echo ok ;;
  *sha256sum*)            sha256sum "$SSH_LOG.tarball" | awk '{{print $1}}' ;;
  *"tar -xzf"*)           echo "  remote dist: 35 files, 3.1M" ;;
  *"grep -oE 'assets/index"*"index.html"*) echo "{BUNDLE_NEU}" ;;
  *readlink*current*)     echo "{"/srv/releases/abc" if release_aktiv else ""}" ;;
  *kai-service-control*)  echo "restarted" ;;
  *http_code*)            echo 200 ;;
  *curl*grep*index*)      echo "{serviert}" ;;
  *curl*health*)          : ;;
  *)                      : ;;
esac
exit 0
''',
    )
    return repo, bin_dir, log


def _lauf(repo: Path, bin_dir: Path, log: Path) -> subprocess.CompletedProcess[str]:
    umgebung = dict(os.environ)
    umgebung["PATH"] = f"{bin_dir}{os.pathsep}{umgebung['PATH']}"
    umgebung["SSH_LOG"] = str(log)
    return subprocess.run(  # noqa: S603
        ["bash", str(repo / "scripts" / "pi_deploy_web.sh"), "ubuntu@pi", "--skip-build"],
        capture_output=True,
        text=True,
        check=False,
        env=umgebung,
        cwd=repo,
    )


def test_bei_aktivem_release_wird_nicht_neu_gestartet(tmp_path: Path) -> None:
    """Die Abwesenheit des Restarts IST die Zusage -- deshalb wird sie belegt."""
    repo, bin_dir, log = _welt(tmp_path, release_aktiv=True, serviert=BUNDLE_ALT)

    ergebnis = _lauf(repo, bin_dir, log)

    assert ergebnis.returncode == 3, ergebnis.stdout + ergebnis.stderr
    assert "SPA_TRANSFERRED_NOT_ACTIVATED" in ergebnis.stderr
    assert "kai-service-control" not in log.read_text(encoding="utf-8"), (
        "es wurde ein Restart ausgeloest, obwohl ein Release regiert — genau der "
        "folgenlose Restart, der den falschen Erfolg erzeugt hat"
    )
    assert "Deploy complete" not in ergebnis.stdout


def test_bei_aktivem_release_wird_der_naechste_schritt_genannt(tmp_path: Path) -> None:
    """Eine Verweigerung ohne Ausweg waere nur eine andere Sackgasse."""
    repo, bin_dir, log = _welt(tmp_path, release_aktiv=True, serviert=BUNDLE_ALT)

    fehler = _lauf(repo, bin_dir, log).stderr

    assert "pi_make_release.sh" in fehler
    assert "--rebuild" in fehler
    assert "pi_activate_release.sh" in fehler


def test_ohne_release_faellt_ein_abweichendes_bundle_durch(tmp_path: Path) -> None:
    """Der Kern: 200 genuegt nicht, das Bundle muss stimmen."""
    repo, bin_dir, log = _welt(tmp_path, release_aktiv=False, serviert=BUNDLE_ALT)

    ergebnis = _lauf(repo, bin_dir, log)

    assert ergebnis.returncode == 2, ergebnis.stdout + ergebnis.stderr
    assert "BUNDLE_MISMATCH" in ergebnis.stderr
    assert "Deploy complete" not in ergebnis.stdout


def test_ohne_release_und_mit_passendem_bundle_ist_der_deploy_fertig(tmp_path: Path) -> None:
    """Gegenprobe -- sonst koennte der Fix darin bestehen, immer zu scheitern."""
    repo, bin_dir, log = _welt(tmp_path, release_aktiv=False, serviert=BUNDLE_NEU)

    ergebnis = _lauf(repo, bin_dir, log)

    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert "Deploy complete" in ergebnis.stdout
    assert "kai-service-control" in log.read_text(encoding="utf-8")
