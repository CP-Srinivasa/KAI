"""Phase-0 Server-Side-SL Tests (Task 41).

Spec: docs/security/kai_light_live_phase0_spec.md §4.

Deckt:
- ``BaseExchangeAdapter._validate_server_sl`` — Pflicht-Felder + SL-Richtung
- ``BinanceAdapter.place_order_with_server_sl`` — OCO-Order via httpx-Mock
- ``BybitAdapter.place_order_with_server_sl`` — V5 tpslMode via httpx-Mock
- Dry-Run-Pfad — beide Adapter
- Partial-OCO-Response-Cancel-Fallback (Binance)

Test-Pattern: pytest-asyncio + monkeypatched httpx.AsyncClient.post/delete.
Keine echten Network-Calls.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.execution.exchanges.base import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
)
from app.execution.exchanges.binance import BinanceAdapter
from app.execution.exchanges.bybit import BybitAdapter


def _make_order(
    *,
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.LIMIT,
    price: float | None = 80000.0,
    stop_loss: float | None = 78000.0,
    quantity: float = 0.001,
    symbol: str = "BTCUSDT",
    client_order_id: str = "test-001",
) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        stop_loss=stop_loss,
        client_order_id=client_order_id,
    )


# -----------------------------------------------------------------
# _validate_server_sl — wirkt für beide Adapter identisch
# -----------------------------------------------------------------


class TestValidateServerSl:
    @pytest.mark.asyncio
    async def test_rejects_missing_sl(self) -> None:
        adapter = BinanceAdapter(dry_run=True)
        res = await adapter.place_order_with_server_sl(_make_order(stop_loss=None))
        assert not res.success
        assert res.error == "server_side_sl_required"

    @pytest.mark.asyncio
    async def test_rejects_zero_sl(self) -> None:
        adapter = BinanceAdapter(dry_run=True)
        res = await adapter.place_order_with_server_sl(_make_order(stop_loss=0))
        assert not res.success
        assert res.error == "server_side_sl_required"

    @pytest.mark.asyncio
    async def test_rejects_market_order(self) -> None:
        adapter = BinanceAdapter(dry_run=True)
        res = await adapter.place_order_with_server_sl(
            _make_order(order_type=OrderType.MARKET, price=None)
        )
        assert not res.success
        assert res.error == "server_side_sl_requires_limit_order"

    @pytest.mark.asyncio
    async def test_rejects_missing_entry_price(self) -> None:
        adapter = BinanceAdapter(dry_run=True)
        res = await adapter.place_order_with_server_sl(_make_order(price=None))
        assert not res.success
        assert res.error == "server_side_sl_requires_entry_price"

    @pytest.mark.asyncio
    async def test_rejects_sl_above_buy_entry(self) -> None:
        # BUY @ 80k, SL @ 81k → SL würde sofort feuern, sinnlos.
        adapter = BinanceAdapter(dry_run=True)
        res = await adapter.place_order_with_server_sl(
            _make_order(side=OrderSide.BUY, price=80000.0, stop_loss=81000.0)
        )
        assert not res.success
        assert "sl_above_buy_entry" in res.error

    @pytest.mark.asyncio
    async def test_rejects_sl_below_sell_entry(self) -> None:
        # SELL @ 80k, SL @ 79k → SL ist UNTEN, müsste ÜBER Entry sein für SELL.
        adapter = BinanceAdapter(dry_run=True)
        res = await adapter.place_order_with_server_sl(
            _make_order(side=OrderSide.SELL, price=80000.0, stop_loss=79000.0)
        )
        assert not res.success
        assert "sl_below_sell_entry" in res.error


# -----------------------------------------------------------------
# Dry-Run-Pfad (beide Adapter)
# -----------------------------------------------------------------


class TestDryRunWithSl:
    @pytest.mark.asyncio
    async def test_binance_dry_run_returns_sl_fields(self) -> None:
        adapter = BinanceAdapter(dry_run=True)
        res = await adapter.place_order_with_server_sl(_make_order())
        assert res.success
        assert res.status == OrderStatus.DRY_RUN
        assert res.sl_order_id.startswith("dry_sl_")
        assert res.sl_price == 78000.0
        assert res.exchange == "binance"

    @pytest.mark.asyncio
    async def test_bybit_dry_run_returns_sl_fields(self) -> None:
        adapter = BybitAdapter(dry_run=True)
        res = await adapter.place_order_with_server_sl(_make_order())
        assert res.success
        assert res.status == OrderStatus.DRY_RUN
        assert res.sl_order_id.startswith("dry_sl_")
        assert res.sl_price == 78000.0
        assert res.exchange == "bybit"


# -----------------------------------------------------------------
# Binance OCO-Response-Mocks
# -----------------------------------------------------------------


def _binance_oco_response_ok() -> dict[str, Any]:
    """Binance OCO erfolgreich: 2 orderReports, LIMIT + STOP_LOSS_LIMIT."""
    return {
        "orderListId": 999,
        "contingencyType": "OCO",
        "listStatusType": "EXEC_STARTED",
        "listOrderStatus": "EXECUTING",
        "orderReports": [
            {"orderId": 11111, "type": "LIMIT_MAKER"},
            {"orderId": 22222, "type": "STOP_LOSS_LIMIT"},
        ],
    }


def _binance_oco_response_partial() -> dict[str, Any]:
    """Binance OCO partial — nur LIMIT, kein STOP_LOSS_LIMIT
    (sollte eigentlich nie passieren, aber Defense-in-Depth)."""
    return {
        "orderListId": 999,
        "orderReports": [
            {"orderId": 11111, "type": "LIMIT_MAKER"},
        ],
    }


class TestBinanceOco:
    @pytest.mark.asyncio
    async def test_oco_success_returns_both_ids(self, monkeypatch) -> None:
        async def mock_post(self, url, *args, **kwargs):  # type: ignore[no-untyped-def]
            return httpx.Response(200, json=_binance_oco_response_ok())

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        adapter = BinanceAdapter(
            api_key="k", api_secret="s", dry_run=False, testnet=True,
        )
        res = await adapter.place_order_with_server_sl(_make_order())
        assert res.success
        assert res.order_id == "11111"
        assert res.sl_order_id == "22222"
        assert res.sl_price == 78000.0
        assert res.status == OrderStatus.SUBMITTED

    @pytest.mark.asyncio
    async def test_oco_partial_triggers_cancel_and_rejects(
        self, monkeypatch
    ) -> None:
        cancel_calls: list[str] = []

        async def mock_post(self, url, *args, **kwargs):  # type: ignore[no-untyped-def]
            return httpx.Response(200, json=_binance_oco_response_partial())

        async def mock_delete(self, url, *args, **kwargs):  # type: ignore[no-untyped-def]
            cancel_calls.append(str(kwargs.get("params", {}).get("orderId", "")))
            return httpx.Response(200, json={"orderId": "cancelled"})

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
        monkeypatch.setattr(httpx.AsyncClient, "delete", mock_delete)

        adapter = BinanceAdapter(
            api_key="k", api_secret="s", dry_run=False, testnet=True,
        )
        res = await adapter.place_order_with_server_sl(_make_order())
        assert not res.success
        assert "sl_placement_failed" in res.error
        # Cancel-Fallback wurde getriggert für die LIMIT-Order, die platziert war.
        assert "11111" in cancel_calls

    @pytest.mark.asyncio
    async def test_oco_http_error_returns_failed(self, monkeypatch) -> None:
        async def mock_post(self, url, *args, **kwargs):  # type: ignore[no-untyped-def]
            return httpx.Response(400, json={"code": -1013, "msg": "Filter failure"})

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        adapter = BinanceAdapter(
            api_key="k", api_secret="s", dry_run=False, testnet=True,
        )
        res = await adapter.place_order_with_server_sl(_make_order())
        assert not res.success
        assert "oco_http_400" in res.error

    @pytest.mark.asyncio
    async def test_no_api_keys_returns_failed(self) -> None:
        adapter = BinanceAdapter(
            api_key="", api_secret="", dry_run=False, testnet=True,
        )
        res = await adapter.place_order_with_server_sl(_make_order())
        assert not res.success
        assert "not configured" in res.error


# -----------------------------------------------------------------
# Bybit V5 stopLoss-Response-Mocks
# -----------------------------------------------------------------


def _bybit_create_response_ok() -> dict[str, Any]:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {"orderId": "bb-77777", "orderLinkId": "test-001"},
    }


def _bybit_create_response_error() -> dict[str, Any]:
    return {
        "retCode": 110007,
        "retMsg": "Insufficient available balance",
        "result": {},
    }


class TestBybitOrderWithSl:
    @pytest.mark.asyncio
    async def test_success_returns_entry_and_synthetic_sl_id(
        self, monkeypatch
    ) -> None:
        captured: dict[str, Any] = {}

        async def mock_post(self, url, *args, **kwargs):  # type: ignore[no-untyped-def]
            captured["body"] = kwargs.get("content", "")
            return httpx.Response(200, json=_bybit_create_response_ok())

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        adapter = BybitAdapter(
            api_key="k", api_secret="s", dry_run=False, testnet=True,
        )
        res = await adapter.place_order_with_server_sl(_make_order())
        assert res.success
        assert res.order_id == "bb-77777"
        assert res.sl_order_id == "bb-77777-sl"
        assert res.sl_price == 78000.0

        body = json.loads(captured["body"])
        assert body["stopLoss"] == "78000.0"
        assert body["tpslMode"] == "Full"
        assert body["orderType"] == "Limit"

    @pytest.mark.asyncio
    async def test_retcode_error_returns_failed(self, monkeypatch) -> None:
        async def mock_post(self, url, *args, **kwargs):  # type: ignore[no-untyped-def]
            return httpx.Response(200, json=_bybit_create_response_error())

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        adapter = BybitAdapter(
            api_key="k", api_secret="s", dry_run=False, testnet=True,
        )
        res = await adapter.place_order_with_server_sl(_make_order())
        assert not res.success
        assert "bybit_sl_order_failed" in res.error
        assert "110007" in res.error

    @pytest.mark.asyncio
    async def test_no_api_keys_returns_failed(self) -> None:
        adapter = BybitAdapter(
            api_key="", api_secret="", dry_run=False, testnet=True,
        )
        res = await adapter.place_order_with_server_sl(_make_order())
        assert not res.success
        assert "not configured" in res.error
