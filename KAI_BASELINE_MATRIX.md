# KAI Baseline Matrix

Verified on: 2026-03-21
Purpose: technical source of truth for the current KAI repo state before further feature sprints.

## Scope

This rebaseline is based on:

1. `AGENTS.md`
2. `docs/kai_identity.md`
3. live inventory helpers in `app/cli/main.py` and `app/agents/mcp_server.py`
4. current registered CLI commands, MCP tools, and test-validated module behavior

When historical sprint text and current code differ, this file is authoritative.

## Canonical Prompt Sources

| Source | Role | Status |
|---|---|---|
| `AGENTS.md` | operator and coding-agent governance | canonical |
| `docs/kai_identity.md` | identity, mission, safety posture | canonical |

## Master-Prompt Baseline Checks

| Check | Status | Evidence |
|---|---|---|
| `ExecutionMode` fail-closed enum exists | pass | `app/core/enums.py`, `app/core/settings.py` |
| `live` is double-gated | pass | `ExecutionSettings.validate_mode_guardrails()` in `app/core/settings.py` |
| non-live remains default | pass | `ExecutionSettings.mode=paper`, `live_enabled=False`, `dry_run=True` |
| Risk Engine is mandatory in canonical execution loops | pass | `app/orchestrator/trading_loop.py`, `app/execution/backtest_engine.py` |
| no unvalidated free-text on critical execution path | pass | typed signal/risk/order models and guarded execution path |
| `CONFIG_SCHEMA.json` exists | pass | root file present, tested |
| `DECISION_SCHEMA.json` exists | pass | root file present, tested |
| schemas are meaningfully bound | partial | settings and `DecisionRecord` are typed; no universal runtime schema-loader exists yet |
| Telegram-first operator surface exists | pass | `app/messaging/telegram_bot.py` |
| voice/persona/avatar stay disabled | pass | `app/messaging/*` and `app/persona/*` are disabled stubs only |

## Canonical Module Matrix

| Area | Canonical | Provisional | Superseded / Compatibility | Notes |
|---|---|---|---|---|
| `app/core/*` | `enums.py`, `settings.py`, `logging.py`, `domain/*` | none | none | settings and enums are current execution source of truth |
| `app/execution/*` | `models.py`, `paper_engine.py`, `backtest_engine.py` | `DecisionRecord` binding is not yet universal | none | paper and backtest paths are validated and non-live by default |
| `app/messaging/*` | `telegram_bot.py` | `persona_service.py`, `text_to_speech_interface.py`, `speech_to_text_interface.py`, `avatar_event_interface.py` | none | Telegram is operator-first; persona/voice/avatar are disabled only |
| `app/research/*` | `execution_handoff.py`, `distribution.py`, `operational_readiness.py`, `artifact_lifecycle.py`, `briefs.py`, `signals.py`, `watchlists.py`, `evaluation.py`, `training.py`, `tuning.py`, `shadow.py`, `distillation.py`, `upgrade_cycle.py`, `inference_profile.py`, `active_route.py`, `route_runner.py`, `abc_result.py` | `datasets.py`, `consumer_collection.py` | `consumer_collection.py` is compatibility-only beside canonical handoff/distribution path | read-only operational stack is anchored in `operational_readiness.py` and `artifact_lifecycle.py` |
| `app/agents/mcp_server.py` | canonical MCP registration and write guard | decision-journal/loop-cycle surfaces need later consolidation review | `get_operational_escalation_summary` | workspace confinement and write audit remain canonical |
| `app/cli/main.py` | canonical CLI registration and research inventory helper | registered-but-unlocked research commands | `research governance-summary` | CLI is authoritative for registered names, but not every registered command is part of the locked final set |

## Canonical Research CLI Surfaces

### Locked Final Commands

`signal-handoff`
`handoff-acknowledge`
`handoff-collector-summary`
`readiness-summary`
`provider-health`
`drift-summary`
`gate-summary`
`remediation-recommendations`
`artifact-inventory`
`artifact-rotate`
`artifact-retention`
`cleanup-eligibility-summary`
`protected-artifact-summary`
`review-required-summary`
`escalation-summary`
`blocking-summary`
`operator-action-summary`
`action-queue-summary`
`blocking-actions`
`prioritized-actions`
`review-required-actions`
`decision-pack-summary`
`operator-runbook`
`runbook-summary`
`runbook-next-steps`
`review-journal-append`
`review-journal-summary`
`resolution-summary`

### Aliases

| Alias | Canonical target | Status |
|---|---|---|
| `consumer-ack` | `handoff-acknowledge` | compatibility alias |
| `handoff-summary` | `handoff-collector-summary` | compatibility alias |
| `operator-decision-pack` | `decision-pack-summary` | compatibility alias |

### Superseded

| Name | Replacement |
|---|---|
| `governance-summary` | `review-required-summary` plus artifact retention surfaces |

### Provisional Registered Commands

These commands are currently registered and tested, but they are not part of the locked final research inventory:

`backtest-run`
`benchmark-companion`
`benchmark-companion-run`
`brief`
`check-promotion`
`dataset-export`
`decision-journal-append`
`decision-journal-summary`
`evaluate`
`evaluate-datasets`
`loop-cycle-summary`
`prepare-tuning-artifact`
`record-promotion`
`shadow-report`
`signals`
`watchlists`

## Canonical MCP Surfaces

### Canonical Read-Only Tools

`get_watchlists`
`get_research_brief`
`get_signal_candidates`
`get_narrative_clusters`
`get_signals_for_execution`
`get_distribution_classification_report`
`get_route_profile_report`
`get_inference_route_profile`
`get_active_route_status`
`get_upgrade_cycle_status`
`get_handoff_collector_summary`
`get_operational_readiness_summary`
`get_provider_health`
`get_distribution_drift`
`get_protective_gate_summary`
`get_remediation_recommendations`
`get_artifact_inventory`
`get_artifact_retention_report`
`get_cleanup_eligibility_summary`
`get_protected_artifact_summary`
`get_review_required_summary`
`get_escalation_summary`
`get_blocking_summary`
`get_operator_action_summary`
`get_action_queue_summary`
`get_blocking_actions`
`get_prioritized_actions`
`get_review_required_actions`
`get_decision_pack_summary`
`get_operator_runbook`
`get_review_journal_summary`
`get_resolution_summary`
`get_decision_journal_summary`
`get_loop_cycle_summary`

### Guarded Write Tools

`create_inference_profile`
`activate_route_profile`
`deactivate_route_profile`
`acknowledge_signal_handoff`
`append_review_journal_entry`
`append_decision_instance`

### Aliases

| Alias | Canonical target | Status |
|---|---|---|
| `get_handoff_summary` | `get_handoff_collector_summary` | compatibility alias |
| `get_operator_decision_pack` | `get_decision_pack_summary` | compatibility alias |

### Superseded

| Name | Replacement |
|---|---|
| `get_operational_escalation_summary` | `get_escalation_summary` |

## Guarded-Write vs Read-Only

| Surface | Classification | Notes |
|---|---|---|
| research readiness/governance/runbook surfaces | read-only | `execution_enabled=False`, `write_back_allowed=False` |
| MCP readiness/governance/runbook surfaces | read-only | workspace-confined path resolution, no core-state mutation |
| handoff acknowledgement | guarded-write | audit-only, artifacts-confined, write-audited |
| review journal append | guarded-write | append-only, no core-state mutation |
| decision journal append | guarded-write but provisional | append-only, currently anchored to `app/decisions/journal.py` |
| route activation/profile writes | guarded-write | artifacts-confined, no trading execution |

## Provisional and Unreviewed Future Work

| Path / Surface | Classification | Reason |
|---|---|---|
| `app/decisions/journal.py` | provisional | active and tested, but not yet consolidated to the stricter `DecisionRecord` runtime contract |
| `research decision-journal-append` / `research decision-journal-summary` | provisional | depend on provisional journal path |
| `get_decision_journal_summary` / `append_decision_instance` | provisional | same reason as above |
| `research loop-cycle-summary` / `get_loop_cycle_summary` | provisional | operator-facing audit surface exists, but not part of locked final CLI inventory |
| `app/persona/*` | unreviewed future work | duplicate disabled interface family beside `app/messaging/*` |
| `app/messaging/persona_service.py`, `text_to_speech_interface.py`, `speech_to_text_interface.py`, `avatar_event_interface.py` | provisional | safe disabled interfaces, not part of critical execution core |
| `app/research/consumer_collection.py` | compatibility / superseded | current runtime uses `execution_handoff.py` plus `distribution.py` for collector summaries |

## Recognized Inconsistencies

1. `docs/kai_identity.md` had stale counts before rebaseline; it now points to this matrix.
2. `TASKLIST.md` is historical and contains older counts; this matrix supersedes count drift.
3. The locked CLI final inventory contains 28 commands, while 47 research commands are actually registered.
4. `DecisionRecord` exists in `app/execution/models.py`, but decision-journal CLI/MCP surfaces still use `app/decisions/journal.py`.
5. `CONFIG_SCHEMA.json` and `DECISION_SCHEMA.json` are present and tested, but runtime binding is still partial rather than universal.
6. There are two disabled persona/voice/avatar interface families: `app/messaging/*` and `app/persona/*`.
7. `app/research/consumer_collection.py` still exists for compatibility/tests even though the active collector path is `distribution.py`.

## Rebaseline Outcome

- Canonical baseline is stable for core settings, paper/backtest execution, Telegram operator surface, readiness/governance/runbook stack, and MCP write guards.
- Current repo is safe by default: non-live modes first, guarded writes only, no unvalidated free-text execution path.
- Future work must classify itself explicitly as `canonical`, `provisional`, or `unreviewed future work` against this matrix before implementation.
