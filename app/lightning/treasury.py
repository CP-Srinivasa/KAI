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
from dataclasses import dataclass, field
from typing import Any

from app.core.settings import LightningSettings, get_settings
from app.lightning.client import LightningUnavailableError, LndRestClient

_CAVEAT = (
    "sats only — USD value and BTC-beta are a separate dimension (not computed here); "
    "'self-funding' is a KI-labelled hypothesis, never a sold forecast (B-004). "
    "tradable is a SHADOW projection — actual allocation is gated at G2. "
    "total_limbo_sat is reported separately and is NEVER available/tradable capital."
)


@dataclass(frozen=True)
class PendingForceClose:
    """One force-closing channel from lnd ``pendingchannels`` (read-only)."""

    channel_point: str
    remote_pubkey: str
    closing_txid: str
    capacity_sat: int
    limbo_balance_sat: int
    recovered_balance_sat: int
    maturity_height: int
    blocks_til_maturity: int


@dataclass(frozen=True)
class PendingChannelsSnapshot:
    """Pending-channel truth kept separate from spendable node balances."""

    state: str
    total_limbo_sat: int = 0
    pending_open_count: int = 0
    pending_closing_count: int = 0
    pending_force_closing_count: int = 0
    waiting_close_count: int = 0
    force_closes: list[PendingForceClose] = field(default_factory=list)
    reason: str = ""


def _as_int(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("sat")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _rows(raw: dict[str, Any], key: str) -> list[Any]:
    value = raw.get(key, [])
    return value if isinstance(value, list) else []


def parse_pending_channels(raw: dict[str, Any]) -> PendingChannelsSnapshot:
    """Parse lnd's pendingchannels response without treating limbo as available."""
    pending_open = _rows(raw, "pending_open_channels")
    pending_closing = _rows(raw, "pending_closing_channels")
    pending_force = _rows(raw, "pending_force_closing_channels")
    waiting_close = _rows(raw, "waiting_close_channels")

    force_closes: list[PendingForceClose] = []
    for entry in pending_force:
        if not isinstance(entry, dict):
            continue
        channel = entry.get("channel")
        channel = channel if isinstance(channel, dict) else {}
        force_closes.append(
            PendingForceClose(
                channel_point=str(channel.get("channel_point", "")),
                remote_pubkey=str(channel.get("remote_node_pub", "")),
                closing_txid=str(entry.get("closing_txid", "")),
                capacity_sat=max(0, _as_int(channel.get("capacity"))),
                limbo_balance_sat=max(0, _as_int(entry.get("limbo_balance"))),
                recovered_balance_sat=max(0, _as_int(entry.get("recovered_balance"))),
                maturity_height=_as_int(entry.get("maturity_height")),
                blocks_til_maturity=_as_int(entry.get("blocks_til_maturity")),
            )
        )

    if "total_limbo_balance" in raw:
        total_limbo = max(0, _as_int(raw.get("total_limbo_balance")))
    else:
        total_limbo = sum(item.limbo_balance_sat for item in force_closes)
        total_limbo += sum(
            max(0, _as_int(item.get("limbo_balance")))
            for item in waiting_close
            if isinstance(item, dict)
        )
    return PendingChannelsSnapshot(
        state="ok",
        total_limbo_sat=total_limbo,
        pending_open_count=len(pending_open),
        pending_closing_count=len(pending_closing),
        pending_force_closing_count=len(pending_force),
        waiting_close_count=len(waiting_close),
        force_closes=force_closes,
    )


async def get_pending_channels_snapshot(
    cfg: LightningSettings | None = None,
) -> PendingChannelsSnapshot:
    """Fetch ``pendingchannels`` read-only; disabled/unavailable never look like zero truth."""
    cfg = cfg or get_settings().lightning
    if not cfg.enabled:
        return PendingChannelsSnapshot(state="disabled", reason="lightning disabled")
    try:
        client = LndRestClient(
            base_url=cfg.base_url,
            macaroon_hex=cfg.macaroon_hex,
            macaroon_path=cfg.macaroon_path,
            tls_cert_path=cfg.tls_cert_path,
            timeout=cfg.timeout_seconds,
        )
        return parse_pending_channels(await client.pending_channels())
    except LightningUnavailableError as exc:
        return PendingChannelsSnapshot(state="unavailable", reason=str(exc))
    except Exception as exc:  # noqa: BLE001 — dashboard read path is fail-soft
        return PendingChannelsSnapshot(state="unavailable", reason=f"unexpected: {exc}")


def compute_treasury_snapshot(
    earnings: Sequence[dict[str, Any]],
    *,
    onchain_sat: int,
    channel_local_sat: int,
    operating_reserve_sat: int,
    total_limbo_sat: int = 0,
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
        # Limbo is a claim under recovery, not a wallet/channel balance available
        # to spend. Surface it, but never add it to node_total or tradable.
        "total_limbo_sat": max(0, int(total_limbo_sat)),
        "usd_value": None,  # B-004: USD is a separate, un-co-mingled dimension
        "caveat": _CAVEAT,
    }


__all__ = [
    "PendingChannelsSnapshot",
    "PendingForceClose",
    "compute_treasury_snapshot",
    "get_pending_channels_snapshot",
    "parse_pending_channels",
]
