"""Sprint 4 — Lightning value-layer SEND methods (capital-OUT, hard-gated).

Safety core: every send (pay/keysend/send_coins/close_channel) is IRREVERSIBLE, so
the default is ``planned`` (no node touch) — execution needs pay_enabled + dry_run
False + confirm True. rebalance_plan is plan-only and NEVER executes. Every
node-touching outcome is written to the ops audit-ledger.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.lightning_settings import LightningSettings
from app.lightning import value_layer as vl
from app.lightning.client import LndRestClient
from app.lightning.ops_ledger import ln_ops_v2_path, verify_ln_ops_ledger
from app.lightning.value_layer import (
    close_channel,
    keysend,
    pay_invoice,
    rebalance_plan,
    send_coins,
)


def _cfg(pay_enabled: bool) -> LightningSettings:
    return LightningSettings(enabled=True, pay_enabled=pay_enabled, tls_cert_path="test-tls.pem")


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
    """PR-C: the outcome closes the write-ahead intent in the v2 money journal."""
    payment_hash = "11" * 32
    client = MagicMock()
    client.decode_pay_req = AsyncMock(
        return_value={
            "num_satoshis": "1",
            "num_msat": "1000",
            "payment_hash": payment_hash,
            "timestamp": "1786032000",
            "expiry": "3600",
        }
    )
    client.pay_invoice = AsyncMock(
        return_value={
            "payment_preimage": "ab",
            "payment_error": "",
            "payment_hash": base64.b64encode(bytes.fromhex(payment_hash)).decode(),
        }
    )
    audited: list[tuple] = []
    monkeypatch.setattr(
        vl,
        "append_ln_outcome",
        lambda action, state, **k: audited.append((action, state, k["intent_id"], k["plan"])),
    )
    with patch("app.lightning.value_layer._build_client", return_value=client):
        r = await pay_invoice(
            payment_request="lnbc10n1xyz", dry_run=False, confirm=True, cfg=_cfg(True)
        )
    assert r.state == "executed"
    client.decode_pay_req.assert_awaited_once_with(payment_request="lnbc10n1xyz")
    client.pay_invoice.assert_awaited_once()
    # The outcome is journalled against the SAME intent that was written ahead of
    # the node call — the pair is what makes the spend accountable.
    assert r.intent_id
    assert audited == [
        (
            "pay_invoice",
            "executed",
            r.intent_id,
            {
                "payment_request": "lnbc10n1xyz",
                "fee_limit_sat": 0,
                "amount_sat": 1,
                "payment_hash": payment_hash,
                "expires_at_unix": 1786035600,
            },
        )
    ]
    assert verify_ln_ops_ledger(ln_ops_v2_path())["ok"] is True


async def test_pay_invoice_decode_amount_mismatch_denies_before_intent_or_send(
    monkeypatch,
) -> None:
    """A BOLT11 whose HRP amount disagrees with lnd's signed decode is not payable."""
    client = MagicMock()
    client.decode_pay_req = AsyncMock(
        return_value={"num_satoshis": "2", "num_msat": "2000", "payment_hash": "22" * 32}
    )
    client.pay_invoice = AsyncMock()
    prepare = MagicMock()
    monkeypatch.setattr(vl, "prepare_ln_intent", prepare)

    with patch("app.lightning.value_layer._build_client", return_value=client):
        result = await pay_invoice(
            payment_request="lnbc10n1xyz", dry_run=False, confirm=True, cfg=_cfg(True)
        )

    assert result.state == "error"
    assert "amount mismatch" in result.detail
    assert result.intent_id == ""
    prepare.assert_not_called()
    client.pay_invoice.assert_not_awaited()


async def test_pay_invoice_invalid_decoded_hash_denies_before_intent_or_send(
    monkeypatch,
) -> None:
    client = MagicMock()
    client.decode_pay_req = AsyncMock(
        return_value={"num_satoshis": "1", "num_msat": "1000", "payment_hash": "not-a-hash"}
    )
    client.pay_invoice = AsyncMock()
    prepare = MagicMock()
    monkeypatch.setattr(vl, "prepare_ln_intent", prepare)

    with patch("app.lightning.value_layer._build_client", return_value=client):
        result = await pay_invoice(
            payment_request="lnbc10n1xyz", dry_run=False, confirm=True, cfg=_cfg(True)
        )

    assert result.state == "error" and "invalid payment_hash" in result.detail
    prepare.assert_not_called()
    client.pay_invoice.assert_not_awaited()


@pytest.mark.parametrize(
    "failed_response",
    [
        {"payment_error": "unable to find a path to destination"},
        {"failure_reason": "FAILURE_REASON_NO_ROUTE"},
    ],
)
async def test_pay_invoice_http_200_failure_is_error_and_closes_the_intent(
    failed_response: dict[str, str],
) -> None:
    """B-12: HTTP 200 is only transport success; lnd can report payment failure inside JSON."""
    payment_hash = "33" * 32
    client = MagicMock()
    client.decode_pay_req = AsyncMock(
        return_value={
            "num_satoshis": "1",
            "num_msat": "1000",
            "payment_hash": payment_hash,
        }
    )
    client.pay_invoice = AsyncMock(
        return_value={
            **failed_response,
            "payment_hash": base64.b64encode(bytes.fromhex(payment_hash)).decode(),
        }
    )

    with patch("app.lightning.value_layer._build_client", return_value=client):
        result = await pay_invoice(
            payment_request="lnbc10n1failure",
            dry_run=False,
            confirm=True,
            intent_id="b12-failed",
            cfg=_cfg(True),
        )

    assert result.state == "error" and result.intent_id == "b12-failed"
    rows = [json.loads(line) for line in ln_ops_v2_path().read_text(encoding="utf-8").splitlines()]
    assert [row["state"] for row in rows] == ["intent", "error"]
    assert rows[0]["plan"]["amount_sat"] == 1
    assert rows[0]["plan"]["payment_hash"] == payment_hash
    assert rows[1]["intent_id"] == "b12-failed"


async def test_pay_invoice_response_hash_mismatch_closes_intent_as_error() -> None:
    payment_hash = "55" * 32
    client = MagicMock()
    client.decode_pay_req = AsyncMock(
        return_value={
            "num_satoshis": "1",
            "num_msat": "1000",
            "payment_hash": payment_hash,
        }
    )
    client.pay_invoice = AsyncMock(return_value={"payment_hash": "66" * 32})

    with patch("app.lightning.value_layer._build_client", return_value=client):
        result = await pay_invoice(
            payment_request="lnbc10n1hashcheck",
            dry_run=False,
            confirm=True,
            cfg=_cfg(True),
        )

    assert result.state == "error"
    assert "mismatches the prepared intent" in result.detail
    rows = [json.loads(line) for line in ln_ops_v2_path().read_text(encoding="utf-8").splitlines()]
    assert [row["state"] for row in rows] == ["intent", "error"]


async def test_executed_error_is_audited_not_disabled(monkeypatch) -> None:
    audited: list[tuple] = []
    monkeypatch.setattr(
        vl, "append_ln_outcome", lambda action, state, **k: audited.append((action, state))
    )
    # disabled (kill-switch) must NOT spam the audit ledger — and must not open an
    # intent either: a non-event leaves no trace in the money journal.
    with patch("app.lightning.value_layer._build_client"):
        r = await send_coins(
            addr="bc1q", amount_sat=1, dry_run=False, confirm=True, cfg=_cfg(False)
        )
    assert audited == [] and r.intent_id == ""
    assert verify_ln_ops_ledger(ln_ops_v2_path())["records"] == 0


async def test_rebalance_plan_never_executes() -> None:
    with patch("app.lightning.value_layer._build_client") as build:
        r = await rebalance_plan(out_channel="1", in_channel="2", amount_sat=1000, cfg=_cfg(True))
    assert r.state == "planned"
    build.assert_not_called()


# --- client wire format ----------------------------------------------------------


async def test_client_decode_pay_req_wire() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET" and req.url.path == "/v1/payreq/lnbc10n1xyz"
        return httpx.Response(
            200,
            json={"num_satoshis": "1", "payment_hash": "44" * 32},
        )

    c = LndRestClient(
        base_url="https://x:8080", macaroon_hex="ab", transport=httpx.MockTransport(handler)
    )
    decoded = await c.decode_pay_req(payment_request="lnbc10n1xyz")
    assert decoded["num_satoshis"] == "1" and decoded["payment_hash"] == "44" * 32


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
