"""D-206 / A3-C1 — bash-integration smoke for ``server_stop.sh --prepare-cutover``.

The Pi-cutover on 2026-05-01 needs the SQLite domain DB at ``data/dev.db``
on the Pi *before* the laptop is powered down, otherwise the Re-Entry
baseline (4651+ docs, 1803+ cycles) is lost. Memo
``artifacts/operator_memos/pi_migration_2026-05-01_status.md`` §9.1 tracks
this as A3 with two paths: C1 (this implementation — automated stop +
sync) or C2 (manual operator sequence). C1 closes the manual-keystroke
risk by orchestrating the four steps inside ``server_stop.sh``.

This module verifies the orchestration without touching real ssh/scp:

* Bash syntax check (catches shell-portability bugs early).
* Argument parsing: ``--prepare-cutover`` without value is rejected,
  unknown flags are rejected, ``--remote-root=`` is accepted.
* Pre-flight: missing ``data/dev.db`` aborts before stopping the server.
* Pre-flight: failing ssh probe aborts before stopping the server
  (PATH-stubbed ``ssh`` exits non-zero).
* Happy path: with stubbed ``ssh``/``scp``/``sha256sum`` the script
  reaches the "sha256 match" branch and exits 0.
* Mismatch path: a stubbed remote sha that does not match the local one
  exits 2 with the expected error.

Skips cleanly when bash is not on PATH (constrained CI runners).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STOP_SCRIPT = REPO_ROOT / "scripts" / "server_stop.sh"


def _require_bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not on PATH")
    return bash


def _stage_sandbox(tmp_path: Path, *, with_db: bool = True) -> Path:
    """Build a minimal repo layout the script expects to operate on."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(STOP_SCRIPT, tmp_path / "scripts")
    # Stub the sibling stop scripts the SUT calls — must be present so the
    # ``bash sibling.sh || true`` line does not noise the output. They
    # exit 0 immediately.
    for sibling in ("agent_worker_stop.sh", "telegram_listener_stop.sh"):
        stub = tmp_path / "scripts" / sibling
        stub.write_text("#!/usr/bin/env bash\nexit 0\n")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if with_db:
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "dev.db").write_bytes(b"sqlite_format_3_stub_payload" * 64)
    return tmp_path


def _stub_dir(tmp_path: Path, stubs: dict[str, str]) -> Path:
    """Write executable shell stubs into a fresh dir; return its path.

    ``stubs`` maps tool name -> script body (without shebang). The stubs
    are added to PATH ahead of system tools so the SUT picks them up.
    """
    bin_dir = tmp_path / "stub_bin"
    bin_dir.mkdir()
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text(f"#!/usr/bin/env bash\n{body}\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _run(
    bash: str,
    sandbox: Path,
    args: list[str],
    *,
    extra_path: Path | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(sandbox)}
    if extra_path is not None:
        env["PATH"] = f"{extra_path}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        # .as_posix(): bash treats backslashes in a Windows path string as escape
        # chars (C:\Users\... -> C:Users...), so the script arg must use forward
        # slashes for the bash integration to work on a Windows dev workstation.
        [bash, (sandbox / "scripts" / "server_stop.sh").as_posix(), *args],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=sandbox,
        env=env,
    )


def test_bash_syntax_check() -> None:
    bash = _require_bash()
    result = subprocess.run(
        [bash, "-n", STOP_SCRIPT.as_posix()],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"syntax errors:\n{result.stderr}"


def test_baseline_no_cutover_no_pid_file_exits_zero(tmp_path: Path) -> None:
    """Without --prepare-cutover and no PID file the script reports
    ``Server not running`` and exits 0 — pre-existing behaviour must
    be preserved when the new flag is absent."""
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    result = _run(bash, sandbox, [])
    assert result.returncode == 0, result.stderr
    assert "No PID file found" in result.stdout


def test_prepare_cutover_without_value_rejected(tmp_path: Path) -> None:
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    result = _run(bash, sandbox, ["--prepare-cutover"])
    assert result.returncode == 2
    assert "requires =<ssh-host>" in result.stderr


def test_unknown_flag_rejected(tmp_path: Path) -> None:
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    result = _run(bash, sandbox, ["--bogus"])
    assert result.returncode == 2
    assert "unknown flag" in result.stderr


def test_cutover_aborts_when_db_missing(tmp_path: Path) -> None:
    """Pre-flight catches missing data/dev.db BEFORE the server stop runs."""
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path, with_db=False)
    # Provide working tool stubs so the missing-DB check is the first failure.
    stubs = _stub_dir(
        tmp_path,
        {
            "ssh": 'echo "stub ssh: $@"; exit 0',
            "scp": "exit 0",
            "sha256sum": 'echo "deadbeef  $1"',
        },
    )
    result = _run(bash, sandbox, ["--prepare-cutover=kai@pi.local"], extra_path=stubs)
    assert result.returncode == 2
    assert "data/dev.db does not exist" in result.stderr
    assert "before server stop" in result.stderr.lower()


def test_cutover_aborts_when_ssh_probe_fails(tmp_path: Path) -> None:
    """Failing ssh probe must abort BEFORE the server is stopped."""
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    stubs = _stub_dir(
        tmp_path,
        {
            # Probe: refuse the cutover_probe_ok command, exit 255 (ssh-style auth-fail)
            "ssh": "exit 255",
            "scp": "exit 0",
            "sha256sum": 'echo "deadbeef  $1"',
        },
    )
    result = _run(bash, sandbox, ["--prepare-cutover=kai@pi.local"], extra_path=stubs)
    assert result.returncode == 2
    assert "ssh probe to kai@pi.local failed" in result.stderr
    assert "before server stop" in result.stderr.lower()


def test_cutover_happy_path_sha_match(tmp_path: Path) -> None:
    """Stubbed ssh+scp+sha256sum produce a matching sha → exit 0.

    The real ssh executes ``sha256sum REMOTE_DB | awk '{print $1}'`` on
    the remote, so production stdout is just the hash. The stub emulates
    that contract: when matching the sha branch we output ONLY the hash
    (no two-column ``hash  path`` output) — otherwise the SUT compares
    against a string that includes the path and falsely reports mismatch.
    """
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    fixed_sha = "abc123def456abc123def456abc123def456abc123def456abc123def456abcd"
    stubs = _stub_dir(
        tmp_path,
        {
            # ssh handles three call sites:
            #   1) probe: `ssh ... echo cutover_probe_ok`
            #   2) mkdir: `ssh ... mkdir -p ...`
            #   3) remote sha: `ssh ... 'sha256sum REMOTE_DB | awk ...'`
            "ssh": (
                'last="${@: -1}"; '
                f'case "$last" in '
                f"  *cutover_probe_ok*) exit 0 ;; "
                f"  *mkdir*) exit 0 ;; "
                f'  *sha256sum*) echo "{fixed_sha}"; exit 0 ;; '
                f'  *) echo "unstubbed ssh: $@" >&2; exit 99 ;; '
                f"esac"
            ),
            "scp": "exit 0",
            # Local sha: the SUT calls `sha256sum data/dev.db` then awks
            # field 1 itself — return the standard two-column format.
            "sha256sum": f'echo "{fixed_sha}  $1"',
        },
    )
    result = _run(bash, sandbox, ["--prepare-cutover=kai@pi.local"], extra_path=stubs)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "Cutover-Sync complete" in result.stdout
    assert "sha256 match" in result.stdout


def test_cutover_sha_mismatch_exits_two(tmp_path: Path) -> None:
    """Different remote sha → exit 2 with mismatch diagnostic."""
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    local_sha = "a" * 64
    remote_sha = "b" * 64
    stubs = _stub_dir(
        tmp_path,
        {
            "ssh": (
                'last="${@: -1}"; '
                f'case "$last" in '
                f"  *cutover_probe_ok*) exit 0 ;; "
                f"  *mkdir*) exit 0 ;; "
                f'  *sha256sum*) echo "{remote_sha}"; exit 0 ;; '
                f"  *) exit 99 ;; "
                f"esac"
            ),
            "scp": "exit 0",
            "sha256sum": f'echo "{local_sha}  $1"',
        },
    )
    result = _run(bash, sandbox, ["--prepare-cutover=kai@pi.local"], extra_path=stubs)
    assert result.returncode == 2
    assert "sha256 MISMATCH" in result.stderr
    assert local_sha in result.stderr
    assert remote_sha in result.stderr


def test_remote_root_override_threads_through(tmp_path: Path) -> None:
    """--remote-root=<custom> changes the path the SUT advertises in the
    Cutover header. Confirms the flag is parsed and applied, not silently
    dropped."""
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    fixed_sha = "0" * 64
    stubs = _stub_dir(
        tmp_path,
        {
            "ssh": (
                'last="${@: -1}"; '
                f'case "$last" in '
                f"  *cutover_probe_ok*) exit 0 ;; "
                f"  *mkdir*) exit 0 ;; "
                f'  *sha256sum*) echo "{fixed_sha}"; exit 0 ;; '
                f"  *) exit 99 ;; "
                f"esac"
            ),
            "scp": "exit 0",
            "sha256sum": f'echo "{fixed_sha}  $1"',
        },
    )
    result = _run(
        bash,
        sandbox,
        ["--prepare-cutover=kai@pi.local", "--remote-root=/srv/kai"],
        extra_path=stubs,
    )
    assert result.returncode == 0, result.stderr
    assert "/srv/kai/data/dev.db" in result.stdout
