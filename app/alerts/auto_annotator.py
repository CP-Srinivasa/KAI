"""Auto-Annotation Agent for directional alerts.

Compares the price at alert dispatch time with the price after a
configurable evaluation window.  Writes hit / miss / inconclusive
annotations to the outcomes JSONL file so the hold-metrics report
can compute precision automatically.

Usage (programmatic):
    results = await auto_annotate_pending(audit_dir)

Usage (CLI):
    python -m app.cli.main alerts auto-annotate
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from app.alerts.audit import (
    AlertAuditRecord,
    AlertOutcomeAnnotation,
    append_outcome_annotation,
    load_alert_audits,
    load_outcome_annotations,
)
from app.market_data.coingecko_adapter import CoinGeckoAdapter

log = structlog.get_logger(__name__)

# Minimum age before we evaluate an alert (hours).
_DEFAULT_MIN_AGE_HOURS = 6.0

# Price-move threshold in percent (adapter returns pct, e.g. 2.0 = 2%).
_DEFAULT_MOVE_THRESHOLD = 1.0  # 1 %

# Delay between CoinGecko API calls to respect rate limits.
_API_DELAY_SECONDS = 12


def _parse_dispatch_time(record: AlertAuditRecord) -> datetime | None:
    """Parse dispatched_at to a timezone-aware datetime, or None."""
    try:
        return datetime.fromisoformat(
            record.dispatched_at.replace("Z", "+00:00"),
        )
    except (ValueError, AttributeError):
        return None


def _primary_symbol(record: AlertAuditRecord) -> str | None:
    """Return the first affected asset as a tradeable symbol (e.g. BTC/USDT).

    The audit record stores assets like ``["BTC/USDT"]`` (post-resolution)
    or ``["BTC"]`` (pre-resolution).  We normalise both to ``X/USDT``.
    """
    if not record.affected_assets:
        return None
    raw = record.affected_assets[0].upper()
    if "/" in raw:
        return raw
    return f"{raw}/USDT"


async def auto_annotate_pending(
    audit_dir: Path,
    *,
    min_age_hours: float = _DEFAULT_MIN_AGE_HOURS,
    move_threshold: float = _DEFAULT_MOVE_THRESHOLD,
    dry_run: bool = False,
) -> list[AlertOutcomeAnnotation]:
    """Annotate all eligible directional alerts that are old enough.

    Returns the list of newly created annotations.
    """
    import asyncio

    audits = load_alert_audits(audit_dir)
    existing = load_outcome_annotations(audit_dir)

    # Build set of (document_id, asset) pairs already annotated.
    annotated_keys: set[tuple[str, str | None]] = {
        (a.document_id, a.asset) for a in existing
    }

    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=min_age_hours)

    # Filter to actionable candidates.
    pending: list[tuple[AlertAuditRecord, datetime]] = []
    for rec in audits:
        if rec.directional_eligible is not True:
            continue
        dt = _parse_dispatch_time(rec)
        if dt is None or dt > cutoff:
            continue
        symbol = _primary_symbol(rec)
        if (rec.document_id, symbol) in annotated_keys:
            continue
        # Deduplicate by document_id (multiple channels produce
        # multiple audit rows for the same document).
        if any(r.document_id == rec.document_id for r, _ in pending):
            continue
        pending.append((rec, dt))

    if not pending:
        log.info("auto_annotate.nothing_pending")
        return []

    log.info("auto_annotate.start", pending_count=len(pending))

    adapter = CoinGeckoAdapter(timeout_seconds=15)
    results: list[AlertOutcomeAnnotation] = []

    for rec, dispatch_time in pending:
        symbol = _primary_symbol(rec)
        if symbol is None:
            continue

        # Evaluate price change from dispatch to now.
        price_data = await adapter.get_price_change_between(
            symbol,
            start_utc=dispatch_time,
            end_utc=now,
        )

        if price_data is None:
            log.warning(
                "auto_annotate.price_unavailable",
                document_id=rec.document_id,
                symbol=symbol,
            )
            # Respect rate limit even on failure.
            await asyncio.sleep(_API_DELAY_SECONDS)
            continue

        start_price, end_price, pct_change = price_data
        elapsed_h = (now - dispatch_time).total_seconds() / 3600

        # Determine outcome.
        sentiment = (rec.sentiment_label or "").lower()
        if sentiment == "bullish" and pct_change >= move_threshold:
            outcome: str = "hit"
        elif sentiment == "bearish" and pct_change <= -move_threshold:
            outcome = "hit"
        elif sentiment == "bullish" and pct_change <= -move_threshold:
            outcome = "miss"
        elif sentiment == "bearish" and pct_change >= move_threshold:
            outcome = "miss"
        else:
            outcome = "inconclusive"

        note = (
            f"auto: {sentiment} {symbol} "
            f"${start_price:,.2f}->${end_price:,.2f} "
            f"({pct_change:+.2f}% over {elapsed_h:.1f}h)"
        )

        annotation = AlertOutcomeAnnotation(
            document_id=rec.document_id,
            outcome=outcome,  # type: ignore[arg-type]
            asset=symbol,
            note=note,
        )

        log.info(
            "auto_annotate.result",
            document_id=rec.document_id,
            outcome=outcome,
            symbol=symbol,
            pct_change=f"{pct_change:+.2f}%",
        )

        if not dry_run:
            append_outcome_annotation(annotation, audit_dir)

        results.append(annotation)

        # Rate-limit between calls.
        await asyncio.sleep(_API_DELAY_SECONDS)

    log.info(
        "auto_annotate.done",
        total=len(results),
        hits=sum(1 for a in results if a.outcome == "hit"),
        misses=sum(1 for a in results if a.outcome == "miss"),
        inconclusive=sum(1 for a in results if a.outcome == "inconclusive"),
    )
    return results
