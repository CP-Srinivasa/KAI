from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest


def test_timer_health_probe_real_systemctl_inactive_single_token(tmp_path) -> None:
    """Regression: the REAL systemctl branch must yield ONE clean finding token.

    `systemctl is-active <x>` prints "inactive" AND exits non-zero. The old
    `|| echo "inactive"` doubled it to "inactive\\ninactive", so the finding
    "<timer> (inactive\\ninactive)" split into two JSON elements and the orphan
    "inactive)" fragment was mis-classified as a critical recurring timer
    (false-positive). This drives the real branch via a fake systemctl on PATH.
    """
    if shutil.which("bash") is None:
        pytest.skip("bash unavailable")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_systemctl = fake_bin / "systemctl"
    # Mimic real systemd: is-active prints the state to stdout AND exits 3 for
    # non-active units. Any other subcommand exits 0 cleanly.
    fake_systemctl.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "is-active" ]]; then echo "inactive"; exit 3; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)

    audit_file_rel = Path("artifacts") / f"timer_health_audit_test_{tmp_path.name}.jsonl"
    audit_file_abs = Path(__file__).resolve().parents[2] / audit_file_rel
    if audit_file_abs.exists():
        audit_file_abs.unlink()

    test_env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "KAI_TIMER_PROBE_TIMERS": "kai-risk-gate-audit-review.timer",
        # NO TEST_STATES → forces the real `systemctl is-active` branch.
        "KAI_TIMER_PROBE_AUDIT_FILE": str(audit_file_rel.as_posix()),
        "KAI_TIMER_PROBE_IGNORE_DOTENV": "1",
        "KAI_TIMER_PROBE_DRY_RUN": "1",
    }

    try:
        res = subprocess.run(
            ["bash", "scripts/pi_timer_health_probe.sh"],
            env=test_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert res.returncode == 0, res.stderr
        lines = audit_file_abs.read_text(encoding="utf-8").splitlines()
        data = json.loads(lines[-1])
        # The finding MUST be a single, well-formed token (no orphan fragment).
        assert data["findings"] == ["kai-risk-gate-audit-review.timer (inactive)"]
    finally:
        if audit_file_abs.exists():
            audit_file_abs.unlink()


def test_timer_health_probe_all_active(tmp_path) -> None:
    # Setup temporary audit file inside the tmp_path.
    # Use a relative path from the current working directory so bash on Windows can resolve it.
    audit_file_rel = Path("artifacts") / f"timer_health_audit_test_{tmp_path.name}.jsonl"
    audit_file_abs = Path(__file__).resolve().parents[2] / audit_file_rel

    # Make sure we clean up if it exists
    if audit_file_abs.exists():
        audit_file_abs.unlink()

    # Environment overrides
    test_env = {
        **os.environ,
        "KAI_TIMER_PROBE_TIMERS": "kai-auto-annotate.timer,kai-pi-health.timer",
        "KAI_TIMER_PROBE_TEST_STATES": "kai-auto-annotate.timer:active,kai-pi-health.timer:active",
        "KAI_TIMER_PROBE_AUDIT_FILE": str(audit_file_rel.as_posix()),
        "KAI_TIMER_PROBE_IGNORE_DOTENV": "1",
        "KAI_TIMER_PROBE_DRY_RUN": "1",
        # WSL integration: propagate these variables from Windows host to WSL bash instance
        "WSLENV": (
            "KAI_TIMER_PROBE_TIMERS/u:"
            "KAI_TIMER_PROBE_TEST_STATES/u:"
            "KAI_TIMER_PROBE_AUDIT_FILE/u:"
            "KAI_TIMER_PROBE_IGNORE_DOTENV/u:"
            "KAI_TIMER_PROBE_DRY_RUN/u"
        ),
    }

    # Run script using relative path
    res = subprocess.run(
        ["bash", "scripts/pi_timer_health_probe.sh"],
        env=test_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    try:
        assert res.returncode == 0
        assert "OK (all timers active)" in res.stdout

        # Verify audit file
        assert audit_file_abs.exists()
        lines = audit_file_abs.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["event"] == "timer_health_probe.ok"
        assert data["findings"] == []
    finally:
        if audit_file_abs.exists():
            audit_file_abs.unlink()


def test_timer_health_probe_inactive_alert_and_throttling(tmp_path) -> None:
    audit_file_rel = Path("artifacts") / f"timer_health_audit_test_{tmp_path.name}.jsonl"
    audit_file_abs = Path(__file__).resolve().parents[2] / audit_file_rel

    if audit_file_abs.exists():
        audit_file_abs.unlink()

    # Scenario 1: First run with an inactive timer
    test_env_1 = {
        **os.environ,
        "KAI_TIMER_PROBE_TIMERS": "kai-auto-annotate.timer,kai-pi-health.timer",
        "KAI_TIMER_PROBE_TEST_STATES": "kai-auto-annotate.timer:inactive,kai-pi-health.timer:active",
        "KAI_TIMER_PROBE_AUDIT_FILE": str(audit_file_rel.as_posix()),
        "KAI_TIMER_PROBE_IGNORE_DOTENV": "1",
        "KAI_TIMER_PROBE_DRY_RUN": "1",
        # WSL integration: propagate these variables from Windows host to WSL bash instance
        "WSLENV": (
            "KAI_TIMER_PROBE_TIMERS/u:"
            "KAI_TIMER_PROBE_TEST_STATES/u:"
            "KAI_TIMER_PROBE_AUDIT_FILE/u:"
            "KAI_TIMER_PROBE_IGNORE_DOTENV/u:"
            "KAI_TIMER_PROBE_DRY_RUN/u"
        ),
    }

    try:
        res_1 = subprocess.run(
            ["bash", "scripts/pi_timer_health_probe.sh"],
            env=test_env_1,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert res_1.returncode == 0
        assert "INACTIVE TIMERS DETECTED - ALERTING Operator" in res_1.stdout
        assert "Reason: first_alert" in res_1.stdout

        # Verify audit entry 1
        assert audit_file_abs.exists()
        lines = audit_file_abs.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        data_1 = json.loads(lines[0])
        assert data_1["event"] == "timer_health_probe.findings"
        assert data_1["findings"] == ["kai-auto-annotate.timer (inactive)"]
        assert data_1["decision_reason"] == "first_alert"
        assert data_1["last_alerted_utc"] != ""

        # Scenario 2: Second run with same findings -> Throttled!
        res_2 = subprocess.run(
            ["bash", "scripts/pi_timer_health_probe.sh"],
            env=test_env_1,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert res_2.returncode == 0
        assert "INACTIVE TIMERS DETECTED - Alert throttled" in res_2.stdout
        assert "reason: throttled" in res_2.stdout.lower()

        # Verify audit entry 2
        lines = audit_file_abs.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        data_2 = json.loads(lines[1])
        assert data_2["event"] == "timer_health_probe.findings"
        assert data_2["findings"] == ["kai-auto-annotate.timer (inactive)"]
        assert data_2["decision_reason"] == "throttled"
        assert data_2["last_alerted_utc"] == data_1["last_alerted_utc"]

        # Probe-Skript schreibt last_alerted_utc mit Sekundenauflösung.
        # In CI laufen die 3 Subprocess-Aufrufe sub-sekündlich → ohne sleep
        # kollidiert Scenario-3 mit Scenario-1 im selben UTC-Sekunden-Bucket
        # und die "last_alerted_utc != data_1.last_alerted_utc"-Assertion failt.
        time.sleep(1.1)

        # Scenario 3: Third run with NEW findings -> Alerting again!
        test_env_3 = {
            **test_env_1,
            "KAI_TIMER_PROBE_TEST_STATES": "kai-auto-annotate.timer:inactive,kai-pi-health.timer:inactive",
        }

        res_3 = subprocess.run(
            ["bash", "scripts/pi_timer_health_probe.sh"],
            env=test_env_3,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert res_3.returncode == 0
        assert "INACTIVE TIMERS DETECTED - ALERTING Operator" in res_3.stdout
        assert "Reason: new_findings" in res_3.stdout

        # Verify audit entry 3
        lines = audit_file_abs.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        data_3 = json.loads(lines[2])
        assert data_3["event"] == "timer_health_probe.findings"
        assert set(data_3["findings"]) == {
            "kai-auto-annotate.timer (inactive)",
            "kai-pi-health.timer (inactive)",
        }
        assert data_3["decision_reason"] == "new_findings"
        assert data_3["last_alerted_utc"] != data_1["last_alerted_utc"]
    finally:
        if audit_file_abs.exists():
            audit_file_abs.unlink()


def test_timer_health_probe_skips_disabled_timer(tmp_path) -> None:
    """A deliberately-disabled timer that is inactive is OFF by design, not a
    finding — it must NOT raise a Timer-Health alert (daily false-positive fix
    for kai-hype-refresh / kai-risk-gate-audit-review)."""
    audit_file_rel = Path("artifacts") / f"timer_health_audit_test_{tmp_path.name}_dis.jsonl"
    audit_file_abs = Path(__file__).resolve().parents[2] / audit_file_rel
    if audit_file_abs.exists():
        audit_file_abs.unlink()
    test_env = {
        **os.environ,
        "KAI_TIMER_PROBE_TIMERS": "kai-hype-refresh.timer",
        "KAI_TIMER_PROBE_TEST_STATES": "kai-hype-refresh.timer:inactive",
        "KAI_TIMER_PROBE_TEST_ENABLED": "kai-hype-refresh.timer:disabled",
        "KAI_TIMER_PROBE_AUDIT_FILE": str(audit_file_rel.as_posix()),
        "KAI_TIMER_PROBE_IGNORE_DOTENV": "1",
        "KAI_TIMER_PROBE_DRY_RUN": "1",
    }
    try:
        res = subprocess.run(
            ["bash", "scripts/pi_timer_health_probe.sh"],
            env=test_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert res.returncode == 0, res.stderr
        data = json.loads(audit_file_abs.read_text(encoding="utf-8").splitlines()[-1])
        assert data["findings"] == []
        assert data["event"] == "timer_health_probe.ok"
    finally:
        if audit_file_abs.exists():
            audit_file_abs.unlink()


def test_timer_health_probe_enabled_inactive_still_flagged(tmp_path) -> None:
    """Control: an ENABLED timer that is inactive IS a real finding — the
    disabled-skip must not swallow genuine faults."""
    audit_file_rel = Path("artifacts") / f"timer_health_audit_test_{tmp_path.name}_en.jsonl"
    audit_file_abs = Path(__file__).resolve().parents[2] / audit_file_rel
    if audit_file_abs.exists():
        audit_file_abs.unlink()
    test_env = {
        **os.environ,
        "KAI_TIMER_PROBE_TIMERS": "kai-paper-trading.timer",
        "KAI_TIMER_PROBE_TEST_STATES": "kai-paper-trading.timer:inactive",
        "KAI_TIMER_PROBE_TEST_ENABLED": "kai-paper-trading.timer:enabled",
        "KAI_TIMER_PROBE_AUDIT_FILE": str(audit_file_rel.as_posix()),
        "KAI_TIMER_PROBE_IGNORE_DOTENV": "1",
        "KAI_TIMER_PROBE_DRY_RUN": "1",
    }
    try:
        res = subprocess.run(
            ["bash", "scripts/pi_timer_health_probe.sh"],
            env=test_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert res.returncode == 0, res.stderr
        data = json.loads(audit_file_abs.read_text(encoding="utf-8").splitlines()[-1])
        assert data["findings"] == ["kai-paper-trading.timer (inactive)"]
    finally:
        if audit_file_abs.exists():
            audit_file_abs.unlink()
