"""Alert price-move checker for semi-automated outcome annotation.

Uses CoinGecko 24h change data to suggest hit/miss/inconclusive outcomes
for pending directional alerts. Best run within ~48h of alert dispatch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.alerts.audit import AlertAuditRecord, OutcomeLabel
from app.market_data.coingecko_adapter import CoinGeckoAdapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceCheckResult:
    """Result of checking a single asset's price move against an alert prediction."""

    document_id: str
    asset: str
    sentiment_label: str
    price_at_alert: float | None = None
    price_at_horizon: float | None = None
    observed_move_pct: float | None = None
    evaluation_mode: str = "historical_window"
    window_start_utc: str | None = None
    window_end_utc: str | None = None
    current_price: float | None = None
    change_pct_24h: float | None = None
    suggested_outcome: OutcomeLabel = "inconclusive"
    reason: str = ""


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
    threshold_pct: float = 2.0,
    horizon_hours: int = 24,
    max_point_gap_seconds: int = 3 * 3600,
    timeout_seconds: int = 10,
) -> list[PriceCheckResult]:
    """Check price moves for a list of alert audit records.

    For each record with affected_assets and a directional sentiment_label
    (bullish/bearish), computes the move between dispatch time and
    (dispatch + horizon) using CoinGecko historical range data.
    Falls back to current ticker 24h move only when historical data is unavailable.

    Args:
        records: Alert audit records to check.
        threshold_pct: Minimum absolute price change (%) to classify as hit/miss.
        horizon_hours: Evaluation horizon in hours (default: 24).
        max_point_gap_seconds: Max allowed timestamp gap to nearest sampled price.
        timeout_seconds: CoinGecko request timeout.

    Returns:
        List of PriceCheckResult, one per (record, asset) pair.
    """
    from app.core.settings import get_settings

    adapter = CoinGeckoAdapter(
        timeout_seconds=timeout_seconds,
        api_key=get_settings().coingecko_api_key or None,
    )
    results: list[PriceCheckResult] = []
    now = datetime.now(UTC)

    # Cache by (asset, dispatched_at_iso, horizon_hours)
    historical_cache: dict[tuple[str, str, int], tuple[float, float, float] | None] = {}
    ticker_cache: dict[str, tuple[float | None, float | None]] = {}

    # Evaluate each record
    for rec in records:
        sentiment = (rec.sentiment_label or "").lower()
        if sentiment not in {"bullish", "bearish"}:
            continue
        try:
            dispatched_at = datetime.fromisoformat(rec.dispatched_at.replace("Z", "+00:00"))
        except ValueError:
            dispatched_at = None

        for raw_asset in rec.affected_assets:
            asset = raw_asset.strip().upper()
            symbol = f"{asset}/USDT" if "/" not in asset else asset

            if dispatched_at is None:
                results.append(
                    PriceCheckResult(
                        document_id=rec.document_id,
                        asset=asset,
                        sentiment_label=sentiment,
                        price_at_alert=None,
                        price_at_horizon=None,
                        observed_move_pct=None,
                        evaluation_mode="historical_window",
                        window_start_utc=None,
                        window_end_utc=None,
                        current_price=None,
                        change_pct_24h=None,
                        suggested_outcome="inconclusive",
                        reason="invalid dispatched_at timestamp",
                    )
                )
                continue

            horizon_ts = dispatched_at + timedelta(hours=horizon_hours)
            if horizon_ts > now:
                remaining_h = (horizon_ts - now).total_seconds() / 3600.0
                results.append(
                    PriceCheckResult(
                        document_id=rec.document_id,
                        asset=asset,
                        sentiment_label=sentiment,
                        price_at_alert=None,
                        price_at_horizon=None,
                        observed_move_pct=None,
                        evaluation_mode="historical_window",
                        window_start_utc=dispatched_at.isoformat(),
                        window_end_utc=horizon_ts.isoformat(),
                        current_price=None,
                        change_pct_24h=None,
                        suggested_outcome="inconclusive",
                        reason=f"horizon not elapsed ({remaining_h:.1f}h remaining)",
                    )
                )
                continue

            bucket_start = dispatched_at.replace(minute=0, second=0, microsecond=0)
            bucket_end = bucket_start + timedelta(hours=horizon_hours)
            hist_key = (asset, bucket_start.isoformat(), horizon_hours)
            if hist_key not in historical_cache:
                try:
                    historical_cache[hist_key] = await adapter.get_price_change_between(
                        symbol,
                        start_utc=bucket_start,
                        end_utc=bucket_end,
                        max_point_gap_seconds=max_point_gap_seconds,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Historical price fetch failed for %s (%s -> %s): %s",
                        asset,
                        bucket_start.isoformat(),
                        bucket_end.isoformat(),
                        exc,
                    )
                    historical_cache[hist_key] = None

            hist = historical_cache[hist_key]
            if hist is not None:
                price_at_alert, price_at_horizon, move_pct = hist
                outcome, reason = _suggest_outcome(sentiment, move_pct, threshold_pct)
                reason = (
                    f"{reason}; historical {horizon_hours}h window "
                    f"({price_at_alert:.2f} -> {price_at_horizon:.2f}, "
                    "hour-bucketed)"
                )
                results.append(
                    PriceCheckResult(
                        document_id=rec.document_id,
                        asset=asset,
                        sentiment_label=sentiment,
                        price_at_alert=price_at_alert,
                        price_at_horizon=price_at_horizon,
                        observed_move_pct=move_pct,
                        evaluation_mode="historical_window",
                        window_start_utc=bucket_start.isoformat(),
                        window_end_utc=bucket_end.isoformat(),
                        current_price=price_at_horizon,
                        change_pct_24h=move_pct,
                        suggested_outcome=outcome,
                        reason=reason,
                    )
                )
                continue

            # Fail-closed fallback: current ticker move when historical window is unavailable
            if asset not in ticker_cache:
                try:
                    ticker = await adapter.get_ticker(symbol)
                    if ticker is not None:
                        ticker_cache[asset] = (ticker.last, ticker.change_pct_24h)
                    else:
                        ticker_cache[asset] = (None, None)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Price fetch failed for %s: %s", asset, exc)
                    ticker_cache[asset] = (None, None)

            price, change_pct = ticker_cache.get(asset, (None, None))
            if price is None or change_pct is None:
                results.append(
                    PriceCheckResult(
                        document_id=rec.document_id,
                        asset=asset,
                        sentiment_label=sentiment,
                        price_at_alert=None,
                        price_at_horizon=None,
                        observed_move_pct=None,
                        evaluation_mode="ticker_24h_fallback",
                        window_start_utc=dispatched_at.isoformat(),
                        window_end_utc=horizon_ts.isoformat(),
                        current_price=None,
                        change_pct_24h=None,
                        suggested_outcome="inconclusive",
                        reason=f"price unavailable for {asset}",
                    )
                )
                continue

            outcome, reason = _suggest_outcome(sentiment, change_pct, threshold_pct)
            reason = f"{reason}; fallback=ticker_24h"
            results.append(
                PriceCheckResult(
                    document_id=rec.document_id,
                    asset=asset,
                    sentiment_label=sentiment,
                    price_at_alert=None,
                    price_at_horizon=price,
                    observed_move_pct=change_pct,
                    evaluation_mode="ticker_24h_fallback",
                    window_start_utc=dispatched_at.isoformat(),
                    window_end_utc=horizon_ts.isoformat(),
                    current_price=price,
                    change_pct_24h=change_pct,
                    suggested_outcome=outcome,
                    reason=reason,
                )
            )

    return results
