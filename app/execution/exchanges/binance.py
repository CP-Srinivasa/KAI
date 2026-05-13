"""Binance Spot exchange adapter.

Supports:
- Testnet: https://testnet.binance.vision (default)
- Production: https://api.binance.com

All requests use HMAC-SHA256 signing. Default mode is dry_run + testnet.
The user must set EXCHANGE_BINANCE_API_KEY and EXCHANGE_BINANCE_SECRET
in .env before any real trading.

Reference: https://developers.binance.com/docs/binance-spot-api-docs
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

import httpx

from app.execution.exchanges.base import (
    BalanceResult,
    BaseExchangeAdapter,
    CancelResult,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
)

logger = logging.getLogger(__name__)

_BINANCE_PROD_URL = "https://api.binance.com"
_BINANCE_TESTNET_URL = "https://testnet.binance.vision"


class BinanceAdapter(BaseExchangeAdapter):
    """Binance Spot API adapter with HMAC-SHA256 authentication."""

    def __init__(
        self,
        *,
        api_key: str = "",
        api_secret: str = "",
        dry_run: bool = True,
        testnet: bool = True,
        timeout: float = 15.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            dry_run=dry_run,
            testnet=testnet,
        )
        self._base_url = _BINANCE_TESTNET_URL if testnet else _BINANCE_PROD_URL
        self._timeout = timeout

    def _sign(self, params: dict[str, str | int | float]) -> dict[str, str | int | float]:
        """Add timestamp and HMAC-SHA256 signature to params."""
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    def _headers(self) -> dict[str, str]:
        return {"X-MBX-APIKEY": self._api_key}

    async def place_order(self, order: OrderRequest) -> OrderResult:
        """Place a spot order on Binance."""
        if self._dry_run:
            return self._dry_run_order(order)

        if not self.is_configured:
            return OrderResult(
                success=False,
                error="Binance API keys not configured",
                exchange="binance",
            )

        params: dict[str, str | int | float] = {
            "symbol": order.symbol.upper().replace("/", ""),
            "side": order.side.upper(),
            "type": "MARKET" if order.order_type == OrderType.MARKET else "LIMIT",
            "quantity": order.quantity,
        }

        if order.order_type == OrderType.LIMIT:
            if order.price is None:
                return OrderResult(
                    success=False,
                    error="Limit order requires price",
                    exchange="binance",
                )
            params["price"] = order.price
            params["timeInForce"] = order.time_in_force

        if order.client_order_id:
            params["newClientOrderId"] = order.client_order_id

        signed = self._sign(params)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/api/v3/order",
                    params=signed,
                    headers=self._headers(),
                )

            if resp.status_code != 200:
                error_data = resp.json()
                return OrderResult(
                    success=False,
                    error=f"HTTP {resp.status_code}: {error_data.get('msg', resp.text[:200])}",
                    exchange="binance",
                    raw_response=error_data,
                )

            data = resp.json()
            return OrderResult(
                success=True,
                order_id=str(data.get("orderId", "")),
                client_order_id=str(data.get("clientOrderId", "")),
                symbol=str(data.get("symbol", "")),
                side=str(data.get("side", "")).lower(),
                order_type=str(data.get("type", "")).lower(),
                quantity=float(data.get("origQty", 0)),
                filled_quantity=float(data.get("executedQty", 0)),
                price=float(data.get("price", 0)),
                status=_map_binance_status(str(data.get("status", ""))),
                exchange="binance",
                raw_response=data,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[BINANCE] Order failed: %s", exc)
            return OrderResult(
                success=False,
                error=str(exc)[:200],
                exchange="binance",
            )

    async def get_balance(self) -> BalanceResult:
        """Fetch account balance from Binance."""
        if not self.is_configured:
            return BalanceResult(
                success=False,
                error="Binance API keys not configured",
                exchange="binance",
            )

        signed = self._sign({})

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/api/v3/account",
                    params=signed,
                    headers=self._headers(),
                )

            if resp.status_code != 200:
                return BalanceResult(
                    success=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    exchange="binance",
                )

            data = resp.json()
            balances = data.get("balances", [])
            assets: dict[str, float] = {}
            for b in balances:
                free = float(b.get("free", 0))
                locked = float(b.get("locked", 0))
                if free > 0 or locked > 0:
                    assets[b["asset"]] = free + locked

            usdt = float(
                next(
                    (b["free"] for b in balances if b["asset"] == "USDT"),
                    0,
                )
            )
            locked_usdt = float(
                next(
                    (b["locked"] for b in balances if b["asset"] == "USDT"),
                    0,
                )
            )

            return BalanceResult(
                success=True,
                total_usd=usdt + locked_usdt,
                available_usd=usdt,
                locked_usd=locked_usdt,
                assets=assets,
                exchange="binance",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[BINANCE] Balance query failed: %s", exc)
            return BalanceResult(
                success=False,
                error=str(exc)[:200],
                exchange="binance",
            )

    async def cancel_order(self, order_id: str, symbol: str = "") -> CancelResult:
        """Cancel an open order on Binance."""
        if not self.is_configured:
            return CancelResult(
                success=False,
                error="Binance API keys not configured",
                exchange="binance",
            )

        if not symbol:
            return CancelResult(
                success=False,
                error="Symbol is required for Binance cancel",
                exchange="binance",
            )

        params: dict[str, str | int | float] = {
            "symbol": symbol.upper().replace("/", ""),
            "orderId": order_id,
        }
        signed = self._sign(params)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.delete(
                    f"{self._base_url}/api/v3/order",
                    params=signed,
                    headers=self._headers(),
                )

            if resp.status_code != 200:
                return CancelResult(
                    success=False,
                    order_id=order_id,
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    exchange="binance",
                )

            return CancelResult(
                success=True,
                order_id=order_id,
                exchange="binance",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[BINANCE] Cancel failed: %s", exc)
            return CancelResult(
                success=False,
                order_id=order_id,
                error=str(exc)[:200],
                exchange="binance",
            )

    async def get_open_orders(self, symbol: str = "") -> list[OrderResult]:
        """List open orders on Binance."""
        if not self.is_configured:
            return []

        params: dict[str, str | int | float] = {}
        if symbol:
            params["symbol"] = symbol.upper().replace("/", "")
        signed = self._sign(params)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/api/v3/openOrders",
                    params=signed,
                    headers=self._headers(),
                )

            if resp.status_code != 200:
                return []

            orders = resp.json()
            return [
                OrderResult(
                    success=True,
                    order_id=str(o.get("orderId", "")),
                    client_order_id=str(o.get("clientOrderId", "")),
                    symbol=str(o.get("symbol", "")),
                    side=str(o.get("side", "")).lower(),
                    order_type=str(o.get("type", "")).lower(),
                    quantity=float(o.get("origQty", 0)),
                    filled_quantity=float(o.get("executedQty", 0)),
                    price=float(o.get("price", 0)),
                    status=_map_binance_status(str(o.get("status", ""))),
                    exchange="binance",
                )
                for o in orders
            ]
        except Exception as exc:  # noqa: BLE001
            logger.error("[BINANCE] Open orders query failed: %s", exc)
            return []


def _map_binance_status(status: str) -> OrderStatus:
    """Map Binance order status strings to OrderStatus enum."""
    mapping = {
        "NEW": OrderStatus.SUBMITTED,
        "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
        "FILLED": OrderStatus.FILLED,
        "CANCELED": OrderStatus.CANCELLED,
        "REJECTED": OrderStatus.REJECTED,
        "EXPIRED": OrderStatus.CANCELLED,
    }
    return mapping.get(status, OrderStatus.PENDING)
