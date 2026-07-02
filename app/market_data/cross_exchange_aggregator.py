"""Cross-exchange aggregation hook (Issue #169, items 3 + 4).

Single place that takes N venues' quotes for the *same* asset and runs them
through :func:`app.market_data.cross_exchange.validate_cross_exchange`. Behind a
default-OFF flag with funnel counters.

No-execution invariant (Issue #169)
-----------------------------------
This module is a **read-only validation / observability** layer. It imports
nothing from ``app.execution`` / ``app.orchestrator`` / ``app.risk``. It never
opens, sizes, blocks, or routes an order, and it never reads or writes
``entry_mode``. Its output is a diagnostic envelope an operator surface can show;
acting on it is a separate, operator-signed-off decision.

Flag: ``APP_CROSS_EXCHANGE_VALIDATION_ENABLED`` (default False). While OFF,
:func:`run_cross_exchange_validation` returns a ``disabled`` envelope without
running the median — live behaviour is unchanged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.market_data.cross_exchange import (
    CrossExchangeConfig,
    CrossExchangeValidation,
    validate_cross_exchange,
)
from app.market_data.models import MarketDataPoint
from app.market_data.quote_builder import Microstructure, build_provider_quote

if TYPE_CHECKING:
    from app.core.settings import AppSettings

# One venue's input to the aggregation: a price point + (optional) microstructure.
VenueInput = tuple[MarketDataPoint, Microstructure | None]


@dataclass(frozen=True)
class CrossExchangeAggregation:
    """Result of an aggregation pass, with funnel counters for observability."""

    asset_id: str
    enabled: bool
    # Funnel: how many venues went in, how many produced a usable quote, and how
    # many were excluded for lacking honest microstructure.
    providers_in: int
    quotes_built: int
    excluded_count: int
    excluded_providers: tuple[str, ...]
    validation: CrossExchangeValidation | None
    reason: str | None = None
    # No-execution invariant marker — always True for this layer.
    influences_execution: bool = field(default=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_type": "cross_exchange_aggregation",
            "asset_id": self.asset_id,
            "enabled": self.enabled,
            "providers_in": self.providers_in,
            "quotes_built": self.quotes_built,
            "excluded_count": self.excluded_count,
            "excluded_providers": list(self.excluded_providers),
            "validation": (
                self.validation.to_output_dict() if self.validation is not None else None
            ),
            "reason": self.reason,
            "influences_execution": self.influences_execution,
        }


def cross_exchange_validation_enabled(settings: AppSettings) -> bool:
    """Default-OFF guard. Reads ``cross_exchange_validation_enabled`` fail-closed."""
    return bool(getattr(settings, "cross_exchange_validation_enabled", False))


def aggregate_and_validate(
    asset_id: str,
    venue_inputs: list[VenueInput],
    *,
    config: CrossExchangeConfig | None = None,
    now_ms: float | None = None,
    volatility: float = 0.0,
    regime: object = None,
) -> CrossExchangeAggregation:
    """Pure aggregation core: build quotes from ``venue_inputs`` (dropping venues
    without honest microstructure) and run the weighted median. No flag check —
    callers gate via :func:`run_cross_exchange_validation`.
    """
    now = now_ms if now_ms is not None else time.time() * 1000.0
    quotes = []
    excluded: list[str] = []
    for point, micro in venue_inputs:
        quote = build_provider_quote(point, micro, now_ms=now)
        if quote is None:
            excluded.append(point.source)
        else:
            quotes.append(quote)

    validation = (
        validate_cross_exchange(
            asset_id, quotes, volatility=volatility, regime=regime, now_ms=now, config=config
        )
        if quotes
        else None
    )
    return CrossExchangeAggregation(
        asset_id=asset_id,
        enabled=True,
        providers_in=len(venue_inputs),
        quotes_built=len(quotes),
        excluded_count=len(excluded),
        excluded_providers=tuple(excluded),
        validation=validation,
        reason=None if quotes else "no_microstructure_quotes",
    )


def run_cross_exchange_validation(
    asset_id: str,
    venue_inputs: list[VenueInput],
    settings: AppSettings,
    *,
    config: CrossExchangeConfig | None = None,
    now_ms: float | None = None,
    volatility: float = 0.0,
    regime: object = None,
) -> CrossExchangeAggregation:
    """Flag-gated entry point. Returns a ``disabled`` envelope (no median run)
    while ``APP_CROSS_EXCHANGE_VALIDATION_ENABLED`` is OFF — live behaviour
    unchanged. Never influences execution either way.
    """
    if not cross_exchange_validation_enabled(settings):
        return CrossExchangeAggregation(
            asset_id=asset_id,
            enabled=False,
            providers_in=len(venue_inputs),
            quotes_built=0,
            excluded_count=0,
            excluded_providers=(),
            validation=None,
            reason="cross_exchange_validation_disabled",
        )
    return aggregate_and_validate(
        asset_id,
        venue_inputs,
        config=config,
        now_ms=now_ms,
        volatility=volatility,
        regime=regime,
    )


__all__ = [
    "CrossExchangeAggregation",
    "VenueInput",
    "aggregate_and_validate",
    "cross_exchange_validation_enabled",
    "run_cross_exchange_validation",
]
