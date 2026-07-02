"""kai-server health watchdog — auto-recover from event-loop wedges.

Defense-in-depth for the 2026-05-29 incident class: if the kai-server event loop
is starved (e.g. a synchronous extraction batch), ``/health`` stops responding
while the process stays "active" — a silent multi-hour outage. This watchdog
polls ``/health`` with a short timeout, tracks consecutive failures in a small
state file, and restarts ``kai-server`` only after ``threshold`` consecutive
failures (with a cooldown), turning a silent wedge into a ~minutes auto-recovery.

Conservative by design:
  - dry-run by default; ``--apply`` is required to actually restart
  - restart only after N *consecutive* failures (transient blips ignored)
  - cooldown after a restart so it can never restart-loop

    python -m scripts.kai_server_health_watchdog            # dry-run probe
    python -m scripts.kai_server_health_watchdog --apply    # may restart (cron)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_STATE = Path("artifacts/kai_server_watchdog_state.json")
_HEALTH_URL = "http://localhost:8000/health"


@dataclass(frozen=True)
class WatchdogDecision:
    healthy: bool
    consecutive_failures: int
    should_restart: bool
    reason: str


def decide(
    *,
    healthy: bool,
    prev_consecutive_failures: int,
    last_restart_epoch: float,
    now_epoch: float,
    threshold: int,
    cooldown_s: float,
) -> WatchdogDecision:
    """Pure decision: restart iff threshold consecutive failures + cooldown passed."""
    if healthy:
        return WatchdogDecision(True, 0, False, "healthy")
    consecutive = prev_consecutive_failures + 1
    if consecutive < threshold:
        return WatchdogDecision(False, consecutive, False, f"failures {consecutive}/{threshold}")
    if (now_epoch - last_restart_epoch) < cooldown_s:
        return WatchdogDecision(
            False, consecutive, False, f"threshold reached but in cooldown ({cooldown_s:.0f}s)"
        )
    return WatchdogDecision(True, consecutive, True, f"{consecutive} consecutive failures")


def _probe(url: str, timeout_s: float) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:  # noqa: S310 — localhost only
            return bool(200 <= resp.status < 300)
    except Exception:  # noqa: BLE001 — any failure = unhealthy
        return False


def _load_state() -> dict[str, float]:
    default: dict[str, float] = {"consecutive_failures": 0, "last_restart_epoch": 0.0}
    if not _STATE.exists():
        return default
    try:
        data = json.loads(_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return data if isinstance(data, dict) else default


def _save_state(state: dict[str, float]) -> None:
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    _STATE.write_text(json.dumps(state), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=_HEALTH_URL)
    ap.add_argument("--timeout-s", type=float, default=6.0)
    ap.add_argument("--threshold", type=int, default=3)
    ap.add_argument("--cooldown-s", type=float, default=300.0)
    ap.add_argument("--service", default="kai-server")
    ap.add_argument("--apply", action="store_true", help="actually restart (default: dry-run)")
    args = ap.parse_args()

    healthy = _probe(args.url, args.timeout_s)
    state = _load_state()
    now = time.time()
    decision = decide(
        healthy=healthy,
        prev_consecutive_failures=int(state.get("consecutive_failures", 0)),
        last_restart_epoch=float(state.get("last_restart_epoch", 0.0)),
        now_epoch=now,
        threshold=args.threshold,
        cooldown_s=args.cooldown_s,
    )
    print(
        f"health={'ok' if healthy else 'DOWN'} consecutive={decision.consecutive_failures} "
        f"restart={decision.should_restart} reason={decision.reason}"
    )

    state["consecutive_failures"] = decision.consecutive_failures
    if decision.should_restart and args.apply:
        try:
            subprocess.run(["sudo", "-n", "systemctl", "restart", args.service], timeout=60)
            state["consecutive_failures"] = 0
            state["last_restart_epoch"] = now
            print(f"RESTARTED {args.service}")
        except Exception as exc:  # noqa: BLE001
            print(f"restart failed: {exc}")
    elif decision.should_restart:
        print(f"DRY-RUN — would restart {args.service} (re-run with --apply)")
    _save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
