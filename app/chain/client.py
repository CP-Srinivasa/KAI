"""Minimal read-only client for the bitcoind JSON-RPC API (KAI L1).

KAI talks to its OWN bitcoind node as a *read-only* client to obtain sovereign
on-chain truth (tip height, chain sync state, fee estimate, mempool state, UTXO
lookups). No wallet/spend RPCs are exposed here.

Resilience: every call raises :class:`ChainUnavailableError` on any transport,
auth or RPC error. The adapter translates that into a fail-closed status so the
trading loop is never blocked and callers can fall back to existing providers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


class ChainUnavailableError(RuntimeError):
    """Raised when the bitcoind node cannot be reached or returns an error."""


@dataclass(frozen=True)
class ChainInfo:
    chain: str
    blocks: int
    headers: int
    verification_progress: float
    initial_block_download: bool
    best_block_hash: str
    extra: dict[str, Any] = field(default_factory=dict)


def _resolve_auth(*, rpc_user: str, rpc_password: str, cookie_path: str) -> tuple[str, str]:
    """Resolve RPC basic-auth credentials.

    Prefers explicit user/password; otherwise reads bitcoind's ``.cookie`` file
    (format ``__cookie__:<hex>``). Raises ChainUnavailableError fail-closed if no
    usable credentials are configured.
    """
    if rpc_user and rpc_password:
        return rpc_user, rpc_password
    path = (cookie_path or "").strip()
    if not path:
        raise ChainUnavailableError("no RPC credentials configured (user/password or cookie)")
    try:
        raw = Path(path).read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ChainUnavailableError(f"cookie file unreadable: {exc}") from exc
    user, _, password = raw.partition(":")
    if not password:
        raise ChainUnavailableError("malformed cookie file (expected user:password)")
    return user, password


class BitcoindRpcClient:
    """Read-only async JSON-RPC client for bitcoind.

    Args:
        base_url:      e.g. ``http://10.27.0.51:8332``.
        rpc_user/rpc_password: basic-auth credentials (or use ``cookie_path``).
        cookie_path:   path to bitcoind ``.cookie`` (alternative to user/password).
        timeout:       per-request timeout in seconds.
        transport:     test seam; an injected transport bypasses real network.
    """

    def __init__(
        self,
        *,
        base_url: str,
        rpc_user: str = "",
        rpc_password: str = "",
        cookie_path: str = "",
        timeout: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = _resolve_auth(
            rpc_user=rpc_user, rpc_password=rpc_password, cookie_path=cookie_path
        )
        self._timeout = timeout
        self._transport = transport

    async def _call(self, method: str, params: list[Any] | None = None) -> Any:
        payload = {"jsonrpc": "1.0", "id": "kai", "method": method, "params": params or []}
        try:
            if self._transport is not None:
                client_kwargs: dict[str, Any] = {
                    "transport": self._transport,
                    "timeout": self._timeout,
                }
            else:
                client_kwargs = {"timeout": self._timeout}
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.post(self._base_url, json=payload, auth=self._auth)
        except httpx.HTTPError as exc:
            raise ChainUnavailableError(f"bitcoind request failed: {exc}") from exc
        if resp.status_code != 200:
            raise ChainUnavailableError(
                f"bitcoind returned {resp.status_code} for {method}: {resp.text[:200]}"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise ChainUnavailableError(f"bitcoind returned non-JSON for {method}") from exc
        if body.get("error"):
            raise ChainUnavailableError(f"bitcoind RPC error for {method}: {body['error']}")
        return body.get("result")

    async def get_block_count(self) -> int:
        return int(await self._call("getblockcount"))

    async def get_blockchain_info(self) -> ChainInfo:
        d = await self._call("getblockchaininfo")
        return ChainInfo(
            chain=str(d.get("chain", "")),
            blocks=int(d.get("blocks", 0) or 0),
            headers=int(d.get("headers", 0) or 0),
            verification_progress=float(d.get("verificationprogress", 0.0) or 0.0),
            initial_block_download=bool(d.get("initialblockdownload", False)),
            best_block_hash=str(d.get("bestblockhash", "")),
            extra=d,
        )

    async def estimate_smart_fee(self, conf_target: int = 6) -> float | None:
        """Return the fee estimate in sat/vByte for ``conf_target`` blocks.

        bitcoind returns BTC/kvB; we convert to sat/vByte. Returns None if the
        node cannot produce an estimate yet (``feerate`` absent).
        """
        d = await self._call("estimatesmartfee", [conf_target])
        feerate_btc_kvb = d.get("feerate")
        if feerate_btc_kvb is None:
            return None
        # BTC/kvB -> sat/vByte: * 1e8 sat/BTC / 1000 vB/kvB = * 1e5
        return round(float(feerate_btc_kvb) * 100_000, 3)

    async def get_mempool_info(self) -> dict[str, Any]:
        result: dict[str, Any] = await self._call("getmempoolinfo")
        return result

    async def get_txout(self, txid: str, vout: int) -> dict[str, Any] | None:
        """gettxout — returns the UTXO, or None if spent/absent (read-only)."""
        result: dict[str, Any] | None = await self._call("gettxout", [txid, vout])
        return result
