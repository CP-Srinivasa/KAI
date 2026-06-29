"""Tests for Sprint 41 trading-loop control-plane and cycle-audit surfaces."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.orchestrator.models import CycleStatus
from app.orchestrator.trading_loop import (
    build_loop_status_summary,
    build_recent_cycles_summary,
    run_trading_loop_once,
)


@pytest.mark.asyncio
async def test_run_trading_loop_once_paper_mode_is_guarded_and_audited(
    tmp_path: Path,
) -> None:
    loop_audit = tmp_path / "loop_audit.jsonl"
    execution_audit = tmp_path / "execution_audit.jsonl"

    cycle = await run_trading_loop_once(
        symbol="BTC/USDT",
        mode="paper",
        provider="mock",
        analysis_profile="conservative",
        loop_audit_path=loop_audit,
        execution_audit_path=execution_audit,
    )

    assert cycle.status == CycleStatus.NO_SIGNAL
    assert cycle.market_data_fetched is True
    assert cycle.signal_generated is False
    assert cycle.order_created is False
    assert cycle.fill_simulated is False

    assert loop_audit.exists()
    rows = [
        json.loads(line)
        for line in loop_audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["status"] == CycleStatus.NO_SIGNAL.value
    assert not execution_audit.exists(), "conservative profile must avoid execution side effects"


@pytest.mark.asyncio
async def test_run_trading_loop_once_shadow_mode_is_allowed(tmp_path: Path) -> None:
    cycle = await run_trading_loop_once(
        symbol="BTC/USDT",
        mode="shadow",
        provider="mock",
        analysis_profile="conservative",
        loop_audit_path=tmp_path / "loop_shadow.jsonl",
        execution_audit_path=tmp_path / "execution_shadow.jsonl",
    )

    assert cycle.status == CycleStatus.NO_SIGNAL
    assert cycle.market_data_fetched is True
    assert cycle.order_created is False


@pytest.mark.asyncio
async def test_run_trading_loop_once_bearish_opens_a_short(
    tmp_path: Path,
) -> None:
    """Goal 2026-06-10 (long+short): a bearish profile now OPENS a short paper
    position. Before the position_side wiring the sell was mis-read as a long
    close → ORDER_FAILED; now it opens a short and fills."""
    loop_audit = tmp_path / "loop_bearish.jsonl"
    execution_audit = tmp_path / "execution_bearish.jsonl"

    cycle = await run_trading_loop_once(
        symbol="ETH/USDT",
        mode="paper",
        provider="mock",
        analysis_profile="bearish",
        loop_audit_path=loop_audit,
        execution_audit_path=execution_audit,
    )

    assert cycle.status == CycleStatus.COMPLETED
    assert cycle.market_data_fetched is True
    assert cycle.signal_generated is True
    assert cycle.risk_approved is True
    assert cycle.order_created is True
    assert cycle.fill_simulated is True

    assert execution_audit.exists()
    exec_rows = [
        json.loads(line)
        for line in execution_audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    event_types = [r["event_type"] for r in exec_rows]
    assert "order_created" in event_types
    assert "order_filled" in event_types
    filled = next(r for r in exec_rows if r["event_type"] == "order_filled")
    assert filled["side"] == "sell"
    assert filled["position_side"] == "short"


@pytest.mark.asyncio
async def test_run_trading_loop_once_rejects_live_mode_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="allowed: paper, shadow"):
        await run_trading_loop_once(
            symbol="BTC/USDT",
            mode="live",
            provider="mock",
            loop_audit_path=tmp_path / "loop_live.jsonl",
            execution_audit_path=tmp_path / "execution_live.jsonl",
        )


@pytest.mark.asyncio
async def test_recent_cycle_and_status_surfaces_show_audit_visibility(
    tmp_path: Path,
) -> None:
    loop_audit = tmp_path / "loop_visibility.jsonl"

    await run_trading_loop_once(
        symbol="BTC/USDT",
        mode="paper",
        provider="mock",
        analysis_profile="conservative",
        loop_audit_path=loop_audit,
        execution_audit_path=tmp_path / "exec_visibility.jsonl",
    )
    await run_trading_loop_once(
        symbol="ETH/USDT",
        mode="shadow",
        provider="mock",
        analysis_profile="conservative",
        loop_audit_path=loop_audit,
        execution_audit_path=tmp_path / "exec_visibility_2.jsonl",
    )

    recent = build_recent_cycles_summary(audit_path=loop_audit, last_n=10)
    status = build_loop_status_summary(audit_path=loop_audit, mode="paper")

    assert recent.total_cycles == 2
    assert recent.status_counts[CycleStatus.NO_SIGNAL.value] == 2
    assert len(recent.recent_cycles) == 2
    assert recent.execution_enabled is False
    assert recent.write_back_allowed is False

    assert status.total_cycles == 2
    assert status.last_cycle_status == CycleStatus.NO_SIGNAL.value
    assert status.last_cycle_symbol == "ETH/USDT"
    assert status.run_once_allowed is True
    assert status.auto_loop_enabled is False


def test_loop_status_marks_live_mode_blocked_without_crashing(tmp_path: Path) -> None:
    status = build_loop_status_summary(
        audit_path=tmp_path / "missing.jsonl",
        mode="live",
    )

    assert status.total_cycles == 0
    assert status.run_once_allowed is False
    assert status.run_once_block_reason is not None
    assert "mode=live" in status.run_once_block_reason
    assert status.execution_enabled is False
    assert status.write_back_allowed is False


@pytest.mark.asyncio
async def test_run_once_does_not_enable_background_autoloop(tmp_path: Path) -> None:
    loop_audit = tmp_path / "loop_no_daemon.jsonl"

    await run_trading_loop_once(
        symbol="BTC/USDT",
        mode="paper",
        provider="mock",
        analysis_profile="conservative",
        loop_audit_path=loop_audit,
        execution_audit_path=tmp_path / "exec_no_daemon.jsonl",
    )
    summary_after_run = build_recent_cycles_summary(audit_path=loop_audit, last_n=20)
    assert summary_after_run.total_cycles == 1

    await asyncio.sleep(0.05)

    summary_after_wait = build_recent_cycles_summary(audit_path=loop_audit, last_n=20)
    assert summary_after_wait.total_cycles == 1


@pytest.mark.asyncio
async def test_run_trading_loop_once_technical_paper_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When settings.technical_paper.enabled is False, a technical paper signal
    must be blocked even when global entry_mode allows autonomous loop entry."""
    from app.core.domain.document import AnalysisResult
    from app.core.enums import EntryMode, SentimentLabel
    from app.core.settings import AppSettings, ExecutionSettings, TechnicalPaperSettings

    settings = AppSettings()
    settings.technical_paper = TechnicalPaperSettings(enabled=False)
    settings.execution = ExecutionSettings(entry_mode=EntryMode.PAPER)

    monkeypatch.setattr("app.orchestrator.trading_loop.get_settings", lambda: settings)

    analysis = AnalysisResult(
        document_id="test_tech_doc",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.8,
        relevance_score=1.0,
        impact_score=1.0,
        confidence_score=0.85,
        actionable=True,
        novelty_score=0.0,
        explanation_short="short explanation",
        explanation_long="long explanation",
    )

    cycle = await run_trading_loop_once(
        symbol="BTC/USDT",
        mode="paper",
        provider="mock",
        analysis_result=analysis,
        analysis_source="technical_paper",
        loop_audit_path=tmp_path / "loop_tech_disabled.jsonl",
        execution_audit_path=tmp_path / "exec_tech_disabled.jsonl",
    )

    assert cycle.status == CycleStatus.ENTRY_MODE_BLOCKED
    assert "route_blocked:technical_paper_disabled" in cycle.notes


@pytest.mark.asyncio
async def test_run_trading_loop_once_technical_paper_enabled_excluded_from_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-002 edge-attribution: when the autonomous loop is ENABLED (entry_mode=
    paper) a technical_paper fill must still be hard-attributed feed_source=
    'technical_paper' so it is excluded from the edge/D-227 headline like canary
    — NOT mislabelled 'autonomous_loop' and counted in the honest forward edge."""
    from app.core.domain.document import AnalysisResult
    from app.core.enums import EntryMode, SentimentLabel
    from app.core.settings import AppSettings, ExecutionSettings, TechnicalPaperSettings

    settings = AppSettings()
    settings.technical_paper = TechnicalPaperSettings(enabled=True)
    settings.execution = ExecutionSettings(entry_mode=EntryMode.PAPER)
    monkeypatch.setattr("app.orchestrator.trading_loop.get_settings", lambda: settings)

    analysis = AnalysisResult(
        document_id="test_tech_doc_enabled",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.9,
        relevance_score=1.0,
        impact_score=1.0,
        confidence_score=0.9,
        actionable=True,
        novelty_score=0.0,
        explanation_short="short explanation",
        explanation_long="long explanation",
    )

    execution_audit = tmp_path / "exec_tech_enabled.jsonl"
    cycle = await run_trading_loop_once(
        symbol="BTC/USDT",
        mode="paper",
        provider="mock",
        analysis_result=analysis,
        analysis_source="technical_paper",
        loop_audit_path=tmp_path / "loop_tech_enabled.jsonl",
        execution_audit_path=execution_audit,
    )

    assert cycle.status == CycleStatus.COMPLETED
    rows = [
        json.loads(line)
        for line in execution_audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    label = next(r for r in rows if r["event_type"] == "paper_trade_label")
    assert label["feed_source"] == "technical_paper"
    assert label["source_name"] == "technical_paper"


@pytest.mark.asyncio
async def test_run_trading_loop_once_momentum_universe_tags_signal_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G2 cohort attribution: a momentum_universe-tagged paper fill must carry
    signal_source='momentum_universe' onto the order/close events so the G3
    cohort-outcomes bridge can isolate it. Before the fix the explicit cohort tag
    fell through the signal_source whitelist (only real_analysis/technical_paper
    were special-cased) and was mislabelled 'autonomous_generator' — invisible to
    extract_cohort_outcomes and wrongly counted in the canonical autonomous edge."""
    from app.core.domain.document import AnalysisResult
    from app.core.enums import EntryMode, SentimentLabel
    from app.core.settings import AppSettings, ExecutionSettings

    settings = AppSettings()
    settings.execution = ExecutionSettings(entry_mode=EntryMode.PAPER)
    monkeypatch.setattr("app.orchestrator.trading_loop.get_settings", lambda: settings)

    # document_id mirrors the real momentum feeder (_build_analysis): non-empty,
    # so the broken path resolves to 'autonomous_generator' (the bug) — the tag
    # must override it.
    analysis = AnalysisResult(
        document_id="momentum_universe_BTCUSDT",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.9,
        relevance_score=1.0,
        impact_score=1.0,
        confidence_score=0.9,
        actionable=True,
        novelty_score=0.0,
        explanation_short="short explanation",
        explanation_long="long explanation",
    )

    execution_audit = tmp_path / "exec_momentum.jsonl"
    cycle = await run_trading_loop_once(
        symbol="BTC/USDT",
        mode="paper",
        provider="mock",
        analysis_result=analysis,
        analysis_source="momentum_universe",
        loop_audit_path=tmp_path / "loop_momentum.jsonl",
        execution_audit_path=execution_audit,
    )

    assert cycle.status == CycleStatus.COMPLETED
    rows = [
        json.loads(line)
        for line in execution_audit.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # The entry fill carries the cohort source, so it propagates to position_closed
    # (which extract_cohort_outcomes reads via signal_source).
    filled = next(r for r in rows if r["event_type"] == "order_filled")
    assert filled["source"] == "momentum_universe"
    label = next(r for r in rows if r["event_type"] == "paper_trade_label")
    assert label["source_name"] == "momentum_universe"
