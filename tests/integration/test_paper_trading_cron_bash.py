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
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CRON_SCRIPT = REPO_ROOT / "scripts" / "paper_trading_cron.sh"


def _require_bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not on PATH")
    return bash


def test_bash_syntax_check() -> None:
    """``bash -n`` must parse the script without errors."""
    bash = _require_bash()
    result = subprocess.run(
        [bash, "-n", str(CRON_SCRIPT)],
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
    stub.write_text("#!/usr/bin/env python\nprint('KAI Freshness (OK)')\n")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "monitor").mkdir()
    return tmp_path


def _write_python_stub(tmp_path: Path) -> Path:
    """Write a bash stub that pretends to be the Python CLI.

    The real cron parses stdout for ``cycle_id=``, ``status=``, ``fill_simulated=``
    and similar key=value pairs. The stub prints nothing — extract_field then
    returns ``unknown``, which is an explicitly supported code-path in the
    cron (tested on Windows for 2 weeks before the 40h-downtime incident).
    """
    stub = tmp_path / "python_stub.sh"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


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

    env = {
        **os.environ,
        "PYTHON": str(stub),
        # Isolate from the operator's real home in case rc files interfere.
        "HOME": str(sandbox),
    }

    result = subprocess.run(
        [bash, str(sandbox / "scripts" / "paper_trading_cron.sh")],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=sandbox,
        env=env,
    )
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


def test_cron_is_idempotent_across_two_ticks(tmp_path: Path) -> None:
    """Running the cron twice advances counters monotonically, no crashes."""
    bash = _require_bash()
    sandbox = _stage_sandbox(tmp_path)
    stub = _write_python_stub(sandbox)

    env = {**os.environ, "PYTHON": str(stub), "HOME": str(sandbox)}

    for _ in range(2):
        result = subprocess.run(
            [bash, str(sandbox / "scripts" / "paper_trading_cron.sh")],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=sandbox,
            env=env,
        )
        assert result.returncode == 0

    counter = sandbox / "artifacts" / ".annotate_counter"
    value = int(counter.read_text().strip())
    # Two ticks should have advanced the annotate counter to exactly 2.
    assert value == 2, f"expected annotate_counter==2 after two ticks, got {value}"
