"""U1 — receive/send gate-split for the Lightning value layer.

Capital-free invoice minting (receive-side) is decoupled from the spend
kill-switch (``pay_enabled``) onto its own ``receive_enabled`` flag. The core
security invariant: enabling receive must NEVER enable any spend path, and ONLY
``create_invoice`` may ever be classified ``receive`` (fail-closed allowlist).

These tests express the satoshi GO-with-conditions: explicit per-method
``direction=`` declaration + a central backstop assertion + the negative
core-invariant as a permanent regression guard.
"""

from __future__ import annotations

import inspect
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.lightning.value_layer as vl
from app.core.lightning_settings import LightningSettings
from app.lightning.value_layer import (
    RECEIVE_ACTIONS,
    _assert_send_allowed,
    close_channel,
    create_invoice,
    keysend,
    open_channel,
    pay_invoice,
    send_coins,
)


def _cfg(*, pay_enabled: bool = False, receive_enabled: bool = False) -> LightningSettings:
    return LightningSettings(enabled=True, pay_enabled=pay_enabled, receive_enabled=receive_enabled)


def _fake_client() -> MagicMock:
    c = MagicMock()
    c.add_invoice = AsyncMock(return_value={"payment_request": "lnbc1...", "r_hash": "aa"})
    return c


# --- receive path is gated by receive_enabled, NOT pay_enabled -------------------


@pytest.mark.asyncio
async def test_invoice_mints_with_receive_enabled_even_when_pay_disabled() -> None:
    """The core capital-free unlock: receive_enabled=True + pay_enabled=False must let
    create_invoice reach the node — minting is receive-side, no spend."""
    client = _fake_client()
    with patch("app.lightning.value_layer._build_client", return_value=client):
        r = await create_invoice(
            value_sat=100,
            memo="kai-oracle:fee-series",
            dry_run=False,
            cfg=_cfg(pay_enabled=False, receive_enabled=True),
        )
    assert r.state == "executed"
    client.add_invoice.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoice_disabled_when_receive_flag_off() -> None:
    with patch("app.lightning.value_layer._build_client") as build:
        r = await create_invoice(
            value_sat=100, dry_run=False, cfg=_cfg(pay_enabled=True, receive_enabled=False)
        )
    assert r.state == "disabled" and "receive_enabled" in r.detail
    build.assert_not_called()


# --- NEGATIVE CORE INVARIANT: receive ON must not open ANY spend -----------------


@pytest.mark.asyncio
async def test_no_spend_path_opens_when_only_receive_enabled() -> None:
    """Permanent regression guard: with receive_enabled=True, pay_enabled=False, every
    spend method stays disabled and the node client is never even built."""
    cfg = _cfg(pay_enabled=False, receive_enabled=True)
    with patch("app.lightning.value_layer._build_client") as build:
        results = [
            await pay_invoice(payment_request="lnbc1", dry_run=False, confirm=True, cfg=cfg),
            await keysend(dest_pubkey_hex="02ab", amt_sat=10, dry_run=False, confirm=True, cfg=cfg),
            await send_coins(addr="bc1q", amount_sat=10, dry_run=False, confirm=True, cfg=cfg),
            await open_channel(
                node_pubkey_hex="02ab", local_funding_sat=10, dry_run=False, confirm=True, cfg=cfg
            ),
            await close_channel(
                funding_txid="ab", output_index=0, dry_run=False, confirm=True, cfg=cfg
            ),
        ]
    assert all(r.state == "disabled" and "pay_enabled" in r.detail for r in results)
    build.assert_not_called()


# --- backstop: a spend may NEVER be classified receive ---------------------------


def test_backstop_spend_action_declaring_receive_raises() -> None:
    with pytest.raises(ValueError):
        _assert_send_allowed(
            "pay_invoice",
            cfg=_cfg(receive_enabled=True),
            dry_run=False,
            confirm=True,
            irreversible=True,
            plan={},
            direction="receive",
        )


def test_receive_actions_allowlist_is_minimal() -> None:
    assert RECEIVE_ACTIONS == frozenset({"create_invoice"})


def test_unknown_direction_falls_back_to_send_gate() -> None:
    """fail-closed: an unrecognised direction must use the stricter send gate."""
    r = _assert_send_allowed(
        "x",
        cfg=_cfg(pay_enabled=False, receive_enabled=True),
        dry_run=False,
        confirm=True,
        irreversible=False,
        plan={},
        direction="bogus",
    )
    assert r is not None and r.state == "disabled" and "pay_enabled" in r.detail


# --- reflection: each write method declares the correct direction ----------------


def test_reflection_direction_declared_correctly_per_method() -> None:
    """Structural invariant: only RECEIVE_ACTIONS may declare direction='receive';
    every other value-layer write must declare 'send'. A spend that silently flips to
    receive in a future refactor fails here."""
    pat = re.compile(r"direction\s*=\s*[\"'](\w+)[\"']")
    checked = 0
    for name, fn in inspect.getmembers(vl, inspect.iscoroutinefunction):
        if name.startswith("_") or getattr(fn, "__module__", "") != vl.__name__:
            continue
        src = inspect.getsource(fn)
        if "_assert_send_allowed" not in src:
            continue
        m = pat.search(src)
        assert m is not None, f"{name} does not declare an explicit direction="
        direction = m.group(1)
        checked += 1
        if name in RECEIVE_ACTIONS:
            assert direction == "receive", f"{name} must declare direction='receive'"
        else:
            assert direction == "send", (
                f"{name} (spend) must declare direction='send', got {direction!r}"
            )
    assert checked >= 6  # all write methods covered
