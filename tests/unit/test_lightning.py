"""Unit tests for the read-only Lightning (lnd REST) integration — Phase 1.

Covers: default-off behaviour, fail-closed on transport/HTTP errors, macaroon
resolution (hex + file), and a happy-path getinfo through a mocked transport.
"""

from __future__ import annotations

import binascii

import httpx
import pytest

from app.core.settings import LightningSettings
from app.lightning import (
    LightningNodeStatus,
    LightningUnavailableError,
    LndRestClient,
    get_node_status,
)


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# --- client: macaroon resolution -------------------------------------------------


def test_macaroon_hex_takes_precedence() -> None:
    client = LndRestClient(base_url="https://x:8080", macaroon_hex="deadbeef")
    assert client._headers["Grpc-Metadata-macaroon"] == "deadbeef"


def test_macaroon_from_file_is_hex_encoded(tmp_path) -> None:
    mac = tmp_path / "readonly.macaroon"
    mac.write_bytes(b"\x00\x01\x02")
    client = LndRestClient(base_url="https://x:8080", macaroon_path=str(mac))
    assert client._headers["Grpc-Metadata-macaroon"] == binascii.hexlify(b"\x00\x01\x02").decode()


def test_no_macaroon_is_fail_closed() -> None:
    with pytest.raises(LightningUnavailableError):
        LndRestClient(base_url="https://x:8080")


def test_missing_macaroon_file_is_fail_closed(tmp_path) -> None:
    with pytest.raises(LightningUnavailableError):
        LndRestClient(base_url="https://x:8080", macaroon_path=str(tmp_path / "nope"))


# --- client: getinfo happy path + errors -----------------------------------------


async def test_get_info_happy_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/getinfo"
        assert request.headers["Grpc-Metadata-macaroon"] == "ab"
        return httpx.Response(
            200,
            json={
                "identity_pubkey": "024a7f",
                "alias": "FlashGordancom",
                "version": "0.19.3-beta",
                "block_height": 953627,
                "synced_to_chain": True,
                "synced_to_graph": False,
                "num_peers": 0,
                "num_active_channels": 0,
            },
        )

    client = LndRestClient(
        base_url="https://x:8080", macaroon_hex="ab", transport=_transport(handler)
    )
    info = await client.get_info()
    assert info.identity_pubkey == "024a7f"
    assert info.block_height == 953627
    assert info.synced_to_chain is True
    assert info.num_active_channels == 0


async def test_non_200_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="node starting")

    client = LndRestClient(
        base_url="https://x:8080", macaroon_hex="ab", transport=_transport(handler)
    )
    with pytest.raises(LightningUnavailableError):
        await client.get_info()


async def test_transport_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    client = LndRestClient(
        base_url="https://x:8080", macaroon_hex="ab", transport=_transport(handler)
    )
    with pytest.raises(LightningUnavailableError):
        await client.get_info()


# --- adapter: default-off + fail-closed ------------------------------------------


async def test_adapter_disabled_makes_no_call() -> None:
    cfg = LightningSettings(enabled=False)
    status = await get_node_status(cfg)
    assert status.state == "disabled"
    assert status.reachable is False


async def test_adapter_unavailable_when_misconfigured() -> None:
    # enabled but no macaroon -> client construction fails -> fail-closed
    cfg = LightningSettings(enabled=True, macaroon_hex="", macaroon_path="")
    status = await get_node_status(cfg)
    assert status.state == "unavailable"
    assert status.reachable is False
    assert status.reason


def test_status_constructors() -> None:
    assert LightningNodeStatus.disabled().state == "disabled"
    assert LightningNodeStatus.unavailable("boom").reason == "boom"


def test_base_url_built_from_host_port() -> None:
    cfg = LightningSettings(host="10.0.0.9", rest_port=8081)
    assert cfg.base_url == "https://10.0.0.9:8081"
