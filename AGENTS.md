# AGENTS.md — KAI Platform

## Current State (2026-03-23)

| Field | Value |
|---|---|
| current_phase | `PHASE 4 (active)` |
| current_sprint | `PH4F_RULE_INPUT_COMPLETENESS_AUDIT (ready to close)` |
| next_required_step | `PH4F_RESULTS_REVIEW_AND_PH4G_SELECTION` |
| ph4f_status_canonical | `active (execution complete — D-69) — closeout review pending` |
| ph4g_status_canonical | `candidate only — PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (not active yet)` |
| phase4_status_canonical | `active — PH4F ready to close; PH4G selection pending` |
| state_note | `canonical rows at top are authoritative for current gate state` |
| ph4a_status | `closed (D-53) — immutable baseline anchor (§67)` |
| ph4b_status | `closed (D-62) — paired_count=69; root cause: keyword coverage blindness` |
| ph4c_status | `closed — rule-keyword gap audit; top-3 gaps: macro, regulatory, AI` |
| ph4d_status | `closed (D-68) — 56 keywords added; zero-hit 42%->37.7%; §71 frozen anchor` |
| ph4e_status | `closed (D-70) — relevance 41.2% of gap; root cause: defaults by design` |
| ph4f_status | `closed (D-68) — actionable 69/69; market_scope 69/69; tags 69/69; relevance floor 56/69; §74 frozen anchor` |
| ph4g_status | `active (definition — D-69) — fallback-path enrichment; execution start pending` |
| baseline | `1519 passed, ruff clean` |
| cli_canonical_count | 53 |
| provisional_cli_count | 0 |
| phase3_status | `closed (2026-03-22) — GO` |
| phase4_status | `active — PH4G fallback-path enrichment (definition D-69)` |
---

> **Verbindliches Betriebsdokument für alle Coding-Agenten.**
> Claude Code · OpenAI Codex · Google Antigravity
> Dieses Dokument lesen, bevor eine einzige Zeile Code angefasst wird.

---

## 1. Platform Identity

**Name**: KAI (AI Analyst Trading Bot)
**Repo**: https://github.com/CP-Srinivasa/KAI
**Mission**: Agent-capable development & operations platform for AI-driven market intelligence.
**Motto**: Simple but Powerful

Dies ist **keine normale App** — es ist eine spec-driven, modulare, maschinenlesbare Plattform,
die von Menschen und AI-Agenten gemeinsam entwickelt und betrieben wird.

---

## 2. Provider-Entscheidungen (fest)

| # | Bereich | Entscheidung |
|---|---|---|
| A | Repo | GitHub |
| B | Sprache | Python 3.12+ |
| C | API Framework | FastAPI |
| D | Datenbank | PostgreSQL (TimescaleDB optional) |
| E | AI-Provider | OpenAI/ChatGPT (primär), Anthropic (optional), Google Antigravity (optional) |

---

## 3. Rollen-Zuordnung

| Agent | Rolle | Zuständig für |
|---|---|---|
| **OpenAI Codex** | Implementierer | Neue Features nach Spec, Unit Tests, Refactoring (1 Modul), CI-Fixes, Lint |
| **Claude Code** | Architekt | Neue Module, Interface-Änderungen, Multi-File, Spec → Code, AGENTS.md pflegen |
| **Google Antigravity** | Orchestrator | Workflows, MCP/Skills, Build/Deploy, multi-Agent-Koordination |

**Vollständiges Rollenmodell → [AGENT_ROLES.md](./AGENT_ROLES.md)**

---

## 4. Task-Typen und Zuständigkeit

| Task-Typ | Zuständiger Agent |
|---|---|
| Feature implementieren (Spec vorhanden, 1 Modul) | Codex |
| Unit Test schreiben oder reparieren | Codex |
| Refactoring innerhalb eines Moduls (kein Interface-Bruch) | Codex |
| CI-Pipeline-Fehler beheben | Codex |
| Lint / Typ-Korrekturen | Codex |
| Neues Modul einführen | Claude Code |
| Interface oder Domain-Modell ändern | Claude Code |
| Multi-File-Änderung (2+ Module) | Claude Code |
| Spec schreiben oder AGENTS.md aktualisieren | Claude Code |
| Agentischen Workflow definieren | Antigravity |
| MCP-Tool oder Skill definieren | Antigravity |
| Build / Deploy / CI-Pipeline aufbauen | Antigravity |
| Unklare Anforderung | → Operator entscheidet |

---

## 5. Pflicht: Kein Agent ändert Architektur ohne Spec

> **RULE-ZERO**: Kein Agent darf ein neues Modul, Interface, Domain-Modell oder
> eine Provider-Abstraktion einführen oder ändern, ohne dass eine Spec existiert.

Eine **Spec** ist eine der folgenden:
- Ein Abschnitt in `PROJECT_SPEC.md`
- Ein `AGENTS.md` im betreffenden Modul
- Eine explizite schriftliche Anweisung des Operators in der aktuellen Session

**Was erlaubt ist ohne Spec:**
- Implementierung innerhalb bestehender Interfaces
- Tests schreiben
- Lint/CI-Fixes
- Dokumentation verbessern

**Was NICHT erlaubt ist ohne Spec:**
- Neue `app/<modul>/` Verzeichnisse anlegen
- Bestehende Pydantic-Modelle umbenennen oder Felder entfernen
- Provider-Abstraktionen ändern
- `app/core/` anfassen
- `AGENTS.md` oder `AGENT_ROLES.md` ändern

---

## 6. Pflicht-Task-Format

Jede Aufgabe, die an einen Agenten übergeben wird, muss folgendes enthalten:

```
## Task: <kurzer Titel>

**Agent**: [Codex | Claude Code | Antigravity]
**Phase**: [P1 | P2 | P3 | ...]
**Modul**: app/<modul>/
**Typ**: [feature | test | refactor | fix | spec | deploy]

### Beschreibung
<Was soll gebaut/geändert werden — in 2–5 Sätzen>

### Spec-Referenz
<Abschnitt in PROJECT_SPEC.md oder AGENTS.md, der diese Aufgabe beschreibt>

### Akzeptanzkriterien
- [ ] <messbares Kriterium 1>
- [ ] <messbares Kriterium 2>

### Constraints
- <Was NICHT geändert werden darf>
- <Welche bestehenden Interfaces stabil bleiben müssen>
```

---

## 7. Pflicht-Output-Format (pro Agent)

### OpenAI Codex

```
## Codex Report
- Task: <Titel>
- Dateien erstellt: [Liste oder "keine"]
- Dateien geändert: [Liste]
- Annahmen: [Liste]
- TODOs: [Liste oder "keine"]
- Test-Befehl: pytest tests/unit/<datei>.py
- ruff check: ✅ / ❌ (Fehler angeben)
```

### Claude Code

```
## Claude Code Report
- Task: <Titel>
- Architekturentscheidung: <Was wurde wie entschieden und warum>
- Dateien erstellt: [Liste]
- Dateien geändert: [Liste]
- Interface-Änderungen: [Ja/Nein — wenn Ja: was genau]
- AGENTS.md aktualisiert: [Ja/Nein — welche Dateien]
- Annahmen: [Liste]
- TODOs: [Liste]
- Test-Befehl: pytest tests/unit/<datei>.py
```

### Google Antigravity

```
## Antigravity Report
- Task: <Titel>
- Workflow-Schritte: [geordnete Liste]
- Eingesetzte Agenten: [Codex | Claude Code | beide]
- MCP/Skills aktiviert: [Liste oder "keine"]
- Ergebnis: <Was wurde produziert>
- Validierung: <Wie wurde das Ergebnis geprüft>
- TODOs: [Liste]
```

---

## 8. Quality Gates (Pflicht)

Jede Aufgabe gilt als **nicht abgeschlossen**, solange einer dieser Gates nicht erfüllt ist:

| Gate | Prüfung |
|---|---|
| **Tests** | `pytest` läuft durch ohne Fehler |
| **Lint** | `ruff check .` ohne Fehler |
| **Typisierung** | Keine `Any`, kein nacktes `dict` in öffentlichen Interfaces |
| **Secrets** | Keine Credentials oder API-Keys im Code |
| **Spec-Konformität** | Implementierung entspricht der referenzierten Spec |
| **AGENTS.md** | Aktualisiert, wenn Interface oder Modul geändert |
| **Failure-Handling** | Fehlerszenarien explizit behandelt (kein `pass` in `except`) |

---

## 9. Modul-Map

```
app/
  core/              → settings, logging, domain types, enums, errors      [AGENTS.md ✅]
    domain/          → CanonicalDocument, AnalysisResult, QuerySpec, events
  ingestion/         → source adapters, resolvers, classifiers, RSS        [AGENTS.md ✅]
    base/            → FetchResult, SourceMetadata, BaseSourceAdapter (ABC)
    rss/             → RSSAdapter, RSSService, collect_rss_feed
    schedulers/      → RSSScheduler (periodic fetch)
    resolvers/       → YouTube, Podcast resolvers
  normalization/     → content cleaning, text normalization
  enrichment/        → deduplication, entity matching                      [AGENTS.md ✅]
    deduplication/   → Deduplicator (batch hash dedup)
    entities/        → hits_to_entity_mentions()
  analysis/          → Analysekern: keyword engine, query DSL, pipeline    [AGENTS.md ✅]
    base/            → LLMAnalysisOutput, BaseAnalysisProvider (ABC)
    keywords/        → KeywordEngine, WatchlistEntry, EntityAlias
    query/           → QueryExecutor (in-memory QuerySpec filter)
    rules/           → RuleAnalyzer, KeywordMatcher, AssetDetector
    historical/      → EventAnalogMatcher, HistoricalEvent (analog detection)
    providers/       → OpenAIAnalysisProvider (analysis layer, structured outputs)
    scoring.py       → compute_priority(), is_alert_worthy(), calculate_final_relevance()
    pipeline.py      → AnalysisPipeline + PipelineResult.apply_to_document()
    validation.py    → validate_llm_output(), sanitize_scores()
  integrations/      → Provider-Implementierungen (HTTP-Schicht)
    openai/          → OpenAIAnalysisProvider (integrations layer, gpt-4o)
    cryptopanic/     → CryptoPanicClient + Adapter (NEWS_API)
  api/               → FastAPI routers + shared deps                       [AGENTS.md ✅]
  cli/               → Typer commands                                      [AGENTS.md ✅]
  alerts/            → Alert System: BaseAlertChannel, Telegram, Email, ThresholdEngine, DigestCollector, AlertService [AGENTS.md ✅]
  pipeline/          → End-to-End Pipeline Service (Fetch → Persist → Analyze → Update)
    service.py       → run_rss_pipeline() — einziger Entry-Point für vollständige Pipeline
  storage/           → DB models, repositories, migrations (PostgreSQL)
    models/          → CanonicalDocumentModel, HistoricalEventModel, SourceModel
    repositories/    → DocumentRepository, EventRepository, SourceRepository
    migrations/      → 0001 sources | 0002 documents | 0003 events | 0004 score columns
    document_ingest.py → persist_fetch_result() helper (ingest + dedup + save)
monitor/             → user-editable source lists, keywords, watchlists
  historical_events.yml → 13 Seed-Events für analog matching
docker/              → Dockerfile (production), docker-compose.yml
tests/unit/          → pytest unit tests (600 passing — ruff clean)
```

---

## 10. Key Domain Models

| Model | Datei | Zweck |
|---|---|---|
| `CanonicalDocument` | `app/core/domain/document.py` | Normalisierte Content-Einheit (Haupt-Datenmodell) |
| `AnalysisResult` | `app/core/domain/document.py` | LLM/Regel-Analyse-Ergebnis (in-memory, nicht persistiert) |
| `EntityMention` | `app/core/domain/document.py` | Strukturierte Entity-Extraktion |
| `QuerySpec` | `app/core/domain/document.py` | Filter-DSL für Dokument-Suche |
| `HistoricalEvent` | `app/core/domain/events.py` | Historisches Marktereignis (Referenzdaten) |
| `EventAnalog` | `app/core/domain/events.py` | Erkannte Analogie zu historischem Event |
| `LLMAnalysisOutput` | `app/analysis/base/interfaces.py` | Strukturierter LLM-Output (Pydantic, via beta.parse) |
| `PipelineResult` | `app/analysis/pipeline.py` | Vollständiges Analyse-Ergebnis inkl. apply_to_document() |
| `KeywordHit` | `app/analysis/keywords/engine.py` | Keyword-Match mit Kategorie und Vorkommen |
| `PriorityScore` | `app/analysis/scoring.py` | Berechneter Priority-Score (1–10) mit Audit-Info |
| `PipelineRunStats` | `app/pipeline/service.py` | Stats-Summary einer vollständigen Pipeline-Run (fetched/saved/analyzed/skipped/failed) |
| `FetchResult` | `app/ingestion/base/interfaces.py` | Adapter-Fetch-Output |
| `SourceMetadata` | `app/ingestion/base/interfaces.py` | Source-Deskriptor |
| `AppSettings` | `app/core/settings.py` | Gesamte Konfiguration |

**End-to-End Datenfluss → [docs/data_flow.md](./docs/data_flow.md)**

---

## 11. Interface-Grenzen (verbindlich)

Jeder Agent darf nur in seinem Bereich schreiben. Grenzüberschreitungen erfordern Spec.

| Grenze | Regel |
|---|---|
| **Ingestion → Storage** | Adapter liefert `FetchResult`. `persist_fetch_result()` in `app/storage/document_ingest.py` ist der einzige Einstiegspunkt für Persistierung nach Ingest. |
| **Storage → Analysis** | `DocumentRepository.list(is_analyzed=False)` liefert die Analyse-Queue. Nie direkt ORM-Modelle an Pipeline übergeben. |
| **Analysis → Storage** | `PipelineResult.apply_to_document()` mutiert `CanonicalDocument`. Danach `repo.update_analysis(doc_id, result)`. Kein anderer Pfad. |
| **Pipeline Entry-Point** | `run_rss_pipeline()` in `app/pipeline/service.py` ist der kanonische End-to-End-Einstiegspunkt. CLI `pipeline run` und Scheduler rufen ausschließlich diese Funktion auf. |
| **Analysis → Alerting** | `is_alert_worthy(result, min_priority)` ist das einzige Gate. Kein direkter Score-Zugriff in Alert-Code. |
| **LLM Provider** | Immer über `BaseAnalysisProvider.analyze()`. Nie direkt `openai.ChatCompletions` im Business-Code aufrufen. |
| **Domain Models** | `app/core/domain/` ist provider-agnostisch. Kein Import von `openai`, `anthropic`, etc. |
| **Config** | Alle Settings über `AppSettings`. Kein `os.environ` direkt. Keine Credentials im Code. |

---

## 12. Branch- und PR-Regeln

**Vollständige Strategie → [.github/BRANCH_STRATEGY.md](./.github/BRANCH_STRATEGY.md)**

| Regel | Wert |
|---|---|
| `master` geschützt | Kein direkter Push — nur via PR |
| Required CI checks | `Lint & Format Check` + `Tests` |
| PR-Template | Pflicht — alle Abschnitte ausfüllen |
| Branch-Schema | `<agent>/<phase>/<name>` z.B. `codex/p2/rss-scheduler` |
| Merge-Strategie | Squash and Merge, Branch danach löschen |

**Branch Protection in GitHub aktivieren:**
> Settings → Branches → Add rule → `master`
> ✅ Require PR · ✅ Require status checks (lint + test) · ✅ No bypass

---

## 13. Aktueller Stand

**Sprint 1 — Foundation & Contracts** ✅ abgeschlossen
**Sprint 2 — Provider Consolidation** ✅ abgeschlossen
**Sprint 3 — Alerting** ✅ abgeschlossen
**Sprint 4 — Research & Signals** ✅ Phases A/B/C/D abgeschlossen
**Sprint 5B — analysis_source Provenance** ✅ Pipeline + Persistenz + DB-Spalte (migration 0006)
**Sprint 5C — Winner-Traceability** ✅ EnsembleProvider winner → doc.provider, ensemble_chain, I-23–25
**Sprint 5D — Corpus Safety + Eval Baseline** ✅ teacher_only I-27, compare_datasets(), EvaluationMetrics, I-27–33
**Sprint 6 — Dataset + Evaluation Harness** ✅ abgeschlossen — dataset-export --teacher-only, evaluate-datasets, 547 Tests
**Sprint 7 — Companion Benchmark + Promotion Gate** ✅ abgeschlossen — validate_promotion tests, check-promotion CLI, --save-report/--save-artifact, 561 Tests
**Sprint 8 — Controlled Companion Inference + Tuning Artifact Flow + Manual Promotion** ✅ abgeschlossen — tuning.py, prepare-tuning-artifact, record-promotion, 571 Tests
**Sprint 9 — Promotion Audit Hardening: I-34 Automation, Artifact Consistency, Gate Summary** ✅ abgeschlossen — gates_summary, Artifact-Linkage-Validation, G6 (I-46–I-50), 598 Tests
**Sprint 10 — Companion Shadow Run** ✅ abgeschlossen — shadow.py, shadow-run CLI, shadow-report-file CLI, get_recent_analyzed, I-51–I-57, 625 Tests
**Sprint 11 — Distillation Harness** ✅ abgeschlossen — distillation.py, distillation-check CLI, compute_shadow_coverage (beide Formate), DistillationReadinessReport, I-58–I-62, 642 Tests
**Sprint 12 — Training Job Record** ✅ abgeschlossen — training.py, prepare-training-job CLI, link-training-evaluation CLI, record-promotion --training-job, shadow schema I-69 (deviations canonical), I-63–I-69, 667 Tests
**Sprint 13 — Evaluation Comparison + Regression Guard + Upgrade Cycle Orchestrator** ✅ abgeschlossen — PromotionRecord.comparison_report_path, record-promotion --comparison (I-72), upgrade_cycle.py, upgrade-cycle-status CLI, I-70–I-79, 694 Tests
**Sprint 14** ✅ abgeschlossen — Controlled A/B/C Inference Profiles + Signal Distribution, inference_profile.py, abc_result.py, distribution.py, CLI: create-inference-profile + abc-run + route-profile, I-80–I-89, 743 Tests
**Sprint 15** ✅ abgeschlossen — Newsdata.io Integration, NewsdataClient, NewsdataAdapter (SourceType.NEWS_API), newsdata_api_key in ProviderSettings, 19 Tests
**Sprint 14C** ✅ abgeschlossen (2026-03-20) — Runtime Route Activation, active_route.py (ActiveRouteState, activate/load/deactivate), CLI: route-activate + route-deactivate, I-90–I-93
**Sprint 16** ✅ abgeschlossen (2026-03-20) — Execution Handoff Contract, execution_handoff.py (SignalHandoff frozen, I-101–I-108), distribution.py (ExecutionHandoffReport, build_execution_handoff_report), MCP: get_signals_for_execution + acknowledge_signal_handoff + get_handoff_summary, CLI: signal-handoff, 30 Tests, 902 Tests gesamt
**Sprint 17** ✅ abgeschlossen (2026-03-20) — analyze-pending Route Integration, route_runner.py (6 Funktionen), analyze-pending Phase 2.5 + Phase ABC, ABCInferenceEnvelopes → JSONL audit-only (I-92, I-93), docs/sprint17_route_integration_contract.md, 906 Tests
**Sprint 18** ✅ abgeschlossen (2026-03-20) — Controlled MCP Server Integration, mcp_server.py: _require_artifacts_subpath() (I-95), _append_mcp_write_audit() → artifacts/mcp_write_audit.jsonl (I-94), 27 MCP-Tests, 864 Tests gesamt
**Sprint 19** ✅ abgeschlossen (2026-03-20) — Route-Aware Signal Distribution, DeliveryClassification, classify_delivery_for_route(), SignalHandoff +4 fields, RouteAwareDistributionSummary, DistributionClassificationReport, I-109–I-115, docs/sprint19_distribution_contract.md, 911 Tests

| Phase | Status | Geliefert |
|---|---|---|
| P1 Foundation | ✅ | Settings, Enums, FastAPI, CLI, DB-Session, Logging |
| P2 Ingestion | ✅ | Source Registry, Classifier, RSS, CryptoPanic, CanonicalDocument, Dedup |
| P3 Analysekern | ✅ | KeywordEngine, RuleAnalyzer, QueryExecutor, OpenAI Provider (structured), Pipeline, Historical Events, Validation |
| P3.5 Contracts | ✅ | End-to-End Data Flow Contract, priority_score, spam_probability in DB, apply_to_document(), docs/data_flow.md |
| P3.6 Pipeline Loop | ✅ | run_rss_pipeline(), pipeline run CLI, query list CLI, test_pipeline_service.py |
| P6 Audit | ✅ | 6 Architectural Invariants geprüft + behoben, tote Provider gelöscht, RSS-Guard, Security-Layer |
| PA Hardening | ✅ | SSRF-Schutz, Bearer-Auth, Secrets-Validation, Pre-commit/Pre-push Hooks, CI-Security-Job, docs/security.md |
| PB Contracts | ✅ | docs/contracts.md §1–17, vollständige Error-Handling-Doku, Repository-Boundary-Doku |
| PC Tests | ✅ | Test-Factory für AnalysisResult, kein Ad-hoc-Mocking mehr |
| PD Provider | ✅ | Claude (Anthropic) + Gemini Provider-Implementierungen |
| P7 Alerting | ✅ | app/alerts/ — Telegram, Email, ThresholdEngine, DigestCollector, AlertService, CLI alerts |
| P8 Research Models | ✅ | WatchlistRegistry, ResearchBrief, SignalCandidate, extract_signal_candidates(), contracts.md §11 |
| P8 Research CLI | ✅ | research brief/watchlists/signals/dataset-export/evaluate CLI commands |
| P8D Provider Tier Stack | ✅ | InternalModelProvider, EnsembleProvider, InternalCompanionProvider, factory.py routing, I-20–22 |
| P9A Provenance Stack | ✅ | AnalysisSource DB-Spalte (migration 0006), effective_analysis_source, Pipeline + Repo + Research consumers, I-18–19 |
| P9B Winner-Traceability | ✅ | _resolve_runtime_provider_name, active_provider_name, provider_chain, ensemble_chain, E2E-Tests, I-23–26 |
| P9C Corpus Safety | ✅ | teacher_only in export_training_data (I-27), compare_datasets + EvaluationMetrics + EvaluationReport + load_jsonl, dataset_evaluation_contract.md, contracts.md §16–17, I-27–33 |
| P10 Distillation Readiness | ✅ | dataset-export --teacher-only/--source-type, research evaluate-datasets JSONL harness, 5 Pflichtmetriken, contracts.md §17 ✅ |
| P11 Benchmark + Promotion Gate | ✅ Sprint 7 | validate_promotion getestet (7 Tests), check-promotion CLI (Exit 0/1), --save-report/--save-artifact auf evaluate-datasets, benchmark-companion command, I-34–I-39 ✅ |
| P12 Tuning + Promotion Record | ✅ Sprint 8 | TuningArtifact, PromotionRecord, save_tuning_artifact (I-40), save_promotion_record (I-43/I-45), prepare-tuning-artifact CLI, record-promotion CLI (gate-gated), I-40–I-45 ✅ |
| P13 Promotion Audit Hardening | ✅ Sprint 9 | PromotionRecord.gates_summary (I-47/I-48), Artifact-Linkage-Validation (I-49), I-46 formalisiert (FAR ≤ 0.05 = G6), I-46–I-50 ✅ |
| P14 Companion Shadow Run | ✅ Sprint 10 | shadow.py (ShadowRunRecord, DivergenceSummary, compute_divergence, run_shadow_batch), shadow-run CLI, shadow-report-file CLI, DocumentRepository.get_recent_analyzed, I-51–I-57 ✅ |
| P15 Distillation Harness | ✅ Sprint 11 | DistillationInputs, ShadowCoverageReport, DistillationReadinessReport, compute_shadow_coverage() (batch+live), build_distillation_report(), save_distillation_manifest(), distillation-check CLI, I-58–I-62 ✅ |
| P16 Training Job Record | ✅ Sprint 12 | TrainingJobRecord, PostTrainingEvaluationSpec, prepare-training-job CLI, link-training-evaluation CLI, record-promotion --training-job extension, shadow schema canonicalization (I-69: deviations canonical + divergence alias), I-63–I-69 ✅ |
| P17 Evaluation Comparison + Regression Guard | ✅ Sprint 13 | EvaluationComparisonReport in evaluation.py, compare_evaluation_reports(), compare-evaluations --out CLI, RegressionSummary, PromotionRecord.comparison_report_path, record-promotion --comparison (I-72, kein auto-Block), I-70–I-74 ✅ |
| P18 Companion Upgrade Cycle Orchestrator | ✅ Sprint 13 | UpgradeCycleReport, build_upgrade_cycle_report(), derive_cycle_status(), save_upgrade_cycle_report(), upgrade-cycle-status CLI, Status-Phasen (prepared/training_recorded/evaluated/compared/promotable/promoted_manual), I-75–I-79 ✅ |
| P19 Controlled A/B/C Inference Profiles + Signal Distribution | ✅ Sprint 14 | inference_profile.py (InferenceRouteProfile, DistributionTarget, save/load), abc_result.py (ABCInferenceEnvelope, PathResultEnvelope, PathComparisonSummary, DistributionMetadata), distribution.py (RouteProfileReport, build_route_profile async, save_route_profile), CLI: create-inference-profile + abc-run + route-profile, I-80–I-89, 42 neue Tests ✅ |
| P20 Newsdata.io Integration | ✅ Sprint 15 | app/integrations/newsdata/ (NewsdataClient, NewsdataAdapter), ProviderSettings.newsdata_api_key, FetchResult → CanonicalDocument (raw_text, source_type=NEWS_API), validate(), 19 Tests ✅ |
| P21 Runtime Route Activation | ✅ Sprint 14C | active_route.py (ActiveRouteState, activate_route_profile, load_active_route_state, deactivate_route_profile), CLI: route-activate + route-deactivate, I-90–I-93, 27 neue Tests (20 unit + 6 CLI) ✅ |
| P22 analyze-pending Route Integration | ✅ Sprint 17 | route_runner.py (map_path_to_provider_name, build_path_result_from_llm_output, build_path_result_from_analysis_result, build_comparison_summaries, build_abc_envelope, run_route_provider), analyze-pending liest ActiveRouteState + schreibt ABCInferenceEnvelopes → JSONL (I-92, I-93), 25 unit + 6 CLI Tests ✅ |
| P23 MCP Write Guard + Audit | ✅ Sprint 18 | mcp_server.py: _require_artifacts_subpath (I-95 — writes auf artifacts/ beschränkt), _append_mcp_write_audit (I-94 — JSONL-Audit für alle Write-Calls → artifacts/mcp_write_audit.jsonl), 27 Tests (19 bestehend + 8 neu) ✅ |
| P24 Execution Handoff Contract | ✅ Sprint 16 | execution_handoff.py (SignalHandoff frozen=True, I-101–I-108), distribution.py (ExecutionHandoffReport, build_execution_handoff_report, execution_enabled=False, write_back_allowed=False), MCP: get_signals_for_execution + acknowledge_signal_handoff + get_handoff_summary, CLI: signal-handoff, docs/sprint16_execution_handoff_contract.md, 30 Tests ✅ |
| P25 Route-Aware Distribution | ✅ Sprint 19 | DeliveryClassification + classify_delivery_for_route() (execution_handoff.py), SignalHandoff +4 fields, classify_delivery_class() + RouteAwareDistributionSummary + DistributionClassificationReport + DistributionAuditRecord (distribution.py), I-109–I-115, docs/sprint19_distribution_contract.md ✅ |
| P26 Consumer Acknowledgement Collection | ✅ Sprint 20/20C | Canonical runtime: execution_handoff.py (HandoffAcknowledgement frozen=True, create_handoff_acknowledgement, append_handoff_acknowledgement_jsonl, load_handoff_acknowledgements) + distribution.py (HandoffCollectorSummaryReport, HandoffCollectorEntry, build_handoff_collector_summary, save_handoff_collector_summary). MCP: acknowledge_signal_handoff (write, audit-only, PermissionError on hidden), get_handoff_collector_summary + get_handoff_summary (read alias). CLI: research handoff-acknowledge + research handoff-collector-summary; compatibility aliases: research consumer-ack + research handoff-summary. HANDOFF_ACK_JSONL_FILENAME = "consumer_acknowledgements.jsonl". consumer_collection.py superseded/removed. I-116–I-122 ✅ |
| P27 Operational Readiness Surface | ✅ Sprint 21 | operational_readiness.py (OperationalReadinessReport frozen=True, execution_enabled=False, write_back_allowed=False; ReadinessIssue, RouteReadinessSummary, AlertDispatchSummary, ProviderHealthSummary, DistributionDriftSummary; build_operational_readiness_report, save_operational_readiness_report) ist der einzige kanonische Monitoring-Stack. MCP: get_operational_readiness_summary (read-only). CLI: research readiness-summary. I-123–I-130 ✅ |
| P28 Provider Health & Distribution Drift Monitoring | ✅ Sprint 22/22C | Provider health und drift sind Readiness-abgeleitete Read-Views. MCP: get_provider_health(handoff_path, state_path, abc_output_path) + get_distribution_drift(handoff_path, state_path, abc_output_path) (read-only, readiness-derived, I-134/I-95). CLI: research provider-health + research drift-summary. operational_alerts.py: Standalone-Check-Library, nicht im MCP/CLI-Pfad (als Produktionsoberfläche superseded). I-131–I-138, contracts.md §34 ✅ |
| P29 Protective Gates & Remediation Recommendations | ✅ Sprint 23/23C | Kanonischer Pfad: operational_readiness.py (ProtectiveGateSummary frozen=True: gate_status/blocking_count/warning_count/advisory_count/items/execution_enabled=False/write_back_allowed=False, ProtectiveGateItem frozen=True: gate_status/severity/category/summary/subsystem/blocking_reason/recommended_actions/evidence_refs, eingebettet in OperationalReadinessReport, _build_protective_gate_summary() intern). protective_gates.py existiert NICHT — war geplant, ist superseded (I-145). Gates sind rein observational — kein Auto-Routing, kein Auto-Promote, kein Trading. Recommendations sind advisory-only für Human Operators. MCP: get_protective_gate_summary(handoff_path, acknowledgement_path, state_path, abc_output_path, alert_audit_dir, stale_after_hours) + get_remediation_recommendations (gleiche Signatur) (read-only). CLI: research gate-summary + research remediation-recommendations. I-139–I-145, contracts.md §35 ✅ |

| P30 Artifact Lifecycle Management | ✅ Sprint 24 | artifact_lifecycle.py: ArtifactEntry (frozen), ArtifactInventoryReport (frozen, execution_enabled=False), ArtifactRotationSummary (frozen). build_artifact_inventory(artifacts_dir, stale_after_days=30.0) scans top-level .json/.jsonl, excludes archive/ subdir. rotate_stale_artifacts(artifacts_dir, stale_after_days=30.0, *, dry_run=True) moves stale rotatable files to artifacts/archive/<timestamp>/ — NEVER deletes, skips protected. MCP: get_artifact_inventory(artifacts_dir, stale_after_days) (read-only, workspace-confined, I-149). CLI: research artifact-inventory + research artifact-rotate (default --dry-run, I-152). I-146–I-152, contracts.md §36 ✅ |

| P31 Safe Artifact Retention & Cleanup Policy | ✅ Sprint 25 | artifact_lifecycle.py (erweitert): ArtifactRetentionEntry (frozen, delete_eligible=False immer), ArtifactRetentionReport (frozen, execution_enabled=False, write_back_allowed=False, delete_eligible_count=0), ArtifactCleanupEligibilitySummary (frozen), ProtectedArtifactSummary (frozen). classify_artifact_retention(entry, *, active_route_active=False) → reine Klassifikation (I-160). build_retention_report(artifacts_dir, stale_after_days=30.0, *, active_route_active=False). Classes: audit_trail (I-156), promotion (I-157), training_data (I-158), active_state (I-159), evaluation, operational, unknown. Klassen: protected/rotatable/review_required. rotate_stale_artifacts() nutzt Retention-Policy: protected + review_required werden IMMER übersprungen (I-155). MCP: get_artifact_retention_report + get_cleanup_eligibility_summary + get_protected_artifact_summary (alle read-only). CLI: research artifact-retention. I-153–I-161, contracts.md §37 ✅ — In Sprint 26 (P32) erweitert: ReviewRequiredArtifactSummary + get_review_required_summary + research review-required-summary |
| P32 Artifact Governance Surfaces & Operator Review Flow | ✅ Sprint 26/26C/26D | artifact_lifecycle.py bleibt der einzige kanonische Governance-/Review-Stack auf Basis des Retention-Reports. Finale read-only Sichten: ArtifactRetentionReport, ArtifactCleanupEligibilitySummary, ProtectedArtifactSummary, ReviewRequiredArtifactSummary. Finale MCP-Surfaces: get_artifact_retention_report + get_cleanup_eligibility_summary + get_protected_artifact_summary + get_review_required_summary. Finale CLI-Surfaces: research artifact-retention + research cleanup-eligibility-summary + research protected-artifact-summary + research review-required-summary. Superseded: ArtifactGovernanceSummary, ArtifactPolicyRationaleSummary, get_governance_summary, get_policy_rationale_summary, research governance-summary. KEIN zweiter Governance-Stack, keine Auto-Deletion, keine Trading-Execution. contracts.md §38 ✅ |
| P33 Safe Operational Escalation Surface | ✅ Sprint 27 | operational_readiness.py bleibt der einzige kanonische Escalation-Backend-Pfad. Finale read-only Modelle: OperationalEscalationItem, OperationalEscalationSummary, BlockingSummary, OperatorActionSummary. Finale MCP-Surfaces: get_escalation_summary + get_blocking_summary + get_operator_action_summary. Finale CLI-Surfaces: research escalation-summary + research blocking-summary + research operator-action-summary. Escalation wird ausschließlich aus ProtectiveGateSummary + ReviewRequiredArtifactSummary projiziert; keine zweite Monitoring-/Gate-Architektur, keine Auto-Remediation, keine Trading-Execution. contracts.md §39 ✅ |
| P34 Safe Operator Action Queue | ✅ Sprint 28 | operational_readiness.py bleibt der einzige kanonische Backend-Pfad (keine zweite Escalation-Architektur). Finale read-only Modelle: ActionQueueItem, ActionQueueSummary, BlockingActionsSummary, PrioritizedActionsSummary, ReviewRequiredActionsSummary. Finale MCP-Surfaces: get_action_queue_summary + get_blocking_actions + get_prioritized_actions + get_review_required_actions. Finale CLI-Surfaces: research action-queue-summary + research blocking-actions + research prioritized-actions + research review-required-actions. Action Queue = Projektion aus OperationalEscalationSummary (operator_action_required=True); action_id deterministisch (sha1, act_+12hex); Priority: blocking→p1, warning/review_required→p2, sonstige→p3; queue_status: blocking > review_required > open > clear. Keine Auto-Remediation, kein Auto-Routing, keine Trading-Execution. contracts.md §40 ✅ — Sprint-28C: F811-Bug in mcp_server.py (doppelte _build_action_queue_summary_payload + get_action_queue_summary) behoben; 16 neue Unit-Tests + 4 CLI-Tests |
| P35 Read-Only Operator Decision Pack | ✅ Sprint 29 | operational_readiness.py bleibt der einzige kanonische Decision-Pack-Backend-Pfad. Finale read-only Modelle/Sichten: OperatorDecisionPack mit `overall_status`, `blocking_count`, `review_required_count`, `action_queue_count`, `affected_subsystems`, `operator_guidance`, `evidence_refs` sowie eingebetteten `readiness_summary`, `blocking_summary`, `action_queue_summary`, `review_required_summary`. Finale MCP-Surfaces: get_decision_pack_summary + get_operator_decision_pack (Alias). Finale CLI-Surfaces: research decision-pack-summary + research operator-decision-pack (Alias). Das Pack bündelt ausschließlich vorhandene kanonische Summaries; keine zweite Readiness-, Gate-, Escalation- oder Governance-Architektur, keine Auto-Remediation, keine Trading-Execution. contracts.md §41 ✅ |
| P36 Read-Only Operator Runbook & Command Safety | ✅ Sprint 30/30C | operational_readiness.py bleibt der einzige kanonische Runbook-Backend-Pfad. Finale read-only Modelle: RunbookStep, OperatorRunbookSummary (execution_enabled=False, write_back_allowed=False, auto_remediation_enabled=False, auto_routing_enabled=False). Finale MCP-Surface: get_operator_runbook (read-only, workspace-confined). Finale CLI-Surfaces: research operator-runbook (vollständiger Runbook mit Steps), research runbook-summary (kompakter Status), research runbook-next-steps (only next_steps slice) — alle drei eigenständige Kommandos, keines ist Alias eines anderen. Command-Referenzen fail-closed gegen get_registered_research_command_names() validiert. Kein Auto-Routing, keine Auto-Remediation, keine Trading-Execution. contracts.md §42 ✅ |
| P37 CLI Contract Lock & Coverage Recovery | ✅ Sprint 31 | app/cli/main.py bleibt der einzige kanonische CLI-Registrierungspfad. Kanonische CLI-Oberfläche eingefroren: 44 Commands (4 query_app + 40 research_app), autorisierte Referenzmenge via get_registered_research_command_names(). Kanonische MCP-Oberfläche eingefroren: 32 read_tools + 4 write_tools + 1 workflow_helper + 1 superseded = 38 registrierte Tools. get_narrative_clusters in read_tools hinzugefügt (I-205). get_operational_escalation_summary explizit als superseded dokumentiert (I-204). 6 Coverage-Lücken geschlossen: signals, benchmark-companion-run, check-promotion, prepare-tuning-artifact, record-promotion, evaluate — je min. 2 targeted Tests. I-201–I-210 in intelligence_architecture.md, §43 in contracts.md. Keine neue Business-Logik, keine Trading-Execution. contracts.md §43 ✅ |
| P38 MCP Contract Lock & Coverage Completion | ✅ Sprint 32 | app/agents/mcp_server.py bleibt der einzige kanonische MCP-Backend-Pfad. Vollständige MCP Tool Classification: 38 registrierte Tools in 4 Klassen (canonical/active_alias/superseded/workflow_helper). Kanonische MCP-Oberfläche: 32 read_tools + 4 write_tools + 1 workflow_helper + 1 superseded (get_operational_escalation_summary) = 38 total. Sprint-31-Inkonsistenzen korrigiert: I-204 (get_handoff_summary ist aktiver Alias), I-208 (44 CLI Commands), I-209 (38 total MCP Tools). Coverage Completion: get_narrative_clusters + get_operational_escalation_summary targeted Tests. §44 MCP Tool Inventory (maschinenlesbare Klasifikationstabelle). I-211–I-220 in intelligence_architecture.md. Keine neue Business-Logik, keine Trading-Execution. contracts.md §44 ✅ |
| P39 Append-Only Operator Review Journal & Resolution Tracking | ✅ Sprint 33 | app/research/operational_readiness.py bleibt der einzige kanonische Backend-Pfad. Neue Modelle: ReviewJournalEntry (frozen, append-only), ReviewJournalSummary (read-only Projektion), ReviewResolutionSummary (read-only Projektion, latest-per-source-ref). Keine parallele Architektur, kein zweites Governance-Stack. Kanonische MCP-Oberfläche erweitert auf 41 Tools: +2 read (get_review_journal_summary, get_resolution_summary), +1 guarded_write (append_review_journal_entry). Kanonische CLI-Oberfläche: 44 Commands inkl. review-journal-append, review-journal-summary, resolution-summary. operator_review_journal.jsonl als protected audit_trail in artifact_lifecycle.py registriert (I-228). I-221–I-230 in intelligence_architecture.md. I-209/I-211/I-218 aktualisiert. §45 in contracts.md. Kein Auto-Routing, kein Auto-Promote, keine Trading-Execution, keine Core-DB-Mutation. contracts.md §45 ✅ |
| P40 KAI Backtest Engine — Signal→Risk→Paper Loop | ✅ Sprint 35 | app/execution/backtest_engine.py: BacktestConfig (frozen), SignalExecutionRecord (frozen), BacktestResult (frozen), BacktestEngine.run(signals, prices). Vollständige Signal→RiskEngine→PaperExecution-Loop ohne Live-Exposure. direction_hint=neutral immer skip; bearish skip wenn long_only=True (A-012). Kill-Switch halts all remaining fills (I-237). Position-Sizing mit Slippage/Fee-Buffer um fill_rejected zu verhindern. Audit append-only zu artifacts/backtest_audit.jsonl (I-240). CLI: research backtest-run mit --signals-path/--out/--min-confidence. Assumptions A-012–A-015 in ASSUMPTIONS.md. I-231–I-240 in intelligence_architecture.md. §46 in contracts.md. 22 BacktestEngine + 3 CLI Tests. 1212 Tests gesamt, ruff clean. contracts.md §46 ✅ | app/research/operational_readiness.py bleibt der einzige kanonische Backend-Pfad. Neue Modelle: ReviewJournalEntry (frozen, append-only), ReviewJournalSummary (read-only Projektion), ReviewResolutionSummary (read-only Projektion, latest-per-source-ref). Keine parallele Architektur, kein zweites Governance-Stack. Kanonische MCP-Oberfläche erweitert auf 41 Tools: +2 read (get_review_journal_summary, get_resolution_summary), +1 guarded_write (append_review_journal_entry). Kanonische CLI-Oberfläche: 44 Commands inkl. review-journal-append, review-journal-summary, resolution-summary. operator_review_journal.jsonl als protected audit_trail in artifact_lifecycle.py registriert (I-228). I-221–I-230 in intelligence_architecture.md. I-209/I-211/I-218 aktualisiert. §45 in contracts.md. Kein Auto-Routing, kein Auto-Promote, keine Trading-Execution, keine Core-DB-Mutation. contracts.md §45 ✅ |

| P41 KAI Core Orchestrator: Signal Engine + Paper Trading Loop | ✅ Sprint 36 | app/signals/models.py: SignalDirection (StrEnum), SignalState (StrEnum), SignalCandidate (frozen dataclass) mit allen KAI-Pflichtfeldern (decision_id, thesis, supporting_factors, contradictory_factors, confidence, confluence, market_regime, volatility_state, liquidity_state, entry/exit/sl/tp, invalidation, risk_assessment, traceability, approval/execution state). app/signals/generator.py: SignalGenerator — 6 Filter-Gates (market data, price, stale, confidence, actionable, sentiment, confluence), Confluence-Berechnung (max 5: impact, relevance, novelty, assets, sentiment_strength), Regime/Volatility-Ableitung aus change_pct_24h, SL/TP-Berechnung (2:1 R/R), never raises. app/orchestrator/models.py: CycleStatus (StrEnum, 7 Werte), LoopCycle (frozen dataclass, alle Step-Flags, Traceability-IDs, notes). app/orchestrator/trading_loop.py: TradingLoop.run_cycle() — 7-Schritt-Pipeline: MarketData→Signal→RiskGate→PositionSize→Order+Fill→DailyLossUpdate→JSONL-Audit; direction→side-Mapping (long→buy, short→sell); never raises; alle Cycles auditiert in artifacts/trading_loop_audit.jsonl. Kernpfad geschlossen: AnalysisResult→SignalCandidate→RiskEngine→PaperExecutionEngine→LoopCycle. 37 neue Tests. 1406 Tests gesamt, ruff clean, mypy 0 Fehler. |

| P42 Decision Journal & TradingLoop CLI/MCP Surface | ✅ Sprint 36 | Neue CLI-Commands: research decision-journal-append, decision-journal-summary, loop-cycle-summary. Neue MCP-Tools: get_decision_journal_summary (canonical_read), get_loop_cycle_summary (canonical_read), append_decision_instance (guarded_write). Alle Tools workspace-confined, artifacts/-restricted. execution_enabled=False, write_back_allowed=False auf allen Antworten. Recording != Execution (kein Trade-Trigger). I-241–I-250 in intelligence_architecture.md. §47 in contracts.md. 34 neue Tests (test_mcp_sprint36.py + test_cli_decision_journal.py). Gesamt: 1315 Tests. ruff clean. |

| P43 Runtime Schema Binding & Decision Backbone Convergence | ✅ Sprint 37+37C | Zwei-Schichten-Architektur: (1) Schema-Integrität: `app/core/schema_binding.py` — prüft Schema-DATEIEN selbst (Struktur, 10 Safety-Consts, Feld-Alignment mit DecisionRecord). (2) Payload-Validierung: `app/schemas/runtime_validator.py` — kanonische Implementierung mit Draft202012Validator + FormatChecker; `settings.py::validate_json_schema_payload()` ist Kompatibilitäts-Wrapper. `DecisionRecord._validate_safe_state()` ruft Validator auf — kein Payload passiert Persistenz ohne Schema-Validierung. `AppSettings.validate_runtime_contract()` ruft Config-Validator beim Startup auf. `DecisionInstance = TypeAlias[DecisionRecord]`. Legacy-Enum-Mapping: auto_approved_paper→not_required, submitted→queued, filled→executed, partial→blocked. DECISION_SCHEMA.json: report_type als optionale Property (nicht required). `DecisionRecord._validate_timestamp_utc` erzwingt ISO 8601. Neue Tests: test_schema_binding.py (14), test_schema_runtime_binding.py (25), test_decision_journal.py (20), test_decision_record.py (9). I-251–I-265 in intelligence_architecture.md. §48 in contracts.md. A-024–A-025 in ASSUMPTIONS.md. Gesamt: 1356 Tests. ruff clean. |

| P44 Telegram Command Hardening & Canonical Read Surfaces | ✅ Sprint 38+38C | Kanonische Telegram-Surface: 15 Kommandos, 3 Klassen (read_only, guarded_audit, guarded_write). Alle read_only Commands via `_load_canonical_surface()` an MCP canonical read tools gebunden: status→get_operational_readiness_summary, health→get_provider_health, positions→get_handoff_collector_summary, risk→get_protective_gate_summary, signals→get_signals_for_execution, journal→get_review_journal_summary, daily_summary→get_decision_pack_summary. `_cmd_risk` verwendet MCP (keine private RiskEngine-Attrs). `/approve`+`/reject`: `decision_ref` Format-Validierung (`^dec_[0-9a-f]{12}$`). `get_telegram_command_inventory()` als maschinenlesbare Vertragsdefinition. Sprint 38C: `incident` aus `_READ_ONLY_COMMANDS` entfernt (Klassifikationskonflikt bereinigt — disjunkte Sets). TELEGRAM_INTERFACE.md (final), §49 contracts.md (final), I-266–I-280 intelligence_architecture.md, A-027–A-031 ASSUMPTIONS.md. tests/unit/test_telegram_bot.py: 28 Tests. Gesamt: 1362 Tests. ruff clean. |

| P45 Market Data Layer — Read-Only Adapter Contract | ✅ Sprint 39 (Implementierung abgeschlossen) | Kanonischer Market-Data-Contract. `MarketDataPoint` + `MarketDataSnapshot` (frozen). `BaseMarketDataAdapter` ABC: never-raise, read-only. `MockMarketDataAdapter`: deterministisch (sinusoidal). `CoinGeckoAdapter`: Spot-only, USD/USDT, freshness-gated, fail-closed. `MarketDataSnapshot`: execution_enabled=False, write_back_allowed=False, is_stale, available, error. `market_data/service.py`: `get_market_data_snapshot()`, `create_market_data_adapter()`. MCP: `get_market_data_quote` (canonical_read). Fail-Closed: None/is_stale → TradingLoop-Zyklus ueberspringen. BacktestEngine: pre-fetched dict. A-037–A-039 in ASSUMPTIONS.md. §50 contracts.md, I-281–I-290 intelligence_architecture.md. |

| P46 Paper Portfolio Read Surface & Exposure Summary | ✅ Sprint 40+40C | Kanonischer Portfolio-Read-Surface implementiert und konsolidiert. Kanonisches Modul: `app/execution/portfolio_read.py`. Modelle (alle frozen): `PortfolioSnapshot` (execution_enabled=False, write_back_allowed=False), `PositionSummary` (MtM-Felder: market_price, market_value_usd, unrealized_pnl_usd, market_data_is_stale, market_data_available), `ExposureSummary` (execution_enabled=False). Builder: `build_portfolio_snapshot()` (async, Audit-JSONL-Replay + optionaler MtM via get_market_data_snapshot). Source of Truth: artifacts/paper_execution_audit.jsonl. Internes Modul `portfolio_surface.py` (TradingLoop-Seite, NOT operator surface). 3 MCP-Tools (canonical_read): get_paper_portfolio_snapshot + get_paper_positions_summary + get_paper_exposure_summary. 3 CLI-Commands: research paper-portfolio-snapshot + research paper-positions-summary + research paper-exposure-summary. Telegram: /positions→get_paper_positions_summary (ersetzt Handoff-Proxy), /exposure→get_paper_exposure_summary (ersetzt Stub), "exposure" in _READ_ONLY_COMMANDS. 32 neue Tests. §51 contracts.md (§51.10+§51.11 final), I-291–I-300 intelligence_architecture.md (40C korrigiert), A-040–A-044 ASSUMPTIONS.md (40C korrigiert). Gesamt: 1426 Tests passing, ruff clean. |

| P47 TradingLoop Control Plane & Cycle Audit Surface | ✅ Sprint 41 — vollständig implementiert | Kanonischer Control-Plane-Surface für den vorhandenen TradingLoop. Modelle (frozen): `LoopStatusSummary` (auto_loop_enabled=False, execution_enabled=False, write_back_allowed=False), `RecentCyclesSummary`. Builder: `build_loop_status_summary()`, `build_recent_cycles_summary()`, `run_trading_loop_once()`. Security: `_run_once_guard()` fail-closed auf mode="live". Default: `provider="mock"` (MockAdapter), `analysis_profile="conservative"` (kein actionable signal). MCP (canonical_read): `get_trading_loop_status`, `get_recent_trading_cycles` (+ Alias `get_loop_cycle_summary`). MCP (guarded_write): `run_trading_loop_once`. CLI: `research trading-loop-status`, `research trading-loop-recent-cycles`, `research trading-loop-run-once`. Kein Daemon, kein Auto-Scheduler, `auto_loop_enabled=False` invariant. Tests: 6+6+3+14+6+6=43 Loop-Tests (loop_surface.py+test_loop_surface.py entfernt Sprint 41C). Gesamt: 1444 Tests, 0 failing, ruff clean. §52 contracts.md, I-301–I-310 intelligence_architecture.md, A-047–A-055 ASSUMPTIONS.md. |

| P48 Telegram Webhook Hardening | ✅ Sprint 42+42C+42D — eingefroren | Kanonischer, fail-closed, auditierbarer Telegram-Webhook-Transport. **Implementiert in `app/messaging/telegram_bot.py`** (kein separates Modul). Modell (frozen): `TelegramWebhookProcessResult` (`accepted`, `processed`, `rejection_reason`, `update_id`, `update_type`). Methoden in `TelegramOperatorBot`: `process_webhook_update()`, `webhook_configured`, `get_webhook_status_summary()`, `_constant_time_secret_match()` (hmac.compare_digest), `_extract_allowed_update_type()`, `_track_webhook_update_id()` (OrderedDict FIFO maxlen=2048), `_audit_webhook_rejection()`, `_reject_webhook()`. Default `webhook_allowed_updates=("message", "edited_message")` (konfigurierbar). 12 Rejection-Reasons: webhook_secret_not_configured / invalid_http_method / invalid_content_type / missing_content_length / invalid_content_length / payload_too_large / missing_secret_token_header / invalid_secret_token / missing_or_invalid_update_body / invalid_update_id / disallowed_update_type / duplicate_update_id. Audit-Separation: Rejections→`telegram_webhook_rejections.jsonl`, Commands→`operator_commands.jsonl`. Kein neues MCP-Tool, kein neuer CLI-Command, kein Live-Pfad. Tests: 15 Webhook-Tests in `test_telegram_bot.py` (gesamt 43). Gesamt: 1456 Tests, 0 failing, ruff clean. §53+§53C+§53D contracts.md, I-311–I-320 intelligence_architecture.md, A-056–A-065 ASSUMPTIONS.md, TELEGRAM_INTERFACE.md. |

**Test-Stand**: 1456 Tests passing, ruff clean (2026-03-21) — Vollvalidierung grün: alle Sprints 1-42D ✅

Vollständige Task-Liste → [TASKLIST.md](./TASKLIST.md)

---

## 14. Dokument-Lebenszyklus (DocumentStatus)

Jedes `CanonicalDocument` durchläuft einen klar definierten Lebenszyklus.
**Ausschließlich** `document_ingest.py` und `document_repo.py` dürfen den Status setzen.

```
                    ┌──────────────────────────┐
                    │       FETCH              │
                    │  (adapter produces doc)  │
                    └──────────┬───────────────┘
                               │
                               ▼
                         [PENDING]            ← in-memory, not yet in DB
                               │
               ┌───────────────┼───────────────┐
               │               │               │
         batch dedup      batch dedup     (success)
          duplicate         error              │
               │               │               ▼
               ▼               ▼         [PERSISTED]    ← saved to DB, is_analyzed=False
          [DUPLICATE]      [FAILED]            │
                                               │
                                  ┌────────────┼────────────┐
                                  │            │            │
                             LLM-Fehler   DB-Fehler    (success)
                                  │            │            │
                                  ▼            ▼            ▼
                              [FAILED]     [FAILED]    [ANALYZED]  ← is_analyzed=True
```

| Status | Wert | Bedeutung | Gesetzt von |
|---|---|---|---|
| `PENDING` | `"pending"` | In-Memory, noch nicht in DB | `prepare_ingested_document()` |
| `PERSISTED` | `"persisted"` | In DB gespeichert, wartet auf Analyse | `DocumentRepository.save_document()` |
| `ANALYZED` | `"analyzed"` | AnalysisResult geschrieben, Scores gesetzt | `DocumentRepository.update_analysis(doc_id, result)` |
| `FAILED` | `"failed"` | Nicht-behebbarer Fehler, für Audit aufbewahrt | `repo.update_status(FAILED)` im Pipeline-Fehler-Handler |
| `DUPLICATE` | `"duplicate"` | Vom Dedup-Gate erkannt und **nicht gespeichert** | In-Memory erkannt; `repo.mark_duplicate()` für bereits persistierte Dokumente |

**Hinweis DUPLICATE/FAILED bei Ingest**: `persist_fetch_result()` erkennt Duplikate in-memory
und verwirft das Dokument (kein DB-Eintrag). Kein DB-Status wird gesetzt. Status `DUPLICATE`
wird nur in DB geschrieben, wenn `repo.mark_duplicate()` explizit auf ein bereits persistiertes Dokument aufgerufen wird.

**Konvenienz-Flags** (für ORM-Queries, bleiben in Sync mit `status`):
- `is_duplicate=True` ↔ `status=DUPLICATE` (nur bei explizit gesetztem DB-Eintrag)
- `is_analyzed=True` ↔ `status=ANALYZED`

---

## 15. Architektur-Invarianten (nicht verhandelbar)

Diese 6 Regeln sind absolut. Jeder Agent, der sie bricht, muss sofort stoppen und zurückrollen.

| # | Regel | Verletzung bedeutet |
|---|---|---|
| 1 | **Kein Agent darf direkt auf `master` arbeiten** | Push blocked by pre-push hook. PR required. |
| 2 | **Keine Business-Logik im API-Layer** | Router ruft Service auf — nie direkt DB, Repo, LLM |
| 3 | **Keine LLM-Logik direkt im Transport-Client** | Provider-Client ist HTTP-only; Prompts in `prompts.py` |
| 4 | **Keine ungelösten Quellen stillschweigend als "ok" markieren** | `SourceStatus.UNRESOLVED` ist kein Fehler-Zustand, der versteckt wird |
| 5 | **Keine Social-/Podcast-/YouTube-Quelle falsch als RSS behandeln** | Classification Guard in `collect_rss_feed()` prüft `_rss_compatible` |
| 6 | **Keine Trading-Ausführung vor stabiler Analysepipeline** | `app/trading/` darf nicht vor Phase 8 existieren |

---

## 16. Security-First Regeln

Vollständige Dokumentation → [docs/security.md](./docs/security.md)

| Regel | Umsetzung |
|---|---|
| Kein Secret im Code | Pre-commit-Hook scannt nach `sk-`, Telegram-Tokens, AWS-IDs |
| SSRF-Schutz | `app/security/ssrf.validate_url()` vor jedem HTTP-Request |
| API-Auth | Bearer Token via `APP_API_KEY`, `secrets.compare_digest()` |
| Startup-Validation | `validate_secrets()` in Lifespan — hard-fail in production |
| `/health` immer public | Docker-Healthcheck nie blockieren |
| Docs in production off | `docs_url=None` wenn `APP_ENV=production` |
| CI Security Job | `pip-audit` + `bandit`, `continue-on-error: false` |

---

## 17. Test & Quality Commands

```bash
# Tests + Lint (lokal)
pytest                                    # 497+ Tests (alle)
ruff check .                              # Lint (muss fehlerfrei sein)
mypy app/                                 # Typ-Check (optional)

# Lokaler Start
cp .env.example .env                      # API-Keys eintragen
uvicorn app.api.main:app --reload         # API auf :8000
python -m app.cli.main --help             # CLI

# Docker
docker compose up --build                 # App + PostgreSQL starten
docker compose --profile dev up adminer  # + DB-UI auf :8080
docker compose down                       # stoppen

# Alembic
alembic upgrade head                      # Migrationen anwenden
alembic revision --autogenerate -m "..."  # neue Migration erstellen
```

---

## 18. Pflicht-Referenzdokumente

| Dokument | Zweck |
|---|---|
| `CLAUDE.md` | Vollständige Architekturprinzipien und Non-Negotiables |
| `PROJECT_SPEC.md` | Fachliche Spezifikation aller Features |
| `TASKLIST.md` | Operativer Task-Plan nach Phase |
| `AGENT_ROLES.md` | Vollständiges Rollenmodell mit Eskalationspfad |
| `.github/BRANCH_STRATEGY.md` | Branch-Namenskonvention, PR-Workflow, Commit-Format |
| `.github/pull_request_template.md` | Pflicht-PR-Template |
| `app/<modul>/AGENTS.md` | Modul-spezifischer Kontrakt |

| P49 FastAPI Operator API Surface | ✅ Sprint 43+43C — implementiert, Restdrift dokumentiert | Kanonischer, fail-closed, read-only FastAPI Operator API Surface. **Implementiert in `app/api/routers/operator.py`**. Auth: `require_operator_api_token` (Router-Dependency, DI), nicht via `app/security/auth.py`-Middleware. Leer `APP_API_KEY` → 503, kein Header → 401, falscher Token → 403, `secrets.compare_digest` constant-time. **7 read-only Endpoints**: `GET /operator/status` + `GET /operator/readiness` (Aliases → `get_operational_readiness_summary()`), `GET /operator/decision-pack` → `get_decision_pack_summary()`, `GET /operator/portfolio-snapshot` → `get_paper_portfolio_snapshot(...)`, `GET /operator/exposure-summary` → `get_paper_exposure_summary(...)`, `GET /operator/trading-loop/status` → `get_trading_loop_status(...)`, `GET /operator/trading-loop/recent-cycles` → `get_recent_trading_cycles(...)`. **1 guarded Endpoint**: `POST /operator/trading-loop/run-once` → `run_trading_loop_once(...)`, ValueError → HTTP 400. Verbotene Pfade: `/operator/trade`, `/operator/execute`, `/operator/order`, `/operator/fill`, `/operator/broker`, `/operator/live`. **NICHT implementiert (Sprint 43+)**: `GET /operator/webhook-status`, `POST /operator/webhook`, `app.state.telegram_bot`. Kanonische Tests: `test_api_operator.py` (13 passing). Stale-spec: `test_operator_api.py` (8 failing — falsche Endpunkt-Namen + fehlende Webhook-Endpoints). Gesamt: **1470 passed, 8 failed**. §54 (korrekter Block) + §54C contracts.md, I-321–I-330 intelligence_architecture.md, A-066–A-072 ASSUMPTIONS.md. |

**Test-Stand**: 1470 Tests passing, 8 failing (test_operator_api.py — stale spec), ruff clean (2026-03-21) — Sprint 43+43C ✅ implementiert, Restdrift dokumentiert

| P50 Operator API Hardening & Request Governance | ⏳ Sprint 44 — Definition ✅, Implementierung pending (Codex) | Transport-/Governance-Layer fuer den kanonischen Operator-API-Surface aus Sprint 43. **Kein neues Modul** — Haertung von `app/api/routers/operator.py`. Erweiterungen: (1) `get_request_id()` Dependency — UUID4 server-generiert oder aus `X-Request-Id` Header (validiert). Response-Body und Header `X-Request-Id` immer gesetzt. (2) In-memory Idempotency-Buffer fuer `POST /operator/trading-loop/run-once` — `OrderedDict` FIFO maxlen=256, `X-Idempotency-Key` Header, HTTP 409 bei Duplikat. (3) `_audit_operator_request()` — Append-only `artifacts/operator_api_audit.jsonl`, post-auth/pre-dispatch, never-raise, keine Secrets. (4) Kanonische Error-Shape `{error, detail, request_id}` fuer alle Fehlercodes (api_key_not_configured/missing_auth_header/invalid_auth_scheme/invalid_api_key/invalid_request/mode_not_allowed/duplicate_idempotency_key/internal_error). Sicherheitsinvarianten: kein unkorrelierter guarded Request, kein doppelter run-once ohne 409, keine ungeregelten Fehlerantworten, kein Live-Pfad, keine Trading-Semantik, Audit getrennt von Telegram-Audits. Neue Tests: `tests/unit/test_operator_governance.py`. Stand: 1470 passed, 8 failed (stale test_operator_api.py — unveraendert). §55 contracts.md, I-331–I-340 intelligence_architecture.md, A-073–A-078 ASSUMPTIONS.md. |

**Test-Stand**: 1470 passed, 8 failed (stale spec — unveraendert bis Sprint 43+). Definition Sprint 44 abgeschlossen 2026-03-21.

| P50 Operator API Hardening & Request Governance | ✅ Sprint 44+44C — implementiert und eingefroren | Transport-/Governance-Layer auf `app/api/routers/operator.py` (597 Zeilen). **Request-Identity**: `bind_operator_request_context` Dependency — `X-Request-ID` (Format: `req_<hex>`) + `X-Correlation-ID` (default = request_id). **Idempotency** (REQUIRED): `Idempotency-Key` Header Pflicht für guarded POST — Replay bei gleichem Key+Payload-SHA256, HTTP 409 `idempotency_key_conflict` bei Key-Konflikt, `OrderedDict` FIFO maxlen=256 Thread-safe. **Rate-Limiter**: Sliding-Window 5 req/30s pro `token_<sha256[:16]>` (operator_subject) → HTTP 429 `guarded_rate_limited`. **Error-Shape**: verschachtelt `{error: {code, message, request_id, correlation_id}, execution_enabled: false, write_back_allowed: false}`. **Audit**: `artifacts/operator_api_guarded_audit.jsonl` (nur guarded POST, never-raise, keine Secrets). **Auth-Codes**: `operator_api_disabled` / `missing_authorization_header` / `invalid_authorization_scheme` / `invalid_api_key`. **Test-Reset**: `_reset_operator_guard_state_for_tests()`. Sprint-44-Tests (20 in `test_api_operator.py`): Auth-fail-closed, request_id passthrough, correlation_id, read-error-shape, run-once idempotency required, modes, live fail-closed, idempotency replay, idempotency conflict, rate-limit. Gesamt: **1498 passed, 0 failed** (kanonischer Referenzstand nach S45C Freeze). §55 (Historischer Entwurf) + §55C contracts.md, I-331–I-340+I-331C–I-340C intelligence_architecture.md, A-073–A-078+A-073C–A-078C ASSUMPTIONS.md. |

**Test-Stand**: 1498 Tests passing, 0 failing, ruff clean (2026-03-22) — kanonischer Referenzstand nach S45C Freeze ✅


| P51 Daily Operator View — S45_OPERATOR_USABILITY_BASELINE | ✅ Sprint 45 — implementiert (Codex) | Kanonischer Daily Operator View fuer Phase 2. **MCP**: `get_daily_operator_summary(...)` delegiert ausschliesslich an bestehende Read-Tools (`get_operational_readiness_summary`, `get_recent_trading_cycles`, `get_paper_portfolio_snapshot`, `get_paper_exposure_summary`, `get_decision_pack_summary`, `get_review_journal_summary`) mit best-effort Aggregation und `execution_enabled=false` / `write_back_allowed=false`. **CLI**: `trading-bot research daily-summary` (+ `--json`). **API**: `GET /operator/daily-summary` unter bestehenden Operator-Auth/Request-Governance-Guardrails. **Telegram**: `/daily_summary` delegiert auf denselben MCP-Daily-Pfad. Kein neuer Datenpfad, kein Live-Pfad, keine DB-Aenderung. §56 contracts.md, I-341–I-350 intelligence_architecture.md. |

**Test-Stand**: Sprint 45 implementiert; Vollvalidierung siehe aktuelle pytest-/ruff-Laeufe.



| P52 Operator Dashboard Baseline — S46_OPERATOR_DASHBOARD_BASELINE | ✅ Sprint 46 — implementiert (Codex) | Minimale visuelle Operator-Sicht via FastAPI HTMLResponse ist umgesetzt. **Route**: `GET /dashboard` in `app/api/routers/dashboard.py`. **Rendering**: f-string HTML mit Inline-CSS, kein Jinja2, kein JS, kein CSS-Framework, Auto-Refresh 60s. **Datenquelle**: ausschliesslich `mcp_server.get_daily_operator_summary()` — kein zweiter Aggregat-Pfad. **Auth-Modell**: fail-closed bei leerem APP_API_KEY (503 `dashboard_disabled`); Browser-Bearer nicht erforderlich fuer `/dashboard` (middleware whitelist + server-side rendering). **Degradation**: Fehler im Daily-Summary-Call liefern HTML-Statusseite `unavailable`, kein Stack-Trace. **Einbindung**: `app/api/main.py` includet `dashboard.router`; `app/security/auth.py` laesst `/dashboard` read-only zu. **Tests**: `tests/unit/test_api_dashboard.py` (5/5 passing). §57 contracts.md, I-351–I-360 intelligence_architecture.md. |

**Test-Stand**: **1510 passed, ruff clean** (S47 baseline 2026-03-22). Kein `/static/dashboard.html` — einziger Pfad ist `GET /dashboard`. I-361..I-363 Freeze-Invarianten. §57C in `docs/contracts.md`.
| P53 Operator Drilldown & History Baseline — S47_OPERATOR_DRILLDOWN_HISTORY_BASELINE | ✅ Sprint 47 — Codex-Implementierung geliefert | Zwei Operator-API-Endpoints als pure Delegation auf bestehende MCP-Tools: `GET /operator/review-journal` → `mcp_server.get_review_journal_summary()`, `GET /operator/resolution-summary` → `mcp_server.get_resolution_summary()`. Gleiche Error-Shape, gleiche Auth, gleiche Governance wie alle Operator-Endpoints. API-Coverage ergänzt (success + fail-closed je Endpoint) in `tests/unit/test_api_operator.py`. §59 contracts.md, I-364..I-368 intelligence_architecture.md. |

**Test-Stand Sprint 47**: Codex-Scope geliefert (2026-03-22), Vollvalidierung grün: **1510 passed, ruff clean**.

| P54 Operator Surface Completion — S48_OPERATOR_SURFACE_COMPLETION | ✅ Sprint 48 abgeschlossen | Umsetzung strikt gemäß §61 (kanonisch): Telegram `/resolution` und `/decision_pack` als delegation-only Read-Surfaces (`_load_canonical_surface`), Dashboard mit statischer Drilldown-Referenz (`/operator/readiness`, `/operator/decision-pack`, `/operator/trading-loop/recent-cycles`, `/operator/review-journal`, `/operator/resolution-summary`) ohne JS und ohne zweiten Backend-Call. Tests ergänzt: Telegram Mapping/Degradation/Help + Dashboard Drilldown-Referenz. Kein neuer Aggregatpfad, keine Trading-Semantik, keine Subpage-Architektur. |

| P55 Operator Alerting / Digest Baseline — S49_OPERATOR_ALERTING_DIGEST_BASELINE | ✅ Sprint 49 abgeschlossen (2026-03-22) | MCP `get_alert_audit_summary()`, `GET /operator/alert-audit`, Telegram `/alert_status`, CLI `research alert-audit-summary`. Keine Pipeline-Änderung, kein neues Aggregat. §62 contracts.md, I-378–I-383 intelligence_architecture.md. |

| P56 Phase-3 Canonical Consolidation — S50/S50A | ✅ S50A frozen (2026-03-22) | Phase 2 formal geschlossen. Phase 3 eröffnet mit S50_CANONICAL_CONSOLIDATION_BASELINE (umbrella). S50A: `CANONICAL_SURFACE_INVENTORY.md` erstellt und eingefroren. Claude Governance-Review: PASS. F-S50A-001 (15 provisional CLI) → S50B. Baseline: 1519 passed, ruff clean. §63 contracts.md. |

| P57 Phase-3 CLI Governance — S50B | ✅ classification complete (2026-03-22) | S50B: alle 15 provisional CLI commands promoted to canonical (D-29). CLI canonical count: 38 → 53. F-S50A-001 resolved. Provisional set: 0. §64 contracts.md. Baseline unverändert: 1519 passed, ruff clean. |

| P58 Phase-3 CLI Contract Freeze — S50C | ✅ closed (2026-03-22) | S50C: §65 CLI contract frozen. 53/53 canonical commands confirmed reachable. 0 provisional. Governance docs contradiction-free. Baseline: 1519 passed, ruff clean. |

| P59 Phase-3 Doc Hygiene and Structure — S50D | ✅ closed (2026-03-22) | S50D: §66 rules applied to TASKLIST, AGENTS, ASSUMPTIONS, intelligence_architecture, DECISION_LOG. contracts.md split/trim plan applied (additive only). Baseline: 1519 passed, ruff clean. |

**Test-Stand Phase 3 / S50D (2026-03-22)**: **1519 passed, ruff clean**.

---

## 19. Phase 3 Sprint Status (2026-03-22)

- Current phase: `PHASE 3 (active)`
- Umbrella sprint: `S50_CANONICAL_CONSOLIDATION_BASELINE`
- Active sub-sprint: `TBD (Phase-3 next sprint definition required)`
- Previous sub-sprint: `S50D_DOC_HYGIENE_AND_STRUCTURE` (**closed**, 2026-03-22)
- Next required step: `PHASE3_NEXT_SPRINT_DEFINITION`
- Canonical baseline: `1519 passed, ruff clean`
- Classification status: **15/15 promoted to canonical** — F-S50A-001 resolved; §65 frozen

### S50 Guardrails

- No new product features in S50
- No new execution or trading semantics
- No second aggregation backbone
- No broad CLI refactor before per-command classification decisions are recorded
- Phase-4 gate blocked until Phase-3 consolidation accepted

---

## 20. S50B Closed — Provisional CLI Governance (2026-03-22)

S50A is formally closed. S50B is now closed after sync/freeze completion.

- `S50A_CANONICAL_PATH_INVENTORY` — **closed**
- Claude governance review — **PASS** (2026-03-22)
- Antigravity readability/onboarding review — **PASS** (2026-03-22)
- `CANONICAL_SURFACE_INVENTORY.md` — frozen inventory artifact
- `F-S50A-001` — **resolved** (2026-03-22); all 15 promoted to canonical
- `S50B_PROVISIONAL_CLI_GOVERNANCE` — **closed** (sync/freeze completed)
- Classification: 15/15 `promote_to_canonical`; CLI canonical count 38 → 53; provisional set 0
- MCP rationale: 3/15 commands MCP-backed; 12/15 internal pipeline/governance commands where MCP is not required
- `S50C_CLI_CONTRACT_FREEZE` — **closed** (2026-03-22); §65 frozen; 53/53 verified; 0 provisional
- `S50D_DOC_HYGIENE_AND_STRUCTURE` — **closed** (2026-03-22); §66 rules applied to all target docs; 1519 passed, ruff clean; no product code changes
