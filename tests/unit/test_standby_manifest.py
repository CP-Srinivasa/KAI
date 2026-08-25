from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STANDBY_SCRIPT = ROOT / "scripts" / "standby_to_usb.sh"


def _require_bash() -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash nicht installiert")


def _write_fake_tools(bin_dir: Path) -> None:
    bin_dir.mkdir()
    mountpoint = bin_dir / "mountpoint"
    tar = bin_dir / "tar"
    mountpoint.write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    tar.write_text(
        """#!/usr/bin/env bash
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
""",
        encoding="utf-8",
        newline="\n",
    )
    mountpoint.chmod(0o755)
    tar.chmod(0o755)


def _run_standby(tmp_path: Path, tar_rc: int, ts: str) -> subprocess.CompletedProcess[str]:
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    shutil.copy2(STANDBY_SCRIPT, scripts / "standby_to_usb.sh")
    fake_bin = tmp_path / "bin"
    _write_fake_tools(fake_bin)
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "artifacts").mkdir()
    env = os.environ.copy()
    command = (
        'export PATH="$PWD/bin:$PATH"; '
        'export KAI_STANDBY_REPO="$PWD/repo"; '
        'export KAI_STANDBY_USB="$PWD/usb"; '
        f"export KAI_STANDBY_TS={ts}; "
        f"export KAI_FAKE_TAR_RC={tar_rc}; "
        "bash scripts/standby_to_usb.sh data"
    )
    return subprocess.run(
        ["bash", "-lc", command],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )


@pytest.mark.parametrize(
    ("tar_rc", "ts", "crash_consistent"),
    [
        (0, "20260825T010203Z", False),
        (1, "20260825T020304Z", True),
    ],
)
def test_standby_data_manifest_records_tar_rc_and_size(
    tmp_path: Path,
    tar_rc: int,
    ts: str,
    crash_consistent: bool,
) -> None:
    _require_bash()

    result = _run_standby(tmp_path, tar_rc, ts)

    assert result.returncode == 0, result.stderr + result.stdout
    archive = tmp_path / "usb" / f"data_{ts}.tar.gz"
    manifest = archive.with_name(archive.name + ".manifest.json")
    assert archive.is_file()
    assert manifest.is_file()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["schema"] == "standby_manifest/v1"
    assert data["archive"].endswith(archive.name)
    assert data["size_bytes"] == archive.stat().st_size
    assert data["tar_rc"] == tar_rc
    assert data["crash_consistent"] is crash_consistent
    assert bool(data["note"])
