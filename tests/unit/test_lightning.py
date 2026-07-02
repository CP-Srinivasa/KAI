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
    get_channels,
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


# --- channels: per-channel breakdown (read-only listchannels) --------------------


async def test_list_channels_happy_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/channels"
        return httpx.Response(200, json={"channels": [{"chan_id": "1", "capacity": "100"}]})

    client = LndRestClient(
        base_url="https://x:8080", macaroon_hex="ab", transport=_transport(handler)
    )
    raw = await client.list_channels()
    assert raw["channels"][0]["chan_id"] == "1"


async def test_get_channels_disabled_makes_no_call() -> None:
    status = await get_channels(LightningSettings(enabled=False))
    assert status.state == "disabled"
    assert status.reachable is False
    assert status.channels == []


async def test_get_channels_ok_parses_and_sorts(monkeypatch) -> None:
    # Two active + one inactive, varying capacity → active-first, capacity desc.
    transport = _routing_transport(
        {
            "/v1/channels": httpx.Response(
                200,
                json={
                    "channels": [
                        {
                            "chan_id": "small",
                            "remote_pubkey": "02aa",
                            "capacity": "1000",
                            "local_balance": "600",
                            "remote_balance": "400",
                            "active": True,
                        },
                        {
                            "channel_point": "txid:0",
                            "remote_pubkey": "02bb",
                            "capacity": "9000",
                            "local_balance": "9000",
                            "remote_balance": "0",
                            "active": False,
                        },
                        {
                            "chan_id": "big",
                            "remote_pubkey": "02cc",
                            "capacity": "5000",
                            "local_balance": "2500",
                            "remote_balance": "2500",
                            "active": True,
                        },
                    ]
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
    status = await get_channels(LightningSettings(enabled=True, macaroon_hex="ab"))
    assert status.state == "ok"
    assert status.reachable is True
    # active-first, then capacity desc: big(5000) > small(1000) > inactive(9000)
    assert [c.channel_id for c in status.channels] == ["big", "small", "txid:0"]
    assert status.channels[0].local_sat == 2500
    assert status.channels[0].remote_sat == 2500
    assert status.channels[2].active is False
    assert status.channels[1].capacity_sat == 1000


async def test_get_channels_unavailable_when_misconfigured() -> None:
    status = await get_channels(LightningSettings(enabled=True, macaroon_hex="", macaroon_path=""))
    assert status.state == "unavailable"
    assert status.reachable is False
    assert status.reason


async def test_get_channels_fail_closed_on_error(monkeypatch) -> None:
    transport = _routing_transport({"/v1/channels": httpx.Response(503, text="busy")})
    monkeypatch.setattr(
        adapter_mod,
        "_build_client",
        lambda cfg: LndRestClient(
            base_url="https://x:8080", macaroon_hex="ab", transport=transport
        ),
    )
    status = await get_channels(LightningSettings(enabled=True, macaroon_hex="ab"))
    assert status.state == "unavailable"
    assert status.channels == []


# --- client: open_channel timeout escalation + honest transport errors -----------


class _KwargsCapturingAsyncClient(httpx.AsyncClient):
    """Records the ctor kwargs so tests can assert the effective timeout."""

    captured: list[dict] = []

    def __init__(self, **kwargs) -> None:
        type(self).captured.append(dict(kwargs))
        super().__init__(**kwargs)


async def test_open_channel_uses_extended_timeout(monkeypatch) -> None:
    """OpenChannelSync blocks for the whole funding workflow — the read-sized
    default timeout (10s) would abort mid-funding with an ambiguous outcome."""
    from app.lightning.client import OPEN_CHANNEL_TIMEOUT_SECONDS

    _KwargsCapturingAsyncClient.captured = []
    monkeypatch.setattr(httpx, "AsyncClient", _KwargsCapturingAsyncClient)
    transport = _routing_transport(
        {"/v1/channels": httpx.Response(200, json={"funding_txid_bytes": "aa"})}
    )
    client = LndRestClient(base_url="https://x:8080", macaroon_hex="ab", transport=transport)
    await client.open_channel(node_pubkey_hex="02aa", local_funding_sat=100_000)
    assert _KwargsCapturingAsyncClient.captured[-1]["timeout"] == OPEN_CHANNEL_TIMEOUT_SECONDS


async def test_add_invoice_keeps_default_timeout(monkeypatch) -> None:
    _KwargsCapturingAsyncClient.captured = []
    monkeypatch.setattr(httpx, "AsyncClient", _KwargsCapturingAsyncClient)
    transport = _routing_transport(
        {"/v1/invoices": httpx.Response(200, json={"payment_request": "lnbc1"})}
    )
    client = LndRestClient(
        base_url="https://x:8080", macaroon_hex="ab", transport=transport, timeout=10.0
    )
    await client.add_invoice(value_sat=10)
    assert _KwargsCapturingAsyncClient.captured[-1]["timeout"] == 10.0


async def test_transport_error_message_names_exception_class() -> None:
    """str(httpx.ReadTimeout("")) is empty — the error must still say WHAT failed
    (regression: value-layer showed 'lnd request failed: ' with no detail)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")

    client = LndRestClient(
        base_url="https://x:8080", macaroon_hex="ab", transport=_transport(handler)
    )
    with pytest.raises(LightningUnavailableError, match="ReadTimeout"):
        await client.open_channel(node_pubkey_hex="02aa", local_funding_sat=100_000)


# --- channels: pending-open surfaced, best-effort ---------------------------------


async def test_get_channels_includes_pending_open(monkeypatch) -> None:
    """A JUST-funded channel (pendingchannels) must be visible next to open ones."""
    transport = _routing_transport(
        {
            "/v1/channels": httpx.Response(200, json={"channels": []}),
            "/v1/channels/pending": httpx.Response(
                200,
                json={
                    "pending_open_channels": [
                        {
                            "channel": {
                                "remote_node_pub": "03864ef0aa",
                                "capacity": "400000",
                                "local_balance": "398708",
                                "channel_point": "abcd:0",
                            }
                        }
                    ]
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
    status = await get_channels(LightningSettings(enabled=True, macaroon_hex="ab"))
    assert status.state == "ok"
    assert status.channels == []
    assert len(status.pending) == 1
    assert status.pending[0].capacity_sat == 400_000
    assert status.pending[0].local_sat == 398_708
    assert status.pending[0].remote_pubkey == "03864ef0aa"


async def test_get_channels_pending_failure_keeps_open_channels(monkeypatch) -> None:
    """pendingchannels erroring must NOT hide the open-channel truth (best-effort)."""
    transport = _routing_transport(
        {
            "/v1/channels": httpx.Response(
                200,
                json={
                    "channels": [
                        {
                            "chan_id": "1",
                            "remote_pubkey": "02aa",
                            "capacity": "100",
                            "local_balance": "50",
                            "remote_balance": "50",
                            "active": True,
                        }
                    ]
                },
            ),
            "/v1/channels/pending": httpx.Response(503, text="busy"),
        }
    )
    monkeypatch.setattr(
        adapter_mod,
        "_build_client",
        lambda cfg: LndRestClient(
            base_url="https://x:8080", macaroon_hex="ab", transport=transport
        ),
    )
    status = await get_channels(LightningSettings(enabled=True, macaroon_hex="ab"))
    assert status.state == "ok"
    assert len(status.channels) == 1
    assert status.pending == []
