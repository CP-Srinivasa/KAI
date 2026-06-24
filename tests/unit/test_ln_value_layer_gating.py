"""Safety invariants for the gated Lightning value layer (L4).

The ONLY thing that matters here: real value never moves unless EVERY gate is
deliberately flipped. We assert the node client is NOT even built/called in any
gated path, and is reached only when all gates pass.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.lightning_settings import LightningSettings
from app.lightning.value_layer import create_invoice, open_channel


def _cfg(pay_enabled: bool, receive_enabled: bool = False) -> LightningSettings:
    return LightningSettings(enabled=True, pay_enabled=pay_enabled, receive_enabled=receive_enabled)


def _fake_client() -> MagicMock:
    c = MagicMock()
    c.add_invoice = AsyncMock(return_value={"payment_request": "lnbc1..."})
    c.open_channel = AsyncMock(return_value={"funding_txid_str": "deadbeef"})
    return c


@pytest.mark.asyncio
async def test_invoice_disabled_when_kill_switch_off() -> None:
    with patch("app.lightning.value_layer._build_client") as build:
        r = await create_invoice(value_sat=1000, dry_run=False, cfg=_cfg(False))
    assert r.state == "disabled"
    build.assert_not_called()  # node never touched


@pytest.mark.asyncio
async def test_invoice_dry_run_default_plans_without_node_call() -> None:
    with patch("app.lightning.value_layer._build_client") as build:
        r = await create_invoice(
            value_sat=1000, cfg=_cfg(True, receive_enabled=True)
        )  # dry_run defaults True
    assert r.state == "planned"
    build.assert_not_called()


@pytest.mark.asyncio
async def test_invoice_executes_only_when_enabled_and_not_dry_run() -> None:
    client = _fake_client()
    with patch("app.lightning.value_layer._build_client", return_value=client):
        r = await create_invoice(
            value_sat=1000, memo="m", dry_run=False, cfg=_cfg(True, receive_enabled=True)
        )
    assert r.state == "executed" and r.response["payment_request"].startswith("lnbc")
    client.add_invoice.assert_awaited_once()


@pytest.mark.asyncio
async def test_open_channel_disabled_when_kill_switch_off() -> None:
    with patch("app.lightning.value_layer._build_client") as build:
        r = await open_channel(
            node_pubkey_hex="02ab",
            local_funding_sat=50000,
            confirm=True,
            dry_run=False,
            cfg=_cfg(False),
        )
    assert r.state == "disabled"
    build.assert_not_called()


@pytest.mark.asyncio
async def test_open_channel_needs_confirm_even_when_enabled_and_not_dry_run() -> None:
    # The critical spend-safety: enabled + dry_run=False but confirm=False MUST
    # NOT broadcast — it only plans.
    with patch("app.lightning.value_layer._build_client") as build:
        r = await open_channel(
            node_pubkey_hex="02ab",
            local_funding_sat=50000,
            confirm=False,
            dry_run=False,
            cfg=_cfg(True),
        )
    assert r.state == "planned" and r.detail == "confirm=False"
    build.assert_not_called()


@pytest.mark.asyncio
async def test_open_channel_executes_only_with_all_gates() -> None:
    client = _fake_client()
    with patch("app.lightning.value_layer._build_client", return_value=client):
        r = await open_channel(
            node_pubkey_hex="02ab",
            local_funding_sat=50000,
            confirm=True,
            dry_run=False,
            cfg=_cfg(True),
        )
    assert r.state == "executed" and r.response["funding_txid_str"] == "deadbeef"
    client.open_channel.assert_awaited_once()
