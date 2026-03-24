"""Guarded write MCP tools: all 7 write-guarded tool implementations.

Design rules:
- All tool functions are plain async functions (no @mcp.tool() decorator).
- Registration is done in app.agents.mcp_server via mcp.add_tool().
- All writes are restricted to workspace/artifacts/ (I-95 write guard).
- All writes are audit-logged via mcp_write_audit.jsonl (I-94).
- execution_enabled: False — no live trading orders are created.
- write_back_allowed: False — no external state mutation beyond artifacts/.
- Never import from app.agents.mcp_server (circular-import guard).

Tool list:
- create_inference_profile: Create a new InferenceRouteProfile artifact
- activate_route_profile: Activate a route profile state file
- deactivate_route_profile: Deactivate the active route profile
- acknowledge_signal_handoff: Acknowledge a signal handoff record
- append_review_journal_entry: Append an operator review journal entry
- append_decision_instance: Append a validated decision instance to the journal
- run_trading_loop_once: Run one guarded paper/shadow trading cycle
"""

from __future__ import annotations

from app.agents.tools._helpers import (
    DECISION_JOURNAL_DEFAULT_PATH,
    HANDOFF_ACK_DEFAULT_PATH,
    JSON_SUFFIXES,
    LOOP_AUDIT_DEFAULT_PATH,
    PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    REVIEW_JOURNAL_DEFAULT_PATH,
    append_mcp_write_audit,
    require_artifacts_subpath,
    resolve_workspace_path,
)
from app.research.active_route import (
    DEFAULT_ACTIVE_ROUTE_PATH,
)
from app.research.active_route import (
    activate_route_profile as persist_active_route_profile,
)
from app.research.active_route import (
    deactivate_route_profile as clear_active_route_profile,
)
from app.research.execution_handoff import (
    append_handoff_acknowledgement_jsonl,
    create_handoff_acknowledgement,
    get_signal_handoff_by_id,
    load_signal_handoffs,
)
from app.research.inference_profile import (
    InferenceRouteProfile,
    save_inference_route_profile,
)

# ---------------------------------------------------------------------------
# Guarded write inventory
# ---------------------------------------------------------------------------

GUARDED_WRITE_TOOL_NAMES: tuple[str, ...] = (
    "create_inference_profile",
    "activate_route_profile",
    "deactivate_route_profile",
    "acknowledge_signal_handoff",
    "append_review_journal_entry",
    "append_decision_instance",
    "run_trading_loop_once",
)


def get_guarded_write_tool_names() -> tuple[str, ...]:
    """Return the locked guarded-write tool name tuple."""
    return GUARDED_WRITE_TOOL_NAMES


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def create_inference_profile(
    profile_name: str,
    route_profile: str,
    primary_path: str = "A.external_llm",
    shadow_paths: list[str] | None = None,
    control_path: str | None = None,
    output_path: str = "inference_route_profile.json",
    notes: list[str] | None = None,
) -> dict[str, object]:
    """Create an inference route profile JSON inside the workspace only."""
    resolved_output = resolve_workspace_path(
        output_path,
        label="Inference route profile output",
        allowed_suffixes=JSON_SUFFIXES,
    )
    require_artifacts_subpath(resolved_output, label="Inference route profile output")
    profile = InferenceRouteProfile(
        profile_name=profile_name,
        route_profile=route_profile,
        active_primary_path=primary_path,
        enabled_shadow_paths=list(shadow_paths or []),
        control_path=control_path,
        notes=list(notes or []),
    )
    saved = save_inference_route_profile(profile, resolved_output)
    append_mcp_write_audit(
        tool="create_inference_profile",
        params={
            "profile_name": profile_name,
            "route_profile": route_profile,
            "output_path": str(saved),
        },
        result_summary=f"saved: {saved}",
    )
    return {
        "output_path": str(saved),
        "profile": profile.to_json_dict(),
    }


async def activate_route_profile(
    profile_path: str,
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
    abc_envelope_output: str | None = None,
) -> dict[str, object]:
    """Activate an existing route profile via ActiveRouteState only."""
    resolved_profile = resolve_workspace_path(
        profile_path,
        label="Inference route profile",
        must_exist=True,
        allowed_suffixes=JSON_SUFFIXES,
    )
    resolved_state = resolve_workspace_path(
        state_path,
        label="Active route state output",
        allowed_suffixes=JSON_SUFFIXES,
    )
    require_artifacts_subpath(resolved_state, label="Active route state output")
    resolved_abc_output = (
        resolve_workspace_path(
            abc_envelope_output,
            label="ABC envelope output",
            allowed_suffixes=frozenset({".jsonl"}),
        )
        if abc_envelope_output is not None
        else None
    )
    if resolved_abc_output is not None:
        require_artifacts_subpath(resolved_abc_output, label="ABC envelope output")
    state = persist_active_route_profile(
        profile_path=resolved_profile,
        state_path=resolved_state,
        abc_envelope_output=(str(resolved_abc_output) if resolved_abc_output is not None else None),
    )
    append_mcp_write_audit(
        tool="activate_route_profile",
        params={
            "profile_path": str(resolved_profile),
            "state_path": str(resolved_state),
            "abc_envelope_output": (
                str(resolved_abc_output) if resolved_abc_output is not None else None
            ),
        },
        result_summary=f"activated: {state.route_profile}",
    )
    return {
        "state_path": str(resolved_state),
        "state": state.to_dict(),
        "app_llm_provider_unchanged": True,  # I-91, I-97
    }


async def deactivate_route_profile(
    state_path: str = str(DEFAULT_ACTIVE_ROUTE_PATH),
) -> dict[str, object]:
    """Deactivate the guarded route state file only."""
    resolved_state = resolve_workspace_path(
        state_path,
        label="Active route state",
        allowed_suffixes=JSON_SUFFIXES,
    )
    require_artifacts_subpath(resolved_state, label="Active route state")
    removed = clear_active_route_profile(resolved_state)
    append_mcp_write_audit(
        tool="deactivate_route_profile",
        params={"state_path": str(resolved_state)},
        result_summary=f"deactivated: {removed}",
    )
    return {
        "deactivated": removed,
        "state_path": str(resolved_state),
    }


async def acknowledge_signal_handoff(
    handoff_path: str,
    handoff_id: str,
    consumer_agent_id: str,
    notes: str = "",
    acknowledgement_output_path: str = HANDOFF_ACK_DEFAULT_PATH,
) -> dict[str, object]:
    """Append an audit-only acknowledgement for an existing visible SignalHandoff.

    Acknowledgement is AUDIT ONLY — not an execution trigger, not an approval,
    and not a routing decision (I-117, I-121, I-122).
    No write-back to KAI core DB (I-118).
    """
    resolved_handoff = resolve_workspace_path(
        handoff_path,
        label="Signal handoff input",
        must_exist=True,
    )
    resolved_ack = resolve_workspace_path(
        acknowledgement_output_path,
        label="Consumer acknowledgement audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    require_artifacts_subpath(
        resolved_ack,
        label="Consumer acknowledgement audit",
    )
    handoffs = load_signal_handoffs(resolved_handoff)
    handoff = get_signal_handoff_by_id(handoffs, handoff_id)

    if handoff.consumer_visibility != "visible":
        raise PermissionError(
            f"Only consumer-visible handoffs can be acknowledged — "
            f"handoff {handoff.handoff_id!r} has "
            f"consumer_visibility={handoff.consumer_visibility!r}."
        )

    ack = create_handoff_acknowledgement(
        handoff,
        consumer_agent_id=consumer_agent_id,
        notes=notes,
    )
    append_handoff_acknowledgement_jsonl(ack, resolved_ack)

    append_mcp_write_audit(
        tool="acknowledge_signal_handoff",
        params={
            "handoff_path": str(resolved_handoff),
            "handoff_id": handoff_id,
            "consumer_agent_id": consumer_agent_id,
            "notes": notes,
            "acknowledgement_output_path": str(resolved_ack),
        },
        result_summary=f"acknowledged handoff {handoff_id} by {consumer_agent_id}",
    )

    return {
        "status": "acknowledged_in_audit_only",
        "handoff_id": ack.handoff_id,
        "signal_id": ack.signal_id,
        "consumer_agent_id": ack.consumer_agent_id,
        "handoff_path": str(resolved_handoff),
        "acknowledgement_path": str(resolved_ack),
        "acknowledgement": ack.to_json_dict(),
        "core_state_unchanged": True,
        "execution_enabled": False,
        "write_back_allowed": False,
    }


async def append_review_journal_entry(
    source_ref: str,
    operator_id: str,
    review_action: str,
    review_note: str,
    evidence_refs: list[str] | None = None,
    journal_output_path: str = REVIEW_JOURNAL_DEFAULT_PATH,
) -> dict[str, object]:
    """Append an operator review journal entry without mutating core operator state."""
    from app.research.operational_readiness import (
        append_review_journal_entry_jsonl,
        create_review_journal_entry,
    )

    resolved = resolve_workspace_path(
        journal_output_path,
        label="Review journal output",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    require_artifacts_subpath(resolved, label="Review journal output")

    entry = create_review_journal_entry(
        source_ref=source_ref,
        operator_id=operator_id,
        review_action=review_action,
        review_note=review_note,
        evidence_refs=evidence_refs,
    )
    append_review_journal_entry_jsonl(entry, resolved)

    append_mcp_write_audit(
        tool="append_review_journal_entry",
        params={
            "source_ref": source_ref,
            "operator_id": operator_id,
            "review_action": review_action,
            "review_note": review_note,
            "evidence_refs": list(evidence_refs or []),
            "journal_output_path": str(resolved),
        },
        result_summary=f"review_journal entry {entry.review_id} appended",
    )

    return {
        "status": "review_journal_appended",
        "review_id": entry.review_id,
        "journal_path": str(resolved),
        "journal_entry": entry.to_json_dict(),
        "core_state_unchanged": True,
        "execution_enabled": False,
        "write_back_allowed": False,
    }


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
