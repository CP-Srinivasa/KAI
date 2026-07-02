#!/usr/bin/env python3
"""One-shot on-chain fee shadow capture (KAI L1, default-off, decoupled).

Records the REAL bitcoind fee + mempool depth to
``artifacts/onchain_fee_shadow.jsonl`` when the sovereign chain feature is
enabled (``APP_CHAIN_ENABLED=true`` + reachable node). Intended for a periodic
cron/timer; it is NOT wired into the trading loop and has NO capital path.

It is deliberately decoupled from the trading CostModel (exchange fees) — this
only captures the sovereign on-chain fee truth for the future on-chain/Lightning
settlement-cost layer.

Exit code: always 0 — a disabled or unreachable chain is a normal no-op, not an
error (the cron should not page on it).
"""

from __future__ import annotations

import asyncio

from app.chain.fee_shadow import record_onchain_fee_shadow


def main() -> int:
    rec = asyncio.run(record_onchain_fee_shadow())
    if rec is None:
        print("onchain-fee-shadow: no record (chain disabled or unavailable)")
    else:
        print(
            "onchain-fee-shadow: recorded "
            f"fee={rec.fee_sat_vb} sat/vB · mempool={rec.mempool_tx} tx · block {rec.blocks}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
