# AGENTS.md — KAI Platform

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
tests/unit/          → pytest unit tests (521 passing — ruff clean)
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

**Test-Stand**: 571 passed, 0 failed, 0 xfailed

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
