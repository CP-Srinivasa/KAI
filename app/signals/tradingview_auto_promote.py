"""WP-C (2026-06-15): gated auto-promotion of accepted TV webhook events.

TV-3.1 makes promotion an explicit operator step (``app/signals/
tradingview_promotion.py``). WP-C automates it behind a default-OFF flag
(``TRADINGVIEW_WEBHOOK_AUTO_PROMOTE``): each open pending event is normalised to
a tradeable pair, judged by the WP-B ``signal_path="technical"`` eligibility
gate, and — only if eligible — promoted to a ``SignalCandidate`` and recorded in
the decision log (idempotent: a decided event is never re-promoted).

Defense in depth: auto-promotion produces an APPROVED candidate, but the
EXECUTION path stays independently gated by entry_mode — auto-promote alone
cannot produce a real fill. Bearish (sell) auto-promotes only when
``allow_short`` is set (WP-E); otherwise it is recorded as a rejected decision.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.alerts.eligibility import (
    SIGNAL_PATH_TECHNICAL,
    evaluate_directional_eligibility,
)
from app.alerts.tv_bridge import _split_ticker
from app.core.logging import get_logger
from app.signals.tradingview_promotion import (
    DecisionRecord,
    PromotionError,
    PromotionInputs,
    append_decision,
    append_promoted_candidate,
    filter_open_events,
    load_decisions,
    load_pending_events,
    promote_event,
)

logger = get_logger(__name__)

_ACTION_TO_SENTIMENT = {"buy": "bullish", "sell": "bearish"}
_AUTO_INPUTS = PromotionInputs(
    thesis="auto_promote:tv_technical",
    risk_assessment="auto_promote_unreviewed",
    position_size_rationale="auto_default",
    venue="paper",
    mode="paper",
)


def _to_pair(ticker: str) -> str | None:
    """Normalise a TV ticker (``BTCUSDT``) to a resolvable pair (``BTC/USDT``)."""
    split = _split_ticker(ticker)
    if split is None:
        return None
    base, quote = split
    return f"{base}/{quote or 'USDT'}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def auto_promote_pending(
    *,
    pending_path: Path,
    decisions_path: Path,
    promoted_path: Path,
    allow_short: bool = False,
    write: bool = True,
    now_iso: str | None = None,
) -> dict[str, object]:
    """Promote eligible open TV events to candidates. Idempotent, fail-soft.

    Records a decision (promoted | rejected) for every event it acts on so it is
    never re-evaluated. Returns a summary dict.
    """
    ts = now_iso or _now()
    events = load_pending_events(pending_path)
    decisions = load_decisions(decisions_path)
    open_events = filter_open_events(events, decisions)

    promoted = 0
    rejected = 0
    for ev in open_events:
        sentiment = _ACTION_TO_SENTIMENT.get(ev.action)
        pair = _to_pair(ev.ticker)
        if sentiment is None or pair is None or ev.price is None:
            rejected += _record_reject(decisions_path, ev.event_id, "unsupported_event", ts, write)
            continue

        decision = evaluate_directional_eligibility(
            sentiment_label=sentiment,
            affected_assets=[pair],
            signal_path=SIGNAL_PATH_TECHNICAL,
            allow_short=allow_short,
        )
        if decision.directional_eligible is not True:
            reason = decision.directional_block_reason or "ineligible"
            rejected += _record_reject(decisions_path, ev.event_id, reason, ts, write)
            continue

        try:
            candidate = promote_event(ev, _AUTO_INPUTS, now_iso=ts)
        except PromotionError as exc:
            rejected += _record_reject(
                decisions_path, ev.event_id, f"promotion_error:{str(exc)[:80]}", ts, write
            )
            continue

        if write:
            append_promoted_candidate(promoted_path, candidate)
            append_decision(
                decisions_path,
                DecisionRecord(
                    event_id=ev.event_id,
                    decision="promoted",
                    timestamp_utc=ts,
                    operator_reason="auto_promote",
                    promoted_decision_id=candidate.decision_id,
                ),
            )
        promoted += 1

    summary: dict[str, object] = {
        "enabled": True,
        "open_events": len(open_events),
        "promoted": promoted,
        "rejected": rejected,
    }
    logger.info("tv_auto_promote.run", **summary)
    return summary


def _record_reject(decisions_path: Path, event_id: str, reason: str, ts: str, write: bool) -> int:
    if write:
        append_decision(
            decisions_path,
            DecisionRecord(
                event_id=event_id,
                decision="rejected",
                timestamp_utc=ts,
                operator_reason=f"auto_promote:{reason}",
                promoted_decision_id=None,
            ),
        )
    return 1


def run_from_settings(now_iso: str | None = None) -> dict[str, object]:
    """Gated entrypoint for CLI / timer. No-op summary when the flag is OFF."""
    from app.core.settings import get_settings

    settings = get_settings()
    tv = settings.tradingview
    if not tv.webhook_auto_promote_enabled:
        return {"enabled": False, "reason": "TRADINGVIEW_WEBHOOK_AUTO_PROMOTE is false"}

    return auto_promote_pending(
        pending_path=Path(tv.webhook_pending_signals_log),
        decisions_path=Path(tv.pending_decisions_log),
        promoted_path=Path(tv.promoted_signals_log),
        allow_short=settings.alerts.allow_short_technical,
        now_iso=now_iso,
    )
