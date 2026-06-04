"""RC-4 (2026-06-04): Target-Completion-Reconciler Skalen-Härtung.

Der Channel postet Touch-Prices ROH in Channel-Skala (CYS 4869, US 16790),
während die offene Paper-Position USD-skaliert eröffnet wurde (entry ~0.4869).
Vorher wurde der rohe Wert direkt als Close-Price genommen → astronomischer,
falscher realized PnL. Diese Tests sichern:

1. Touch-Price wird auf die Skala der offenen Position gebracht (kein Müll-PnL).
2. Implausible Skala → status=requires_scale_review, KEIN PnL gebucht.
3. requires_scale_review ist retrybar (blockiert späteren Reconcile nicht).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.execution.paper_engine import PaperExecutionEngine
from app.execution.target_completion_reconciler import reconcile_target_completion
from app.ingestion.telegram_channel_parser import TargetCompletionEvent


@pytest.fixture
def engine() -> PaperExecutionEngine:
    return PaperExecutionEngine(initial_equity=10_000.0, fee_pct=0.0, slippage_pct=0.0)


def _open_long(engine: PaperExecutionEngine, symbol: str, entry: float, qty: float) -> None:
    order = engine.create_order(
        symbol=symbol,
        side="buy",
        quantity=qty,
        order_type="market",
        idempotency_key=f"open:{symbol}",
        position_side="long",
        source="telegram_premium_channel_approved",
    )
    engine.fill_order(order, entry)
    assert symbol in engine.portfolio.positions


def test_raw_touch_price_is_scaled_to_position_scale(
    engine: PaperExecutionEngine, tmp_path: Path
) -> None:
    """CYS: entry 0.4869 USD, Channel-Touch 4869 (×1e4) → skaliert auf ~0.4869.

    PnL muss klein und plausibel sein (Touch ≈ Entry → ~0), NICHT (4869-0.4869)·qty.
    """
    _open_long(engine, "CYS/USDT", entry=0.4869, qty=100.0)
    event = TargetCompletionEvent(
        symbol="CYSUSDT", display_symbol="CYS/USDT", touch_price=4869.0, raw_text="🎯"
    )
    out = reconcile_target_completion(
        event,
        source_envelope_id="ENV-cys-1",
        engine=engine,
        reconcile_log_path=tmp_path / "reconcile.jsonl",
    )
    assert out.status == "closed"
    assert out.audit_record["scale_factor_applied"] == 1e4
    # Close ~0.4869 ≈ entry → |PnL| muss winzig sein, kein Müllwert.
    assert out.realized_pnl_usd is not None
    assert abs(out.realized_pnl_usd) < 1.0


def test_implausible_scale_blocks_pnl(engine: PaperExecutionEngine, tmp_path: Path) -> None:
    """Touch-Price ohne erkennbare Skala (Faktor 1.0, aber 100× über Entry) →
    requires_scale_review, KEIN PnL, Position bleibt offen."""
    _open_long(engine, "WTF/USDT", entry=1.0, qty=10.0)
    # 250 / 1.0 → auch mit 1e2-Support implausibel außerhalb der Toleranz.
    event = TargetCompletionEvent(
        symbol="WTFUSDT", display_symbol="WTF/USDT", touch_price=250.0, raw_text="🎯"
    )
    out = reconcile_target_completion(
        event,
        source_envelope_id="ENV-wtf-1",
        engine=engine,
        reconcile_log_path=tmp_path / "reconcile.jsonl",
    )
    assert out.status == "requires_scale_review"
    assert out.realized_pnl_usd is None
    assert "WTF/USDT" in engine.portfolio.positions  # Position NICHT geschlossen


def test_requires_scale_review_is_retryable(engine: PaperExecutionEngine, tmp_path: Path) -> None:
    """Ein requires_scale_review-Record darf einen späteren Reconcile NICHT
    als duplicate blockieren (es wurde nie PnL gebucht)."""
    log = tmp_path / "reconcile.jsonl"
    _open_long(engine, "WTF/USDT", entry=1.0, qty=10.0)
    event = TargetCompletionEvent(
        symbol="WTFUSDT", display_symbol="WTF/USDT", touch_price=250.0, raw_text="🎯"
    )
    first = reconcile_target_completion(
        event, source_envelope_id="ENV-wtf-1", engine=engine, reconcile_log_path=log
    )
    assert first.status == "requires_scale_review"
    # zweiter Versuch desselben envelope: NICHT duplicate
    second = reconcile_target_completion(
        event, source_envelope_id="ENV-wtf-1", engine=engine, reconcile_log_path=log
    )
    assert second.status != "duplicate"


def test_clean_close_is_idempotent(engine: PaperExecutionEngine, tmp_path: Path) -> None:
    """Ein erfolgreich geschlossener Reconcile blockiert den zweiten als duplicate."""
    log = tmp_path / "reconcile.jsonl"
    _open_long(engine, "CYS/USDT", entry=0.4869, qty=100.0)
    event = TargetCompletionEvent(
        symbol="CYSUSDT", display_symbol="CYS/USDT", touch_price=4869.0, raw_text="🎯"
    )
    first = reconcile_target_completion(
        event, source_envelope_id="ENV-cys-1", engine=engine, reconcile_log_path=log
    )
    assert first.status == "closed"
    second = reconcile_target_completion(
        event, source_envelope_id="ENV-cys-1", engine=engine, reconcile_log_path=log
    )
    assert second.status == "duplicate"
