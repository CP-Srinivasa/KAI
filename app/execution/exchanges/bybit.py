"""Bybit exchange adapter (Unified Trading Account V5 API).

Supports:
- Testnet: https://api-testnet.bybit.com (default)
- Production: https://api.bybit.com

All requests use HMAC-SHA256 signing. Default mode is dry_run + testnet.

Reference: https://bybit-exchange.github.io/docs/v5/intro
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

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

_BYBIT_PROD_URL = "https://api.bybit.com"
_BYBIT_TESTNET_URL = "https://api-testnet.bybit.com"
_RECV_WINDOW = "5000"


class BybitAdapter(BaseExchangeAdapter):
    """Bybit V5 Unified Trading API adapter."""

    def __init__(
        self,
        *,
        api_key: str = "",
        api_secret: str = "",
        dry_run: bool = True,
        testnet: bool = True,
        timeout: float = 15.0,
        category: str = "spot",
    ) -> None:
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            dry_run=dry_run,
            testnet=testnet,
        )
        self._base_url = _BYBIT_TESTNET_URL if testnet else _BYBIT_PROD_URL
        self._timeout = timeout
        self._category = category  # "spot", "linear", "inverse"

    def _sign_headers(self, payload_str: str) -> dict[str, str]:
        """Generate Bybit V5 auth headers (HMAC-SHA256)."""
        timestamp = str(int(time.time() * 1000))
        sign_str = f"{timestamp}{self._api_key}{_RECV_WINDOW}{payload_str}"
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": _RECV_WINDOW,
            "Content-Type": "application/json",
        }

    async def place_order(self, order: OrderRequest) -> OrderResult:
        """Place a spot order on Bybit."""
        if self._dry_run:
            return self._dry_run_order(order)

        if not self.is_configured:
            return OrderResult(
                success=False,
                error="Bybit API keys not configured",
                exchange="bybit",
            )

        payload: dict[str, str | float] = {
            "category": self._category,
            "symbol": order.symbol.upper().replace("/", ""),
            "side": "Buy" if order.side == "buy" else "Sell",
            "orderType": "Market" if order.order_type == OrderType.MARKET else "Limit",
            "qty": str(order.quantity),
        }

        if order.order_type == OrderType.LIMIT:
            if order.price is None:
                return OrderResult(
                    success=False,
                    error="Limit order requires price",
                    exchange="bybit",
                )
            payload["price"] = str(order.price)
            payload["timeInForce"] = order.time_in_force

        if order.client_order_id:
            payload["orderLinkId"] = order.client_order_id

        import json
        payload_str = json.dumps(payload)
        headers = self._sign_headers(payload_str)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/v5/order/create",
                    content=payload_str,
                    headers=headers,
                )

            data = resp.json()
            ret_code = data.get("retCode", -1)

            if ret_code != 0:
                return OrderResult(
                    success=False,
                    error=f"Bybit error {ret_code}: {data.get('retMsg', '')}",
                    exchange="bybit",
                    raw_response=data,
                )

            result = data.get("result", {})
            return OrderResult(
                success=True,
                order_id=str(result.get("orderId", "")),
                client_order_id=str(result.get("orderLinkId", "")),
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                status=OrderStatus.SUBMITTED,
                exchange="bybit",
                raw_response=data,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[BYBIT] Order failed: %s", exc)
            return OrderResult(
                success=False,
                error=str(exc)[:200],
                exchange="bybit",
            )

    async def get_balance(self) -> BalanceResult:
        """Fetch unified account balance from Bybit."""
        if not self.is_configured:
            return BalanceResult(
                success=False,
                error="Bybit API keys not configured",
                exchange="bybit",
            )

        params = "accountType=UNIFIED"
        headers = self._sign_headers(params)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/v5/account/wallet-balance",
                    params={"accountType": "UNIFIED"},
                    headers=headers,
                )

            data = resp.json()
            if data.get("retCode", -1) != 0:
                return BalanceResult(
                    success=False,
                    error=f"Bybit error: {data.get('retMsg', '')}",
                    exchange="bybit",
                )

            accounts = data.get("result", {}).get("list", [])
            if not accounts:
                return BalanceResult(
                    success=True,
                    exchange="bybit",
                )

            account = accounts[0]
            total_equity = float(account.get("totalEquity", 0))
            available = float(account.get("totalAvailableBalance", 0))

            assets: dict[str, float] = {}
            for coin in account.get("coin", []):
                equity = float(coin.get("equity", 0))
                if equity > 0:
                    assets[coin["coin"]] = equity

            return BalanceResult(
                success=True,
                total_usd=total_equity,
                available_usd=available,
                locked_usd=total_equity - available,
                assets=assets,
                exchange="bybit",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[BYBIT] Balance query failed: %s", exc)
            return BalanceResult(
                success=False,
                error=str(exc)[:200],
                exchange="bybit",
            )

    async def cancel_order(self, order_id: str, symbol: str = "") -> CancelResult:
        """Cancel an open order on Bybit."""
        if not self.is_configured:
            return CancelResult(
                success=False,
                error="Bybit API keys not configured",
                exchange="bybit",
            )

        import json
        payload = {
            "category": self._category,
            "orderId": order_id,
        }
        if symbol:
            payload["symbol"] = symbol.upper().replace("/", "")

        payload_str = json.dumps(payload)
        headers = self._sign_headers(payload_str)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/v5/order/cancel",
                    content=payload_str,
                    headers=headers,
                )

            data = resp.json()
            if data.get("retCode", -1) != 0:
                return CancelResult(
                    success=False,
                    order_id=order_id,
                    error=f"Bybit error: {data.get('retMsg', '')}",
                    exchange="bybit",
                )

            return CancelResult(
                success=True,
                order_id=order_id,
                exchange="bybit",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[BYBIT] Cancel failed: %s", exc)
            return CancelResult(
                success=False,
                order_id=order_id,
                error=str(exc)[:200],
                exchange="bybit",
            )

    async def get_open_orders(self, symbol: str = "") -> list[OrderResult]:
        """List open orders on Bybit."""
        if not self.is_configured:
            return []

        params_dict: dict[str, str] = {"category": self._category}
        if symbol:
            params_dict["symbol"] = symbol.upper().replace("/", "")

        from urllib.parse import urlencode
        params_str = urlencode(params_dict)
        headers = self._sign_headers(params_str)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/v5/order/realtime",
                    params=params_dict,
                    headers=headers,
                )

            data = resp.json()
            if data.get("retCode", -1) != 0:
                return []

            orders = data.get("result", {}).get("list", [])
            return [
                OrderResult(
                    success=True,
                    order_id=str(o.get("orderId", "")),
                    client_order_id=str(o.get("orderLinkId", "")),
                    symbol=str(o.get("symbol", "")),
                    side=str(o.get("side", "")).lower(),
                    order_type=str(o.get("orderType", "")).lower(),
                    quantity=float(o.get("qty", 0)),
                    filled_quantity=float(o.get("cumExecQty", 0)),
                    price=float(o.get("price", 0)),
                    status=_map_bybit_status(str(o.get("orderStatus", ""))),
                    exchange="bybit",
                )
                for o in orders
            ]
        except Exception as exc:  # noqa: BLE001
            logger.error("[BYBIT] Open orders query failed: %s", exc)
            return []


def _map_bybit_status(status: str) -> OrderStatus:
    """Map Bybit order status to OrderStatus enum."""
    mapping = {
        "New": OrderStatus.SUBMITTED,
        "PartiallyFilled": OrderStatus.PARTIALLY_FILLED,
        "Filled": OrderStatus.FILLED,
        "Cancelled": OrderStatus.CANCELLED,
        "Rejected": OrderStatus.REJECTED,
        "Deactivated": OrderStatus.CANCELLED,
    }
    return mapping.get(status, OrderStatus.PENDING)
