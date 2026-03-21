# AGENTS.md - app/research/

> Module-level agent contract for the Research and Signal Generation layer.
> All agents must read this before modifying any file in `app/research/`.

---

## Purpose

`app/research/` is the research output layer of KAI.

It consumes analyzed `CanonicalDocument` objects from storage and produces:
- `ResearchBrief` - aggregated snapshot for a named cluster (watchlist, asset, topic)
- `SignalCandidate` - filtered high-priority research signal for human review
- `WatchlistRegistry` - access layer for `monitor/watchlists.yml`
- JSONL dataset exports for teacher, benchmark, and rule-baseline corpora
- Offline evaluation and benchmark artifacts for companion readiness
- Tuning manifests and promotion records for controlled companion rollout
- Training job records and post-training evaluation links for controlled external training

This layer is read-only relative to the DB. No document writes. No score mutations.
No direct DB access. All input arrives as `list[CanonicalDocument]`.

---

## Module Files

| File | Responsibility |
|---|---|
| `__init__.py` | Public API - re-exports key classes |
| `watchlists.py` | `WatchlistRegistry` - tag-based watchlist access from `monitor/watchlists.yml` |
| `briefs.py` | `ResearchBrief`, `ResearchBriefBuilder` - cluster-level research snapshot |
| `signals.py` | `SignalCandidate`, `extract_signal_candidates()` - high-priority signals extraction |
| `datasets.py` | `export_training_data()` - JSONL export for teacher, benchmark, and baseline corpora |
| `evaluation.py` | `compare_outputs()`, `compare_datasets()`, `compare_metrics()`, `load_jsonl()`, `load_saved_evaluation_report()`, `compare_evaluation_reports()`, `save_jsonl_rows()`, `save_evaluation_report()`, `save_evaluation_comparison_report()`, `save_benchmark_artifact()` - offline comparison and benchmark helpers |
| `tuning.py` | `TuningArtifact`, `PromotionRecord`, `save_tuning_artifact()`, `save_promotion_record()` - file-based tuning and promotion artifacts |
| `training.py` | `TrainingJobRecord`, `PostTrainingEvaluationSpec`, `save_training_job_record()`, `save_post_training_eval_spec()` - file-based training intent and post-training audit linkage |
| `upgrade_cycle.py` | `UpgradeCycleReport`, `derive_cycle_status()`, `build_upgrade_cycle_report()`, `save_upgrade_cycle_report()` - file-based upgrade-cycle status summary and orchestration |
| `execution_handoff.py` | `SignalHandoff`, `HandoffAcknowledgement`, route-aware delivery classification, `create_signal_handoff()`, `create_handoff_acknowledgement()`, `load_signal_handoffs()`, `load_handoff_acknowledgements()`, `save_signal_handoff()`, `save_signal_handoff_batch_jsonl()` - canonical immutable external signal-consumption and audit-only acknowledgement artifacts |
| `distribution.py` | `RouteProfileReport`, `ExecutionHandoffReport`, `DistributionClassificationReport`, `HandoffCollectorSummaryReport`, `build_route_profile()`, `build_execution_handoff_report()`, `build_distribution_classification_report()`, `build_handoff_collector_summary()`, `save_route_profile()`, `save_execution_handoff_report()`, `save_distribution_classification_report()`, `save_handoff_collector_summary()` - read-only distribution, batch handoff, and collector-summary reports built from canonical `SignalHandoff` artifacts and persisted ABC audit envelopes |
| `operational_readiness.py` | `OperationalReadinessReport`, `ReadinessIssue`, `RouteReadinessSummary`, `AlertDispatchSummary`, `ProviderHealthSummary`, `DistributionDriftSummary`, `ProtectiveGateSummary`, `ProtectiveGateItem`, `OperationalEscalationItem`, `OperationalEscalationSummary`, `BlockingSummary`, `OperatorActionSummary`, `ActionQueueItem`, `ActionQueueSummary`, `BlockingActionsSummary`, `PrioritizedActionsSummary`, `ReviewRequiredActionsSummary`, `OperatorDecisionPack`, `RunbookStep`, `OperatorRunbookSummary`, `OperationalArtifactRefs`, `build_operational_readiness_report()`, `build_operational_escalation_summary()`, `build_blocking_summary()`, `build_operator_action_summary()`, `build_action_queue_summary()`, `build_blocking_actions()`, `build_prioritized_actions()`, `build_review_required_actions()`, `build_operator_decision_pack()`, `build_operator_runbook()`, `save_operational_readiness_report()`, `save_operational_escalation_summary()`, `save_operator_decision_pack()`, `save_operator_runbook()` - read-only operational readiness, escalation, operator-action queue, decision-pack, and operator-runbook surfaces derived from existing handoff, acknowledgement, route-state, ABC-envelope, alert-audit, and governance artifacts only |
| `artifact_lifecycle.py` | `ArtifactEntry`, `ArtifactInventoryReport`, `ArtifactRotationSummary`, `ArtifactRetentionEntry`, `ArtifactRetentionReport`, `ArtifactCleanupEligibilitySummary`, `ProtectedArtifactSummary`, `ReviewRequiredArtifactSummary`, `build_artifact_inventory()`, `rotate_stale_artifacts()`, `classify_artifact_retention()`, `build_retention_report()`, `build_cleanup_eligibility_summary()`, `build_protected_artifact_summary()`, `build_review_required_summary()`, `save_artifact_inventory()`, `save_artifact_rotation_summary()`, `save_retention_report()`, `save_review_required_summary()` - canonical artifact lifecycle layer for inventory, dry-run-first archival, protected artifact flags, cleanup eligibility, operator review visibility, and protected handoff audit-trail classification derived from one retention stack only |

---

## Key Contracts

### WatchlistRegistry

- Loaded from `monitor/watchlists.yml` via `WatchlistRegistry.from_monitor_dir(path)`
- Supports four watchlist types: `"assets"`, `"persons"`, `"topics"`, `"sources"`
- `get_watchlist(tag, item_type="assets")` -> `list[str]`
- `get_all_watchlists(item_type="assets")` -> `Mapping[str, list[str]]`
- `filter_documents(documents, tag, item_type="assets")` -> `list[CanonicalDocument]`
- `get_symbols_for_category(category)` -> `list[str]` (assets only)

### ResearchBrief

- Built via `ResearchBriefBuilder(cluster_name).build(documents)`
- Input: `list[CanonicalDocument]`; only `is_analyzed=True` docs are used
- Actionable threshold: `priority_score >= 8`
- Output fields:
  - `cluster_name`, `title`, `summary`
  - `document_count`, `average_priority`, `overall_sentiment`
  - `top_documents`
  - `top_assets`
  - `top_entities`
  - `top_actionable_signals`
  - `key_documents`
- Serialization: `.to_markdown()`, `.to_json_dict()`

### SignalCandidate

- Produced via `extract_signal_candidates(documents, min_priority=8, watchlist_boosts=None)`
- Only documents with `is_analyzed=True` and `effective_priority >= min_priority` are included
- `direction_hint` values are always `"bullish"`, `"bearish"`, or `"neutral"`
- `document_id` is required for traceability

### Dataset and Evaluation Helpers

- `export_training_data(documents, output_path, teacher_only=False)` reuses persisted document fields only
- Teacher eligibility is determined only by `analysis_source=EXTERNAL_LLM`
- `compare_datasets()` matches rows only by `metadata.document_id`
- `save_evaluation_report()` writes a structured JSON report for offline review
- `load_saved_evaluation_report()` fail-closed validates persisted evaluation report structure
- `compare_evaluation_reports()` compares two saved `evaluation_report.json` files only; no DB reads, no model calls
- `save_evaluation_comparison_report()` writes an audit-only baseline-vs-candidate comparison artifact
- `save_benchmark_artifact()` writes a small manifest for future companion tuning artifacts
- `save_tuning_artifact()` writes a training-manifest JSON only; it does not train a model
- `save_training_job_record()` writes a pre-training job record only; it does not train a model
- `save_post_training_eval_spec()` links a finished training run to its evaluation artifact
- `save_promotion_record()` writes an immutable audit record only; it does not change routing
- `build_upgrade_cycle_report()` reads existing artifacts only; it does not run training, evaluation, or promotion

### Distribution and Execution Handoff

- `build_route_profile()` summarizes analyzed documents only; no DB writes, no routing changes
- `create_signal_handoff(candidate, document=None)` is the canonical per-signal external artifact builder; it stays immutable, excludes `recommended_next_step`, and classifies delivery from `route_path`
- `create_handoff_acknowledgement(handoff, consumer_agent_id, notes="")` is audit-only, append-only, and allowed only for `consumer_visibility="visible"` handoffs
- `build_execution_handoff_report(signals, documents)` composes a read-only batch handoff from existing `SignalCandidate` outputs via canonical `SignalHandoff` rows
- `build_distribution_classification_report(signals, documents, envelopes)` reuses primary `SignalHandoff` rows plus persisted `ABCInferenceEnvelope` audit artifacts; it must not create a second signal stack
- `build_handoff_collector_summary(handoffs, acknowledgements)` correlates existing handoff artifacts with append-only acknowledgement audit rows; it must not write back, rescore, or mutate core analysis state
- `build_operational_readiness_report(...)` derives readiness, provider health, distribution drift, and protective gate recommendations only from existing audit artifacts; it must not introduce a second monitoring stack, trigger remediation, or mutate routing/core state
- `build_operational_escalation_summary(...)` projects blocking, review-required, and operator-action rows only from canonical readiness plus governance summaries; it must stay read-only and must not introduce a second gate stack
- `build_action_queue_summary(...)` projects open, blocking, prioritized, and review-required operator actions only from the canonical escalation summary; it must stay read-only and must not introduce a second escalation or queue stack
- `build_operator_decision_pack(...)` bundles canonical readiness, blocking, action-queue, and governance review summaries only; it must not introduce secondary overview/focus/subsystem side-surfaces
- `build_operator_runbook(...)` derives ordered next steps and validated command references only from the canonical decision pack; it must stay read-only and must not reference superseded CLI commands
- `build_retention_report(...)` classifies artifacts only; it must stay read-only, keep `delete_eligible=False`, and feed cleanup/protected/review summaries without a second lifecycle stack
- `rotate_stale_artifacts(...)` may archive only `rotatable=True` artifacts and must skip protected artifacts fail-closed
- `save_execution_handoff_report()` writes a JSON artifact only; it does not trigger trading execution, write-back, or routing changes

---

## Immutable Rules

| Rule | Detail |
|---|---|
| R-1 | No DB writes in this layer |
| R-2 | `apply_to_document()` is the only score mutation point and must not be called here |
| R-3 | `direction_hint` is always `"bullish"`, `"bearish"`, or `"neutral"` |
| R-4 | `SignalCandidate` is a research artifact, never an execution order |
| R-5 | Watchlist boosts are research hints only and never override stored scores |
| R-6 | `WatchlistRegistry` reads `monitor/watchlists.yml`, never a DB table |
| R-7 | `extract_signal_candidates()` filters on `is_analyzed=True` before priority checks |
| R-8 | Teacher-only dataset export uses `analysis_source` only, never `provider` or metadata traces |
| R-9 | Offline evaluation and benchmark helpers must stay network-free and DB-free |

---

## What Agents May Do Here

- Extend serialization formats for research outputs
- Add small lookup helpers in `WatchlistRegistry`
- Extend offline evaluation and benchmark reporting without changing the dataset schema
- Write or extend tests in `tests/unit/test_research_*.py`, `tests/unit/test_datasets.py`, and `tests/unit/test_evaluation.py`

## What Agents Must NOT Do Here

- Write to the DB or call repository write methods
- Mutate document scores or call `apply_to_document()`
- Add provider-specific network logic to this layer
- Use `provider` or `ensemble_chain` as teacher-eligibility criteria
- Introduce a second dataset schema parallel to the existing JSONL contract

---

## Tests

```bash
pytest tests/unit/test_research_signals.py -v
pytest tests/unit/test_research_briefs.py -v
pytest tests/unit/test_research_watchlists.py -v
pytest tests/unit/test_datasets.py -v
pytest tests/unit/test_evaluation.py -v
pytest tests/unit/test_training.py -v
```
