"""Sovereign Bitcoin chain (bitcoind RPC) integration settings.

KAI L1 — "Souveräne On-Chain-Wahrheit": KAI reads chain/mempool/fee truth from
its OWN bitcoind node (RaspiBlitz, over the WireGuard overlay) instead of trusting
a third-party API. Default-off, read-only, fail-closed — the trading loop is never
blocked by chain-node availability; if disabled or unreachable, callers fall back
to the existing market-data providers.

See KAI-mirror/kai_btc_ln_future_integration_20260616.md (Layer 1).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChainSettings(BaseSettings):
    """bitcoind JSON-RPC client config (read-only).

      - ``enabled=False`` (default): no chain surface is consulted anywhere.
      - ``enabled=True``: read-only JSON-RPC to the own node (getblockcount,
        getblockchaininfo, estimatesmartfee, getmempoolinfo, gettxout).

    Auth: either basic auth (``rpc_user`` + ``rpc_password``) or the bitcoind
    cookie file (``cookie_path``; ``__cookie__:<hex>``). bitcoind RPC is plain
    HTTP — only ever expose it over the WireGuard overlay, never the open LAN.
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_CHAIN_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    # bitcoind RPC endpoint (WireGuard overlay IP of the node, not the open LAN).
    host: str = Field(default="10.27.0.51")
    rpc_port: int = Field(default=8332, ge=1, le=65535)
    rpc_user: str = Field(default="", repr=False)
    rpc_password: str = Field(default="", repr=False)
    # Alternative to user/password: path to bitcoind's .cookie file.
    cookie_path: str = Field(default="")
    timeout_seconds: float = Field(default=8.0, gt=0)

    @property
    def base_url(self) -> str:
        # bitcoind RPC is HTTP; confidentiality comes from the WireGuard tunnel.
        return f"http://{self.host}:{self.rpc_port}"
