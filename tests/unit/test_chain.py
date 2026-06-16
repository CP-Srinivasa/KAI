"""Unit tests for the sovereign bitcoind read-only chain integration (KAI L1).

Covers: auth resolution (user/pass + cookie + fail-closed), JSON-RPC happy path,
error handling, fee conversion, and the default-off / fail-closed adapter.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.chain import BitcoindRpcClient, ChainStatus, ChainUnavailableError, get_chain_status
from app.chain import adapter as chain_adapter
from app.core.chain_settings import ChainSettings


def _rpc_transport(results: dict[str, object]) -> httpx.MockTransport:
    """Route by JSON-RPC method to a canned result (or an {error} for unknown)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("method")
        if method not in results:
            return httpx.Response(200, json={"result": None, "error": {"message": "unknown"}})
        return httpx.Response(200, json={"result": results[method], "error": None})

    return httpx.MockTransport(handler)


# --- auth resolution -------------------------------------------------------------


def test_auth_userpass() -> None:
    c = BitcoindRpcClient(base_url="http://x:8332", rpc_user="u", rpc_password="p")
    assert c._auth == ("u", "p")


def test_auth_cookie(tmp_path) -> None:
    cookie = tmp_path / ".cookie"
    cookie.write_text("__cookie__:deadbeef", encoding="ascii")
    c = BitcoindRpcClient(base_url="http://x:8332", cookie_path=str(cookie))
    assert c._auth == ("__cookie__", "deadbeef")


def test_auth_missing_is_fail_closed() -> None:
    with pytest.raises(ChainUnavailableError):
        BitcoindRpcClient(base_url="http://x:8332")


def test_auth_malformed_cookie(tmp_path) -> None:
    cookie = tmp_path / ".cookie"
    cookie.write_text("no-colon-here", encoding="ascii")
    with pytest.raises(ChainUnavailableError):
        BitcoindRpcClient(base_url="http://x:8332", cookie_path=str(cookie))


# --- client RPC ------------------------------------------------------------------


async def test_get_blockchain_info_happy() -> None:
    t = _rpc_transport(
        {
            "getblockchaininfo": {
                "chain": "main",
                "blocks": 953902,
                "headers": 953902,
                "verificationprogress": 0.9999,
                "initialblockdownload": False,
                "bestblockhash": "abc",
            }
        }
    )
    c = BitcoindRpcClient(base_url="http://x:8332", rpc_user="u", rpc_password="p", transport=t)
    info = await c.get_blockchain_info()
    assert info.blocks == 953902 and info.headers == 953902
    assert info.initial_block_download is False


async def test_estimate_smart_fee_converts_and_none() -> None:
    c = BitcoindRpcClient(
        base_url="http://x:8332",
        rpc_user="u",
        rpc_password="p",
        transport=_rpc_transport({"estimatesmartfee": {"feerate": 0.00002009}}),
    )
    assert await c.estimate_smart_fee(6) == pytest.approx(2.009, abs=0.001)

    c2 = BitcoindRpcClient(
        base_url="http://x:8332",
        rpc_user="u",
        rpc_password="p",
        transport=_rpc_transport({"estimatesmartfee": {"errors": ["no data"]}}),
    )
    assert await c2.estimate_smart_fee(6) is None


async def test_rpc_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": None, "error": {"message": "boom"}})

    c = BitcoindRpcClient(
        base_url="http://x:8332",
        rpc_user="u",
        rpc_password="p",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ChainUnavailableError):
        await c.get_block_count()


async def test_non_200_raises() -> None:
    c = BitcoindRpcClient(
        base_url="http://x:8332",
        rpc_user="u",
        rpc_password="p",
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text="unauthorized")),
    )
    with pytest.raises(ChainUnavailableError):
        await c.get_block_count()


# --- adapter ---------------------------------------------------------------------


async def test_adapter_disabled_makes_no_call() -> None:
    status = await get_chain_status(ChainSettings(enabled=False))
    assert status.state == "disabled" and status.reachable is False


async def test_adapter_unavailable_when_no_creds() -> None:
    status = await get_chain_status(ChainSettings(enabled=True))
    assert status.state == "unavailable" and status.reachable is False
    assert status.reason


async def test_adapter_ok_full(monkeypatch) -> None:
    t = _rpc_transport(
        {
            "getblockchaininfo": {
                "chain": "main",
                "blocks": 953902,
                "headers": 953902,
                "verificationprogress": 0.9999,
                "initialblockdownload": False,
                "bestblockhash": "abc",
            },
            "estimatesmartfee": {"feerate": 0.00002009},
            "getmempoolinfo": {"size": 42},
        }
    )
    monkeypatch.setattr(
        chain_adapter,
        "_build_client",
        lambda cfg: BitcoindRpcClient(
            base_url="http://x:8332", rpc_user="u", rpc_password="p", transport=t
        ),
    )
    status = await get_chain_status(ChainSettings(enabled=True, rpc_user="u", rpc_password="p"))
    assert status.state == "ok" and status.reachable is True
    assert status.synced is True
    assert status.fee_sat_vb == pytest.approx(2.009, abs=0.001)
    assert status.mempool_tx == 42


def test_status_constructors() -> None:
    assert ChainStatus.disabled().state == "disabled"
    assert ChainStatus.unavailable("x").reason == "x"


def test_base_url() -> None:
    assert ChainSettings(host="10.27.0.51", rpc_port=8332).base_url == "http://10.27.0.51:8332"
