# SPRINT_TRACEABILITY.md

## Kontext

Sprints 1–8 haben individuelle Git-Commits (fee38ab..2ea2139).
Sprints 9–36 landeten als einzelner Catch-Up-Commit in **`6cc3a79`**
("Sprint 9-36 catch-up — full platform foundation to signal execution").

**Warum ein Commit?** Die Sprints wurden iterativ entwickelt, aber nicht
individuell committed (Git-Hygiene-Lücke, im Commit-Message dokumentiert).

**Was dieses Dokument leistet:**
- Sprint-zu-Modul-Mapping (virtual traceability)
- Review-Einstiegspunkt pro Sprint
- Basis für `git tag sprint/NN` Navigation

**Individuelle Commits existieren ab Sprint 37** (e7b89e0 ff.).

---

## Sprints 1–8 — individuelle Commits

| Sprint | Commit | Thema |
|---|---|---|
| 1 | mehrere | Foundation & Contracts — 398 Tests |
| 2 | mehrere | Provider Consolidation — OpenAI + Claude + Gemini |
| 3 | mehrere | Alerting — Telegram, Email, ThresholdEngine |
| 4 | 9600de0..8bd8d9d | Research & Signals — Watchlists, Signal Candidates |
| 5 | 0f8ac3a..197082e | Corpus Safety + Eval Baseline — I-27–I-33 |
| 6 | 276687e..8cd81b2 | Dataset + Evaluation Harness — 547 Tests |
| 7 | fee38ab | Companion Benchmark + Promotion Gate — 561 Tests |
| 8 | 2ea2139 | Controlled Companion Inference + Tuning — 571 Tests |

---

## Sprints 9–36 — alle in Commit `6cc3a79`

> Alle Tags `sprint/09`–`sprint/36` zeigen auf `6cc3a79`.
> Traceability erfolgt über Modul-Grenzen, nicht über Commit-Grenzen.

---

### Sprint 9 — Promotion Audit Hardening

**Tests:** 598 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P10, I-43–I-50

**Deliverables:**
- `gates_summary` im Promotion-Gate (validate_promotion erweitert)
- Artifact-Linkage-Validation: Setzt Promotion-Artefaktpfad voraus
- G6-Gate: comparison_report_path Pflichtfeld in Evaluation-Record
- `record-promotion --comparison` CLI

**Key Files (geändert/erweitert):**
```
app/research/evaluation.py          # EvaluationComparisonReport, RegressionSummary
```

**Tests:**
```
tests/unit/test_e2e_system.py       # E2E-Integration Sprint-9-Abdeckung
```

---

### Sprint 10 — Companion Shadow Run

**Tests:** 625 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P11, I-51–I-57

**Deliverables:**
- `shadow.py`: ShadowRunResult, run_shadow_analysis(), shadow-run CLI
- Shadow-Ergebnisse nie in ANALYZED-Pfad geschrieben
- Schreibschutz: shadow_result = read-only comparison artifact

**Key Files (neu):**
```
app/research/shadow.py              # ShadowRunResult, run_shadow_analysis()
```

**Tests:**
```
tests/unit/test_shadow.py
tests/unit/test_shadow_run.py
tests/create_pending_doc.py         # Hilfs-Script für shadow-run Integration
```

---

### Sprint 11 — Distillation Harness

**Tests:** 642 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P12, I-58–I-64

**Deliverables:**
- `distillation.py`: DistillationDataset, prepare_distillation_dataset()
- `distillation-check` CLI: prüft Datensatz-Qualität vor Training
- Teacher-only I-27 Constraint bleibt aktiv

**Key Files (neu):**
```
app/research/distillation.py        # DistillationDataset, quality gates
```

**Tests:**
```
tests/unit/test_distillation.py
tests/setup_sprint11_data.py        # Hilfs-Script für Distillation-Tests
```

---

### Sprint 12 — Training Job Record

**Tests:** 667 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P13, I-65–I-70

**Deliverables:**
- `training.py`: TrainingJobRecord (frozen), prepare_training_job(), save_training_job()
- `prepare-training-job` CLI
- Shadow-Schema I-69: TrainingJobRecord trägt shadow_run_id
- JSONL-Persistierung: artifacts/training_jobs/

**Key Files (neu):**
```
app/research/training.py            # TrainingJobRecord, prepare_training_job()
```

**Tests:**
```
tests/unit/test_training.py
tests/generate_candidate_jsonl.py   # Hilfs-Script für Kandidaten-JSONL
```

---

### Sprint 13 — Evaluation Comparison + Regression Guard + Upgrade Cycle

**Tests:** 701 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P14, I-71–I-79

**Deliverables:**
- `EvaluationComparisonReport` in evaluation.py (kanonisch, kein comparison.py)
- `compare-evaluations --out` CLI, `RegressionSummary`
- `record-promotion --comparison` (I-72, Warning bei has_regression)
- `upgrade_cycle.py` + `upgrade-cycle-status` CLI (I-75–I-79)

**Key Files (neu/erweitert):**
```
app/research/upgrade_cycle.py       # UpgradeCycle, upgrade-cycle-status
app/research/evaluation.py         # EvaluationComparisonReport, RegressionSummary
```

**Tests:**
```
tests/unit/test_upgrade_cycle.py
```

---

### Sprint 14 — Controlled A/B/C Inference Profiles + Signal Distribution

**Tests:** 743 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P15, I-80–I-89

**Deliverables:**
- `InferenceRouteProfile` (declarative, inert): primary_only / primary_with_shadow / ...
- `ABCInferenceEnvelope`: document_id + A (primary) + B (shadow) + C (control) + summary
- `PathResultEnvelope`, `PathComparisonSummary`, `DistributionMetadata`
- CLI: `create-inference-profile`, `abc-run`, `route-profile`

**Key Files (neu):**
```
app/research/inference_profile.py   # InferenceRouteProfile, ABCInferenceEnvelope
app/research/abc_result.py          # ABCResult, PathResultEnvelope
app/research/distribution.py       # DistributionMetadata, SignalDistribution
```

**Tests:**
```
tests/unit/test_inference_profile.py
tests/unit/test_abc_result.py
tests/unit/test_distribution.py
```

---

### Sprint 14C — Runtime Route Activation

**Tests:** 801 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P15, I-90–I-93

**Deliverables:**
- `active_route.py`: `ActiveRouteState`, `activate_route_profile()`, `load_active_route_state()`
- State-Datei: `artifacts/active_route_profile.json` (NEVER .env)
- CLI: `route-activate`, `route-status`, `route-deactivate`

**Key Files (neu):**
```
app/research/active_route.py        # ActiveRouteState, activate/deactivate
app/research/route_runner.py        # RouteRunner, run_with_active_route()
```

**Tests:**
```
tests/unit/test_active_route.py
tests/unit/test_route_runner.py
```

---

### Sprint 15 — Newsdata.io Integration

**Tests:** 801 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P16, I-94–I-100

**Deliverables:**
- `NewsdataClient` (httpx, /api/1/latest), `NewsdataArticle` (frozen dataclass)
- `NewsdataAdapter` (BaseSourceAdapter, SourceType.NEWS_API)
- `ProviderSettings.newsdata_api_key`

**Key Files (neu):**
```
app/integrations/newsdata/__init__.py
app/integrations/newsdata/client.py     # NewsdataClient, NewsdataArticle
app/integrations/newsdata/adapter.py    # NewsdataAdapter
```

**Tests:**
```
tests/unit/test_newsdata_client.py
tests/unit/test_newsdata_adapter.py
```

---

### Sprint 16 — Immutable Signal Handoff Layer

**Tests:** ~820 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P17, I-101–I-108

**Deliverables:**
- `SignalHandoff` (frozen=True): evidence ≤ 500 chars, no recommended_next_step
- `create_signal_handoff()`, `save_signal_handoff()`, `save_signal_handoff_batch_jsonl()`
- `consumer_note` always present, `provenance_complete` flag

**Key Files (neu):**
```
app/research/execution_handoff.py   # SignalHandoff, create/save_signal_handoff()
```

**Tests:**
```
tests/unit/test_execution_handoff.py
```

---

### Sprints 17–20 — MCP Server + Operational Surfaces

**Tests:** ~900 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P18–P21, I-109–I-150

**Deliverables:**
- `app/agents/mcp_server.py`: MCP-Server mit 40+ read-only Audit-Surfaces
- `consumer_collection.py`: ConsumerCollection, ConsumerRecord
- `operational_readiness.py`: OperationalReadinessReport, readiness gates
- Telegram Bot: kanonische Command-Surface (15 Kommandos)

**Key Files (neu):**
```
app/agents/mcp_server.py                # MCP-Server, Tool-Registry
app/research/consumer_collection.py    # ConsumerCollection, ConsumerRecord
app/research/operational_readiness.py  # OperationalReadinessReport
app/messaging/telegram_bot.py          # TelegramBot, Command-Surface
app/messaging/persona_service.py
app/messaging/avatar_event_interface.py
```

**Tests:**
```
tests/unit/test_mcp_server.py           # (später migriert zu tests/unit/mcp/)
tests/unit/test_consumer_collection.py
tests/unit/test_operational_readiness.py
tests/unit/test_telegram_bot.py
```

---

### Sprints 21–24 — Artifact Lifecycle + Operational Escalation

**Tests:** ~1050 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P22–P25, I-151–I-190

**Deliverables:**
- `artifact_lifecycle.py`: ArtifactInventory, rotation gates, retention policy
- Escalation-Surface: EscalationSummary, BlockingSummary
- Persona/Avatar-Service (experimental, UI-Vorbereitung)
- Narrative Clustering: NarrativeCluster, cluster_by_topic()

**Key Files (neu):**
```
app/research/artifact_lifecycle.py      # ArtifactInventory, rotation/retention
app/analysis/narratives/__init__.py
app/analysis/narratives/cluster.py     # NarrativeCluster, cluster_by_topic()
app/persona/__init__.py
app/persona/avatar_events.py
app/persona/persona_service.py
app/persona/speech_to_text.py
app/persona/text_to_speech.py
```

**Tests:**
```
tests/unit/test_artifact_lifecycle.py
tests/unit/test_operational_escalation.py
tests/unit/test_narrative_clustering.py
tests/unit/test_persona.py
```

---

### Sprints 25–30 — Governance: Review Journal, Action Queue, Decision Pack

**Tests:** ~1200 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P26–P31, I-191–I-240

**Deliverables:**
- `decisions/journal.py`: DecisionRecord, DecisionJournal, DecisionInstance
- Review Journal: operator_review_journal.jsonl, resolution tracking
- Action Queue: OperatorActionQueue, pending/acknowledged/resolved states
- Decision Pack: integrierte Governance-Surface für Operator

**Key Files (neu):**
```
app/decisions/__init__.py
app/decisions/journal.py            # DecisionRecord, DecisionJournal
```

**Tests:**
```
tests/unit/test_decision_journal.py
tests/unit/test_decision_record.py
tests/unit/test_review_journal.py
tests/unit/test_operator_action_queue.py
tests/unit/test_cli_decision_journal.py
```

---

### Sprints 31–33 — Security + ABC Inference + Schema Binding

**Tests:** ~1300 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P32–P34, I-241–I-270

**Deliverables:**
- SSRF-Schutz: `ssrf.py` — private IPs blockiert, nur http/https
- Bearer Auth: `secrets.compare_digest()` — kein Timing-Attack
- ABC-Inference: kanonische Result-Hülle (ABCInferenceEnvelope → Erweiterung Sprint 14)
- Schema-Binding: `core/schema_binding.py` — 10 Safety-Konsts, Feld-Alignment
- Runtime-Validator: Draft202012Validator + FormatChecker

**Key Files (neu):**
```
app/core/schema_binding.py          # Schema-Integrität, Safety-Consts
app/schemas/__init__.py
app/schemas/runtime_validator.py    # Draft202012Validator
```

**Tests:**
```
tests/unit/test_schema_binding.py
tests/unit/test_schema_runtime_binding.py
tests/unit/test_platform_contracts.py
```

---

### Sprints 34–35 — Risk Engine + Paper Execution + Backtest

**Tests:** ~1360 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P35–P40, I-271–I-300

**Deliverables:**
- Risk Engine: 8 Pre-Order-Gates, Kill-Switch, Position Sizing (2 % Equity-Risk)
- `PaperExecutionEngine`: never raises, Idempotenz, JSONL-Audit
- Backtest Engine: Signal→Risk→Paper Loop ohne Live-Exposure
- `ExecutionRecord` (frozen): vollständige Trade-Trace

**Key Files (neu):**
```
app/risk/__init__.py
app/risk/engine.py                  # RiskEngine, 8 gates, kill-switch
app/risk/models.py                  # RiskDecision, PositionSize, RiskLimits
app/execution/__init__.py
app/execution/models.py             # ExecutionRecord, OrderSide, ExecutionMode
app/execution/paper_engine.py      # PaperExecutionEngine, never-raise contract
app/execution/backtest_engine.py    # BacktestEngine, Signal→Risk→Paper loop
app/execution/portfolio_surface.py  # PortfolioSurface (Sprint-35-Ergänzung)
```

**Tests:**
```
tests/unit/test_risk_engine.py
tests/unit/test_paper_execution.py
tests/unit/test_backtest_engine.py
```

---

### Sprint 36 — Signal Engine + Core Orchestrator

**Tests:** 1406 | **Commit-Ref:** `6cc3a79` | **Doku-Ref:** AGENTS.md P41, I-301–I-320

**Deliverables:**
- `SignalDirection` (StrEnum), `SignalState` (StrEnum), `SignalCandidate` (frozen dataclass)
- `SignalGenerator`: 6 Filter-Gates, Confluence (max 5), SL/TP (2:1 R/R)
- `CycleStatus` (7 Werte), `LoopCycle` (frozen, Traceability-IDs)
- `TradingLoop.run_cycle()`: 7-Schritt-Pipeline, never raises, JSONL-Audit
- direction→side-Mapping (long→buy, short→sell)

**Key Files (neu):**
```
app/signals/__init__.py
app/signals/models.py               # SignalDirection, SignalState, SignalCandidate
app/signals/generator.py            # SignalGenerator, 6 filter gates
app/orchestrator/__init__.py
app/orchestrator/models.py          # CycleStatus, LoopCycle
app/orchestrator/trading_loop.py    # TradingLoop.run_cycle(), JSONL audit
```

**Tests:**
```
tests/unit/test_signals.py
tests/unit/test_trading_loop.py
tests/unit/test_mcp_sprint36.py
```

---

### Auxiliary Modules (Sprint-übergreifend)

Diese Module wurden als Teil von `6cc3a79` hinzugefügt; ihre Sprint-Zuordnung
ist heuristisch (keine Einzelcommits).

| Modul | Heuristischer Sprint | Funktion |
|---|---|---|
| `app/market_data/` | Sprint 17–20 | CoinGecko Spot-Adapter, Mock-Adapter, Freshness-Gate |
| `app/messaging/` | Sprint 17–20 | Telegram Bot, Persona-Interface, TTS/STT |
| `app/persona/` | Sprint 21–24 | Avatar Events, Persona Service |
| `app/analysis/narratives/` | Sprint 21–24 | NarrativeCluster, Topic-Clustering |
| `app/alerts/audit.py` | Sprint 18–20 | Alert-Audit-JSONL |

---

## Verwendung der Tags

```bash
# Alle Sprint-Tags anzeigen
git tag -l 'sprint/*'

# Annotation eines Tags lesen (Deliverables)
git show sprint/36

# Diff zwischen diesem Commit und Sprint 37 (erster Einzelcommit)
git diff 6cc3a79 e7b89e0 --stat

# Welche Dateien gehören zu Sprint 14 (nach Modul)?
git show sprint/14   # zeigt Annotation mit Key Files
```

---

## Traceability-Qualität

| Ebene | Verfügbar |
|---|---|
| Sprint → Modul | ✅ vollständig (dieses Dokument) |
| Sprint → Test-Datei | ✅ vollständig (dieses Dokument) |
| Sprint → Git-Diff | ⚠️ nur als Ganzes (`6cc3a79`), nicht individuell |
| Sprint → Einzelcommit | ❌ nicht vorhanden (Sprints 9–36) |
| Sprint-Entscheidungen | ✅ AGENTS.md P10–P41, DECISION_LOG.md |

**Ab Sprint 37:** individuelle Commits, vollständige Traceability.
