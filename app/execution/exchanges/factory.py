"""Exchange adapter factory — creates adapters from ExchangeSettings.

Usage:
    from app.core.settings import ExchangeSettings
    from app.execution.exchanges.factory import create_exchange_adapter

    settings = ExchangeSettings()
    adapter = create_exchange_adapter(settings)
    # or for a specific exchange:
    adapter = create_exchange_adapter(settings, exchange="bybit")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.settings import ExchangeSettings

from app.execution.exchanges.base import BaseExchangeAdapter

logger = logging.getLogger(__name__)


def create_exchange_adapter(
    settings: ExchangeSettings,
    *,
    exchange: str = "",
) -> BaseExchangeAdapter:
    """Create an exchange adapter from ExchangeSettings.

    Args:
        settings: ExchangeSettings instance
        exchange: Override the default exchange ("binance" or "bybit")

    Returns:
        Configured exchange adapter (dry_run + testnet by default)
    """
    target = (exchange or settings.default_exchange).strip().lower()

    if target == "binance":
        from app.execution.exchanges.binance import BinanceAdapter

        adapter = BinanceAdapter(
            api_key=settings.binance_api_key,
            api_secret=settings.binance_secret,
            dry_run=settings.dry_run,
            testnet=settings.testnet,
            timeout=settings.timeout_seconds,
        )
        logger.info(
            "[EXCHANGE] Created Binance adapter (dry_run=%s, testnet=%s, configured=%s)",
            settings.dry_run,
            settings.testnet,
            adapter.is_configured,
        )
        return adapter

    if target == "bybit":
        from app.execution.exchanges.bybit import BybitAdapter

        adapter = BybitAdapter(
            api_key=settings.bybit_api_key,
            api_secret=settings.bybit_secret,
            dry_run=settings.dry_run,
            testnet=settings.testnet,
            timeout=settings.timeout_seconds,
            category=settings.bybit_category,
        )
        logger.info(
            "[EXCHANGE] Created Bybit adapter (dry_run=%s, testnet=%s, configured=%s)",
            settings.dry_run,
            settings.testnet,
            adapter.is_configured,
        )
        return adapter

    raise ValueError(
        f"Unknown exchange: '{target}'. Supported: binance, bybit"
    )
