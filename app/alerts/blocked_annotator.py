"""Auto-annotation for *blocked* directional alerts (D-148 recall proxy).

Mirrors ``auto_annotator.auto_annotate_pending`` but operates on the
blocked-alert stream (``blocked_alerts.jsonl``) and writes would-have-been
outcomes to ``blocked_outcomes.jsonl``.

The intent is a recall-loss proxy:
    false_negative_rate = would_have_hits / (would_have_hits + misses)
    per block_reason.

Implementation notes:
- Same threshold logic (``_scaled_threshold``) and API cadence as the
  dispatched-alert annotator — reuses the helpers from
  ``app.alerts.auto_annotator`` to avoid drift.
- ``blocked_at`` replaces ``dispatched_at`` as the attribution anchor.
- First entry of ``blocked_assets`` picks the primary symbol.
- ``sentiment_label`` drives hit/miss direction identical to the
  dispatched-alert logic (bullish hit = up, bearish hit = down).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from app.alerts.auto_annotator import (
    _API_DELAY_SECONDS,
    _DEFAULT_BACKFILL_BATCH,
    _DEFAULT_MAX_AGE_HOURS,
    _DEFAULT_MIN_AGE_HOURS,
    _DEFAULT_MOVE_THRESHOLD,
    _REEVAL_MIN_AGE_HOURS,
    _STALE_REEVAL_WINDOW_HOURS,
    _scaled_threshold,
)
from app.alerts.blocked_audit import (
    BlockedAlertRecord,
    BlockedOutcomeAnnotation,
    append_blocked_outcome,
    load_blocked_alerts,
    load_blocked_outcomes,
)
from app.market_data.coingecko_adapter import CoinGeckoAdapter

log = structlog.get_logger(__name__)


def _parse_blocked_time(record: BlockedAlertRecord) -> datetime | None:
    try:
        return datetime.fromisoformat(record.blocked_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _primary_blocked_symbol(record: BlockedAlertRecord) -> str | None:
    if not record.blocked_assets:
        return None
    raw = record.blocked_assets[0].upper()
    if "/" in raw:
        return raw
    return f"{raw}/USDT"


async def auto_annotate_blocked(
    audit_dir: Path,
    *,
    min_age_hours: float = _DEFAULT_MIN_AGE_HOURS,
    max_age_hours: float = _DEFAULT_MAX_AGE_HOURS,
    move_threshold: float = _DEFAULT_MOVE_THRESHOLD,
    reeval_inconclusive: bool = True,
    backfill_batch: int = _DEFAULT_BACKFILL_BATCH,
    dry_run: bool = False,
) -> list[BlockedOutcomeAnnotation]:
    """Resolve would-have-been outcomes for blocked directional alerts.

    Candidate selection, window scaling and CoinGecko cadence mirror
    ``auto_annotate_pending``. Directional-sentiment requirement: only
    records with a usable ``sentiment_label`` and non-empty
    ``blocked_assets`` are processed (bearish/bullish direction needed).
    """
    blocked = load_blocked_alerts(audit_dir)
    existing = load_blocked_outcomes(audit_dir)

    latest_by_doc: dict[str, str] = {}
    for a in existing:
        latest_by_doc[a.document_id] = a.outcome

    now = datetime.now(UTC)
    min_cutoff = now - timedelta(hours=min_age_hours)
    max_cutoff = now - timedelta(hours=max_age_hours)
    reeval_cutoff = now - timedelta(hours=_REEVAL_MIN_AGE_HOURS)

    pending: list[tuple[BlockedAlertRecord, datetime, bool]] = []
    seen_doc_ids: set[str] = set()
    stale_count = 0
    for rec in blocked:
        sentiment = (rec.sentiment_label or "").lower()
        if sentiment not in ("bullish", "bearish"):
            continue
        if not rec.blocked_assets:
            continue
        dt = _parse_blocked_time(rec)
        if dt is None or dt > min_cutoff:
            continue
        if rec.document_id in seen_doc_ids:
            continue

        current_outcome = latest_by_doc.get(rec.document_id)
        is_stale = dt < max_cutoff

        if current_outcome is None:
            if is_stale:
                continue
        elif current_outcome == "inconclusive" and reeval_inconclusive:
            if dt > reeval_cutoff:
                continue
            if is_stale and stale_count >= backfill_batch:
                continue
        else:
            continue

        seen_doc_ids.add(rec.document_id)
        if is_stale:
            stale_count += 1
        pending.append((rec, dt, is_stale))

    if not pending:
        log.info("auto_annotate_blocked.nothing_pending")
        return []

    fresh_count = sum(1 for _, _, s in pending if not s)
    log.info(
        "auto_annotate_blocked.start",
        pending_count=len(pending),
        fresh=fresh_count,
        stale_backfill=stale_count,
    )

    from app.core.settings import get_settings

    adapter = CoinGeckoAdapter(
        timeout_seconds=15,
        api_key=get_settings().coingecko_api_key or None,
    )

    volatility_24h: float | None = None
    try:
        btc_ticker = await adapter.get_ticker("BTC/USDT")
        if btc_ticker is not None:
            volatility_24h = btc_ticker.change_pct_24h
            log.info(
                "auto_annotate_blocked.volatility",
                btc_24h_change=f"{volatility_24h:+.2f}%",
            )
    except Exception:  # noqa: BLE001
        log.warning("auto_annotate_blocked.volatility_fetch_failed")

    results: list[BlockedOutcomeAnnotation] = []

    for rec, blocked_time, is_stale_reeval in pending:
        symbol = _primary_blocked_symbol(rec)
        if symbol is None:
            continue

        if is_stale_reeval:
            eval_end = blocked_time + timedelta(hours=_STALE_REEVAL_WINDOW_HOURS)
            if eval_end > now:
                eval_end = now
        else:
            eval_end = now

        price_data = await adapter.get_price_change_between(
            symbol,
            start_utc=blocked_time,
            end_utc=eval_end,
        )

        if price_data is None:
            log.warning(
                "auto_annotate_blocked.price_unavailable",
                document_id=rec.document_id,
                symbol=symbol,
                stale=is_stale_reeval,
            )
            await asyncio.sleep(_API_DELAY_SECONDS)
            continue

        start_price, end_price, pct_change = price_data
        elapsed_h = (eval_end - blocked_time).total_seconds() / 3600

        threshold = _scaled_threshold(elapsed_h, move_threshold, volatility_24h)

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
            f"{tag}[blocked:{rec.block_reason}]: {sentiment} {symbol} "
            f"${start_price:,.2f}->${end_price:,.2f} "
            f"({pct_change:+.2f}% over {elapsed_h:.1f}h, "
            f"thr={threshold:.2f}%)"
        )

        annotation = BlockedOutcomeAnnotation(
            document_id=rec.document_id,
            outcome=outcome,  # type: ignore[arg-type]
            asset=symbol,
            note=note,
            block_reason=rec.block_reason,
            sentiment_label=rec.sentiment_label,
            directional_confidence=rec.directional_confidence,
            source_name=rec.source_name,
        )

        log.info(
            "auto_annotate_blocked.result",
            document_id=rec.document_id,
            outcome=outcome,
            block_reason=rec.block_reason,
            symbol=symbol,
            pct_change=f"{pct_change:+.2f}%",
            threshold=f"{threshold:.2f}%",
            reeval=is_reeval,
        )

        if not dry_run:
            append_blocked_outcome(annotation, audit_dir)

        results.append(annotation)
        await asyncio.sleep(_API_DELAY_SECONDS)

    log.info(
        "auto_annotate_blocked.done",
        total=len(results),
        hits=sum(1 for a in results if a.outcome == "hit"),
        misses=sum(1 for a in results if a.outcome == "miss"),
        inconclusive=sum(1 for a in results if a.outcome == "inconclusive"),
    )
    return results
