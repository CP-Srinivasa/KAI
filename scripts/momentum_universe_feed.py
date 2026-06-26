#!/usr/bin/env python3
"""Oneshot: feed the Momentum-Universe into PAPER (G2). Gated, default-off.

PAPER only — NO capital. Turns top-N universe symbols (minus those the G1
rotation FSM flagged/archived) into LONG paper signals tagged
``analysis_source="momentum_universe"``. Fired by the kai-momentum-universe-feed
timer. Fail-safe: any error is logged and the process exits 0 (the unit's ``-``
ExecStart prefix also prevents propagation); ``run_momentum_feeder`` is a no-op
unless ``MOMENTUM_UNIVERSE_FEED_ENABLED=true``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.observability.momentum_universe_feeder import run_momentum_feeder  # noqa: E402


def main() -> int:
    try:
        result = asyncio.run(run_momentum_feeder())
        print(f"momentum_universe_feed: {result}")
        return 0
    except Exception as exc:  # noqa: BLE001 — paper feeder, never break a timer
        print(f"momentum_universe_feed failed: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
