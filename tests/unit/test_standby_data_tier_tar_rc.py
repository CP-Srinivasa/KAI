"""Datentier des Standby-Backups: tar ueber einen LEBENDEN Baum.

Der laufende Bot haengt waehrend des Lesens an JSONL-Dateien an; GNU tar meldet
das mit rc=1 ("file changed as we read it"). Das Archiv ist dann
crash-konsistent und wird akzeptiert — rc>=2 ist ein echter Fehler und darf
kein fertiges Archiv hinterlassen.

Bis 2026-09-26 pruefte ``test_standby_manifest.py`` das an
``scripts/standby_to_usb.sh`` — einer seit dem 04.09. abgehaengten Zweitfassung,
die nie mehr ausgerollt wurde. Live laeuft die gepinnte Fassung aus
``deploy/bin/`` (Installation ueber ``install_standby_backup.sh``); deren
``standby_manifest/v1``-Sidecar gibt es dort nicht (seit 07.09. live nicht mehr
geschrieben, kein Leser). Dieser Test haelt die tar-rc-Regel an der Fassung
fest, die wirklich laeuft.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SKRIPT = Path(__file__).resolve().parents[2] / "deploy" / "bin" / "standby_to_usb.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="Der Backup-Vertrag ist ein POSIX-Shell-Skript (laeuft in CI auf Linux)",
)

_FAKE_TAR = """#!/usr/bin/env bash
out=""
prev=""
for arg in "$@"; do
    if [ "$prev" = "czf" ]; then
        out="$arg"
        break
    fi
    prev="$arg"
done
printf 'fake standby archive rc=%s\\n' "${KAI_FAKE_TAR_RC:-0}" > "$out"
exit "${KAI_FAKE_TAR_RC:-0}"
"""


def _lauf(tmp_path: Path, tar_rc: int) -> tuple[subprocess.CompletedProcess[str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tar = fake_bin / "tar"
    tar.write_text(_FAKE_TAR, encoding="utf-8", newline="\n")
    tar.chmod(0o755)
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "artifacts").mkdir()
    usb = tmp_path / "usb"
    ergebnis = subprocess.run(  # noqa: S603
        ["bash", str(SKRIPT), "data"],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/local/bin",
            "KAI_STANDBY_REPO": str(repo),
            "KAI_STANDBY_USB": str(usb),
            "KAI_STANDBY_MOUNT_GUARD": "",  # kein USB im Test
            "KAI_FAKE_TAR_RC": str(tar_rc),
        },
        timeout=20,
        check=False,
    )
    return ergebnis, usb


@pytest.mark.parametrize("tar_rc", [0, 1])
def test_sauberes_und_crash_konsistentes_archiv_wird_fertiggestellt(
    tmp_path: Path, tar_rc: int
) -> None:
    ergebnis, usb = _lauf(tmp_path, tar_rc)

    assert ergebnis.returncode == 0, ergebnis.stderr + ergebnis.stdout
    fertig = sorted(usb.glob("data_*.tar.gz"))
    assert len(fertig) == 1, "genau ein fertiges Datenarchiv"
    assert not list(usb.glob("*.part")), "kein halbes Archiv bleibt liegen"
    log = (usb / "standby.log").read_text(encoding="utf-8")
    assert "done: data_" in log
    # rc=1 wird ausdruecklich als akzeptiert protokolliert, rc=0 nicht.
    assert ("tar rc=1" in log) is (tar_rc == 1)


def test_echter_tar_fehler_bricht_ab_ohne_fertiges_archiv(tmp_path: Path) -> None:
    ergebnis, usb = _lauf(tmp_path, 2)

    assert ergebnis.returncode != 0
    assert not list(usb.glob("data_*.tar.gz")), "rc>=2 darf nie als fertiges Archiv erscheinen"
    assert "FAIL: tar rc=2" in (usb / "standby.log").read_text(encoding="utf-8")
