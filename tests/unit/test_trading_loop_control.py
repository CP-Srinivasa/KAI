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
