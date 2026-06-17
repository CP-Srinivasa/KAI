"""Sovereign Bitcoin chain integration — KAI as read-only bitcoind client (L1).

Default-off, read-only, fail-closed. KAI's own node as the source of on-chain
truth (tip/fees/mempool/UTXO). See
KAI-mirror/kai_btc_ln_future_integration_20260616.md (Layer 1 / UC-4).
"""

from app.chain.adapter import ChainStatus, get_chain_status
from app.chain.client import BitcoindRpcClient, ChainInfo, ChainUnavailableError

__all__ = [
    "BitcoindRpcClient",
    "ChainInfo",
    "ChainStatus",
    "ChainUnavailableError",
    "get_chain_status",
]
