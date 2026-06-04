from __future__ import annotations

from pathlib import Path

from app.execution.paper_engine import PaperExecutionEngine
from app.execution.target_completion_reconciler import reconcile_target_completion
from app.ingestion.telegram_channel_parser import TargetCompletionEvent


def test_target_completion_refuses_non_premium_position_source(tmp_path: Path) -> None:
    engine = PaperExecutionEngine(initial_equity=10_000.0, fee_pct=0.0, slippage_pct=0.0)
    order = engine.create_order(
        symbol="CYS/USDT",
        side="buy",
        quantity=100.0,
        order_type="market",
        idempotency_key="open:autonomous:cys",
        position_side="long",
        source="autonomous_generator",
        correlation_id="AUTO-CYS-1",
    )
    engine.fill_order(order, 0.4869)

    event = TargetCompletionEvent(
        symbol="CYSUSDT",
        display_symbol="CYS/USDT",
        touch_price=4869.0,
        raw_text="🎯#CYS/USDT has touched 4869 and has completed all the profit targets",
    )
    out = reconcile_target_completion(
        event,
        source_envelope_id="ENV-premium-cys-1",
        engine=engine,
        reconcile_log_path=tmp_path / "reconcile.jsonl",
    )

    assert out.status == "requires_review"
    assert out.reason == "non_premium_position_source"
    assert out.realized_pnl_usd is None
    assert out.audit_record["position_source"] == "autonomous_generator"
    assert out.audit_record["match_strategy"] == "symbol_single_open_position"
    assert "CYS/USDT" in engine.portfolio.positions
