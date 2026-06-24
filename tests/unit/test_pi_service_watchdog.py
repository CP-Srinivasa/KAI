"""Unit tests for scripts/pi_service_watchdog.sh transient-state tolerance.

2026-06-24: the watchdog treated ANY non-active state as down, so a unit caught
mid-restart (ActiveState=deactivating/activating) triggered a noisy
"kai-server=deactivating; restart=start_ok" alarm and a redundant restart. The
fix re-checks once after a short settle before declaring the unit down.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _fake_systemctl(fake_bin: Path, body: str) -> None:
    fake_bin.mkdir(parents=True, exist_ok=True)
    sc = fake_bin / "systemctl"
    sc.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    sc.chmod(0o755)


def _run_watchdog(tmp_path: Path, fake_bin: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "KAI_SERVICE_WATCHDOG_UNITS": "kai-server",
        "KAI_SERVICE_WATCHDOG_STATE_DIR": str(tmp_path / "state"),
        "KAI_SERVICE_WATCHDOG_TRANSIENT_SETTLE_SEC": "0",
        # No telegram creds → send_telegram is a no-op.
        "ALERT_TELEGRAM_TOKEN": "",
        "ALERT_TELEGRAM_CHAT_ID": "",
    }
    return subprocess.run(
        ["bash", "scripts/pi_service_watchdog.sh"],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_watchdog_tolerates_transient_then_active(tmp_path) -> None:
    """deactivating → active across the settle re-check: OK, no alarm, no restart."""
    if shutil.which("bash") is None:
        pytest.skip("bash unavailable")
    counter = tmp_path / "calls"
    _fake_systemctl(
        tmp_path / "bin",
        f'C="{counter.as_posix()}"\n'
        'if [[ "$1" == "is-active" ]]; then\n'
        '  n=$(cat "$C" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$C"\n'
        '  if [[ "$n" -le 1 ]]; then echo deactivating; exit 3; fi\n'
        "  echo active; exit 0\n"
        "fi\n"
        "exit 0\n",
    )
    res = _run_watchdog(tmp_path, tmp_path / "bin")
    assert res.returncode == 0, res.stderr
    assert "OK" in res.stdout
    assert "restart" not in res.stdout.lower()
    assert "alarm" not in res.stdout.lower()


def test_watchdog_still_restarts_genuinely_dead_unit(tmp_path) -> None:
    """Control: a unit that stays inactive across the re-check IS restarted —
    the transient tolerance must not mask a real outage."""
    if shutil.which("bash") is None:
        pytest.skip("bash unavailable")
    _fake_systemctl(
        tmp_path / "bin",
        'if [[ "$1" == "is-active" ]]; then echo inactive; exit 3; fi\n'
        "exit 0\n",  # start (and everything else) succeeds
    )
    res = _run_watchdog(tmp_path, tmp_path / "bin")
    assert res.returncode == 0, res.stderr
    assert "alarm" in res.stdout.lower()
    assert "kai-server" in res.stdout
