"""Sprint 4 — Lightning value-layer SEND methods (capital-OUT, hard-gated).

Safety core: every send (pay/keysend/send_coins/close_channel) is IRREVERSIBLE, so
the default is ``planned`` (no node touch) — execution needs pay_enabled + dry_run
False + confirm True. rebalance_plan is plan-only and NEVER executes. Every
node-touching outcome is written to the ops audit-ledger.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.lightning_settings import LightningSettings
from app.lightning import value_layer as vl
from app.lightning.client import LndRestClient
from app.lightning.value_layer import (
    close_channel,
    keysend,
    pay_invoice,
    rebalance_plan,
    send_coins,
)


def _cfg(pay_enabled: bool) -> LightningSettings:
    return LightningSettings(enabled=True, pay_enabled=pay_enabled)


_SENDS = [
    lambda c: pay_invoice(payment_request="lnbc1xyz", dry_run=False, confirm=False, cfg=c),
    lambda c: send_coins(addr="bc1qxyz", amount_sat=1000, dry_run=False, confirm=False, cfg=c),
    lambda c: close_channel(
        funding_txid="abcd", output_index=0, dry_run=False, confirm=False, cfg=c
    ),
    lambda c: keysend(dest_pubkey_hex="02ab", amt_sat=100, dry_run=False, confirm=False, cfg=c),
]


@pytest.mark.parametrize("call", _SENDS)
async def test_send_irreversible_planned_without_confirm(call) -> None:
    with patch("app.lightning.value_layer._build_client") as build:
        r = await call(_cfg(True))
    assert r.state == "planned" and r.detail == "confirm=False"
    build.assert_not_called()  # node never touched without explicit confirm


@pytest.mark.parametrize("call", _SENDS)
async def test_send_disabled_when_kill_switch_off(call) -> None:
    with patch("app.lightning.value_layer._build_client") as build:
        r = await call(_cfg(False))
    assert r.state == "disabled"
    build.assert_not_called()


async def test_send_dry_run_default_plans_without_node(monkeypatch) -> None:
    with patch("app.lightning.value_layer._build_client") as build:
        r = await pay_invoice(payment_request="lnbc1", cfg=_cfg(True))  # dry_run defaults True
    assert r.state == "planned" and r.detail == "dry_run"
    build.assert_not_called()


async def test_pay_invoice_executes_with_all_gates_and_audits(monkeypatch) -> None:
    client = MagicMock()
    client.pay_invoice = AsyncMock(return_value={"payment_preimage": "ab", "payment_error": ""})
    audited: list[tuple] = []
    monkeypatch.setattr(
        vl, "append_ln_op", lambda action, state, **k: audited.append((action, state))
    )
    with patch("app.lightning.value_layer._build_client", return_value=client):
        r = await pay_invoice(payment_request="lnbc1", dry_run=False, confirm=True, cfg=_cfg(True))
    assert r.state == "executed"
    client.pay_invoice.assert_awaited_once()
    assert ("pay_invoice", "executed") in audited  # node-touching outcome audited


async def test_executed_error_is_audited_not_disabled(monkeypatch) -> None:
    audited: list[tuple] = []
    monkeypatch.setattr(
        vl, "append_ln_op", lambda action, state, **k: audited.append((action, state))
    )
    # disabled (kill-switch) must NOT spam the audit ledger
    with patch("app.lightning.value_layer._build_client"):
        await send_coins(addr="bc1q", amount_sat=1, dry_run=False, confirm=True, cfg=_cfg(False))
    assert audited == []


async def test_rebalance_plan_never_executes() -> None:
    with patch("app.lightning.value_layer._build_client") as build:
        r = await rebalance_plan(out_channel="1", in_channel="2", amount_sat=1000, cfg=_cfg(True))
    assert r.state == "planned"
    build.assert_not_called()


# --- client wire format ----------------------------------------------------------


async def test_client_send_coins_wire() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST" and req.url.path == "/v1/transactions"
        return httpx.Response(200, json={"txid": "deadbeef"})

    c = LndRestClient(
        base_url="https://x:8080", macaroon_hex="ab", transport=httpx.MockTransport(handler)
    )
    r = await c.send_coins(addr="bc1q", amount_sat=1000)
    assert r["txid"] == "deadbeef"


async def test_client_close_channel_delete_wire() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "DELETE" and req.url.path == "/v1/channels/abcd/0"
        return httpx.Response(200, json={"close_pending": {"txid": "cc"}})

    c = LndRestClient(
        base_url="https://x:8080", macaroon_hex="ab", transport=httpx.MockTransport(handler)
    )
    r = await c.close_channel(funding_txid="abcd", output_index=0, force=True)
    assert r["close_pending"]["txid"] == "cc"


async def test_client_add_invoice_sets_short_expiry() -> None:
    """U1 receive-path hardening: unpaid invoices must NOT linger on the node (DB row +
    HTLC-slot expectation), so add_invoice posts a bounded ``expiry`` by default."""
    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST" and req.url.path == "/v1/invoices"
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"payment_request": "lnbc1", "r_hash": "aa"})

    c = LndRestClient(
        base_url="https://x:8080", macaroon_hex="ab", transport=httpx.MockTransport(handler)
    )
    await c.add_invoice(value_sat=100, memo="kai-oracle:fee-series")
    body = captured["body"]
    assert isinstance(body, dict) and "expiry" in body
    assert 0 < int(body["expiry"]) <= 600
