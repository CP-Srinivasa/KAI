"""STAB-2026-09-01 §21 — the watchdog's diagnostic command must match the incident.

``scripts/pi_service_watchdog.sh`` appended this to EVERY alarm:

    Next: journalctl -u kai-agent-worker -u kai-tg-listener \\
          --since '2026-05-02 20:00' --no-pager

A bash double-quoted literal with no parameter expansion. Both halves were frozen
when the line was written (3e13d72d): the unit list was whichever two units had
failed that day, and the timestamp was that day's incident clock. `git log -L`
shows exactly one commit in the line's history, so it was never revisited. An
operator investigating a kai-health-check failure was handed a ten-minute window
around an incident four months earlier, on two units that were healthy.

The rule these tests pin: a failure on unit A must never emit a diagnostic for
unit B.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "pi_service_watchdog.sh"


def _fake_systemctl(fake_bin: Path, body: str) -> None:
    fake_bin.mkdir(parents=True, exist_ok=True)
    sc = fake_bin / "systemctl"
    sc.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    sc.chmod(0o755)


def _run(tmp_path: Path, units: str, state: str) -> str:
    _fake_systemctl(
        tmp_path / "bin",
        'case "$1" in\n'
        f'  is-active) echo "{state}" ; exit 3 ;;\n'
        "  list-unit-files|list-units) exit 0 ;;\n"
        "  start|restart) exit 1 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
    )
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        "KAI_SERVICE_WATCHDOG_UNITS": units,
        "KAI_SERVICE_WATCHDOG_STATE_DIR": str(tmp_path / "state"),
        "KAI_SERVICE_WATCHDOG_TRANSIENT_SETTLE_SEC": "0",
        "ALERT_TELEGRAM_TOKEN": "",
        "ALERT_TELEGRAM_CHAT_ID": "",
    }
    proc = subprocess.run(
        ["bash", str(_SCRIPT)],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return proc.stdout


@pytest.fixture(autouse=True)
def _need_bash() -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash unavailable")


# --------------------------------------------------------------------------
# The regression
# --------------------------------------------------------------------------
def test_the_frozen_2026_05_02_literal_is_gone_from_the_code() -> None:
    """It may survive as documentation, never as an emitted string."""
    for line in _SCRIPT.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert "2026-05-02" not in line, f"stale incident literal still emitted: {line}"


def test_a_failure_on_one_unit_does_not_name_another(tmp_path: Path) -> None:
    """THE contract: unit A fails => diagnostics for unit A, and nothing else."""
    out = _run(tmp_path, "kai-health-check", "failed")
    hint = next((line for line in out.splitlines() if line.startswith("Next: journalctl")), "")
    assert hint, f"no diagnostic hint emitted:\n{out}"
    assert "-u kai-health-check" in hint
    assert "kai-agent-worker" not in hint
    assert "kai-tg-listener" not in hint


def test_the_window_is_anchored_to_now_not_to_a_literal(tmp_path: Path) -> None:
    out = _run(tmp_path, "kai-health-check", "failed")
    hint = next(line for line in out.splitlines() if line.startswith("Next: journalctl"))
    assert "2026-05-02" not in hint
    # --since AND --until, both real timestamps around the detection moment.
    stamps = re.findall(r"'(\d{4}-\d{2}-\d{2} \d{2}:\d{2})'", hint)
    assert len(stamps) == 2, hint
    assert "--until" in hint


def test_every_failed_unit_appears_exactly_once(tmp_path: Path) -> None:
    out = _run(tmp_path, "kai-health-check kai-server kai-health-check", "failed")
    hint = next(line for line in out.splitlines() if line.startswith("Next: journalctl"))
    assert hint.count("-u kai-health-check") == 1
    assert hint.count("-u kai-server") == 1


def test_no_alarm_means_no_diagnostic_hint(tmp_path: Path) -> None:
    """NEGATIVE CONTROL: a healthy sweep emits no journalctl line at all."""
    _fake_systemctl(
        tmp_path / "bin",
        'case "$1" in\n'
        '  is-active) echo "active" ; exit 0 ;;\n'
        "  list-unit-files|list-units) exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
    )
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        "KAI_SERVICE_WATCHDOG_UNITS": "kai-server",
        "KAI_SERVICE_WATCHDOG_STATE_DIR": str(tmp_path / "state"),
        "KAI_SERVICE_WATCHDOG_TRANSIENT_SETTLE_SEC": "0",
        "ALERT_TELEGRAM_TOKEN": "",
        "ALERT_TELEGRAM_CHAT_ID": "",
    }
    proc = subprocess.run(
        ["bash", str(_SCRIPT)],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "journalctl" not in proc.stdout
