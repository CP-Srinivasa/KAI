"""Premium-Fastlane end-to-end replay (Goal 2026-06-05 §20/§21).

Drives the 15-symbol fixture through the deterministic fastlane replay and pins
the contract: every complete signal reaches at least order_submitted /
pending_entry / requires_scale_review, with zero errors and no signal blocked on
approval / allowlist / entry_mode / quality.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.replay_premium_fastlane import run_replay

_FIXTURE = Path("tests/fixtures/latest_premium_signals.json")


def test_fastlane_replay_counts() -> None:
    totals = run_replay(_FIXTURE)
    assert totals.signals == 15
    assert totals.errors == 0
    assert totals.fastlane_valid == 15
    # every signal reaches a terminal-ish fastlane outcome
    reached = totals.orders_submitted + totals.pending_entries + totals.requires_scale_review
    assert reached == 15
    assert totals.orders_submitted >= 1
    assert totals.requires_scale_review >= 1
    assert totals.schema_rejected == 0


def test_fastlane_replay_no_zero_quantity_orders() -> None:
    totals = run_replay(_FIXTURE)
    for row in totals.rows:
        if row.get("stage") in {"order_submitted", "pending_entry"}:
            assert row.get("quantity", 0) > 0, row
            assert row.get("notional_usdt", 0) > 0, row
            assert row.get("leverage", 0) <= 10, row


def test_fastlane_replay_cli_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.replay_premium_fastlane", "--fixture", str(_FIXTURE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "signals=15" in result.stdout
    assert "errors=0" in result.stdout
