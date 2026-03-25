"""Alert price-move checker for semi-automated outcome annotation.

Uses CoinGecko 24h change data to suggest hit/miss/inconclusive outcomes
for pending directional alerts. Best run within ~48h of alert dispatch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.alerts.audit import AlertAuditRecord, OutcomeLabel
from app.market_data.coingecko_adapter import CoinGeckoAdapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceCheckResult:
    """Result of checking a single asset's price move against an alert prediction."""

    document_id: str
    asset: str
    sentiment_label: str
    current_price: float | None
    change_pct_24h: float | None
    suggested_outcome: OutcomeLabel
    reason: str


def _suggest_outcome(
    sentiment: str,
    change_pct: float,
    threshold_pct: float,
) -> tuple[OutcomeLabel, str]:
    """Determine outcome from sentiment direction and observed price change.

    Returns (outcome, reason) tuple.
    """
    abs_change = abs(change_pct)
    direction = "up" if change_pct >= 0 else "down"

    if abs_change < threshold_pct:
        return "inconclusive", f"{direction} {abs_change:.1f}% < {threshold_pct}% threshold"

    if sentiment == "bullish":
        if change_pct >= threshold_pct:
            return "hit", f"bullish confirmed: +{change_pct:.1f}%"
        return "miss", f"bullish missed: {change_pct:.1f}%"

    if sentiment == "bearish":
        if change_pct <= -threshold_pct:
            return "hit", f"bearish confirmed: {change_pct:.1f}%"
        return "miss", f"bearish missed: +{abs_change:.1f}%"

    return "inconclusive", f"non-directional sentiment: {sentiment}"


async def check_alert_price_moves(
    records: list[AlertAuditRecord],
    *,
    threshold_pct: float = 5.0,
    timeout_seconds: int = 10,
) -> list[PriceCheckResult]:
    """Check price moves for a list of alert audit records.

    For each record with affected_assets and a directional sentiment_label
    (bullish/bearish), fetches the current 24h price change from CoinGecko
    and suggests an outcome.

    Args:
        records: Alert audit records to check.
        threshold_pct: Minimum absolute price change (%) to classify as hit/miss.
        timeout_seconds: CoinGecko request timeout.

    Returns:
        List of PriceCheckResult, one per (record, asset) pair.
    """
    adapter = CoinGeckoAdapter(timeout_seconds=timeout_seconds)
    results: list[PriceCheckResult] = []

    # Deduplicate assets across all records to minimize API calls
    all_assets: set[str] = set()
    for rec in records:
        for asset in rec.affected_assets:
            all_assets.add(asset.strip().upper())

    # Fetch prices for all unique assets
    price_cache: dict[str, tuple[float | None, float | None]] = {}
    for asset in sorted(all_assets):
        symbol = f"{asset}/USDT" if "/" not in asset else asset
        try:
            ticker = await adapter.get_ticker(symbol)
            if ticker is not None:
                price_cache[asset] = (ticker.last, ticker.change_pct_24h)
            else:
                price_cache[asset] = (None, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Price fetch failed for %s: %s", asset, exc)
            price_cache[asset] = (None, None)

    # Evaluate each record
    for rec in records:
        sentiment = (rec.sentiment_label or "").lower()
        if sentiment not in {"bullish", "bearish"}:
            continue

        for raw_asset in rec.affected_assets:
            asset = raw_asset.strip().upper()
            price, change_pct = price_cache.get(asset, (None, None))

            if price is None or change_pct is None:
                results.append(
                    PriceCheckResult(
                        document_id=rec.document_id,
                        asset=asset,
                        sentiment_label=sentiment,
                        current_price=None,
                        change_pct_24h=None,
                        suggested_outcome="inconclusive",
                        reason=f"price unavailable for {asset}",
                    )
                )
                continue

            outcome, reason = _suggest_outcome(sentiment, change_pct, threshold_pct)
            results.append(
                PriceCheckResult(
                    document_id=rec.document_id,
                    asset=asset,
                    sentiment_label=sentiment,
                    current_price=price,
                    change_pct_24h=change_pct,
                    suggested_outcome=outcome,
                    reason=reason,
                )
            )

    return results
