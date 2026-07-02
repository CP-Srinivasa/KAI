#!/usr/bin/env python3
"""Oneshot: build the Momentum cross-check snapshot (G4, informational).

READ-ONLY: own momentum rank vs own-TA rating (the ToS-compliant TradingView-
rating substitute computed from our OWN OHLCV — no scraping, no key). NO trades,
NO sizing. Fired by the kai-momentum-crosscheck timer. Fail-safe: any error is
logged and the process exits 0 (the unit's ``-`` ExecStart prefix also prevents
propagation); an empty universe keeps the last snapshot.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.observability.momentum_crosscheck import (  # noqa: E402
    append_crosscheck,
    build_crosscheck,
)

_LEDGER = Path("artifacts/momentum_crosscheck.jsonl")


def main() -> int:
    from app.market_data.bybit_adapter import BybitAdapter

    try:
        rows = asyncio.run(build_crosscheck(BybitAdapter(), top_n=15))
        if not rows:
            print("momentum_crosscheck: no universe snapshot — keeping last")
            return 0
        record = append_crosscheck(_LEDGER, rows, now=datetime.now(UTC))
        print(f"momentum_crosscheck: wrote {record['count']} rows")
        return 0
    except Exception as exc:  # noqa: BLE001 — informational, never break a timer
        print(f"momentum_crosscheck failed: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
