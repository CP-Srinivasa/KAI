"""Alert hit-rate computation.

Resolves dispatched alert predictions against observed market price
movements and computes hit-rate statistics.

Definition
----------
- **Directional alert**: sentiment_label is ``bullish`` or ``bearish``.
- **Hit (bullish)**: asset price at T+horizon > price at T.
- **Hit (bearish)**: asset price at T+horizon < price at T.
- ``neutral`` / ``mixed`` / unknown sentiment are excluded.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AlertOutcome:
    """Resolved outcome of a single directional alert."""

    document_id: str
    asset: str
    sentiment_label: str  # "bullish" | "bearish"
    dispatched_at: str
    price_at_alert: float | None = None
    price_at_resolution: float | None = None
    is_hit: bool | None = None  # None = unresolved
    return_pct: float | None = None
    resolved_at: str | None = None
    channel: str | None = None
    priority: int | None = None


@dataclass(frozen=True)
class HitRateReport:
    """Aggregated hit-rate statistics."""

    total_alerts: int
    directional_alerts: int
    resolved_count: int
    unresolved_count: int
    hit_count: int
    miss_count: int
    hit_rate_pct: float | None  # None if resolved_count == 0
    sufficient_sample: bool  # True if resolved_count >= min_sample
    min_sample: int
    by_sentiment: dict[str, SentimentBreakdown] = field(
        default_factory=dict,
    )
    by_asset: dict[str, AssetBreakdown] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "total_alerts": self.total_alerts,
            "directional_alerts": self.directional_alerts,
            "resolved_count": self.resolved_count,
            "unresolved_count": self.unresolved_count,
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "hit_rate_pct": self.hit_rate_pct,
            "sufficient_sample": self.sufficient_sample,
            "min_sample": self.min_sample,
            "by_sentiment": {
                k: v.to_dict() for k, v in self.by_sentiment.items()
            },
            "by_asset": {
                k: v.to_dict() for k, v in self.by_asset.items()
            },
        }


@dataclass(frozen=True)
class SentimentBreakdown:
    """Hit-rate breakdown for a single sentiment label."""

    count: int
    resolved: int
    hits: int
    hit_rate_pct: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "resolved": self.resolved,
            "hits": self.hits,
            "hit_rate_pct": self.hit_rate_pct,
        }


@dataclass(frozen=True)
class AssetBreakdown:
    """Hit-rate breakdown for a single asset."""

    count: int
    resolved: int
    hits: int
    hit_rate_pct: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "resolved": self.resolved,
            "hits": self.hits,
            "hit_rate_pct": self.hit_rate_pct,
        }


_DIRECTIONAL = {"bullish", "bearish"}


def classify_hit(
    sentiment: str,
    price_at_alert: float,
    price_at_resolution: float,
) -> bool:
    """Return True if the price movement matches the sentiment prediction."""
    if sentiment == "bullish":
        return price_at_resolution > price_at_alert
    if sentiment == "bearish":
        return price_at_resolution < price_at_alert
    msg = f"Cannot classify hit for sentiment={sentiment!r}"
    raise ValueError(msg)


def build_outcomes_from_records(
    records: list[object],
    price_lookup: dict[tuple[str, str], tuple[float, float, str]] | None = None,
) -> list[AlertOutcome]:
    """Build AlertOutcome list from AlertAuditRecords.

    Parameters
    ----------
    records:
        List of ``AlertAuditRecord`` instances with enriched prediction fields.
    price_lookup:
        Optional mapping of ``(asset, dispatched_at)`` → ``(price_at_alert,
        price_at_resolution, resolved_at)``.  If ``None`` or key missing,
        outcome is unresolved.

    Returns
    -------
    List of ``AlertOutcome`` — one per (record, asset) pair for directional
    alerts only.
    """
    from app.alerts.audit import AlertAuditRecord

    outcomes: list[AlertOutcome] = []
    lookup = price_lookup or {}

    for rec in records:
        if not isinstance(rec, AlertAuditRecord):
            continue
        sentiment = (rec.sentiment_label or "").lower()
        if sentiment not in _DIRECTIONAL:
            continue
        assets = rec.affected_assets or []
        if not assets:
            # No asset → cannot resolve, but still count
            outcomes.append(
                AlertOutcome(
                    document_id=rec.document_id,
                    asset="unknown",
                    sentiment_label=sentiment,
                    dispatched_at=rec.dispatched_at,
                    channel=rec.channel,
                    priority=rec.priority,
                )
            )
            continue
        for asset in assets:
            key = (asset, rec.dispatched_at)
            prices = lookup.get(key)
            if prices is None:
                outcomes.append(
                    AlertOutcome(
                        document_id=rec.document_id,
                        asset=asset,
                        sentiment_label=sentiment,
                        dispatched_at=rec.dispatched_at,
                        channel=rec.channel,
                        priority=rec.priority,
                    )
                )
            else:
                p_alert, p_res, resolved_at = prices
                is_hit = classify_hit(sentiment, p_alert, p_res)
                ret_pct = (
                    ((p_res - p_alert) / p_alert * 100.0)
                    if p_alert != 0
                    else 0.0
                )
                outcomes.append(
                    AlertOutcome(
                        document_id=rec.document_id,
                        asset=asset,
                        sentiment_label=sentiment,
                        dispatched_at=rec.dispatched_at,
                        price_at_alert=p_alert,
                        price_at_resolution=p_res,
                        is_hit=is_hit,
                        return_pct=round(ret_pct, 4),
                        resolved_at=resolved_at,
                        channel=rec.channel,
                        priority=rec.priority,
                    )
                )
    return outcomes


def compute_hit_rate(
    outcomes: list[AlertOutcome],
    *,
    min_sample: int = 50,
) -> HitRateReport:
    """Compute aggregated hit-rate statistics from resolved outcomes.

    A sample is considered sufficient only when at least ``min_sample`` alerts
    have resolved outcomes (``is_hit`` is not None).
    """
    resolved = [o for o in outcomes if o.is_hit is not None]
    hits = [o for o in resolved if o.is_hit]
    misses = [o for o in resolved if not o.is_hit]

    hit_rate = (
        round(len(hits) / len(resolved) * 100.0, 2)
        if resolved
        else None
    )

    # Per-sentiment breakdown
    by_sentiment: dict[str, SentimentBreakdown] = {}
    for label in sorted({o.sentiment_label for o in outcomes}):
        subset = [o for o in outcomes if o.sentiment_label == label]
        res = [o for o in subset if o.is_hit is not None]
        h = [o for o in res if o.is_hit]
        by_sentiment[label] = SentimentBreakdown(
            count=len(subset),
            resolved=len(res),
            hits=len(h),
            hit_rate_pct=(
                round(len(h) / len(res) * 100.0, 2) if res else None
            ),
        )

    # Per-asset breakdown
    by_asset: dict[str, AssetBreakdown] = {}
    for asset in sorted({o.asset for o in outcomes}):
        subset = [o for o in outcomes if o.asset == asset]
        res = [o for o in subset if o.is_hit is not None]
        h = [o for o in res if o.is_hit]
        by_asset[asset] = AssetBreakdown(
            count=len(subset),
            resolved=len(res),
            hits=len(h),
            hit_rate_pct=(
                round(len(h) / len(res) * 100.0, 2) if res else None
            ),
        )

    return HitRateReport(
        total_alerts=len(outcomes),
        directional_alerts=len(outcomes),
        resolved_count=len(resolved),
        unresolved_count=len(outcomes) - len(resolved),
        hit_count=len(hits),
        miss_count=len(misses),
        hit_rate_pct=hit_rate,
        sufficient_sample=len(resolved) >= min_sample,
        min_sample=min_sample,
        by_sentiment=by_sentiment,
        by_asset=by_asset,
    )
