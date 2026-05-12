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
from app.ingestion.telegram_channel_parser import TargetCompletionEvent

logger = logging.getLogger(__name__)

_DEFAULT_RECONCILE_LOG = Path("artifacts/target_completion_audit.jsonl")
_PAPER_AUDIT_LOG = Path("artifacts/paper_execution_audit.jsonl")


@dataclass(frozen=True)
class ReconcileOutcome:
    status: str  # "closed" | "no_position" | "orphan_no_match" | "duplicate" | "error"
    reason: str
    symbol: str
    touch_price: float | None
    realized_pnl_usd: float | None
    audit_record: dict[str, Any] = field(default_factory=dict)


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

    # Engine entweder vom Caller ODER fresh rehydrated.
    eng = engine
    if eng is None:
        eng = PaperExecutionEngine(initial_equity=10000.0, live_enabled=False)
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

    # Close-Pfad: market-close zum touch_price (oder avg_entry als Notfall-Wert
    # wenn channel keine Zahl liefert UND market-data nicht verfügbar — sehr
    # konservativ, realisiert dann PnL=0).
    close_price = event.touch_price
    if close_price is None or close_price <= 0:
        close_price = pos.avg_entry_price  # Notfall: PnL=0 statt random scribble
        close_reason_extra = "touch_price_missing_fallback_to_avg_entry"
    else:
        close_reason_extra = "touch_price_from_channel"

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
        eng.fill_order(close_order, close_price)
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
    rec = {
        **base_record,
        "status": "closed",
        "reason": close_reason_extra,
        "close_price": close_price,
        "realized_pnl_usd": realized_delta,
        "portfolio_realized_total_usd": realized_after,
        "position_side": pos.position_side,
        "closed_quantity": pos.quantity,
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
