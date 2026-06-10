"""Target-Completion Reconciler — 🎯 all-TP-hit-Meldungen auf offene Positionen mappen.

2026-05-12 Sprint D, per Operator-Auftrag "Premium Telegram Signals End-to-End
Execution Fix" Sektion 3 + 9.

Vertrag
-------
- Eingang: ``TargetCompletionEvent`` aus telegram_channel_parser.
- Ausgang: ``ReconcileOutcome`` mit Status (matched / orphan / no_position / closed).
- Side-Effects (gewollt):
  - Wenn matching offene Position für Symbol existiert → market-Close zum
    touch_price (oder zum aktuellen market-Price wenn channel keine Zahl gibt),
    realized PnL fließt ins audit.
  - Wenn KEINE Position für das Symbol existiert → ``orphan_target_completion``
    Audit-Event in ``artifacts/target_completion_audit.jsonl`` damit Operator
    sehen kann dass das Outcome registriert wurde aber kein Position-Match gab
    (z.B. Signal wurde nicht gefilled oder bereits über SL geschlossen).

Fail-Closed
-----------
- Ohne offene Position kein Force-Close — nur audit-Eintrag.
- Touch-Price-Sanity: muss > 0 sein, sonst fallback auf market-data-Snapshot.
- Audit-write-Fehler werden geloggt aber niemals propagiert — Reconciler darf
  den telegram_channel_worker nie crashen.

Idempotency
-----------
- Pro envelope_id wird max. ein reconcile-Versuch durchgeführt; jeder weitere
  ist no-op. ``_iter_prior_reconciled_ids`` liest die letzten 500 records aus
  dem reconcile-audit. Symmetrisch zu ``telegram_channel_envelope``-Dedup.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.execution.paper_engine import PaperExecutionEngine
from app.execution.paper_engine_singleton import get_paper_engine
from app.execution.scale_resolver import detect_scale_factor
from app.ingestion.telegram_channel_parser import TargetCompletionEvent

logger = logging.getLogger(__name__)

_DEFAULT_RECONCILE_LOG = Path("artifacts/target_completion_audit.jsonl")
_PAPER_AUDIT_LOG = Path("artifacts/paper_execution_audit.jsonl")


@dataclass(frozen=True)
class ReconcileOutcome:
    # closed | orphan_no_match | duplicate | error | requires_scale_review | requires_review
    status: str
    reason: str
    symbol: str
    touch_price: float | None
    realized_pnl_usd: float | None
    audit_record: dict[str, Any] = field(default_factory=dict)


# Terminal statuses block a re-reconcile. ``requires_scale_review`` and
# ``error`` are intentionally retryable: the first attempt did NOT book PnL,
# so a later run (after manual review / scale fix) must be able to proceed.
_TERMINAL_RECONCILE_STATUSES = frozenset({"closed", "orphan_no_match", "duplicate"})


def _iter_prior_reconciled_ids(path: Path, *, lookback: int = 500) -> set[str]:
    if not path.exists():
        return set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("[reconcile] audit read failed: %s", exc)
        return set()
    seen: set[str] = set()
    for raw in lines[-lookback:]:
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        env_id = rec.get("source_envelope_id")
        if rec.get("status") not in _TERMINAL_RECONCILE_STATUSES:
            continue
        if isinstance(env_id, str) and env_id:
            seen.add(env_id)
    return seen


def _append_audit(path: Path, record: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("[reconcile] audit write failed: %s", exc)


def _canonical_display_symbol(internal_or_display: str) -> str:
    """Match parser's normalize: 'TRUTHUSDT' → 'TRUTH/USDT'."""
    s = internal_or_display.strip().upper()
    if "/" in s:
        return s
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[: -len(quote)]}/{quote}"
    return s


def reconcile_target_completion(
    event: TargetCompletionEvent,
    *,
    source_envelope_id: str,
    paper_audit_path: Path | None = None,
    reconcile_log_path: Path | None = None,
    engine: PaperExecutionEngine | None = None,
    now: datetime | None = None,
) -> ReconcileOutcome:
    """Map a 🎯-completion event to an open paper position and close it.

    Returns ``ReconcileOutcome`` regardless of branch — caller must not assume
    success. Engine is optional; when omitted a fresh PaperExecutionEngine is
    constructed and rehydrated from audit (read-only style, no live).
    """
    audit_path = paper_audit_path or _PAPER_AUDIT_LOG
    reconcile_path = reconcile_log_path or _DEFAULT_RECONCILE_LOG
    ts = (now or datetime.now(UTC)).isoformat()
    display_sym = _canonical_display_symbol(event.display_symbol or event.symbol)

    base_record: dict[str, Any] = {
        "timestamp_utc": ts,
        "event": "target_completion_reconcile",
        "source_envelope_id": source_envelope_id,
        "symbol": display_sym,
        "raw_text": event.raw_text,
        "touch_price": event.touch_price,
    }

    # Idempotency: ein Reconcile pro envelope_id reicht.
    prior_ids = _iter_prior_reconciled_ids(reconcile_path)
    if source_envelope_id in prior_ids:
        rec = {**base_record, "status": "duplicate", "reason": "already_reconciled"}
        _append_audit(reconcile_path, rec)
        return ReconcileOutcome(
            status="duplicate",
            reason="already_reconciled",
            symbol=display_sym,
            touch_price=event.touch_price,
            realized_pnl_usd=None,
            audit_record=rec,
        )

    # Engine entweder vom Caller ODER aus dem Prozess-Singleton.
    eng = engine
    if eng is None:
        eng = get_paper_engine()
        eng.rehydrate_from_audit(audit_path)

    pos = eng.portfolio.positions.get(display_sym)
    if pos is None:
        # Orphan-Pfad: kein matching position. Bewusst kein Force-Aktion —
        # Operator-Auftrag Sektion 9 verlangt "im Dashboard sichtbar machen".
        rec = {
            **base_record,
            "status": "orphan_no_match",
            "reason": "no_open_position_for_symbol",
        }
        _append_audit(reconcile_path, rec)
        return ReconcileOutcome(
            status="orphan_no_match",
            reason="no_open_position_for_symbol",
            symbol=display_sym,
            touch_price=event.touch_price,
            realized_pnl_usd=None,
            audit_record=rec,
        )

    position_source = (pos.source or "").strip()
    position_correlation_id = (pos.correlation_id or "").strip()
    match_strategy = (
        "origin_signal_id"
        if position_correlation_id and source_envelope_id == position_correlation_id
        else "symbol_single_open_position"
    )
    if not position_source.lower().startswith("telegram_premium"):
        rec = {
            **base_record,
            "status": "requires_review",
            "reason": "non_premium_position_source",
            "match_strategy": match_strategy,
            "position_source": position_source or "unknown",
            "position_correlation_id": position_correlation_id or None,
        }
        _append_audit(reconcile_path, rec)
        return ReconcileOutcome(
            status="requires_review",
            reason="non_premium_position_source",
            symbol=display_sym,
            touch_price=event.touch_price,
            realized_pnl_usd=None,
            audit_record=rec,
        )

    # Close-Pfad: market-close zum touch_price (oder avg_entry als Notfall-Wert
    # wenn channel keine Zahl liefert).
    #
    # RC-4 (2026-06-04): Der Channel-Touch-Price kommt ROH in Channel-Skala
    # (z.B. CYS 4869, US 16790, APR 26892). Die Position wurde aber USD-skaliert
    # eröffnet (entry ~0.4869). Vorher wurde der rohe Wert direkt als close_price
    # genommen → realized PnL = (4869 - 0.4869)·qty = astronomischer Müll. Wir
    # bringen den Touch-Price über DENSELBEN Scale-Resolver auf die Skala der
    # offenen Position und buchen bei implausibler Skala KEINEN PnL.
    scale_factor_applied = 1.0
    raw_touch = event.touch_price
    if raw_touch is None or raw_touch <= 0:
        close_price = pos.avg_entry_price  # Notfall: PnL=0 statt random scribble
        close_reason_extra = "touch_price_missing_fallback_to_avg_entry"
    else:
        if pos.avg_entry_price and pos.avg_entry_price > 0:
            scale_factor_applied = detect_scale_factor(raw_touch, pos.avg_entry_price)
        close_price = raw_touch / scale_factor_applied if scale_factor_applied > 0 else raw_touch
        # Plausibilitäts-Guard: eine all-TP-hit-Meldung kann nach korrekter
        # Skalierung nicht um Faktor 10 vom Entry abweichen. Liegt sie es doch,
        # ist die Skala unklar → KEIN Close, kein PnL, sichtbarer Review-Status.
        ratio = (
            close_price / pos.avg_entry_price
            if pos.avg_entry_price and pos.avg_entry_price > 0
            else 0.0
        )
        if ratio < 0.1 or ratio > 10.0:
            rec = {
                **base_record,
                "status": "requires_scale_review",
                "reason": "touch_price_scale_implausible",
                "raw_touch_price": raw_touch,
                "scaled_touch_price": close_price,
                "scale_factor_applied": scale_factor_applied,
                "avg_entry_price": pos.avg_entry_price,
                "match_strategy": match_strategy,
                "position_source": position_source,
                "position_correlation_id": position_correlation_id or None,
            }
            _append_audit(reconcile_path, rec)
            logger.warning(
                "[reconcile] scale-review symbol=%s envelope=%s raw_touch=%.6g "
                "avg_entry=%.6g factor=%.0e scaled=%.6g ratio=%.3g — kein PnL gebucht",
                display_sym,
                source_envelope_id,
                raw_touch,
                pos.avg_entry_price,
                scale_factor_applied,
                close_price,
                ratio,
            )
            return ReconcileOutcome(
                status="requires_scale_review",
                reason="touch_price_scale_implausible",
                symbol=display_sym,
                touch_price=raw_touch,
                realized_pnl_usd=None,
                audit_record=rec,
            )
        # 2026-06-10 PnL-truth: a "completed ALL profit targets" event is by
        # definition a profitable close — a long must realise ABOVE its entry,
        # a short BELOW it. If the scaled touch lands on the WRONG side of entry
        # the close would book a loss on a signal the channel reported as a full
        # win — the unmistakable signature of a still-misresolved scale (entry
        # and touch booked on different scales) that the coarse 0.1–10× ratio
        # guard above let through. Refuse to book a wrong-sign PnL: surface a
        # retryable scale-review instead of a phantom loss. A tiny epsilon
        # absorbs rounding/slippage so a legitimately flat touch is not flagged.
        _eps = 1e-3  # 0.1% tolerance for rounding/slippage at the boundary
        wrong_side = (
            pos.position_side == "long" and close_price < pos.avg_entry_price * (1.0 - _eps)
        ) or (pos.position_side == "short" and close_price > pos.avg_entry_price * (1.0 + _eps))
        if pos.avg_entry_price and pos.avg_entry_price > 0 and wrong_side:
            rec = {
                **base_record,
                "status": "requires_scale_review",
                "reason": "touch_price_wrong_side_of_entry",
                "raw_touch_price": raw_touch,
                "scaled_touch_price": close_price,
                "scale_factor_applied": scale_factor_applied,
                "avg_entry_price": pos.avg_entry_price,
                "position_side": pos.position_side,
                "match_strategy": match_strategy,
                "position_source": position_source,
                "position_correlation_id": position_correlation_id or None,
            }
            _append_audit(reconcile_path, rec)
            logger.warning(
                "[reconcile] wrong-side-review symbol=%s envelope=%s side=%s "
                "raw_touch=%.6g avg_entry=%.6g factor=%.0e scaled=%.6g — "
                "all-TP-hit cannot close at a loss, kein PnL gebucht",
                display_sym,
                source_envelope_id,
                pos.position_side,
                raw_touch,
                pos.avg_entry_price,
                scale_factor_applied,
                close_price,
            )
            return ReconcileOutcome(
                status="requires_scale_review",
                reason="touch_price_wrong_side_of_entry",
                symbol=display_sym,
                touch_price=raw_touch,
                realized_pnl_usd=None,
                audit_record=rec,
            )
        close_reason_extra = (
            "touch_price_from_channel_scaled"
            if scale_factor_applied != 1.0
            else "touch_price_from_channel"
        )

    close_side = "sell" if pos.position_side == "long" else "buy"
    realized_before = eng.portfolio.realized_pnl_usd
    try:
        close_order = eng.create_order(
            symbol=display_sym,
            side=close_side,
            quantity=pos.quantity,
            order_type="market",
            idempotency_key=f"reconcile:{source_envelope_id}",
            position_side=pos.position_side,
            source=pos.source,
            leverage=pos.leverage,
        )
        fill = eng.fill_order(close_order, close_price)
        if fill is None:
            raise RuntimeError("close order fill returned None")
    except Exception as exc:  # noqa: BLE001 — reconcile must never propagate
        logger.warning(
            "[reconcile] close failed symbol=%s envelope=%s err=%s",
            display_sym,
            source_envelope_id,
            exc,
        )
        rec = {
            **base_record,
            "status": "error",
            "reason": f"close_exception:{type(exc).__name__}",
        }
        _append_audit(reconcile_path, rec)
        return ReconcileOutcome(
            status="error",
            reason=f"close_exception:{type(exc).__name__}",
            symbol=display_sym,
            touch_price=close_price,
            realized_pnl_usd=None,
            audit_record=rec,
        )

    realized_after = eng.portfolio.realized_pnl_usd
    realized_delta = realized_after - realized_before

    # Keep portfolio-read aggregation in sync with target-completion closes.
    paper_close_record = {
        "event_type": "position_closed",
        "timestamp_utc": ts,
        "symbol": display_sym,
        "reason": f"reconcile:{close_reason_extra}",
        "quantity": pos.quantity,
        "entry_price": pos.avg_entry_price,
        "exit_price": close_price,
        "fill_id": fill.fill_id,
        "order_id": close_order.order_id,
        "realized_pnl_usd": realized_after,
        "trade_pnl_usd": realized_delta,
        "fee_usd": fill.fee_usd,
        "position_side": pos.position_side,
        "signal_source": position_source,
        "document_id": pos.document_id,
    }
    _append_audit(audit_path, paper_close_record)

    rec = {
        **base_record,
        "status": "closed",
        "reason": close_reason_extra,
        "close_price": close_price,
        "scale_factor_applied": scale_factor_applied,
        "realized_pnl_usd": realized_delta,
        "portfolio_realized_total_usd": realized_after,
        "position_side": pos.position_side,
        "closed_quantity": pos.quantity,
        "match_strategy": match_strategy,
        "position_source": position_source,
        "position_correlation_id": position_correlation_id or None,
    }
    _append_audit(reconcile_path, rec)
    logger.info(
        "[reconcile] closed symbol=%s envelope=%s qty=%s close=%.6g pnl=%.4f",
        display_sym,
        source_envelope_id,
        pos.quantity,
        close_price,
        realized_delta,
    )
    return ReconcileOutcome(
        status="closed",
        reason=close_reason_extra,
        symbol=display_sym,
        touch_price=close_price,
        realized_pnl_usd=realized_delta,
        audit_record=rec,
    )


__all__ = [
    "ReconcileOutcome",
    "reconcile_target_completion",
]
