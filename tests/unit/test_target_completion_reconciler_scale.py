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
    # 250 / 1.0 = ratio 250 → außerhalb aller erkannten Bänder (1e1/1e2 sind
    # bewusst nicht erkannt) → Faktor 1.0 → implausibel → requires_scale_review.
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


def _open_short(engine: PaperExecutionEngine, symbol: str, entry: float, qty: float) -> None:
    order = engine.create_order(
        symbol=symbol,
        side="sell",
        quantity=qty,
        order_type="market",
        idempotency_key=f"open:{symbol}",
        position_side="short",
        source="telegram_premium_channel_approved",
    )
    engine.fill_order(order, entry)
    assert symbol in engine.portfolio.positions


def test_long_all_targets_below_entry_is_wrong_side_review(
    engine: PaperExecutionEngine, tmp_path: Path
) -> None:
    """2026-06-10 PnL-truth: an 'all profit targets completed' long whose scaled
    touch lands BELOW entry cannot be a real win — it is a misresolved scale.
    Refuse to book the phantom loss: requires_scale_review, position stays open."""
    _open_long(engine, "FOO/USDT", entry=1.0, qty=10.0)
    # touch 0.85 same scale as entry (factor 1.0) but below entry → wrong side
    event = TargetCompletionEvent(
        symbol="FOOUSDT", display_symbol="FOO/USDT", touch_price=0.85, raw_text="🎯"
    )
    out = reconcile_target_completion(
        event,
        source_envelope_id="ENV-foo-1",
        engine=engine,
        reconcile_log_path=tmp_path / "reconcile.jsonl",
    )
    assert out.status == "requires_scale_review"
    assert out.reason == "touch_price_wrong_side_of_entry"
    assert out.realized_pnl_usd is None
    assert "FOO/USDT" in engine.portfolio.positions  # NICHT als Verlust geschlossen


def test_short_all_targets_above_entry_is_wrong_side_review(
    engine: PaperExecutionEngine, tmp_path: Path
) -> None:
    """Symmetric guard for shorts: a completed-targets short must close BELOW
    entry; a scaled touch above entry is a wrong-sign scale error."""
    _open_short(engine, "BAR/USDT", entry=2.0, qty=10.0)
    event = TargetCompletionEvent(
        symbol="BARUSDT", display_symbol="BAR/USDT", touch_price=2.4, raw_text="🎯"
    )
    out = reconcile_target_completion(
        event,
        source_envelope_id="ENV-bar-1",
        engine=engine,
        reconcile_log_path=tmp_path / "reconcile.jsonl",
    )
    assert out.status == "requires_scale_review"
    assert out.reason == "touch_price_wrong_side_of_entry"
    assert out.realized_pnl_usd is None
    assert "BAR/USDT" in engine.portfolio.positions


def test_long_profitable_close_still_books_pnl(
    engine: PaperExecutionEngine, tmp_path: Path
) -> None:
    """Regression guard: the wrong-side check must NOT block a genuine winning
    close (long touch above entry books a positive PnL as before)."""
    _open_long(engine, "WIN/USDT", entry=1.0, qty=10.0)
    event = TargetCompletionEvent(
        symbol="WINUSDT", display_symbol="WIN/USDT", touch_price=1.08, raw_text="🎯"
    )
    out = reconcile_target_completion(
        event,
        source_envelope_id="ENV-win-1",
        engine=engine,
        reconcile_log_path=tmp_path / "reconcile.jsonl",
    )
    assert out.status == "closed"
    assert out.realized_pnl_usd is not None and out.realized_pnl_usd > 0
    assert "WIN/USDT" not in engine.portfolio.positions


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
