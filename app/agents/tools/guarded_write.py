"""Guarded write MCP tools.

Design rules:
- All tool functions are plain async functions (no @mcp.tool() decorator).
- Registration is done in app.agents.mcp_server via mcp.add_tool().
- All writes are restricted to workspace/artifacts/ (I-95 write guard).
- All writes are audit-logged via mcp_write_audit.jsonl (I-94).
- execution_enabled: False -- no live trading orders are created.
- write_back_allowed: False -- no external state mutation beyond artifacts/.
- Never import from app.agents.mcp_server (circular-import guard).
- Companion-ML subsystem removed (D-107).

Tool list:
- append_decision_instance: Append a validated decision instance to the journal
- run_trading_loop_once: Run one guarded paper/shadow trading cycle
"""

from __future__ import annotations

from app.agents.tools._helpers import (
    DECISION_JOURNAL_DEFAULT_PATH,
    LOOP_AUDIT_DEFAULT_PATH,
    PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    append_mcp_write_audit,
    require_artifacts_subpath,
    resolve_workspace_path,
)

# ---------------------------------------------------------------------------
# Guarded write inventory
# ---------------------------------------------------------------------------

GUARDED_WRITE_TOOL_NAMES: tuple[str, ...] = (
    "append_decision_instance",
    "run_trading_loop_once",
)


def get_guarded_write_tool_names() -> tuple[str, ...]:
    """Return the locked guarded-write tool name tuple."""
    return GUARDED_WRITE_TOOL_NAMES


# ---------------------------------------------------------------------------
# Real implementations
# ---------------------------------------------------------------------------


async def append_decision_instance(
    symbol: str,
    thesis: str,
    mode: str = "research",
    market: str = "crypto",
    venue: str = "paper",
    confidence_score: float = 0.5,
    supporting_factors: list[str] | None = None,
    contradictory_factors: list[str] | None = None,
    entry_logic: str = "manual_entry",
    exit_logic: str = "manual_exit",
    stop_loss: float = 0.0,
    invalidation_condition: str = "thesis_invalidated",
    model_version: str = "manual",
    prompt_version: str = "v0",
    data_sources_used: list[str] | None = None,
    journal_output_path: str = DECISION_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Append a validated decision instance to the append-only decision journal.

    This is an audit-only write. execution_enabled and write_back_allowed remain False.
    No trade is triggered by this call.
    """
    from app.decisions.journal import (
        RiskAssessment,
        append_decision_jsonl,
        create_decision_instance,
    )

    resolved = resolve_workspace_path(
        journal_output_path,
        label="Decision journal output",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    require_artifacts_subpath(resolved, label="Decision journal output")

    risk = RiskAssessment(
        risk_level="unassessed",
        max_position_pct=0.0,
        drawdown_remaining_pct=100.0,
    )
    decision = create_decision_instance(
        symbol=symbol,
        market=market,
        venue=venue,
        mode=mode,
        thesis=thesis,
        supporting_factors=list(supporting_factors or ["mcp_operator_input"]),
        contradictory_factors=list(contradictory_factors or []),
        confidence_score=confidence_score,
        market_regime="unknown",
        volatility_state="unknown",
        liquidity_state="unknown",
        risk_assessment=risk,
        entry_logic=entry_logic,
        exit_logic=exit_logic,
        stop_loss=stop_loss,
        invalidation_condition=invalidation_condition,
        position_size_rationale="manual sizing",
        max_loss_estimate=0.0,
        data_sources_used=list(data_sources_used or ["operator_input"]),
        model_version=model_version,
        prompt_version=prompt_version,
    )
    append_decision_jsonl(decision, resolved)

    append_mcp_write_audit(
        tool="append_decision_instance",
        params={
            "symbol": symbol,
            "mode": mode,
            "thesis": thesis[:80],
            "journal_output_path": str(resolved),
        },
        result_summary=f"decision_instance {decision.decision_id} appended",
    )

    return {
        "status": "decision_appended",
        "decision_id": decision.decision_id,
        "journal_path": str(resolved),
        "decision": decision.to_json_dict(),
        "execution_enabled": False,
        "write_back_allowed": False,
    }


async def run_trading_loop_once(
    symbol: str = "BTC/USDT",
    mode: str = "paper",
    provider: str = "mock",
    analysis_profile: str = "conservative",
    loop_audit_path: str = LOOP_AUDIT_DEFAULT_PATH,
    execution_audit_path: str = PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> dict[str, object]:
    """Run one guarded paper/shadow cycle and append audit rows."""
    from app.orchestrator.trading_loop import run_trading_loop_once as run_once_cycle

    resolved_loop_audit = resolve_workspace_path(
        loop_audit_path,
        label="Loop audit output",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    require_artifacts_subpath(resolved_loop_audit, label="Loop audit output")

    resolved_execution_audit = resolve_workspace_path(
        execution_audit_path,
        label="Execution audit output",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    require_artifacts_subpath(resolved_execution_audit, label="Execution audit output")

    cycle = await run_once_cycle(
        symbol=symbol,
        mode=mode,
        provider=provider,
        analysis_profile=analysis_profile,
        loop_audit_path=resolved_loop_audit,
        execution_audit_path=resolved_execution_audit,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )

    cycle_payload = {
        "cycle_id": cycle.cycle_id,
        "started_at": cycle.started_at,
        "completed_at": cycle.completed_at,
        "symbol": cycle.symbol,
        "status": cycle.status.value,
        "market_data_fetched": cycle.market_data_fetched,
        "signal_generated": cycle.signal_generated,
        "risk_approved": cycle.risk_approved,
        "order_created": cycle.order_created,
        "fill_simulated": cycle.fill_simulated,
        "decision_id": cycle.decision_id,
        "risk_check_id": cycle.risk_check_id,
        "order_id": cycle.order_id,
        "notes": list(cycle.notes),
    }

    append_mcp_write_audit(
        tool="run_trading_loop_once",
        params={
            "symbol": symbol,
            "mode": mode,
            "provider": provider,
            "analysis_profile": analysis_profile,
            "loop_audit_path": str(resolved_loop_audit),
            "execution_audit_path": str(resolved_execution_audit),
        },
        result_summary=(
            f"trading_loop cycle {cycle.cycle_id} completed with status={cycle.status.value}"
        ),
    )

    return {
        "status": "cycle_completed",
        "mode": mode,
        "provider": provider,
        "analysis_profile": analysis_profile,
        "loop_audit_path": str(resolved_loop_audit),
        "execution_audit_path": str(resolved_execution_audit),
        "cycle": cycle_payload,
        "auto_loop_enabled": False,
        "execution_enabled": False,
        "write_back_allowed": False,
    }
