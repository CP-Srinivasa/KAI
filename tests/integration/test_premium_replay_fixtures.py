from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_premium_replay_fixture_matrix_passes() -> None:
    fixture = Path("tests/fixtures/latest_premium_signals.json")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.replay_premium_signals", "--fixture", str(fixture)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Signal" in result.stdout
    assert "Final State" in result.stdout
    assert "signals=15" in result.stdout
    assert "errors=0" in result.stdout
