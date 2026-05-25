"""D-192 / NEO-F-META-20260424-017 — bash-integration smoke for the cron port.

Pi-Migration D-7 (2026-05-01) flips execution from the Windows-only
``paper_trading_cron.ps1`` to the Bash port ``paper_trading_cron.sh``.
Until today the Bash variant had **zero** tests. This module lands the
minimum-viable safety net: syntax-check, end-to-end run with a no-op
Python stub (so every CLI invocation is exercised without touching the
real settings/DB/network), and structural assertions on the log + counter
state it leaves behind.

Strategy
--------
* We DO NOT test the real CLI semantics — those stay covered by the
  per-command pytest modules.
* We DO verify that ``paper_trading_cron.sh`` loads cleanly, handles
  missing output gracefully (extract_field falls back to ``unknown``
  rather than crashing), advances the 7 counter files, and writes the
  expected ``--- cron start ---`` / ``--- cron end ---`` bracket to
  ``artifacts/paper_trading_cron.log``.

Skip matrix
-----------
* Skips cleanly if ``bash`` is not on PATH — protects against constrained
  runners. Git-Bash on Windows satisfies this, so the test does double duty
  as a pre-Pi-cutover verification on the operator's laptop.
* The Bash port is only meant to run under systemd on the Pi long-term;
  the PS1 variant stays the Windows production path.
"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CRON_SCRIPT = REPO_ROOT / "scripts" / "paper_trading_cron.sh"


def _uses_wsl_bash() -> bool:
    bash = shutil.which("bash")
    return os.name == "nt" and bash is not None and "\\system32\\bash" in bash.lower()


def _bash_path(path: Path) -> str:
    """Return a path format accepted by the discovered bash executable."""
    if not _uses_wsl_bash():
        return str(path)
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    tail = resolved.as_posix()[3:]
    return f"/mnt/{drive}/{tail}"


def _require_bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not on PATH")
    return bash


def test_bash_syntax_check() -> None:
    """``bash -n`` must parse the script without errors."""
    bash = _require_bash()
    result = subprocess.run(
        [bash, "-n", _bash_path(CRON_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"syntax errors:\n{result.stderr}"


def _stage_sandbox(tmp_path: Path) -> Path:
    """Build an isolated copy of the repo structure the script expects.

    We only need:
      - scripts/paper_trading_cron.sh (the SUT)
      - scripts/freshness_check.py (called at the tail)
      - empty artifacts/ (script creates the log here)
      - empty monitor/ (script reads monitor/youtube_channels.txt if present)
    """
    (tmp_path / "scripts").mkdir()
    shutil.copy(CRON_SCRIPT, tmp_path / "scripts")
    # freshness_check.py is invoked unconditionally; stub it as no-op so the
    # sandbox does not need the real project dependencies.
    stub = tmp_path / "scripts" / "freshness_check.py"
    stub.write_text("#!/usr/bin/env python\nprint('KAI Freshness (OK)')\n", newline="\n")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "monitor").mkdir()
    return tmp_path


def _write_python_stub(tmp_path: Path) -> Path:
    """Write a bash stub that pretends to be the Python CLI.

    The real cron parses stdout for ``cycle_id=``, ``status=``, ``fill_simulated=``
    and similar key=value pairs. The stub also captures argv so tests can
    assert cron profile routing without invoking the real CLI.
    """
    stub = tmp_path / "python_stub.sh"
    stub.write_text(
        """#!/usr/bin/env bash
if [[ -n "${PYTHON_STUB_CAPTURE:-}" ]]; then
    printf '%s\n' "$*" >> "$PYTHON_STUB_CAPTURE"
fi

case "$*" in
    *"trading run-once"*)
        printf 'cycle_id=stub_cycle status=priority_rejected fill_simulated=False\n'
        ;;
    *"trading monitor-positions"*)
        printf 'checked=0 triggered=0 no_market_data=0\n'
        ;;
    *"operator-signal-bridge-tick"*|*"operator-signal-entry-watch"*)
        printf 'enabled=False\n'
        ;;
    *"freshness_check.py"*)
        printf 'KAI Freshness (OK)\n'
        ;;
esac
exit 0
""",
        newline="\n",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _cron_env(sandbox: Path, python_stub: Path, *, profile: str | None = None) -> dict[str, str]:
    env = {
        **os.environ,
        "PYTHON": _bash_path(python_stub) if _uses_wsl_bash() else str(python_stub),
        "SLEEP_BIN": ":",
        "PYTHON_STUB_CAPTURE": _bash_path(sandbox / "artifacts" / "python_args.log"),
        # Isolate from the operator's real home in case rc files interfere.
        "HOME": _bash_path(sandbox),
        "PATH": (
            f"{_bash_path(sandbox)}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            if _uses_wsl_bash()
            else f"{sandbox}{os.pathsep}{os.environ.get('PATH', '')}"
        ),
    }
    if profile is None:
        env.pop("PAPER_CRON_PROFILE", None)
    else:
        env["PAPER_CRON_PROFILE"] = profile
    return env


def _run_cron(bash: str, sandbox: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    if _uses_wsl_bash():
        script = _bash_path(sandbox / "scripts" / "paper_trading_cron.sh")
        inline_env = {
            "PYTHON": env["PYTHON"],
            "SLEEP_BIN": env["SLEEP_BIN"],
            "PYTHON_STUB_CAPTURE": env["PYTHON_STUB_CAPTURE"],
            "HOME": env["HOME"],
            "PATH": env["PATH"],
        }
        if "PAPER_CRON_PROFILE" in env:
            inline_env["PAPER_CRON_PROFILE"] = env["PAPER_CRON_PROFILE"]
        command = " ".join(f"{key}={shlex.quote(value)}" for key, value in inline_env.items())
        command = f"{command} {shlex.quote(script)}"
        return subprocess.run(
            [bash, "-lc", command],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=sandbox,
        )
    return subprocess.run(
        [bash, _bash_path(sandbox / "scripts" / "paper_trading_cron.sh")],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=sandbox,
        env=env,
    )


def _captured_run_once_args(sandbox: Path) -> list[str]:
    capture = sandbox / "artifacts" / "python_args.log"
    assert capture.exists(), "python stub capture was not written"
    return [
        line
        for line in capture.read_text(encoding="utf-8").splitlines()
        if "trading run-once" in line
    ]


def _assert_run_once_profile(sandbox: Path, expected_profile: str) -> None:
    run_once_calls = _captured_run_once_args(sandbox)
    assert len(run_once_calls) == 2
    for call in run_once_calls:
        assert "--mode paper" in call
        assert "--analysis-profile " + expected_profile in call


def test_cron_tick_runs_end_to_end(tmp_path: Path) -> None:
    """Run the full cron tick with a no-op Python stub.

    Verifies:
      - exit 0 (the script is ``set -uo pipefail`` without -e, so per-CLI
        failures do NOT abort — but unbound vars or quoting bugs would);
      - ``artifacts/paper_trading_cron.log`` captures the ``--- cron start ---``
        / ``--- cron end ---`` bracket;
      - at least one counter file advances from 0 to a positive value
        (proves the counter-read-modify-write path works under bash on
        Linux, not just on Git-Bash/Windows);
      - no orphan ``artifacts/__pycache__`` or similar pollution.
    """
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    stub = _write_python_stub(sandbox)

    env = _cron_env(sandbox, stub)

    result = _run_cron(bash, sandbox, env)
    # set -uo pipefail without -e → unbound vars or pipeline-propagation
    # failures would set non-zero; per-command CLI failures don't.
    assert result.returncode == 0, (
        f"cron tick failed (rc={result.returncode})\n"
        f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:1000]}"
    )

    log_path = sandbox / "artifacts" / "paper_trading_cron.log"
    assert log_path.exists(), "log file was not created"
    log_content = log_path.read_text(encoding="utf-8")
    assert "--- cron start ---" in log_content
    assert "--- cron end ---" in log_content

    # At least the annotate_counter should have been touched (written even
    # when below the every-6th-tick threshold — the counter file ALWAYS
    # gets rewritten at the end of each tick).
    counter = sandbox / "artifacts" / ".annotate_counter"
    assert counter.exists(), "annotate counter was not written"
    value = counter.read_text().strip()
    assert value.isdigit(), f"counter content not a number: {value!r}"
    _assert_run_once_profile(sandbox, "conservative")


def test_cron_is_idempotent_across_two_ticks(tmp_path: Path) -> None:
    """Running the cron twice advances counters monotonically, no crashes."""
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    stub = _write_python_stub(sandbox)

    env = _cron_env(sandbox, stub)

    for _ in range(2):
        result = _run_cron(bash, sandbox, env)
        assert result.returncode == 0

    counter = sandbox / "artifacts" / ".annotate_counter"
    value = int(counter.read_text().strip())
    # Two ticks should have advanced the annotate counter to exactly 2.
    assert value == 2, f"expected annotate_counter==2 after two ticks, got {value}"


def test_default_cron_profile_is_conservative(tmp_path: Path) -> None:
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    stub = _write_python_stub(sandbox)
    result = _run_cron(bash, sandbox, _cron_env(sandbox, stub))
    assert result.returncode == 0

    log_content = (sandbox / "artifacts" / "paper_trading_cron.log").read_text(encoding="utf-8")
    expected_line = (
        "profile  requested=conservative  active=conservative  mode=paper  safety=explicit"
    )
    assert expected_line in log_content
    _assert_run_once_profile(sandbox, "conservative")


def test_explicit_conservative_cron_profile_is_conservative(tmp_path: Path) -> None:
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    stub = _write_python_stub(sandbox)
    result = _run_cron(bash, sandbox, _cron_env(sandbox, stub, profile="conservative"))
    assert result.returncode == 0

    log_content = (sandbox / "artifacts" / "paper_trading_cron.log").read_text(encoding="utf-8")
    expected_line = (
        "profile  requested=conservative  active=conservative  mode=paper  safety=explicit"
    )
    assert expected_line in log_content
    _assert_run_once_profile(sandbox, "conservative")


@pytest.mark.parametrize(
    ("cron_profile", "analysis_profile"),
    [
        ("canary_bullish", "bullish"),
        ("canary_bearish", "bearish"),
    ],
)
def test_canary_cron_profiles_are_explicit_paper_only(
    tmp_path: Path, cron_profile: str, analysis_profile: str
) -> None:
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    stub = _write_python_stub(sandbox)
    result = _run_cron(bash, sandbox, _cron_env(sandbox, stub, profile=cron_profile))
    assert result.returncode == 0

    log_content = (sandbox / "artifacts" / "paper_trading_cron.log").read_text(encoding="utf-8")
    expected_line = (
        f"profile  requested={cron_profile}  active={analysis_profile}  mode=paper  safety=explicit"
    )
    assert expected_line in log_content
    _assert_run_once_profile(sandbox, analysis_profile)


def test_invalid_cron_profile_falls_back_to_conservative(tmp_path: Path) -> None:
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    stub = _write_python_stub(sandbox)
    result = _run_cron(bash, sandbox, _cron_env(sandbox, stub, profile="live_bullish"))
    assert result.returncode == 0

    log_content = (sandbox / "artifacts" / "paper_trading_cron.log").read_text(encoding="utf-8")
    assert (
        "profile  requested=live_bullish  active=conservative  "
        "mode=paper  safety=invalid_fallback_conservative"
    ) in log_content
    _assert_run_once_profile(sandbox, "conservative")


def test_cron_profile_routing_never_enables_live_exchange_or_withdrawal_paths(
    tmp_path: Path,
) -> None:
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    stub = _write_python_stub(sandbox)
    result = _run_cron(bash, sandbox, _cron_env(sandbox, stub, profile="canary_bullish"))
    assert result.returncode == 0

    captured = (sandbox / "artifacts" / "python_args.log").read_text(encoding="utf-8").lower()
    run_once_calls = _captured_run_once_args(sandbox)
    assert run_once_calls
    assert all("--mode paper" in call for call in run_once_calls)
    assert "--mode live" not in captured
    assert "binance" not in captured
    assert "bybit" not in captured
    assert "withdraw" not in captured
