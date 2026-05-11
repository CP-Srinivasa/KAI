"""Phase-0 Integration-Tests gegen Binance/Bybit-Testnet (Task N+4).

Spec: docs/security/kai_light_live_phase0_spec.md §10 (Integration-Test).

**Default: SKIPPED.** Tests laufen NUR wenn:
- ``KAI_TESTNET_INTEGRATION=1`` als env-Var gesetzt
- Real Testnet-API-Keys in env: ``BINANCE_TESTNET_API_KEY``/``SECRET``,
  ``BYBIT_TESTNET_API_KEY``/``SECRET``

Pytest-Marker ``@pytest.mark.testnet`` ist in ``pyproject.toml`` registered
und wird via ``addopts = ["-m", "not testnet"]`` default-ausgeschlossen.

Operator-Anleitung:
```bash
# Testnet-Keys von Binance: https://testnet.binance.vision/
# Testnet-Keys von Bybit: https://testnet.bybit.com/

export BINANCE_TESTNET_API_KEY="..."
export BINANCE_TESTNET_API_SECRET="..."
export BYBIT_TESTNET_API_KEY="..."
export BYBIT_TESTNET_API_SECRET="..."
export KAI_TESTNET_INTEGRATION=1

pytest tests/integration/test_phase0_testnet.py -m testnet -v
```

**WICHTIG:** Diese Tests platzieren ECHTE Testnet-Orders mit kleinen
quantities. Testnet-Coins sind wertlos, aber API-Rate-Limits gelten weiterhin.
Maximum 10 Orders pro Test-Lauf.
"""

from __future__ import annotations

import os
import time

import pytest

from app.execution.exchanges.base import (
    OrderRequest,
    OrderSide,
    OrderType,
)
from app.execution.exchanges.binance import BinanceAdapter
from app.execution.exchanges.bybit import BybitAdapter

pytestmark = pytest.mark.testnet


def _testnet_enabled() -> bool:
    return os.getenv("KAI_TESTNET_INTEGRATION") == "1"


def _binance_keys() -> tuple[str, str]:
    return (
        os.getenv("BINANCE_TESTNET_API_KEY", ""),
        os.getenv("BINANCE_TESTNET_API_SECRET", ""),
    )


def _bybit_keys() -> tuple[str, str]:
    return (
        os.getenv("BYBIT_TESTNET_API_KEY", ""),
        os.getenv("BYBIT_TESTNET_API_SECRET", ""),
    )


# Skip the whole file if env-Flag nicht gesetzt — schneller als per-Test-Skip
pytestmark = [
    pytest.mark.testnet,
    pytest.mark.skipif(
        not _testnet_enabled(),
        reason="KAI_TESTNET_INTEGRATION env-flag not set",
    ),
]


@pytest.mark.asyncio
async def test_binance_testnet_oco_happy_path() -> None:
    """Place BUY-LIMIT mit Server-SL gegen Binance Testnet.

    Erwartung:
    - retCode 200, success=True
    - sl_order_id != ""
    - sl_price == stop_loss aus Request
    - main + sl Order beide stornierbar
    """
    key, secret = _binance_keys()
    if not key or not secret:
        pytest.skip("BINANCE_TESTNET_API_KEY/SECRET nicht in env")

    adapter = BinanceAdapter(
        api_key=key, api_secret=secret,
        dry_run=False, testnet=True,
    )

    # BTC-Preis aus Testnet-Markt holen wäre korrekter — für Skeleton
    # nutzen wir feste Werte weit unter aktuellem Markt-Preis, damit die
    # LIMIT-Order nicht sofort fillt.
    client_id = f"int-{int(time.time() * 1000)}"
    order = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.001,
        price=30000.0,  # 50%+ unter Markt
        stop_loss=29000.0,
        client_order_id=client_id,
    )

    res = await adapter.place_order_with_server_sl(order)
    assert res.success, f"OCO failed: {res.error}"
    assert res.order_id, "missing entry order_id"
    assert res.sl_order_id, "missing SL order_id"
    assert res.sl_price == 29000.0

    # Cleanup: cancel beide Orders
    cancel_main = await adapter.cancel_order(res.order_id, "BTCUSDT")
    cancel_sl = await adapter.cancel_order(res.sl_order_id, "BTCUSDT")
    assert cancel_main.success or "NEW" in str(cancel_main.error), \
        f"main-cancel failed: {cancel_main.error}"
    assert cancel_sl.success or "NEW" in str(cancel_sl.error), \
        f"sl-cancel failed: {cancel_sl.error}"


@pytest.mark.asyncio
async def test_bybit_testnet_sl_happy_path() -> None:
    """Place LIMIT mit tpslMode=Full gegen Bybit Testnet."""
    key, secret = _bybit_keys()
    if not key or not secret:
        pytest.skip("BYBIT_TESTNET_API_KEY/SECRET nicht in env")

    adapter = BybitAdapter(
        api_key=key, api_secret=secret,
        dry_run=False, testnet=True, category="spot",
    )

    client_id = f"int-{int(time.time() * 1000)}"
    order = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.001,
        price=30000.0,
        stop_loss=29000.0,
        client_order_id=client_id,
    )

    res = await adapter.place_order_with_server_sl(order)
    assert res.success, f"Bybit place_with_sl failed: {res.error}"
    assert res.order_id
    assert res.sl_order_id == f"{res.order_id}-sl"

    # Cleanup
    cancel = await adapter.cancel_order(res.order_id, "BTCUSDT")
    assert cancel.success, f"cancel failed: {cancel.error}"


@pytest.mark.asyncio
async def test_binance_testnet_balance_query() -> None:
    """Smoke: balance-query reicht Connectivity + Sign-Path."""
    key, secret = _binance_keys()
    if not key or not secret:
        pytest.skip("BINANCE_TESTNET keys missing")

    adapter = BinanceAdapter(
        api_key=key, api_secret=secret,
        dry_run=False, testnet=True,
    )
    bal = await adapter.get_balance()
    assert bal.success, f"balance-query failed: {bal.error}"
    # Testnet-Account hat 0+ USDT — wir verifizieren nur dass die Map da ist
    assert isinstance(bal.assets, dict)


@pytest.mark.asyncio
async def test_bybit_testnet_balance_query() -> None:
    """Smoke: balance-query reicht Connectivity + Sign-Path."""
    key, secret = _bybit_keys()
    if not key or not secret:
        pytest.skip("BYBIT_TESTNET keys missing")

    adapter = BybitAdapter(
        api_key=key, api_secret=secret,
        dry_run=False, testnet=True,
    )
    bal = await adapter.get_balance()
    assert bal.success, f"balance-query failed: {bal.error}"
    assert isinstance(bal.assets, dict)
