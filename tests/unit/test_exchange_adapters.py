"""Tests for exchange adapters — base, binance, bybit, factory.

All tests use dry_run mode so no real API calls are made.
The adapters are designed to be testable without credentials.
"""

from __future__ import annotations

import pytest

from app.execution.exchanges.base import (
    BalanceResult,
    CancelResult,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from app.execution.exchanges.binance import BinanceAdapter, _map_binance_status
from app.execution.exchanges.bybit import BybitAdapter, _map_bybit_status
from app.execution.exchanges.factory import create_exchange_adapter

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def market_order() -> OrderRequest:
    return OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.001,
        client_order_id="test_001",
    )


@pytest.fixture
def limit_order() -> OrderRequest:
    return OrderRequest(
        symbol="ETHUSDT",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        quantity=0.5,
        price=3500.0,
        stop_loss=3200.0,
        take_profit=4000.0,
        client_order_id="test_002",
    )


# ---------------------------------------------------------------------------
# Base adapter / model tests
# ---------------------------------------------------------------------------

class TestOrderModels:
    """Test OrderRequest, OrderResult, and related models."""

    def test_order_request_immutable(self, market_order: OrderRequest) -> None:
        assert market_order.symbol == "BTCUSDT"
        assert market_order.side == OrderSide.BUY
        assert market_order.order_type == OrderType.MARKET
        assert market_order.quantity == 0.001
        with pytest.raises(AttributeError):
            market_order.symbol = "ETHUSDT"  # type: ignore[misc]

    def test_order_result_default(self) -> None:
        result = OrderResult(success=True)
        assert result.success is True
        assert result.status == OrderStatus.PENDING
        assert result.exchange == ""

    def test_balance_result_default(self) -> None:
        result = BalanceResult(success=False, error="no keys")
        assert result.success is False
        assert result.total_usd == 0.0

    def test_cancel_result(self) -> None:
        result = CancelResult(success=True, order_id="123", exchange="binance")
        assert result.order_id == "123"


# ---------------------------------------------------------------------------
# Binance adapter tests (dry-run only)
# ---------------------------------------------------------------------------

class TestBinanceAdapter:
    """Test BinanceAdapter in dry-run mode."""

    def test_default_config(self) -> None:
        adapter = BinanceAdapter()
        assert adapter._dry_run is True
        assert adapter._testnet is True
        assert adapter.exchange_name == "binance"
        assert adapter.is_configured is False
        assert adapter.is_live is False

    def test_configured(self) -> None:
        adapter = BinanceAdapter(api_key="key", api_secret="secret")
        assert adapter.is_configured is True
        assert adapter.is_live is False  # dry_run still True

    def test_live_mode(self) -> None:
        adapter = BinanceAdapter(
            api_key="key", api_secret="secret", dry_run=False
        )
        assert adapter.is_live is True

    @pytest.mark.asyncio
    async def test_dry_run_market_order(self, market_order: OrderRequest) -> None:
        adapter = BinanceAdapter()
        result = await adapter.place_order(market_order)
        assert result.success is True
        assert result.status == OrderStatus.DRY_RUN
        assert result.exchange == "binance"
        assert result.symbol == "BTCUSDT"
        assert result.quantity == 0.001

    @pytest.mark.asyncio
    async def test_dry_run_limit_order(self, limit_order: OrderRequest) -> None:
        adapter = BinanceAdapter()
        result = await adapter.place_order(limit_order)
        assert result.success is True
        assert result.status == OrderStatus.DRY_RUN
        assert result.price == 3500.0

    @pytest.mark.asyncio
    async def test_unconfigured_real_order_fails(
        self, market_order: OrderRequest
    ) -> None:
        adapter = BinanceAdapter(dry_run=False)
        result = await adapter.place_order(market_order)
        assert result.success is False
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_unconfigured_balance_fails(self) -> None:
        adapter = BinanceAdapter()
        result = await adapter.get_balance()
        assert result.success is False

    @pytest.mark.asyncio
    async def test_unconfigured_cancel_fails(self) -> None:
        adapter = BinanceAdapter()
        result = await adapter.cancel_order("123", symbol="BTCUSDT")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_cancel_requires_symbol(self) -> None:
        adapter = BinanceAdapter(
            api_key="key", api_secret="secret", dry_run=False
        )
        result = await adapter.cancel_order("123")
        assert result.success is False
        assert "Symbol is required" in result.error

    @pytest.mark.asyncio
    async def test_limit_order_without_price_fails(self) -> None:
        adapter = BinanceAdapter(
            api_key="key", api_secret="secret", dry_run=False
        )
        order = OrderRequest(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.001,
            # no price!
        )
        result = await adapter.place_order(order)
        assert result.success is False
        assert "price" in result.error.lower()

    def test_sign_adds_timestamp_and_signature(self) -> None:
        adapter = BinanceAdapter(api_key="key", api_secret="secret")
        params: dict[str, str | int | float] = {"symbol": "BTCUSDT"}
        signed = adapter._sign(params)
        assert "timestamp" in signed
        assert "signature" in signed
        assert isinstance(signed["signature"], str)
        assert len(signed["signature"]) == 64  # SHA256 hex


class TestBinanceStatusMapping:
    """Test Binance status string mapping."""

    def test_new(self) -> None:
        assert _map_binance_status("NEW") == OrderStatus.SUBMITTED

    def test_filled(self) -> None:
        assert _map_binance_status("FILLED") == OrderStatus.FILLED

    def test_canceled(self) -> None:
        assert _map_binance_status("CANCELED") == OrderStatus.CANCELLED

    def test_unknown(self) -> None:
        assert _map_binance_status("UNKNOWN") == OrderStatus.PENDING


# ---------------------------------------------------------------------------
# Bybit adapter tests (dry-run only)
# ---------------------------------------------------------------------------

class TestBybitAdapter:
    """Test BybitAdapter in dry-run mode."""

    def test_default_config(self) -> None:
        adapter = BybitAdapter()
        assert adapter._dry_run is True
        assert adapter._testnet is True
        assert adapter.exchange_name == "bybit"
        assert adapter.is_configured is False

    def test_configured(self) -> None:
        adapter = BybitAdapter(api_key="key", api_secret="secret")
        assert adapter.is_configured is True

    @pytest.mark.asyncio
    async def test_dry_run_order(self, market_order: OrderRequest) -> None:
        adapter = BybitAdapter()
        result = await adapter.place_order(market_order)
        assert result.success is True
        assert result.status == OrderStatus.DRY_RUN
        assert result.exchange == "bybit"

    @pytest.mark.asyncio
    async def test_unconfigured_real_order_fails(
        self, market_order: OrderRequest
    ) -> None:
        adapter = BybitAdapter(dry_run=False)
        result = await adapter.place_order(market_order)
        assert result.success is False
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_unconfigured_balance_fails(self) -> None:
        adapter = BybitAdapter()
        result = await adapter.get_balance()
        assert result.success is False

    @pytest.mark.asyncio
    async def test_unconfigured_cancel_fails(self) -> None:
        adapter = BybitAdapter()
        result = await adapter.cancel_order("123")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_limit_order_without_price_fails(self) -> None:
        adapter = BybitAdapter(
            api_key="key", api_secret="secret", dry_run=False
        )
        order = OrderRequest(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.001,
        )
        result = await adapter.place_order(order)
        assert result.success is False
        assert "price" in result.error.lower()

    def test_sign_headers(self) -> None:
        adapter = BybitAdapter(api_key="key", api_secret="secret")
        headers = adapter._sign_headers('{"symbol":"BTCUSDT"}')
        assert "X-BAPI-API-KEY" in headers
        assert "X-BAPI-SIGN" in headers
        assert headers["X-BAPI-API-KEY"] == "key"
        assert len(headers["X-BAPI-SIGN"]) == 64


class TestBybitStatusMapping:
    """Test Bybit status string mapping."""

    def test_new(self) -> None:
        assert _map_bybit_status("New") == OrderStatus.SUBMITTED

    def test_filled(self) -> None:
        assert _map_bybit_status("Filled") == OrderStatus.FILLED

    def test_cancelled(self) -> None:
        assert _map_bybit_status("Cancelled") == OrderStatus.CANCELLED

    def test_unknown(self) -> None:
        assert _map_bybit_status("Something") == OrderStatus.PENDING


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------

class TestExchangeFactory:
    """Test create_exchange_adapter factory."""

    def test_creates_binance_by_default(self) -> None:
        from app.core.settings import ExchangeSettings
        settings = ExchangeSettings(
            binance_api_key="bk",
            binance_secret="bs",
        )
        adapter = create_exchange_adapter(settings)
        assert adapter.exchange_name == "binance"
        assert adapter.is_configured is True

    def test_creates_bybit(self) -> None:
        from app.core.settings import ExchangeSettings
        settings = ExchangeSettings(
            bybit_api_key="yk",
            bybit_secret="ys",
        )
        adapter = create_exchange_adapter(settings, exchange="bybit")
        assert adapter.exchange_name == "bybit"
        assert adapter.is_configured is True

    def test_unknown_exchange_raises(self) -> None:
        from app.core.settings import ExchangeSettings
        settings = ExchangeSettings()
        with pytest.raises(ValueError, match="Unknown exchange"):
            create_exchange_adapter(settings, exchange="kraken")

    def test_respects_dry_run(self) -> None:
        from app.core.settings import ExchangeSettings
        settings = ExchangeSettings(
            binance_api_key="k",
            binance_secret="s",
            dry_run=True,
        )
        adapter = create_exchange_adapter(settings)
        assert adapter.is_live is False

    def test_respects_testnet(self) -> None:
        from app.core.settings import ExchangeSettings
        settings = ExchangeSettings(
            binance_api_key="k",
            binance_secret="s",
            testnet=False,
            dry_run=False,
        )
        adapter = create_exchange_adapter(settings)
        assert adapter.is_live is True
