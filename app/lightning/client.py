"""Minimal async client for the lnd REST API (read-only, Phase 1).

KAI talks to the RaspiBlitz node as a *client* only. Phase 1 uses the
``readonly.macaroon`` and never touches any write/send endpoint. Authentication
is the standard lnd scheme: the hex-encoded macaroon in the
``Grpc-Metadata-macaroon`` header, TLS verified against the node's ``tls.cert``.

lnd REST docs: https://lightning.engineering/api-docs/api/lnd/

Resilience: every call raises :class:`LightningUnavailableError` on any transport,
TLS, auth or non-2xx error. Callers (the adapter) translate that into a
fail-closed status so the trading loop is never blocked by Lightning.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

# OpenChannelSync (POST /v1/channels) blocks while lnd runs the funding workflow
# with the remote peer — coin selection, commitment negotiation, funding-tx build —
# which legitimately takes tens of seconds. The regular per-request timeout is
# sized for reads and would abort mid-funding with an ambiguous outcome.
OPEN_CHANNEL_TIMEOUT_SECONDS = 120.0


class LightningUnavailableError(RuntimeError):
    """Raised when the lnd node cannot be reached or returns an error."""


@dataclass(frozen=True)
class LndInfo:
    identity_pubkey: str
    alias: str
    version: str
    block_height: int
    synced_to_chain: bool
    synced_to_graph: bool
    num_peers: int
    num_active_channels: int
    num_pending_channels: int
    extra: dict[str, Any] = field(default_factory=dict)


def _load_macaroon_hex(*, macaroon_hex: str, macaroon_path: str) -> str:
    """Resolve the macaroon to a hex string.

    Prefers an explicit hex value; otherwise reads the binary macaroon file and
    hex-encodes it. Raises LightningUnavailableError if neither is usable so the
    failure is fail-closed rather than a silent unauthenticated request.
    """
    hexed = (macaroon_hex or "").strip()
    if hexed:
        return hexed
    path = (macaroon_path or "").strip()
    if not path:
        raise LightningUnavailableError("no macaroon configured (hex or path)")
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise LightningUnavailableError(f"macaroon file unreadable: {exc}") from exc
    return binascii.hexlify(raw).decode("ascii")


class LndRestClient:
    """Read-only async client for a subset of the lnd REST API.

    Args:
        base_url:      e.g. ``https://192.168.178.51:8080``.
        macaroon_hex:  hex-encoded macaroon (takes precedence over the path).
        macaroon_path: path to a binary macaroon file (e.g. readonly.macaroon).
        tls_cert_path: path to the node's ``tls.cert`` used as the CA. Empty
                       string disables verification (NOT recommended; only for
                       throwaway local testing).
        timeout:       per-request timeout in seconds.
    """

    def __init__(
        self,
        *,
        base_url: str,
        macaroon_hex: str = "",
        macaroon_path: str = "",
        tls_cert_path: str = "",
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._macaroon_hex = _load_macaroon_hex(
            macaroon_hex=macaroon_hex, macaroon_path=macaroon_path
        )
        # httpx verify: a path string is used as the CA bundle; False disables.
        self._verify: str | bool = tls_cert_path if tls_cert_path else False
        self._timeout = timeout
        # Test seam: an injected transport bypasses real TLS/network.
        self._transport = transport

    @property
    def _headers(self) -> dict[str, str]:
        return {"Grpc-Metadata-macaroon": self._macaroon_hex}

    async def _get(self, path: str) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            if self._transport is not None:
                client_kwargs: dict[str, Any] = {
                    "transport": self._transport,
                    "timeout": self._timeout,
                }
            else:
                client_kwargs = {"verify": self._verify, "timeout": self._timeout}
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(url, headers=self._headers)
        except httpx.HTTPError as exc:
            raise LightningUnavailableError(
                f"lnd request failed: {type(exc).__name__}: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise LightningUnavailableError(
                f"lnd returned {resp.status_code} for {path}: {resp.text[:200]}"
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise LightningUnavailableError(f"lnd returned non-JSON for {path}") from exc
        if not isinstance(data, dict):
            raise LightningUnavailableError(f"lnd returned non-object JSON for {path}")
        return data

    async def _stream_get_last(self, path: str) -> dict[str, Any]:
        """Consume one lnd server-streaming REST response and return its last item.

        grpc-gateway renders streaming messages as newline-delimited JSON objects,
        normally wrapped as ``{"result": ...}``.  A transport failure or a stream
        that ends without any valid object is UNKNOWN, never proof of failure.
        """
        url = f"{self._base_url}{path}"
        try:
            if self._transport is not None:
                client_kwargs: dict[str, Any] = {
                    "transport": self._transport,
                    "timeout": self._timeout,
                }
            else:
                client_kwargs = {"verify": self._verify, "timeout": self._timeout}
            async with httpx.AsyncClient(**client_kwargs) as client:
                async with client.stream("GET", url, headers=self._headers) as resp:
                    if resp.status_code != 200:
                        raw = (await resp.aread()).decode("utf-8", errors="replace")
                        raise LightningUnavailableError(
                            f"lnd returned {resp.status_code} for {path}: {raw[:200]}"
                        )
                    latest: dict[str, Any] | None = None
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            message = json.loads(line)
                        except ValueError as exc:
                            raise LightningUnavailableError(
                                f"lnd returned malformed stream JSON for {path}"
                            ) from exc
                        if not isinstance(message, dict):
                            continue
                        if "error" in message:
                            raise LightningUnavailableError(
                                f"lnd stream error for {path}: {str(message['error'])[:200]}"
                            )
                        result = message.get("result", message)
                        if isinstance(result, dict):
                            latest = result
        except LightningUnavailableError:
            raise
        except httpx.HTTPError as exc:
            raise LightningUnavailableError(
                f"lnd stream failed: {type(exc).__name__}: {exc}"
            ) from exc
        if latest is None:
            raise LightningUnavailableError(f"lnd returned an empty stream for {path}")
        return latest

    async def get_state(self) -> str:
        """GET /v1/state — cheap readiness probe (no wallet/chain work).

        Returns the wallet/server state string (e.g. ``SERVER_ACTIVE``). This is
        the lnd-recommended liveness signal: it stays fast even when ``getinfo``
        is slow (e.g. while lnd resolves its Tor ``uris`` after a restart).
        """
        data = await self._get("/v1/state")
        return str(data.get("state", ""))

    async def get_info(self) -> LndInfo:
        """GET /v1/getinfo — node identity + chain/graph sync state."""
        data = await self._get("/v1/getinfo")
        return LndInfo(
            identity_pubkey=str(data.get("identity_pubkey", "")),
            alias=str(data.get("alias", "")),
            version=str(data.get("version", "")),
            block_height=int(data.get("block_height", 0) or 0),
            synced_to_chain=bool(data.get("synced_to_chain", False)),
            synced_to_graph=bool(data.get("synced_to_graph", False)),
            num_peers=int(data.get("num_peers", 0) or 0),
            num_active_channels=int(data.get("num_active_channels", 0) or 0),
            num_pending_channels=int(data.get("num_pending_channels", 0) or 0),
            extra=data,
        )

    async def channel_balance(self) -> dict[str, Any]:
        """GET /v1/balance/channels — off-chain balances (read-only)."""
        return await self._get("/v1/balance/channels")

    async def fee_report(self) -> dict[str, Any]:
        """GET /v1/fees — routing fee report (read-only)."""
        return await self._get("/v1/fees")

    async def wallet_balance(self) -> dict[str, Any]:
        """GET /v1/balance/blockchain — on-chain wallet balance (read-only)."""
        return await self._get("/v1/balance/blockchain")

    async def list_channels(self) -> dict[str, Any]:
        """GET /v1/channels — open channels with per-channel balances (read-only)."""
        return await self._get("/v1/channels")

    async def pending_channels(self) -> dict[str, Any]:
        """GET /v1/channels/pending — channels awaiting confirmations/close (read-only).

        Covers ``pending_open_channels`` (funding tx broadcast, not yet enough
        confs) plus force-close/waiting-close limbo — without this an open that
        was JUST funded is invisible to every channel view."""
        return await self._get("/v1/channels/pending")

    # ── write surface (value layer; gated) ────────────────────────────────────
    # Used ONLY by app.lightning.value_layer behind the pay_enabled kill-switch.
    # Requires a SCOPE-MINIMAL macaroon (invoices / channel-open) — NEVER the
    # readonly macaroon, NEVER admin. Read-only Phase-1 deployments never reach
    # these (the value layer refuses while pay_enabled is False).
    async def _post(
        self, path: str, body: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        effective_timeout = self._timeout if timeout is None else timeout
        try:
            if self._transport is not None:
                client_kwargs: dict[str, Any] = {
                    "transport": self._transport,
                    "timeout": effective_timeout,
                }
            else:
                client_kwargs = {"verify": self._verify, "timeout": effective_timeout}
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.post(url, headers=self._headers, json=body)
        except httpx.HTTPError as exc:
            raise LightningUnavailableError(
                f"lnd request failed: {type(exc).__name__}: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise LightningUnavailableError(
                f"lnd returned {resp.status_code} for {path}: {resp.text[:200]}"
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise LightningUnavailableError(f"lnd returned non-JSON for {path}") from exc
        if not isinstance(data, dict):
            raise LightningUnavailableError(f"lnd returned non-object JSON for {path}")
        return data

    async def add_invoice(
        self, *, value_sat: int, memo: str = "", expiry_seconds: int = 300
    ) -> dict[str, Any]:
        """POST /v1/invoices — create a BOLT11 invoice (RECEIVE side, no spend).

        ``expiry_seconds`` caps how long an UNPAID invoice lingers on the node (DB row
        + HTLC-slot expectation). Short by default so unpaid L402 challenges cannot
        accumulate; the caller simply re-mints on demand. ``<=0`` falls back to the
        node default (not recommended)."""
        body: dict[str, Any] = {"value": str(int(value_sat)), "memo": memo}
        if expiry_seconds > 0:
            body["expiry"] = str(int(expiry_seconds))
        return await self._post("/v1/invoices", body)

    async def list_invoices(self, *, num_max_invoices: int = 1000) -> list[dict[str, Any]]:
        """GET /v1/invoices — list this node's OWN invoices (read-only, capital-free).

        Returns the ``invoices`` array (``[]`` on a malformed response). Used by the
        earnings-booking job to find settled inbound payments."""
        data = await self._get(f"/v1/invoices?num_max_invoices={int(num_max_invoices)}")
        invoices = data.get("invoices", [])
        return invoices if isinstance(invoices, list) else []

    async def open_channel(
        self, *, node_pubkey_hex: str, local_funding_sat: int, sat_per_vbyte: int = 0
    ) -> dict[str, Any]:
        """POST /v1/channels — open a channel (SPENDS on-chain; irreversible).

        OpenChannelSync blocks while lnd negotiates funding with the remote peer
        (tens of seconds); the default read timeout would cut the response off
        mid-funding and leave the caller unable to tell "failed" from "still
        funding" — so this call always gets the extended timeout.
        """
        body: dict[str, Any] = {
            "node_pubkey_string": node_pubkey_hex,
            "local_funding_amount": str(int(local_funding_sat)),
        }
        if sat_per_vbyte > 0:
            body["sat_per_vbyte"] = str(int(sat_per_vbyte))
        return await self._post(
            "/v1/channels", body, timeout=max(self._timeout, OPEN_CHANNEL_TIMEOUT_SECONDS)
        )

    async def _delete(self, path: str) -> dict[str, Any]:
        """HTTP DELETE for the lnd REST write surface (e.g. close channel)."""
        url = f"{self._base_url}{path}"
        try:
            if self._transport is not None:
                client_kwargs: dict[str, Any] = {
                    "transport": self._transport,
                    "timeout": self._timeout,
                }
            else:
                client_kwargs = {"verify": self._verify, "timeout": self._timeout}
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.delete(url, headers=self._headers)
        except httpx.HTTPError as exc:
            raise LightningUnavailableError(
                f"lnd request failed: {type(exc).__name__}: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise LightningUnavailableError(
                f"lnd returned {resp.status_code} for {path}: {resp.text[:200]}"
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise LightningUnavailableError(f"lnd returned non-JSON for {path}") from exc
        if not isinstance(data, dict):
            raise LightningUnavailableError(f"lnd returned non-object JSON for {path}")
        return data

    async def pay_invoice(self, *, payment_request: str, fee_limit_sat: int = 0) -> dict[str, Any]:
        """POST /v1/channels/transactions — pay a BOLT11 invoice (SPENDS; irreversible)."""
        body: dict[str, Any] = {"payment_request": payment_request}
        if fee_limit_sat > 0:
            body["fee_limit"] = {"fixed": str(int(fee_limit_sat))}
        return await self._post("/v1/channels/transactions", body)

    async def decode_pay_req(self, payment_request: str) -> dict[str, Any]:
        """Decode BOLT11 before sending so its stable payment hash is journalled."""
        return await self._get(f"/v1/payreq/{quote(payment_request, safe='')}")

    async def track_payment_v2(self, payment_hash: str) -> dict[str, Any]:
        """Track an outgoing payment to a terminal or latest known state.

        LND's REST path represents the protobuf ``bytes`` hash as URL-safe base64.
        DecodePayReq returns the same hash as hex, while legacy SendPaymentSync may
        return ordinary base64; both forms are accepted here.
        """
        value = payment_hash.strip()
        if not value:
            raise LightningUnavailableError("payment_hash required for TrackPaymentV2")
        try:
            raw = bytes.fromhex(value) if len(value) == 64 else base64.urlsafe_b64decode(value)
        except (ValueError, binascii.Error) as exc:
            raise LightningUnavailableError("payment_hash is neither hex nor base64") from exc
        if len(raw) != 32:
            raise LightningUnavailableError("payment_hash must decode to 32 bytes")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii")
        return await self._stream_get_last(
            f"/v2/router/track/{encoded}?no_inflight_updates=true"
        )

    async def list_payments(
        self, *, creation_date_start: int, creation_date_end: int
    ) -> list[dict[str, Any]]:
        """List today's outgoing payments without route hops (read-only)."""
        data = await self._get(
            "/v1/payments?include_incomplete=true&omit_hops=true"
            f"&creation_date_start={int(creation_date_start)}"
            f"&creation_date_end={int(creation_date_end)}"
        )
        payments = data.get("payments", [])
        return payments if isinstance(payments, list) else []

    async def keysend(
        self, *, dest_pubkey_hex: str, amt_sat: int, fee_limit_sat: int = 0
    ) -> dict[str, Any]:
        """POST /v1/channels/transactions — spontaneous keysend (SPENDS; irreversible).

        Generates a random preimage client-side; the keysend TLV record (5482373484)
        carries it so the destination can settle without a pre-issued invoice.
        """
        import base64
        import hashlib
        import os

        preimage = os.urandom(32)
        payment_hash = hashlib.sha256(preimage).digest()
        body: dict[str, Any] = {
            "dest": base64.b64encode(bytes.fromhex(dest_pubkey_hex)).decode("ascii"),
            "amt": str(int(amt_sat)),
            "payment_hash": base64.b64encode(payment_hash).decode("ascii"),
            "dest_custom_records": {"5482373484": base64.b64encode(preimage).decode("ascii")},
        }
        if fee_limit_sat > 0:
            body["fee_limit"] = {"fixed": str(int(fee_limit_sat))}
        return await self._post("/v1/channels/transactions", body)

    async def send_coins(
        self, *, addr: str, amount_sat: int, sat_per_vbyte: int = 0
    ) -> dict[str, Any]:
        """POST /v1/transactions — on-chain withdraw (SPENDS on-chain; irreversible)."""
        body: dict[str, Any] = {"addr": addr, "amount": str(int(amount_sat))}
        if sat_per_vbyte > 0:
            body["sat_per_vbyte"] = str(int(sat_per_vbyte))
        return await self._post("/v1/transactions", body)

    async def close_channel(
        self, *, funding_txid: str, output_index: int, force: bool = False, sat_per_vbyte: int = 0
    ) -> dict[str, Any]:
        """DELETE /v1/channels/{txid}/{index} — close a channel (irreversible)."""
        path = f"/v1/channels/{funding_txid}/{int(output_index)}"
        params: list[str] = []
        if force:
            params.append("force=true")
        if sat_per_vbyte > 0:
            params.append(f"sat_per_vbyte={int(sat_per_vbyte)}")
        if params:
            path = f"{path}?{'&'.join(params)}"
        return await self._delete(path)
