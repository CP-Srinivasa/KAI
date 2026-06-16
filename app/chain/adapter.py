"""KAI-facing sovereign-chain adapter (fail-closed, default-off) — L1.

The only surface the rest of KAI imports for chain truth. Guarantees:
  * **default-off** — when ``settings.chain.enabled`` is False it returns a
    ``disabled`` status without any network call.
  * **fail-closed** — any node/transport/auth error is caught and surfaced as an
    ``unavailable`` status; it NEVER raises into the trading loop. Callers fall
    back to existing market-data providers.

Read-only by construction; no wallet/spend paths exist here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.chain.client import BitcoindRpcClient, ChainUnavailableError
from app.core.chain_settings import ChainSettings
from app.core.settings import get_settings


@dataclass(frozen=True)
class ChainStatus:
    """Snapshot of KAI's own bitcoind node.

    ``state``: ``disabled`` (feature off) / ``unavailable`` (enabled but node not
    reachable) / ``ok`` (reachable). ``fee_sat_vb`` is best-effort (may be None).
    """

    state: str
    reachable: bool
    chain: str = ""
    blocks: int = 0
    headers: int = 0
    synced: bool = False
    fee_sat_vb: float | None = None
    mempool_tx: int = 0
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def disabled(cls) -> ChainStatus:
        return cls(state="disabled", reachable=False, reason="chain disabled")

    @classmethod
    def unavailable(cls, reason: str) -> ChainStatus:
        return cls(state="unavailable", reachable=False, reason=reason)


def _build_client(cfg: ChainSettings) -> BitcoindRpcClient:
    return BitcoindRpcClient(
        base_url=cfg.base_url,
        rpc_user=cfg.rpc_user,
        rpc_password=cfg.rpc_password,
        cookie_path=cfg.cookie_path,
        timeout=cfg.timeout_seconds,
    )


async def get_chain_status(cfg: ChainSettings | None = None) -> ChainStatus:
    """Return the own-node chain status, never raising.

    ``synced`` means blocks==headers and not in initial block download. The fee
    estimate and mempool size are best-effort enrichment (a failure there does not
    flip a reachable node to unavailable).
    """
    cfg = cfg or get_settings().chain
    if not cfg.enabled:
        return ChainStatus.disabled()
    try:
        client = _build_client(cfg)
        info = await client.get_blockchain_info()
    except ChainUnavailableError as exc:
        return ChainStatus.unavailable(str(exc))
    except Exception as exc:  # noqa: BLE001 — adapter must never leak into the loop
        return ChainStatus.unavailable(f"unexpected: {exc}")

    fee: float | None = None
    mempool_tx = 0
    try:
        fee = await client.estimate_smart_fee(6)
        mempool_tx = int((await client.get_mempool_info()).get("size", 0) or 0)
    except Exception:  # noqa: BLE001 — enrichment is best-effort, node stays reachable
        pass

    synced = info.blocks == info.headers and not info.initial_block_download
    return ChainStatus(
        state="ok",
        reachable=True,
        chain=info.chain,
        blocks=info.blocks,
        headers=info.headers,
        synced=synced,
        fee_sat_vb=fee,
        mempool_tx=mempool_tx,
    )
