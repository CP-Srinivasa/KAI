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
from app.lightning import adapter as adapter_mod


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _routing_transport(responses: dict[str, httpx.Response]) -> httpx.MockTransport:
    """Route by URL path; unmapped paths raise to surface test gaps."""

    def handler(request: httpx.Request) -> httpx.Response:
        resp = responses.get(request.url.path)
        if resp is None:
            raise AssertionError(f"unexpected path {request.url.path}")
        return resp

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


async def test_get_state_happy_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/state"
        return httpx.Response(200, json={"state": "SERVER_ACTIVE"})

    client = LndRestClient(
        base_url="https://x:8080", macaroon_hex="ab", transport=_transport(handler)
    )
    assert await client.get_state() == "SERVER_ACTIVE"


async def test_adapter_degraded_when_getinfo_fails(monkeypatch) -> None:
    # /v1/state OK (reachable) but getinfo errors -> node stays reachable/ok,
    # info_available False. Mirrors the live Tor-node getinfo-hang finding.
    # Balances are fetched independent of getinfo, so they stay present even
    # when getinfo hangs — the whole point of the Phase-1.5 split.
    transport = _routing_transport(
        {
            "/v1/state": httpx.Response(200, json={"state": "SERVER_ACTIVE"}),
            "/v1/balance/channels": httpx.Response(
                200, json={"local_balance": {"sat": "800"}, "remote_balance": {"sat": "200"}}
            ),
            "/v1/balance/blockchain": httpx.Response(
                200, json={"confirmed_balance": "1643768", "total_balance": "1643768"}
            ),
            "/v1/getinfo": httpx.Response(503, text="slow"),
        }
    )
    monkeypatch.setattr(
        adapter_mod,
        "_build_client",
        lambda cfg: LndRestClient(
            base_url="https://x:8080", macaroon_hex="ab", transport=transport
        ),
    )
    status = await get_node_status(LightningSettings(enabled=True, macaroon_hex="ab"))
    assert status.state == "ok"
    assert status.reachable is True
    assert status.server_state == "SERVER_ACTIVE"
    assert status.info_available is False
    assert "getinfo" in status.reason
    # balances survive the getinfo hang
    assert status.balances_available is True
    assert status.channel_local_sat == 800
    assert status.channel_remote_sat == 200
    assert status.wallet_confirmed_sat == 1643768


async def test_adapter_ok_full(monkeypatch) -> None:
    transport = _routing_transport(
        {
            "/v1/state": httpx.Response(200, json={"state": "SERVER_ACTIVE"}),
            "/v1/balance/channels": httpx.Response(
                200, json={"local_balance": {"sat": "5000"}, "remote_balance": {"sat": "1500"}}
            ),
            "/v1/balance/blockchain": httpx.Response(
                200, json={"confirmed_balance": "798269", "total_balance": "800000"}
            ),
            "/v1/getinfo": httpx.Response(
                200,
                json={
                    "identity_pubkey": "024a7f",
                    "block_height": 953644,
                    "synced_to_chain": True,
                    "synced_to_graph": True,
                    "num_active_channels": 4,
                    "num_pending_channels": 3,
                },
            ),
        }
    )
    monkeypatch.setattr(
        adapter_mod,
        "_build_client",
        lambda cfg: LndRestClient(
            base_url="https://x:8080", macaroon_hex="ab", transport=transport
        ),
    )
    status = await get_node_status(LightningSettings(enabled=True, macaroon_hex="ab"))
    assert status.state == "ok"
    assert status.info_available is True
    assert status.block_height == 953644
    assert status.synced_to_chain is True
    assert status.synced_to_graph is True  # lnd gossip-graph sync surfaced
    assert status.num_active_channels == 4
    assert status.num_pending_channels == 3  # surfaced from getinfo (B2 force-closes)
    assert status.balances_available is True
    assert status.channel_local_sat == 5000
    assert status.channel_remote_sat == 1500
    assert status.wallet_total_sat == 800000


async def test_adapter_balances_fail_soft(monkeypatch) -> None:
    # Balance endpoints error but state+getinfo OK -> node stays ok, balances
    # simply absent (balances_available False). Best-effort never flips liveness.
    transport = _routing_transport(
        {
            "/v1/state": httpx.Response(200, json={"state": "SERVER_ACTIVE"}),
            "/v1/balance/channels": httpx.Response(503, text="busy"),
            "/v1/balance/blockchain": httpx.Response(503, text="busy"),
            "/v1/getinfo": httpx.Response(200, json={"identity_pubkey": "024a7f"}),
        }
    )
    monkeypatch.setattr(
        adapter_mod,
        "_build_client",
        lambda cfg: LndRestClient(
            base_url="https://x:8080", macaroon_hex="ab", transport=transport
        ),
    )
    status = await get_node_status(LightningSettings(enabled=True, macaroon_hex="ab"))
    assert status.state == "ok"
    assert status.info_available is True
    assert status.balances_available is False
    assert status.channel_local_sat == 0 and status.wallet_total_sat == 0


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
