"""kai-server health-watchdog decision logic (2026-05-29 wedge defense-in-depth).

The existing pi_service_watchdog restarts only on `systemctl is-active` failure;
during the event-loop wedge kai-server stayed *active* but unresponsive, so it
was never restarted. This watchdog restarts on /health unresponsiveness — these
tests lock in the consecutive-failure + cooldown logic so it can't restart-loop.
"""

from __future__ import annotations

from scripts.kai_server_health_watchdog import decide


def test_healthy_resets_failures() -> None:
    d = decide(
        healthy=True,
        prev_consecutive_failures=2,
        last_restart_epoch=0.0,
        now_epoch=1000.0,
        threshold=3,
        cooldown_s=300.0,
    )
    assert d.healthy and d.consecutive_failures == 0 and not d.should_restart


def test_single_failure_does_not_restart() -> None:
    d = decide(
        healthy=False,
        prev_consecutive_failures=0,
        last_restart_epoch=0.0,
        now_epoch=1000.0,
        threshold=3,
        cooldown_s=300.0,
    )
    assert d.consecutive_failures == 1 and not d.should_restart


def test_threshold_failures_trigger_restart() -> None:
    d = decide(
        healthy=False,
        prev_consecutive_failures=2,
        last_restart_epoch=0.0,
        now_epoch=10_000.0,
        threshold=3,
        cooldown_s=300.0,
    )
    assert d.consecutive_failures == 3 and d.should_restart


def test_cooldown_blocks_restart_loop() -> None:
    d = decide(
        healthy=False,
        prev_consecutive_failures=5,
        last_restart_epoch=9900.0,
        now_epoch=10_000.0,  # only 100s since last restart < 300s cooldown
        threshold=3,
        cooldown_s=300.0,
    )
    assert not d.should_restart and "cooldown" in d.reason


def test_restart_allowed_after_cooldown() -> None:
    d = decide(
        healthy=False,
        prev_consecutive_failures=5,
        last_restart_epoch=9000.0,
        now_epoch=10_000.0,  # 1000s > 300s cooldown
        threshold=3,
        cooldown_s=300.0,
    )
    assert d.should_restart
