"""Tests for D-182 paper-execution priority-tier gate + D-184 summary."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.execution.paper_engine import PaperExecutionEngine
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.orchestrator.models import CycleStatus
from app.orchestrator.trading_loop import (
    TradingLoop,
    build_loop_trigger_analysis,
    build_priority_gate_summary,
)
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.signals.generator import SignalGenerator


def _loop(tmp_path) -> TradingLoop:
    risk = RiskEngine(
        RiskLimits(
            initial_equity=10000.0,
            max_risk_per_trade_pct=0.25,
            max_daily_loss_pct=1.0,
            max_total_drawdown_pct=5.0,
            max_open_positions=3,
            max_leverage=1.0,
            require_stop_loss=True,
            allow_averaging_down=False,
            allow_martingale=False,
            kill_switch_enabled=True,
            min_signal_confidence=0.75,
            min_signal_confluence_count=2,
        )
    )
    exec_eng = PaperExecutionEngine(
        initial_equity=10000.0,
        fee_pct=0.1,
        slippage_pct=0.05,
        live_enabled=False,
        audit_log_path=str(tmp_path / "exec_audit.jsonl"),
    )
    return TradingLoop(
        risk_engine=risk,
        execution_engine=exec_eng,
        market_data_adapter=MockMarketDataAdapter(),
        signal_generator=SignalGenerator(
            min_confidence=0.75,
            min_confluence=2,
            stop_loss_pct=2.5,
            take_profit_pct=5.0,
        ),
        audit_log_path=str(tmp_path / "loop_audit.jsonl"),
    )


def _analysis(priority: int | None) -> AnalysisResult:
    return AnalysisResult(
        document_id=f"doc_p{priority}",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.85,
        relevance_score=0.90,
        impact_score=0.80,
        confidence_score=0.85,
        novelty_score=0.70,
        actionable=True,
        affected_assets=["BTC", "BTC/USDT"],
        tags=["etf", "bullish"],
        spam_probability=0.02,
        explanation_short="Strong bullish catalyst.",
        explanation_long="Reasoning.",
        recommended_priority=priority,
    )


@pytest.mark.asyncio
async def test_default_threshold_allows_any_priority(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """paper_min_priority=1 (default) must not block low-priority analyses.

    Explicit setenv defends against a .env file that already raised the gate
    on the host — tests must not depend on ambient env state.
    """
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "1")
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=3), "BTC/USDT")
    assert cycle.status != CycleStatus.PRIORITY_REJECTED


@pytest.mark.asyncio
async def test_threshold_10_blocks_below_tier(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P<10 must be rejected when threshold is raised to the high-conviction tier."""
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "10")
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=8), "BTC/USDT")
    assert cycle.status == CycleStatus.PRIORITY_REJECTED
    assert not cycle.market_data_fetched  # gate runs before market-data fetch
    assert any("priority_gate_reject:8" in n for n in cycle.notes)
    assert any("threshold:10" in n for n in cycle.notes)


@pytest.mark.asyncio
async def test_threshold_10_allows_p10(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P=10 must pass the gate even at threshold=10 (boundary, inclusive)."""
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "10")
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=10), "BTC/USDT")
    assert cycle.status != CycleStatus.PRIORITY_REJECTED


@pytest.mark.asyncio
async def test_none_priority_blocked_when_gate_active(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """priority=None is treated as 'unknown' and rejected when threshold>1."""
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "10")
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=None), "BTC/USDT")
    assert cycle.status == CycleStatus.PRIORITY_REJECTED
    assert any("priority_gate_reject:None" in n for n in cycle.notes)


@pytest.mark.asyncio
async def test_none_priority_allowed_at_default_threshold(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """priority=None must not be penalised while the gate is the no-op default."""
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "1")
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=None), "BTC/USDT")
    assert cycle.status != CycleStatus.PRIORITY_REJECTED


# --- Paper-Learning P3: Gate-2 source-aware threshold (Goal 2026-06-10) ---
#
# For source=real_analysis the D-182 gate uses real_analysis_paper.min_priority;
# every other source keeps the global execution.paper_min_priority. entry_mode
# defaults to PAPER here so the decoupling branch is skipped and Gate 2 is hit
# directly — isolating the threshold-selection logic.


@pytest.mark.asyncio
async def test_p3_gate2_real_analysis_uses_feeder_threshold_blocks_p5(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """source=real_analysis + feeder min_priority=10 → a P5 analysis is rejected
    at Gate 2, even though the GLOBAL paper_min_priority is the no-op 1."""
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "1")
    monkeypatch.setenv("REAL_ANALYSIS_PAPER_MIN_PRIORITY", "10")
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=5), "BTC/USDT", analysis_source="real_analysis")
    assert cycle.status == CycleStatus.PRIORITY_REJECTED
    assert any("priority_gate_reject:5" in n for n in cycle.notes)
    assert any("threshold:10" in n for n in cycle.notes)


@pytest.mark.asyncio
async def test_p3_gate2_real_analysis_threshold5_allows_p5(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """source=real_analysis + feeder min_priority=5 → a P5 analysis passes Gate 2
    (block < 5), while the global paper_min_priority stays at the strict 10."""
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "10")
    monkeypatch.setenv("REAL_ANALYSIS_PAPER_MIN_PRIORITY", "5")
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=5), "BTC/USDT", analysis_source="real_analysis")
    assert cycle.status != CycleStatus.PRIORITY_REJECTED


@pytest.mark.asyncio
async def test_p3_gate2_other_source_keeps_global_threshold(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVARIANT: a non-feeder source (analysis_source=None, autonomous loop)
    keeps the GLOBAL paper_min_priority and ignores the feeder threshold.
    Global=10 blocks P8; the lenient feeder=5 must NOT leak into this path."""
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "10")
    monkeypatch.setenv("REAL_ANALYSIS_PAPER_MIN_PRIORITY", "5")
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=8), "BTC/USDT")
    assert cycle.status == CycleStatus.PRIORITY_REJECTED
    assert any("threshold:10" in n for n in cycle.notes)


@pytest.mark.asyncio
async def test_p3_gate2_feeder_default_strict_blocks_p5(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default REAL_ANALYSIS_PAPER_MIN_PRIORITY (10) keeps the feeder strict:
    a P5 real_analysis cycle is rejected without any env opt-in."""
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "1")
    monkeypatch.delenv("REAL_ANALYSIS_PAPER_MIN_PRIORITY", raising=False)
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_analysis(priority=5), "BTC/USDT", analysis_source="real_analysis")
    assert cycle.status == CycleStatus.PRIORITY_REJECTED
    assert any("threshold:10" in n for n in cycle.notes)


# --- D-184: operator-visibility summary ---


def _write_cycle_row(audit_path: Path, *, status: str, started_at: str) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "cycle_id": f"c_{status}_{started_at[-8:]}",
                    "started_at": started_at,
                    "completed_at": started_at,
                    "symbol": "BTC/USDT",
                    "status": status,
                    "market_data_fetched": False,
                    "signal_generated": False,
                    "risk_approved": False,
                    "order_created": False,
                    "fill_simulated": False,
                    "decision_id": None,
                    "risk_check_id": None,
                    "order_id": None,
                    "notes": [],
                }
            )
            + "\n"
        )


def test_priority_gate_summary_counts_by_status(tmp_path: Path) -> None:
    """Buckets cycles in-window by status; out-of-window rows are excluded."""
    audit = tmp_path / "loop_audit.jsonl"
    now = datetime.now(UTC)
    recent = (now - timedelta(hours=1)).isoformat()
    old = (now - timedelta(hours=48)).isoformat()

    # in-window: 2 priority_rejected, 1 completed, 1 risk_rejected
    _write_cycle_row(audit, status="priority_rejected", started_at=recent)
    _write_cycle_row(audit, status="priority_rejected", started_at=recent)
    _write_cycle_row(audit, status="completed", started_at=recent)
    _write_cycle_row(audit, status="risk_rejected", started_at=recent)
    # out-of-window: ignored
    _write_cycle_row(audit, status="priority_rejected", started_at=old)
    _write_cycle_row(audit, status="completed", started_at=old)

    summary = build_priority_gate_summary(audit_path=audit, window_hours=24)
    assert summary.total_cycles == 4
    assert summary.priority_rejected == 2
    assert summary.completed == 1
    assert summary.other_rejected == 1
    assert summary.window_hours == 24


def test_priority_gate_summary_reflects_current_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """threshold field + gate_active mirror the active (applied) setting.

    get_settings() is process-cached (settings-cache fix), so an EXECUTION_PAPER_
    MIN_PRIORITY change takes effect once the cache is rebuilt — in production that
    is the service restart; here we clear the cache to simulate it. The summary
    must then reflect the new threshold.
    """
    from app.core.settings import get_settings

    audit = tmp_path / "loop_audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.touch()

    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "1")
    get_settings.cache_clear()
    summary_off = build_priority_gate_summary(audit_path=audit)
    assert summary_off.threshold == 1
    assert summary_off.gate_active is False

    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "10")
    get_settings.cache_clear()  # simulate the restart that applies the env change
    summary_on = build_priority_gate_summary(audit_path=audit)
    assert summary_on.threshold == 10
    assert summary_on.gate_active is True


def test_priority_gate_summary_handles_missing_audit(tmp_path: Path) -> None:
    """No audit file → zeros everywhere, but threshold still surfaced."""
    audit = tmp_path / "never_written.jsonl"
    summary = build_priority_gate_summary(audit_path=audit)
    assert summary.total_cycles == 0
    assert summary.priority_rejected == 0
    assert summary.completed == 0
    assert summary.other_rejected == 0
    # threshold still read from settings — operator-visible even before first cycle
    assert summary.threshold >= 1


# --- NEO-P-PRIO-20260425-03: regression for the 2026-04-19→25 cron blackout ---
#
# build_loop_trigger_analysis() defaulted recommended_priority to None and the
# D-182 gate at threshold=10 silently rejected every cron-triggered cycle for
# 6 days. CI was structurally blind to this because conftest pins
# EXECUTION_PAPER_MIN_PRIORITY=1 globally. These tests deliberately raise the
# threshold inside the test scope so a future regression in the trigger
# factory cannot ride the conftest-default again.


def test_build_loop_trigger_analysis_sets_priority_for_every_profile() -> None:
    """recommended_priority must be populated for all supported profiles."""
    conservative = build_loop_trigger_analysis(symbol="BTC/USDT", analysis_profile="conservative")
    bullish = build_loop_trigger_analysis(symbol="BTC/USDT", analysis_profile="bullish")
    bearish = build_loop_trigger_analysis(symbol="BTC/USDT", analysis_profile="bearish")
    assert conservative.recommended_priority is not None
    assert bullish.recommended_priority is not None
    assert bearish.recommended_priority is not None
    # Conservative is intentionally below the high-conviction tier.
    assert conservative.recommended_priority < 10
    # Bullish/bearish probes must clear the highest documented gate (=10).
    assert bullish.recommended_priority >= 10
    assert bearish.recommended_priority >= 10


@pytest.mark.asyncio
async def test_bullish_probe_passes_gate_at_threshold_10(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bullish trigger-factory output must NOT be rejected at threshold=10.

    This is the exact failure that ran for 6 days unnoticed: cron fires the
    bullish/conservative profile, gate is at 10, the factory leaves
    recommended_priority=None → 100% rejection. The test forces the gate to
    10 (overriding the conftest default of 1) so a regression cannot hide
    behind the dev-friendly default again.
    """
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "10")
    loop = _loop(tmp_path)
    analysis = build_loop_trigger_analysis(symbol="BTC/USDT", analysis_profile="bullish")
    cycle = await loop.run_cycle(analysis, "BTC/USDT")
    assert cycle.status != CycleStatus.PRIORITY_REJECTED


@pytest.mark.asyncio
async def test_conservative_probe_blocked_at_threshold_10(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Conservative profile is *intentionally* below the high-conviction gate.

    Documents the contract: conservative is a health-check probe that must
    NOT actually fill paper trades when the strict gate is on — bullish is
    the profile that exercises the engine. If a future refactor lifts
    conservative to >=10 by accident, this test catches it.
    """
    monkeypatch.setenv("EXECUTION_PAPER_MIN_PRIORITY", "10")
    loop = _loop(tmp_path)
    analysis = build_loop_trigger_analysis(symbol="BTC/USDT", analysis_profile="conservative")
    cycle = await loop.run_cycle(analysis, "BTC/USDT")
    assert cycle.status == CycleStatus.PRIORITY_REJECTED
