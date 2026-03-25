"""Abstract exchange adapter interface and shared models.

All exchange adapters must implement BaseExchangeAdapter.
This ensures a consistent interface for order placement,
balance queries, and order management regardless of exchange.

Design invariants:
- All adapters default to dry_run=True (no real trades)
- All orders are logged before submission
- All adapters support testnet URLs
- Errors never raise — they return error results
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

logger = logging.getLogger(__name__)


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"
    DRY_RUN = "dry_run"


@dataclass(frozen=True)
class OrderRequest:
    """Immutable order request — input to exchange adapter."""

    symbol: str  # e.g. "BTCUSDT"
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None = None  # required for LIMIT orders
    stop_loss: float | None = None
    take_profit: float | None = None
    client_order_id: str = ""  # for idempotency
    time_in_force: str = "GTC"  # GTC, IOC, FOK


@dataclass(frozen=True)
class OrderResult:
    """Immutable order result — output from exchange adapter."""

    success: bool
    order_id: str = ""
    client_order_id: str = ""
    symbol: str = ""
    side: str = ""
    order_type: str = ""
    quantity: float = 0.0
    filled_quantity: float = 0.0
    price: float = 0.0
    avg_fill_price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    error: str = ""
    timestamp_utc: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    exchange: str = ""
    raw_response: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class BalanceResult:
    """Immutable balance query result."""

    success: bool
    total_usd: float = 0.0
    available_usd: float = 0.0
    locked_usd: float = 0.0
    assets: dict[str, float] = field(default_factory=dict)
    error: str = ""
    exchange: str = ""
    timestamp_utc: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


@dataclass(frozen=True)
class CancelResult:
    """Immutable cancel result."""

    success: bool
    order_id: str = ""
    error: str = ""
    exchange: str = ""


class BaseExchangeAdapter(ABC):
    """Abstract base class for exchange adapters.

    All adapters must implement:
    - place_order: Submit an order to the exchange
    - get_balance: Query account balance
    - cancel_order: Cancel an open order
    - get_open_orders: List open orders

    Config:
    - dry_run=True by default (no real trades)
    - testnet=True by default (use sandbox APIs)
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        dry_run: bool = True,
        testnet: bool = True,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._dry_run = dry_run
        self._testnet = testnet

    @property
    def exchange_name(self) -> str:
        """Return the exchange identifier (e.g. 'binance', 'bybit')."""
        return self.__class__.__name__.lower().replace("adapter", "")

    @property
    def is_configured(self) -> bool:
        """True if API credentials are set."""
        return bool(self._api_key) and bool(self._api_secret)

    @property
    def is_live(self) -> bool:
        """True if adapter will submit real orders."""
        return not self._dry_run and self.is_configured

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResult:
        """Submit an order to the exchange."""

    @abstractmethod
    async def get_balance(self) -> BalanceResult:
        """Query account balance."""

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str = "") -> CancelResult:
        """Cancel an open order."""

    @abstractmethod
    async def get_open_orders(self, symbol: str = "") -> list[OrderResult]:
        """List open orders, optionally filtered by symbol."""

    def _dry_run_order(self, order: OrderRequest) -> OrderResult:
        """Return a simulated order result for dry-run mode."""
        logger.info(
            "[%s DRY RUN] %s %s %.6f %s @ %s",
            self.exchange_name.upper(),
            order.side.upper(),
            order.symbol,
            order.quantity,
            order.order_type,
            order.price or "MARKET",
        )
        return OrderResult(
            success=True,
            order_id=f"dry_{order.client_order_id or 'none'}",
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            filled_quantity=order.quantity,
            price=order.price or 0.0,
            avg_fill_price=order.price or 0.0,
            status=OrderStatus.DRY_RUN,
            exchange=self.exchange_name,
        )
