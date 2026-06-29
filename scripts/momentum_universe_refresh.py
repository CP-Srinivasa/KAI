#!/usr/bin/env python3
"""Decoupled refresh: build the Momentum-Universe snapshot + append to the ledger.

READ-ONLY: NO trades, NO capital. Mirrors ``funding_cache_refresh`` — a oneshot
the systemd timer fires periodically; the dashboard/API only ever read the warm
``artifacts/momentum_universe_candidates.jsonl`` snapshot.

Fail-safe: ``build_universe`` is already fail-soft (a dead source yields ``[]``);
this refresher then KEEPS the last good snapshot rather than overwriting it with
an empty one. Any unexpected error is logged and the process exits 0 (the unit's
``-`` ExecStart prefix also prevents propagation).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.observability.momentum_universe_builder import (  # noqa: E402
    MomentumUniverseSource,
    build_universe,
)
from app.observability.momentum_universe_ledger import append_snapshot  # noqa: E402
from app.observability.symbol_eligibility_ledger import (  # noqa: E402
    append_eligibility_snapshot,
)
from app.trading.symbol_eligibility_fetch import build_eligibility  # noqa: E402

_LEDGER = Path("artifacts/momentum_universe_candidates.jsonl")
_ELIG_LEDGER = Path("artifacts/symbol_eligibility_audit.jsonl")
_DEADLINE_S = 120.0
_ELIG_DEADLINE_S = 60.0
_TOP_N = 15
_UNIVERSE_LIMIT = 50


async def _run(
    source: MomentumUniverseSource,
    ledger: Path,
    elig_ledger: Path,
) -> int:
    ranked = await asyncio.wait_for(
        build_universe(source, top_n=_TOP_N, universe_limit=_UNIVERSE_LIMIT),
        _DEADLINE_S,
    )
    if not ranked:
        print("momentum_universe_refresh: source unavailable — keeping last snapshot")
        return 0

    # Shadow eligibility against the CANONICAL venue (Binance) — flags, never filters.
    elig_map: dict[str, dict[str, object]] = {}
    try:
        from app.market_data.binance_adapter import BinanceAdapter

        verdicts = await asyncio.wait_for(
            build_eligibility(BinanceAdapter(), [r.symbol for r in ranked]), _ELIG_DEADLINE_S
        )
        append_eligibility_snapshot(elig_ledger, verdicts, now=datetime.now(UTC))
        elig_map = {v.symbol: {"eligible": v.eligible, "reasons": v.reasons} for v in verdicts}
    except Exception as exc:  # noqa: BLE001 — eligibility is shadow; never break the refresh
        print(f"momentum_universe_refresh: eligibility skipped ({exc})", file=sys.stderr)

    record = append_snapshot(ledger, ranked, now=datetime.now(UTC), eligibility=elig_map or None)
    print(f"momentum_universe_refresh: wrote {record['count']} symbols")
    return 0


def main() -> int:
    from app.market_data.bybit_adapter import BybitAdapter

    try:
        return asyncio.run(_run(BybitAdapter(), _LEDGER, _ELIG_LEDGER))
    except Exception as exc:  # noqa: BLE001 — fail-safe: keep the last good snapshot
        print(f"momentum_universe_refresh failed: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
