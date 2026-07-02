"""Phase-B loop hook: shadow-candidate recording under entry_mode=disabled.

Contract:
- shadow_diagnostics OFF (default) -> entry_mode=disabled keeps the cheapest
  early-return: ENTRY_MODE_BLOCKED, no market-data fetch, NO candidate written.
- shadow_diagnostics ON -> entry_mode=disabled runs the read-only pipeline
  (market-data + signal + geometry), records ONE hypothetical candidate (no
  fill/order/position), and still returns ENTRY_MODE_BLOCKED.

Reuses the construction pattern from test_trading_loop_entry_mode.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.execution.paper_engine import PaperExecutionEngine
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.orchestrator.models import CycleStatus
from app.orchestrator.trading_loop import TradingLoop
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.signals.generator import SignalGenerator


def _limits() -> RiskLimits:
    return RiskLimits(
        initial_equity=10000.0,
        max_risk_per_trade_pct=0.25,
        max_daily_loss_pct=1.0,
        max_total_drawdown_pct=5.0,
        max_open_positions=5,
        max_leverage=1.0,
        require_stop_loss=True,
        allow_averaging_down=False,
        allow_martingale=False,
        kill_switch_enabled=True,
        min_signal_confidence=0.75,
        min_signal_confluence_count=2,
        regime_filter_enabled=False,
    )


def _loop(tmp_path: Path) -> TradingLoop:
    exec_eng = PaperExecutionEngine(
        initial_equity=10000.0,
        fee_pct=0.1,
        slippage_pct=0.05,
        live_enabled=False,
        audit_log_path=str(tmp_path / "exec_audit.jsonl"),
    )
    return TradingLoop(
        risk_engine=RiskEngine(_limits()),
        execution_engine=exec_eng,
        market_data_adapter=MockMarketDataAdapter(),
        signal_generator=SignalGenerator(
            min_confidence=0.75, min_confluence=2, stop_loss_pct=2.5, take_profit_pct=5.0
        ),
        audit_log_path=str(tmp_path / "loop_audit.jsonl"),
    )


def _strong_eth() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc_eth_shadow",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.85,
        relevance_score=0.90,
        impact_score=0.80,
        confidence_score=0.85,
        novelty_score=0.70,
        actionable=True,
        affected_assets=["ETH", "ETH/USDT"],
        tags=["bullish"],
        spam_probability=0.02,
        explanation_short="ETH strong bullish catalyst.",
        explanation_long="Detailed reasoning.",
        recommended_priority=10,
    )


def _ledger(tmp_path: Path) -> Path:
    return tmp_path / "artifacts" / "shadow_candidate_ledger.jsonl"


@pytest.mark.asyncio
async def test_shadow_off_keeps_cheap_early_return(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.delenv("EXECUTION_SHADOW_DIAGNOSTICS", raising=False)
    monkeypatch.chdir(tmp_path)
    loop = _loop(tmp_path)

    cycle = await loop.run_cycle(_strong_eth(), "ETH/USDT")

    assert cycle.status == CycleStatus.ENTRY_MODE_BLOCKED
    assert cycle.market_data_fetched is False  # never reached the pipeline
    assert not any("shadow_candidate_recorded" in n for n in cycle.notes)
    assert not _ledger(tmp_path).exists()


@pytest.mark.asyncio
async def test_shadow_on_records_candidate_without_execution(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.setenv("EXECUTION_SHADOW_DIAGNOSTICS", "true")
    monkeypatch.chdir(tmp_path)
    loop = _loop(tmp_path)

    cycle = await loop.run_cycle(_strong_eth(), "ETH/USDT")

    # still blocked, still no order — but the pipeline ran and recorded.
    assert cycle.status == CycleStatus.ENTRY_MODE_BLOCKED
    assert cycle.order_created is False
    assert cycle.market_data_fetched is True
    assert cycle.signal_generated is True
    assert any("shadow_candidate_recorded" in n for n in cycle.notes)

    ledger = _ledger(tmp_path)
    assert ledger.exists()
    rows = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(rows) == 1
    rec = rows[0]
    assert rec["symbol"] == "ETH/USDT"
    assert rec["side"] == "long"
    assert rec["entry_mode"] == "disabled"
    # NEO-P-002 (Weg B): a real generator signal (doc_id has no loop_control_
    # prefix) is now attributed via the shared derive_autonomous_signal_source
    # helper as "autonomous_generator" — the unified taxonomy with the fill path
    # (#132). "autonomous_loop" is no longer emitted as a NEW source value.
    assert rec["source"] == "autonomous_generator"
    assert rec["candidate_kind"] == "signal_candidate"
    assert rec["source_stage"] == "signal_generator"
    assert rec["signal_origin"] == "autonomous_generator"
    assert rec["is_canary"] is False
    assert rec["is_synthetic_default"] is False
    assert rec["document_id"] == "doc_eth_shadow"
    assert rec["schema_version"] == "v2"
    assert rec["entry_price"] > 0
    assert rec["stop_price"] is not None
    assert rec["take_price"] is not None
    # geometry derived
    assert rec["stop_dist_bps"] is not None
    assert rec["rr"] is not None
    # gate verdict captured (report-not-act): present, boolean
    assert isinstance(rec["gate_would_reject"], bool)

    # hard invariant: no paper fill happened
    exec_audit = tmp_path / "exec_audit.jsonl"
    if exec_audit.exists():
        lines = exec_audit.read_text(encoding="utf-8").splitlines()
        events = [json.loads(x) for x in lines if x.strip()]
        assert not any(e.get("event_type") == "order_filled" for e in events)
