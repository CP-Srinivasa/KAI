"""On-chain fee shadow recorder (KAI L1 observability, default-off).

Captures the REAL bitcoind fee estimate + mempool depth into a shadow stream when
the sovereign chain feature is enabled. It is DELIBERATELY DECOUPLED from the
trading ``CostModel`` (``app/execution/cost_model.py``): that model is exchange
maker/taker fees + spread + slippage and rightly has no on-chain input — folding
an on-chain sat/vB fee into ``total_cost_bps`` would corrupt exchange-trade costs.

What this is for: capturing the sovereign on-chain fee truth NOW so that when the
on-chain / Lightning settlement-cost layer (Phase 4/5) is eventually built, the
historical fee series already exists and a real-vs-estimate comparison is possible.

Guarantees: default-off (chain disabled → no-op, returns None), read-only (only
reads the chain status), append-only to its own shadow artifact, and fail-soft —
it NEVER raises and is NOT wired into the trading loop (no capital path).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.chain.adapter import get_chain_status
from app.core.chain_settings import ChainSettings

logger = logging.getLogger(__name__)

_SHADOW_PATH = Path("artifacts/onchain_fee_shadow.jsonl")


@dataclass(frozen=True)
class OnchainFeeShadowRecord:
    """One sovereign on-chain fee observation (shadow, no capital path)."""

    ts: str  # ISO-8601 UTC
    chain: str
    blocks: int
    fee_sat_vb: float | None  # bitcoind estimatesmartfee(6), best-effort
    mempool_tx: int


async def record_onchain_fee_shadow(
    cfg: ChainSettings | None = None,
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> OnchainFeeShadowRecord | None:
    """Append one on-chain fee shadow record IFF chain is reachable (state ``ok``).

    Returns the written record, or ``None`` when there was nothing to record
    (chain ``disabled``/``unavailable``) or when persistence failed (logged).
    Never raises — safe to call from a cron/timer.
    """
    status = await get_chain_status(cfg)
    if status.state != "ok":
        return None

    rec = OnchainFeeShadowRecord(
        ts=(now or datetime.now(UTC)).isoformat(),
        chain=status.chain,
        blocks=status.blocks,
        fee_sat_vb=status.fee_sat_vb,
        mempool_tx=status.mempool_tx,
    )
    out = path or _SHADOW_PATH
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec)) + "\n")
    except OSError as exc:
        logger.warning("[onchain-fee-shadow] persist failed: %s", exc)
        return None
    return rec
