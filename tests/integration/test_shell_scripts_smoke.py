"""D-195 / NEO-F-META-20260424-006 — Shell-Script-Safety-Net.

Extends the A5 pattern (test_paper_trading_cron_bash.py) to the other
operator-critical bash scripts that ship in the repo:

  - scripts/server_start.sh              (D-188 watchdog callsite)
  - scripts/server_stop.sh               (D-185 kill-verify contract)
  - scripts/server_restart.sh
  - scripts/server_status.sh
  - scripts/agent_worker_start.sh
  - scripts/agent_worker_stop.sh
  - scripts/pi_install_systemd.sh        (D-190)
  - scripts/pi_transfer_artifacts.sh     (D-191)

The coverage is intentionally **smoke-level**, not functional: each script
gets a ``bash -n`` parse-check plus one or two behaviour probes that the
script can be trusted to satisfy without a real server, a real Pi, or
real SSH. This is the minimum-viable safety net Neo asked for in META-006
before Pi-migration activates the Bash path for production.

PowerShell scripts (``paper_trading_cron.ps1``) are out of scope — that
path retires with the Laptop-Cutover.

All tests skip cleanly when ``bash`` is not on PATH (Git-Bash on Windows
satisfies this, which doubles the tests as pre-cutover verification).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _require_bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not on PATH")
    return bash


def _repo_bash_path(path: Path) -> str:
    """Return a repo-relative path for bash launched with cwd=REPO_ROOT."""
    return path.resolve().relative_to(REPO_ROOT).as_posix()


# ---------------------------------------------------------------------------
# Syntax check across the board
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script_name",
    [
        "server_start.sh",
        "server_stop.sh",
        "server_restart.sh",
        "server_status.sh",
        "agent_worker_start.sh",
        "agent_worker_stop.sh",
        "paper_trading_cron.sh",
        "pi_install_systemd.sh",
        "pi_transfer_artifacts.sh",
        "pi_build_web.sh",
        "pi_deploy_web.sh",
        "pi_health_digest.sh",
        "pi_service_watchdog.sh",
    ],
)
def test_bash_syntax(script_name: str) -> None:
    """``bash -n`` must parse each shipped script without errors."""
    bash = _require_bash()
    path = SCRIPTS / script_name
    assert path.exists(), f"missing: {path}"
    rel_path = shlex.quote(_repo_bash_path(path))
    result = subprocess.run(
        [bash, "-lc", f"tr -d '\\r' < {rel_path} | bash -n"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"bash -n failed for {script_name}:\n{result.stderr}"


# ---------------------------------------------------------------------------
# pi_install_systemd.sh — D-190
# ---------------------------------------------------------------------------


def test_pi_install_systemd_help_works() -> None:
    bash = _require_bash()
    result = subprocess.run(
        [bash, _repo_bash_path(SCRIPTS / "pi_install_systemd.sh"), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    # Help prints the leading comment-block (usage lines 3-17) via sed.
    assert "--dry-run" in out
    assert "--uninstall" in out


def test_pi_install_systemd_installs_health_units() -> None:
    text = (SCRIPTS / "pi_install_systemd.sh").read_text(encoding="utf-8")
    assert '"kai-pi-health.service"' in text
    assert '"kai-pi-health.timer"' in text
    assert '"kai-service-watchdog.service"' in text
    assert '"kai-service-watchdog.timer"' in text
    assert "kai-service-watchdog.timer" in text


def test_pi_install_systemd_uses_external_install_command() -> None:
    text = (SCRIPTS / "pi_install_systemd.sh").read_text(encoding="utf-8")
    assert "install()" in text
    assert "run command install -m 0644" in text
    assert "run install -m 0644" not in text


def test_pi_service_watchdog_is_external_and_restart_capable() -> None:
    text = (SCRIPTS / "pi_service_watchdog.sh").read_text(encoding="utf-8")
    assert "kai-agent-worker" in text
    assert "kai-tg-listener" in text
    assert "systemctl is-active" in text
    assert 'systemctl start "$unit"' in text
    assert "KAI_SERVICE_WATCHDOG_THROTTLE_SECONDS" in text
    assert "ALERT_TELEGRAM_TOKEN" in text


def test_pi_install_systemd_refuses_non_root(tmp_path: Path) -> None:
    """Without root (EUID != 0), the install path must abort with exit 1.

    Skipped on Windows (Git-Bash reports EUID=0 by default).
    """
    bash = _require_bash()
    if os.name == "nt":
        pytest.skip("Git-Bash on Windows reports EUID=0; root-check is unreachable")
    result = subprocess.run(
        [bash, _repo_bash_path(SCRIPTS / "pi_install_systemd.sh"), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=REPO_ROOT,
    )
    # --dry-run still runs require_root and exits 1.
    assert result.returncode == 1
    assert "must run as root" in result.stderr


# ---------------------------------------------------------------------------
# pi_transfer_artifacts.sh — D-191
# ---------------------------------------------------------------------------


def test_pi_transfer_help_works() -> None:
    bash = _require_bash()
    result = subprocess.run(
        [bash, _repo_bash_path(SCRIPTS / "pi_transfer_artifacts.sh"), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    assert "Transfer operational artifacts" in out or "--group=" in out


def test_pi_transfer_requires_host() -> None:
    """No positional host → exit 2 with clear stderr."""
    bash = _require_bash()
    result = subprocess.run(
        [bash, _repo_bash_path(SCRIPTS / "pi_transfer_artifacts.sh"), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 2
    assert "remote host required" in result.stderr


def test_pi_transfer_env_group_shows_secrets_handler() -> None:
    """``--group=env --dry-run kai@pi.local`` must print the secrets handler.

    We pick the ``env`` group because it is the only group that does NOT
    invoke rsync (and therefore does not try to resolve the fake host).
    It surfaces the sensitive-files handler (header + manual-transfer
    section), which is enough to verify the env-secrets path is wired in.

    Note: the actual ``scp ...`` instruction lines are conditional on
    ``.env`` files being present in the repo root — CI does not ship any,
    so we assert the handler-trigger lines instead.
    """
    bash = _require_bash()
    result = subprocess.run(
        [
            bash,
            _repo_bash_path(SCRIPTS / "pi_transfer_artifacts.sh"),
            "kai@pi.local",
            "--dry-run",
            "--group=env",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"rc={result.returncode} stderr={result.stderr[:300]}"
    out = result.stdout
    assert "[group=env]" in out
    assert "SENSITIVE" in out
    assert "Transfer these yourself with:" in out  # manual-transfer section header


# ---------------------------------------------------------------------------
# server_stop.sh — D-185 contract: kill-verify or explicit error
# ---------------------------------------------------------------------------


def test_server_stop_exposes_d185_verify_block() -> None:
    """Source-level assertion: the post-kill verify + explicit error are present.

    A behavioural test would require spawning a real long-runner, killing
    it, and observing the exit. That is out-of-scope for a smoke; instead
    we grep for the D-185 anchor text that guards the behaviour. If the
    block is removed during a refactor the test fails loud and early.
    """
    text = (SCRIPTS / "server_stop.sh").read_text(encoding="utf-8")
    # D-185 ensures the script verifies is_pid_running after SIGKILL and
    # exits 1 with a clear message rather than falsely reporting success.
    assert 'is_pid_running "$PID"' in text or 'is_pid_running "$PID"' in text
    assert "still running after SIGTERM+SIGKILL" in text
    assert "D-185" in text


def test_server_stop_handles_stale_pid_file(tmp_path: Path) -> None:
    """Stale PID file with a definitely-dead PID → exit 0, remove file.

    Runs the script in a sandbox so it does not touch the real
    ``.server.pid`` of the live server.
    """
    bash = _require_bash()
    # Sandbox: copy scripts/ into tmp_path so cd "$(dirname "$0")/.." lands
    # in tmp_path and the script reads/writes an isolated .server.pid.
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "scripts").mkdir()
    for name in ("server_stop.sh", "agent_worker_stop.sh", "telegram_listener_stop.sh"):
        src = SCRIPTS / name
        if src.exists():
            shutil.copy(src, sandbox / "scripts")
    # Fake PID that cannot be running (PID 1 is init on Linux — skip; use
    # a high number + Git-Bash/Linux-compatible strategy).
    # We use PID 999999999 — too large for real systems, guaranteed absent.
    (sandbox / ".server.pid").write_text("999999999\n", encoding="utf-8")

    result = subprocess.run(
        [bash, "scripts/server_stop.sh"],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=sandbox,
    )
    assert result.returncode == 0, f"rc={result.returncode} stderr={result.stderr}"
    # Either "Server was not running" (PID absent) or "Server stopped"
    # (if by some race a matching PID existed). Stale PID case is expected.
    assert "Server was not running" in result.stdout or "Server stopped" in result.stdout, (
        result.stdout
    )
    # PID file must be removed after a clean non-running path.
    assert not (sandbox / ".server.pid").exists()


def test_server_stop_handles_no_pid_file(tmp_path: Path) -> None:
    """No PID file → exit 0 with informational message, no crash."""
    bash = _require_bash()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "scripts").mkdir()
    shutil.copy(SCRIPTS / "server_stop.sh", sandbox / "scripts")

    result = subprocess.run(
        [bash, "scripts/server_stop.sh"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=sandbox,
    )
    assert result.returncode == 0
    assert "No PID file found" in result.stdout
