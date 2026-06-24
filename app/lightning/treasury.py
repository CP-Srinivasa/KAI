"""Sprint 7 — Self-Funding treasury accounting (UC-7, shadow-only).

Aggregates the inbound earnings ledger + the node's own balances into three
separated accounts — ``earnings`` (raw inflow) / ``operating`` (reserve for node
operation) / ``tradable`` (what COULD be allocated to trading) — so the dashboard
can answer "is KAI self-funding?" without ever moving capital (allocation is gated
at G2).

B-004 (anti-contamination): this layer is **sats only**. USD-at-time / BTC-beta is a
SEPARATE dimension and is NOT computed here — a self-funding claim must never
silently measure beta instead of edge. The treasury namespace is also strictly
separate from the trade/PnL ledger (no co-mingling). Pure, read-only.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

_CAVEAT = (
    "sats only — USD value and BTC-beta are a separate dimension (not computed here); "
    "'self-funding' is a KI-labelled hypothesis, never a sold forecast (B-004). "
    "tradable is a SHADOW projection — actual allocation is gated at G2."
)


def compute_treasury_snapshot(
    earnings: Sequence[dict[str, Any]],
    *,
    onchain_sat: int,
    channel_local_sat: int,
    operating_reserve_sat: int,
) -> dict[str, Any]:
    """Aggregate earnings + balances into earnings/operating/tradable (sats).

    ``operating`` is the reserve held back for node operation (capped at what is
    actually available); ``tradable`` is the remainder (never negative). No USD, no
    allocation, no spend.
    """
    earnings_total = 0
    by_source: dict[str, int] = {}
    for e in earnings:
        if not isinstance(e, dict):
            continue
        amt = int(e.get("amount_sat", 0) or 0)
        earnings_total += amt
        src = str(e.get("source", "unknown"))
        by_source[src] = by_source.get(src, 0) + amt

    node_total = int(onchain_sat) + int(channel_local_sat)
    operating = min(max(0, int(operating_reserve_sat)), node_total)
    tradable = max(0, node_total - operating)

    return {
        "currency": "sat",
        "earnings_total_sat": earnings_total,
        "earnings_by_source": by_source,
        "node_total_sat": node_total,
        "operating_sat": operating,
        "tradable_sat": tradable,
        "usd_value": None,  # B-004: USD is a separate, un-co-mingled dimension
        "caveat": _CAVEAT,
    }


__all__ = ["compute_treasury_snapshot"]
