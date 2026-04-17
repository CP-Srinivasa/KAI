"""Auto-Annotation Agent for directional alerts.

Compares the price at alert dispatch time with the price after a
configurable evaluation window.  Writes hit / miss / inconclusive
annotations to the outcomes JSONL file so the hold-metrics report
can compute precision automatically.

Tuning (D-132):
- Volatility-adaptive thresholds scale with 24h market volatility
- Re-evaluates prior inconclusive annotations after 24h
- API delay reduced to 5s (CoinGecko free tier ~10/min)
- Window: min 4h, max 72h for fresh alerts

D-138: Stale inconclusive re-evaluation
- Inconclusives older than 72h are re-evaluated with a fixed 7-day
  attribution window (dispatch_time → dispatch_time + 7d).
- No max_age limit for inconclusive re-evaluation.
- Batch-size limit prevents CoinGecko rate exhaustion in cron.

Usage (programmatic):
    results = await auto_annotate_pending(audit_dir)

Usage (CLI):
    python -m app.cli.main alerts auto-annotate
    python -m app.cli.main alerts auto-annotate --backfill-batch 200
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
from app.alerts.eligibility import evaluate_directional_eligibility
from app.market_data.coingecko_adapter import CoinGeckoAdapter

log = structlog.get_logger(__name__)

# Minimum age before we evaluate an alert (hours).
_DEFAULT_MIN_AGE_HOURS = 4.0

# Maximum age — alerts older than this are too stale for reliable evaluation.
_DEFAULT_MAX_AGE_HOURS = 72.0

# Price-move threshold in percent (base, before volatility scaling).
# Scales with evaluation window and market volatility.
_DEFAULT_MOVE_THRESHOLD = 1.0

# Delay between CoinGecko API calls to respect rate limits.
# CoinGecko free tier: ~10-30 req/min. 5s = 12/min (safe margin).
_API_DELAY_SECONDS = 5

# Re-evaluate inconclusive annotations older than this many hours.
_REEVAL_MIN_AGE_HOURS = 24.0

# Fixed attribution window for stale inconclusive re-evaluation.
# Alerts older than max_age use dispatch_time + this window instead
# of dispatch_time → now.  7 days is the longest reasonable window
# for attributing a price move to a specific news event.
_STALE_REEVAL_WINDOW_HOURS = 168.0  # 7 days

# Default batch size for stale inconclusive backfill.
# Limits API calls per run to avoid rate exhaustion in cron.
_DEFAULT_BACKFILL_BATCH = 30


def _scaled_threshold(
    elapsed_hours: float,
    base_threshold: float,
    volatility_24h: float | None = None,
) -> float:
    """Return a move threshold that scales with window and volatility.

    Base scaling by window size:
      <=8h  -> base * 0.7  (short window, small moves matter)
      <=12h -> base * 1.0
      <=24h -> base * 1.5
      <=48h -> base * 2.0
      >48h  -> base * 2.5

    Volatility adjustment: if 24h vol is available, scale the
    threshold down in low-vol markets and up in high-vol markets.
    This prevents too many inconclusives during calm markets.
    """
    # Window scaling
    if elapsed_hours <= 8.0:
        window_factor = 0.7
    elif elapsed_hours <= 12.0:
        window_factor = 1.0
    elif elapsed_hours <= 24.0:
        window_factor = 1.5
    elif elapsed_hours <= 48.0:
        window_factor = 2.0
    else:
        window_factor = 2.5

    threshold = base_threshold * window_factor

    # Volatility scaling: use abs(24h change) as proxy for volatility.
    # Low vol (<1%): scale down to 60% of threshold.
    # Normal vol (1-3%): keep threshold.
    # High vol (>3%): scale up to 150% of threshold.
    if volatility_24h is not None:
        abs_vol = abs(volatility_24h)
        if abs_vol < 1.0:
            vol_factor = 0.6
        elif abs_vol < 3.0:
            vol_factor = 0.6 + (abs_vol - 1.0) * 0.2  # 0.6..1.0
        else:
            vol_factor = min(1.0 + (abs_vol - 3.0) * 0.1, 1.5)
        threshold *= vol_factor

    return max(threshold, 0.3)  # floor: never below 0.3%


def _parse_dispatch_time(record: AlertAuditRecord) -> datetime | None:
    """Parse dispatched_at to a timezone-aware datetime, or None."""
    try:
        return datetime.fromisoformat(
            record.dispatched_at.replace("Z", "+00:00"),
        )
    except (ValueError, AttributeError):
        return None


def _primary_symbol(record: AlertAuditRecord) -> str | None:
    """Return the first affected asset as a tradeable symbol."""
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
    max_age_hours: float = _DEFAULT_MAX_AGE_HOURS,
    move_threshold: float = _DEFAULT_MOVE_THRESHOLD,
    reeval_inconclusive: bool = True,
    backfill_batch: int = _DEFAULT_BACKFILL_BATCH,
    dry_run: bool = False,
) -> list[AlertOutcomeAnnotation]:
    """Annotate all eligible directional alerts that are old enough.

    When ``reeval_inconclusive`` is True, alerts that were previously
    annotated as ``inconclusive`` get re-evaluated:
    - Within the normal window (4h–72h): compared to current price.
    - Beyond the normal window (>72h): compared to dispatch + 7d price
      using a fixed attribution window. No max_age limit — even very
      old inconclusives are re-evaluated (D-138).

    ``backfill_batch`` limits how many stale (>72h) inconclusives are
    processed per run to avoid CoinGecko rate exhaustion.

    Returns the list of newly created annotations.
    """
    import asyncio

    audits = load_alert_audits(audit_dir)
    existing = load_outcome_annotations(audit_dir)

    # Latest annotation per document_id (last entry wins).
    latest_by_doc: dict[str, str] = {}
    for a in existing:
        latest_by_doc[a.document_id] = a.outcome

    now = datetime.now(UTC)
    min_cutoff = now - timedelta(hours=min_age_hours)
    max_cutoff = now - timedelta(hours=max_age_hours)
    reeval_cutoff = now - timedelta(hours=_REEVAL_MIN_AGE_HOURS)

    # Filter to actionable candidates.
    # Two pools: fresh (within normal window) and stale (beyond, inconclusives only).
    pending: list[tuple[AlertAuditRecord, datetime, bool]] = []  # (rec, dt, is_stale)
    seen_doc_ids: set[str] = set()
    stale_count = 0
    for rec in audits:
        if rec.directional_eligible is False:
            continue
        if rec.directional_eligible is None:
            # Legacy record without eligibility field — recompute.
            legacy = evaluate_directional_eligibility(
                sentiment_label=rec.sentiment_label,
                affected_assets=list(rec.affected_assets or []),
            )
            if legacy.directional_eligible is not True:
                continue
        dt = _parse_dispatch_time(rec)
        if dt is None or dt > min_cutoff:
            continue
        if rec.document_id in seen_doc_ids:
            continue

        current_outcome = latest_by_doc.get(rec.document_id)
        is_stale = dt < max_cutoff

        if current_outcome is None:
            # Never annotated — only within normal window.
            if is_stale:
                continue
        elif current_outcome == "inconclusive" and reeval_inconclusive:
            # Re-evaluate if old enough (24h+ since dispatch).
            if dt > reeval_cutoff:
                continue
            # D-138: stale inconclusives use fixed 7d window, batch-limited.
            if is_stale and stale_count >= backfill_batch:
                continue
        else:
            # Already annotated with hit/miss — skip.
            continue

        seen_doc_ids.add(rec.document_id)
        if is_stale:
            stale_count += 1
        pending.append((rec, dt, is_stale))

    if not pending:
        log.info("auto_annotate.nothing_pending")
        return []

    fresh_count = sum(1 for _, _, s in pending if not s)
    log.info(
        "auto_annotate.start",
        pending_count=len(pending),
        fresh=fresh_count,
        stale_backfill=stale_count,
    )

    from app.core.settings import get_settings

    adapter = CoinGeckoAdapter(
        timeout_seconds=15,
        api_key=get_settings().coingecko_api_key or None,
    )

    # Fetch current volatility for threshold scaling.
    volatility_24h: float | None = None
    try:
        btc_ticker = await adapter.get_ticker("BTC/USDT")
        if btc_ticker is not None:
            volatility_24h = btc_ticker.change_pct_24h
            log.info(
                "auto_annotate.volatility",
                btc_24h_change=f"{volatility_24h:+.2f}%",
            )
    except Exception:  # noqa: BLE001
        log.warning("auto_annotate.volatility_fetch_failed")

    results: list[AlertOutcomeAnnotation] = []

    for rec, dispatch_time, is_stale_reeval in pending:
        symbol = _primary_symbol(rec)
        if symbol is None:
            continue

        # D-138: Stale inconclusives use a fixed 7d attribution window
        # instead of dispatch → now (which would be weeks/months and
        # destroy any causal attribution to the news event).
        if is_stale_reeval:
            eval_end = dispatch_time + timedelta(hours=_STALE_REEVAL_WINDOW_HOURS)
            if eval_end > now:
                eval_end = now
        else:
            eval_end = now

        price_data = await adapter.get_price_change_between(
            symbol,
            start_utc=dispatch_time,
            end_utc=eval_end,
        )

        if price_data is None:
            log.warning(
                "auto_annotate.price_unavailable",
                document_id=rec.document_id,
                symbol=symbol,
                stale=is_stale_reeval,
            )
            await asyncio.sleep(_API_DELAY_SECONDS)
            continue

        start_price, end_price, pct_change = price_data
        elapsed_h = (eval_end - dispatch_time).total_seconds() / 3600

        threshold = _scaled_threshold(
            elapsed_h, move_threshold, volatility_24h,
        )

        sentiment = (rec.sentiment_label or "").lower()
        if sentiment == "bullish" and pct_change >= threshold:
            outcome: str = "hit"
        elif sentiment == "bearish" and pct_change <= -threshold:
            outcome = "hit"
        elif sentiment == "bullish" and pct_change <= -threshold:
            outcome = "miss"
        elif sentiment == "bearish" and pct_change >= threshold:
            outcome = "miss"
        else:
            outcome = "inconclusive"

        is_reeval = rec.document_id in latest_by_doc
        tag = "backfill" if is_stale_reeval else ("reeval" if is_reeval else "auto")
        note = (
            f"{tag}: {sentiment} {symbol} "
            f"${start_price:,.2f}->${end_price:,.2f} "
            f"({pct_change:+.2f}% over {elapsed_h:.1f}h, "
            f"thr={threshold:.2f}%)"
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
            threshold=f"{threshold:.2f}%",
            reeval=is_reeval,
        )

        if not dry_run:
            append_outcome_annotation(annotation, audit_dir)

        results.append(annotation)
        await asyncio.sleep(_API_DELAY_SECONDS)

    log.info(
        "auto_annotate.done",
        total=len(results),
        hits=sum(1 for a in results if a.outcome == "hit"),
        misses=sum(1 for a in results if a.outcome == "miss"),
        inconclusive=sum(
            1 for a in results if a.outcome == "inconclusive"
        ),
    )
    return results
