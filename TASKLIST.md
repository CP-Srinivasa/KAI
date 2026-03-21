# TASKLIST.md — KAI Platform Sprint Plan

> Sprints sind **streng sequenziell**. Sprint N startet erst, wenn Sprint N-1 vollständig abgeschlossen ist.
> Letzte Aktualisierung: 2026-03-21
> Rebaseline-Hinweis (2026-03-21): Historische Sprint-Abschnitte bleiben Verlauf. Der verifizierte Ist-Stand und alle aktuellen Surface-Zaehlungen stehen in `KAI_BASELINE_MATRIX.md`.

---

## Sprint 1 — Foundation & Contracts (aktuell)

**Ziel**: Stabiles, vollständig getestetes Fundament. Kein Sprint 2 ohne grünes Sprint 1.

**Status**: ✅ abgeschlossen — 398 Tests passing, ruff clean, contracts vollständig

| # | Task | Status |
|---|---|---|
| 1.1 | End-to-End Data Flow Contract (`docs/data_flow.md`) | ✅ |
| 1.2 | Shared Contracts (`docs/contracts.md`) | ✅ |
| 1.3 | AGENTS.md vollständig + aktuell | ✅ |
| 1.4 | `run_rss_pipeline()` — Pipeline Loop geschlossen | ✅ |
| 1.5 | `pipeline run` + `query list` CLI | ✅ |
| 1.6 | `DocumentStatus` Lifecycle Enum | ✅ |
| 1.7 | `apply_to_document()` als einziger Score-Mutationspunkt | ✅ |
| 1.8 | `priority_score` + `spam_probability` in DB + ORM | ✅ |
| 1.9 | 361 Tests passing, ruff clean | ✅ |
| 1.10 | `test_e2e_system.py` xfail auflösen | ⏳ |
| 1.11 | `docs/contracts.md` von allen Agenten bestätigt | ✅ |

**Sprint 1 gilt als abgeschlossen wenn**: `pytest` vollständig grün (kein xfail), `ruff check` sauber, alle Contracts bestätigt.

---

## Sprint 2 — Provider Consolidation ✅

**Status**: ✅ abgeschlossen — 386 Tests passing, ruff clean

**Ziel**: Einheitliches, stabiles LLM-Provider-System ohne Chaos. Alle drei Provider gegen dasselbe strukturierte Analyseformat.

| # | Task | Status |
|---|---|---|
| 2.1 | OpenAI Provider — `beta.chat.completions.parse`, `LLMAnalysisOutput` als `response_format` | ✅ |
| 2.2 | Claude (Anthropic) Provider — Tool-Use mit `record_analysis` Tool + schema enforcement | ✅ |
| 2.3 | Google Gemini Provider — `response_schema=LLMAnalysisOutput` via google-genai SDK | ✅ |
| 2.4 | Einheitliches `LLMAnalysisOutput` — alle drei Provider liefern dasselbe Schema | ✅ |
| 2.5 | `app/analysis/factory.py` — `create_provider()` sauber für alle drei + `claude`-Alias | ✅ |
| 2.6 | Tests für alle drei Provider: Metadaten, `analyze()`, Fehler, `from_settings()`, Prompt | ✅ |
| 2.7 | `test_factory.py` — 10 Tests für alle Factory-Pfade | ✅ |
| 2.8 | Prompts nach `app/analysis/prompts.py` verschoben — provider-agnostisch | ✅ |
| 2.9 | Gemini `_timeout` Bug dokumentiert + gespeichert | ✅ |

---

## Sprint 3 — Alerting ✅

**Status**: ✅ abgeschlossen — 445 Tests passing, ruff clean

**Ziel**: Echte Alerts auf der Basis analysierter, gescorter Dokumente.

| # | Task | Status |
|---|---|---|
| 3.1 | Telegram Alerting — Bot-Integration, Nachrichtenformat | ✅ |
| 3.2 | E-Mail Alerting — SMTP via smtplib, dry_run default | ✅ |
| 3.3 | Threshold Engine — `ThresholdEngine` wraps `is_alert_worthy()`, konfigurierbar | ✅ |
| 3.4 | Digest-Logik — `DigestCollector` (deque-basiert), `send_digest()` in `AlertService` | ✅ |
| 3.5 | Alert-Regeln in `monitor/alert_rules.yml` konfigurierbar | ✅ |
| 3.6 | Tests: 47 Tests — Trigger, Nicht-Trigger, Digest, Formatters, Channels, Service | ✅ |

**Architektur**: `app/alerts/` — BaseAlertChannel ABC, TelegramAlertChannel, EmailAlertChannel, ThresholdEngine, DigestCollector, AlertService (from_settings factory). Pipeline integriert.

---

## Sprint 4 — Research & Signal Preparation

> **Startet erst nach Sprint 3-Abschluss.** Sprint 3 ✅

**Ziel**: Verwertbare Outputs für Entscheidungen — Watchlists, Briefs, Signal-Kandidaten.

**Architektur-Basis (Claude Code, abgeschlossen ✅)**:
- `app/research/watchlists.py` — `WatchlistRegistry` (multi-type: assets/persons/topics/sources), `filter_documents()`, `from_file()`, `save()`
- `app/research/briefs.py` — `ResearchBrief`, `BriefFacet`, `BriefDocument`, `ResearchBriefBuilder`
- `app/research/signals.py` — `SignalCandidate`, `extract_signal_candidates()`
- `app/research/__init__.py` — öffentliche Exports definiert
- `app/research/AGENTS.md` — Modul-Kontrakt für alle Agenten
- `app/analysis/keywords/watchlist.py` — `load_watchlist()` jetzt mit `persons`+`topics`
- `docs/contracts.md §11` — vollständige Sprint 4 Contracts dokumentiert
- `monitor/watchlists.yml` — Seed-Datei vorhanden (13 crypto, 8 equity, 5 ETF, 10 persons, 10 topics)

> ⚠ **Offene Lücke**: `WatchlistRegistry.find_by_text()` — in früherer Spec referenziert, aber nicht in
> finaler Implementierung enthalten. Vorgesehen für Sprint 4B. Bis dahin: `filter_documents()` verwenden.

---

### Sprint 4 Phase A — Watchlist + Research Brief CLI ✅

**Status**: ✅ abgeschlossen — CLI + API + Tests vollständig

| # | Task | Agent | Status |
|---|---|---|---|
| 4.1 | `research watchlists list` CLI | — | ✅ |
| 4.2 | `research watchlists for <tag>` CLI | — | ✅ |
| 4.3 | `research brief <cluster>` CLI | — | ✅ |
| 4.4 | `GET /research/brief` API-Endpoint | — | ✅ |
| 4.5 | Tests für CLI + API | — | ✅ |

**Codex-Spec für 4.4–4.5 (CLI ✅ — nur noch API + Tests):**

```
## Task: Sprint 4A — Research API-Endpoint + Tests

Agent: Codex
Phase: Sprint 4A
Modul: app/api/routers/research.py
Typ: feature

Beschreibung:
  CLI-Commands (4.1–4.3) sind bereits implementiert in app/cli/main.py.
  Offene Aufgabe: API-Endpoint + Tests für Research Brief.

Spec-Referenz: app/research/__init__.py, docs/research_outputs.md, app/research/AGENTS.md

Constraints:
  - NICHT: app/research/*.py ändern (alle Models sind final)
  - NICHT: app/cli/main.py ändern (CLI ist fertig)
  - NICHT: WatchlistRegistry.find_by_text() verwenden — nutze filter_documents() stattdessen
  - API-Router analog zu app/api/routers/alerts.py (Bearer-Auth-Pattern)
  - Kein Trading-Execution-Code
  - Kein Alert-Upgrade

API-Endpoint:
  GET /research/briefs/{cluster}?watchlist_type=assets&limit=100&format=md
    → WatchlistRegistry.from_monitor_dir(settings.monitor_dir)
    → registry.filter_documents(docs, cluster, item_type=resolved_type)
    → ResearchBriefBuilder(cluster).build(filtered_docs)
    → to_markdown() oder to_json_dict() je nach format-Parameter
    Auth: Bearer-Token (analog /alerts/test)

Akzeptanzkriterien:
  - [x] ruff check . sauber
  - [ ] pytest tests/unit/test_research_api.py grün (neuer Test-File)
  - [ ] WatchlistRegistry korrekt aus monitor_dir geladen
  - [ ] Brief-Output enthält cluster_name, document_count, top_actionable_signals
  - [ ] Kein DB-Schreiben im Router
  - [ ] 401 bei fehlendem Bearer-Token
```

---

### Sprint 4 Phase B — Signal Candidates ✅

**Status**: ✅ abgeschlossen — CLI + API + Tests vollständig

| # | Task | Agent | Status |
|---|---|---|---|
| 4.6 | `research signals [--watchlist <tag>] [--min-priority 8]` CLI | — | ✅ |
| 4.7 | `GET /research/signals` API-Endpoint | — | ✅ |
| 4.8 | Watchlist-Boost-Integration in signals CLI | — | ✅ |
| 4.9 | Tests für Signal-CLI + API | — | ✅ |

**Codex-Spec für 4.6–4.9:**

```
## Task: Sprint 4B — Signal Candidates CLI + API

Agent: Codex
Phase: Sprint 4B
Modul: app/cli/main.py, app/api/routers/research.py
Typ: feature

Beschreibung:
  Baue die Signal-Candidates-Ausgabeschicht auf der bestehenden
  extract_signal_candidates()-Funktion.

Spec-Referenz: docs/contracts.md §11c, app/research/signals.py

CLI:
  research signals list [--watchlist <tag>] [--min-priority 8] [--format json|table]
    → WatchlistRegistry laden
    → wenn --watchlist: watchlist_boosts = {symbol: 1 for symbol in get_watchlist(tag)}
    → repo.list(is_analyzed=True) holen
    → extract_signal_candidates(docs, min_priority, watchlist_boosts)
    → Rich Table ausgeben: signal_id, target_asset, direction_hint, priority, confidence

API:
  GET /research/signals?watchlist=<tag>&min_priority=8
    → analog CLI-Logik
    → Returns: list[SignalCandidate.to_json_dict()]

Constraints:
  - direction_hint darf NIEMALS "buy"/"sell"/"hold" sein
  - Jeder Signal-Output muss document_id enthalten (Traceability)
  - NICHT: neue Models einführen

Akzeptanzkriterien:
  - [x] ruff check . sauber
  - [ ] pytest tests/unit/test_research_signals_cli.py grün
  - [ ] direction_hint-Werte sind ausschließlich "bullish"/"bearish"/"neutral"
  - [ ] document_id-Traceability in jedem Kandidaten
```

---

---

### Sprint 4 Phase C — Fallback Pipeline Guardrails ✅

**Status**: ✅ abgeschlossen

**Ziel**: Produktionssichere Analyse ohne LLM — kein harter Absturz, kein stilles Überspringen,
klare Unterscheidung zwischen LLM-enriched und rule-based Ergebnissen.

| # | Task | Agent | Status |
|---|---|---|---|
| 4.10 | `apply_to_document()` Fallback — Scores auch ohne `llm_output` schreiben | — | ✅ (war bereits korrekt) |
| 4.11 | `analyze_pending` None-Guard — `analysis_result=None` → FAILED statt silent | — | ✅ |
| 4.12 | Tests: `test_pipeline_fallback.py` — rule-only Pfad, kein Score-Verlust, I-12 guard | — | ✅ |

**Codex-Spec für 4.10–4.12:**

```
## Task: Sprint 4C — Fallback Pipeline Guardrails

Agent: Codex
Phase: Sprint 4C
Modul: app/analysis/pipeline.py, app/cli/main.py
Typ: fix

Beschreibung:
  Sichert den Analyse-Pfad ohne LLM-Provider ab.
  Zwei Kernprobleme beheben — keine neuen Features, nur Robustheit.

Spec-Referenz: docs/contracts.md §12b, §12c, I-12, I-13

Änderung 1 — app/analysis/pipeline.py (apply_to_document):
  Aktuell: if not self.analysis_result or not self.llm_output: return
  Neu:     if not self.analysis_result: return
           # llm_output-abhängige Felder (spam_prob, credibility) nur setzen wenn llm_output da
           spam_prob = self.llm_output.spam_probability if self.llm_output else 0.0
           # credibility_score = 1.0 - spam_prob (schon korrekt)
           # market_scope aus llm_output nur wenn vorhanden

Änderung 2 — app/cli/main.py (analyze_pending):
  Nach res.apply_to_document():
  if res.analysis_result is None:
      await repo.update_status(str(doc.id), DocumentStatus.FAILED)
      console.print(f"[yellow]Skipped {doc.id} — no analysis result (no provider?)[/yellow]")
      skip_count += 1
      continue

Constraints:
  - NICHT: neue Provider-Logik oder Factory-Änderungen
  - NICHT: RuleAnalyzer in AnalysisPipeline autowire — bleibt separater Pfad
  - NICHT: Scoring-Formel ändern
  - direction_hint-Invariante bleibt: "bullish"/"bearish"/"neutral"
  - I-12 einhalten: analysis_result=None → NEVER update_analysis aufrufen

Akzeptanzkriterien:
  - [x] ruff check . sauber
  - [ ] pytest tests/unit/test_pipeline_fallback.py grün (neu)
  - [ ] apply_to_document() schreibt Scores wenn analysis_result gesetzt, llm_output=None
  - [ ] analyze_pending markiert FAILED wenn analysis_result=None (nie silent skip)
  - [ ] Bestehende tests/unit/test_analysis_pipeline.py weiterhin grün
```

---

## Sprint 4 Phase D — Provider-Independent Intelligence Architecture ✅

**Ziel**: Three-tier provider architecture: Rule-based → InternalModelProvider → EnsembleProvider + Companion

| # | Task | Status |
|---|---|---|
| 4D.1 | `app/analysis/internal_model/provider.py` — `InternalModelProvider` (heuristisch, zero deps, immer verfügbar) | ✅ |
| 4D.2 | `app/analysis/ensemble/provider.py` — `EnsembleProvider` (ordered fallback, first success wins) | ✅ |
| 4D.3 | `app/analysis/providers/companion.py` — `InternalCompanionProvider` (HTTP zu lokalem Endpoint) | ✅ |
| 4D.4 | `app/analysis/factory.py` — `"internal"` → `InternalModelProvider`, `"companion"` → `InternalCompanionProvider` | ✅ |
| 4D.5 | `app/core/settings.py` — `companion_model_*` Felder + localhost-Validator | ✅ |
| 4D.6 | `tests/unit/test_internal_model_provider.py` — 10 Tests | ✅ |
| 4D.7 | `tests/unit/test_ensemble_provider.py` — 7 Tests | ✅ |
| 4D.8 | `docs/contracts.md` — I-20/I-21/I-22 Provider-Tier Invarianten | ✅ |

**Contracts (I-20–22)**:
- `InternalModelProvider`: `provider_name="internal"`, `priority≤5`, `actionable=False`, `sentiment=NEUTRAL`
- `InternalCompanionProvider`: `provider_name="companion"`, `impact_score≤0.8`, localhost-only endpoint
- `EnsembleProvider`: min 1 provider, InternalModelProvider als letzter Eintrag = garantierter Fallback

---

## Sprint 5C — Winner-Traceability

**Ziel**: `EnsembleProvider`-Runs schreiben den tatsächlichen Gewinner als `doc.provider` und die korrekte `analysis_source` — kein konservativer `INTERNAL`-Override mehr wenn `openai` gewonnen hat.

**Contract-Basis**: `docs/contracts.md §15` (I-23/I-24/I-25)

| # | Task | Agent | Status |
|---|---|---|---|
| 5C.1 | `_resolve_runtime_provider_name()` + `_resolve_trace_metadata()` — duck-typing winner resolution | Codex | ✅ |
| 5C.2 | Pipeline: post-analyze winner-Resolution via `active_provider_name` property | Codex | ✅ |
| 5C.3 | `doc.provider` = winner name; `doc.metadata["ensemble_chain"]` via `trace_metadata` | Codex | ✅ |
| 5C.4 | Tests: `test_ensemble_openai_wins_sets_external_llm_source`, `test_ensemble_internal_fallback_sets_internal_source` | Codex | ✅ |
| 5C.5 | `EnsembleProvider.active_provider_name` + `provider_chain` public properties | Codex | ✅ |
| 5C.6 | Verifikation: `analyze-pending` CLI + DB-Lauf, `doc.analysis_source` korrekt nach Ensemble-Run | Antigravity | ✅ |
| 5C.7 | Contract-Abnahme + Commit | Claude Code | ✅ |

**Codex-Spec:**

```
## Task: Sprint 5C — EnsembleProvider Winner-Traceability

Agent: Codex
Phase: Sprint 5C
Modul: app/analysis/pipeline.py, tests/unit/test_analysis_pipeline.py
Typ: contract-fix (minimaler Hook, keine neue ML-Logik)

Spec-Referenz: docs/contracts.md §15 (I-23/I-24/I-25)

Änderungen:

1. app/analysis/pipeline.py

   a) Neue Funktion (string-basiert, für post-analyze):

      def _resolve_analysis_source_from_winner(winning_name: str) -> AnalysisSource:
          name = winning_name.strip().lower()
          if not name or name in {"fallback", "rule", "internal", "companion"}:
              return AnalysisSource.INTERNAL
          return AnalysisSource.EXTERNAL_LLM

   b) In run() — nach erfolgreichem llm_output = await self._provider.analyze(...):

      winning_name = self._provider.model or self._provider.provider_name
      analysis_source = _resolve_analysis_source_from_winner(winning_name)
      provider_name = winning_name   # winner name, nicht Composite-String

      WICHTIG: Das ERSETZT die bisherige pre-analyze Resolution NUR im LLM-Erfolgsfall.
      Fehlerfall (except-Branch) und fallback_reason-Branch bleiben unverändert:
      - Fallback-Branch: analysis_source = AnalysisSource.RULE (unveränderlich)
      - Except-Branch: ruft _build_fallback_analysis(), bleibt RULE

   c) In apply_to_document() — ensemble_chain in metadata:

      if self.provider_name and "," in (self._document_ensemble_chain or ""):
          # nur wenn Ensemble: speichere Kette in metadata
          pass

      Einfacher: In PipelineResult, neues Feld:
          ensemble_chain: list[str] | None = None

      In run() befüllen:
          from app.analysis.ensemble.provider import EnsembleProvider
          ensemble_chain = None
          if isinstance(self._provider, EnsembleProvider):
              ensemble_chain = [p.provider_name for p in self._provider._providers]

      In apply_to_document():
          if self.ensemble_chain:
              meta = dict(self.document.metadata or {})
              meta["ensemble_chain"] = self.ensemble_chain
              self.document.metadata = meta

2. tests/unit/test_analysis_pipeline.py — neue Tests:

   test_ensemble_openai_wins_sets_external_llm_source:
     - EnsembleProvider([mock_openai, InternalModelProvider])
     - mock_openai.analyze returns valid LLMAnalysisOutput
     - result.analysis_result.analysis_source == AnalysisSource.EXTERNAL_LLM
     - result.provider_name == "openai"
     - result.document.metadata["ensemble_chain"] == ["openai", "internal"]

   test_ensemble_internal_fallback_sets_internal_source:
     - EnsembleProvider([failing_openai, InternalModelProvider])
     - result.analysis_result.analysis_source == AnalysisSource.INTERNAL
     - result.provider_name == "internal"

   test_direct_provider_source_resolution_unchanged:
     - Direkt OpenAIProvider (kein Ensemble) → EXTERNAL_LLM (unveränderlich)
     - Direkt InternalModelProvider → INTERNAL (unveränderlich)

Acceptance Criteria:
  - ruff check . sauber
  - pytest -q grün (alle bestehenden + neue Tests)
  - _resolve_analysis_source() (alte Funktion) bleibt für non-ensemble Providers
  - _resolve_analysis_source_from_winner() wird NUR im LLM-Erfolgsfall nach analyze() genutzt
  - Kein Scope-Drift: keine anderen Dateien berühren
  - doc.provider == "openai" (nicht "ensemble(openai,internal)") wenn openai gewann
```

---

## Sprint 5D — Distillation Corpus Safety + Evaluation Baseline

**Ziel**: Teacher-Eligibility-Guardrail auf Funktionsebene. Sprint-6 kann direkt mit teacher-only Export und Evaluation-Metriken starten.

**Contract-Basis**: `docs/contracts.md §16` (I-27/I-28)

| # | Task | Agent | Status |
|---|---|---|---|
| 5D.1 | `export_training_data(docs, path, *, teacher_only=False)` — `teacher_only=True` skippt RULE + INTERNAL + legacy-None (I-27) | Codex | ✅ |
| 5D.2 | CLI `dataset-export --teacher-only` Flag → ruft Funktion mit `teacher_only=True` | Codex | ✅ |
| 5D.3 | Tests: Corpus-Safety-Suite in `test_datasets.py` (alle §16c-Fälle) | Codex | ✅ |
| 5D.4 | Verifikation: CLI-Lauf mit `--teacher-only`, DB-Durchlauf, Corpus-Integrität | Antigravity | ✅ |
| 5D.5 | Contract-Abnahme + Commit | Claude Code | ✅ |

**Codex-Spec:**

```
## Task: Sprint 5D — teacher_only Guardrail in export_training_data()

Agent: Codex
Phase: Sprint 5D
Modul: app/research/datasets.py, app/cli/main.py, tests/unit/test_datasets.py
Typ: safety-hardening (minimale Änderung, kein neues Feature)

Spec-Referenz: docs/contracts.md §16 (I-27)

Änderungen:

1. app/research/datasets.py

   Signatur:
     def export_training_data(
         documents: list[CanonicalDocument],
         output_path: Path,
         *,
         teacher_only: bool = False,
     ) -> int:

   Skip-Logik (nach is_analyzed-Check, vor text_block-Check):
     if teacher_only:
         if doc.analysis_source != AnalysisSource.EXTERNAL_LLM:
             continue
         # Hinweis: doc.analysis_source (nicht effective_analysis_source) prüfen.
         # Legacy-Rows ohne explizit gesetztes analysis_source (None) werden übersprungen.
         # effective_analysis_source würde sie via provider-Fallback ggf. zulassen — zu riskant.

   Import ergänzen: from app.core.enums import AnalysisSource (falls noch nicht vorhanden)

2. app/cli/main.py — dataset-export Command

   Neues Flag:
     teacher_only: bool = typer.Option(False, "--teacher-only", help="Export only EXTERNAL_LLM docs (I-27 safety guardrail)")

   Übergabe an Funktion:
     count = export_training_data(docs, out_path, teacher_only=teacher_only)

   WICHTIG: bestehender --source-type Filter BLEIBT. Beide Mechanismen arbeiten zusammen:
   - --source-type filtert VOR dem Funktionsaufruf (DB-Query-Schicht)
   - --teacher-only ist Guardrail IN der Funktion (kann nicht umgangen werden)

3. tests/unit/test_datasets.py — Corpus-Safety-Suite

   Neue Tests (§16c alle Fälle):

   test_teacher_only_skips_rule_documents:
     doc mit analysis_source=AnalysisSource.RULE, is_analyzed=True, raw_text vorhanden
     export_training_data([doc], path, teacher_only=True) → count == 0

   test_teacher_only_skips_internal_documents:
     doc mit analysis_source=AnalysisSource.INTERNAL, is_analyzed=True, raw_text vorhanden
     export_training_data([doc], path, teacher_only=True) → count == 0

   test_teacher_only_skips_legacy_none_analysis_source:
     doc mit analysis_source=None, provider="openai" (würde via effective→EXTERNAL_LLM)
     export_training_data([doc], path, teacher_only=True) → count == 0
     (Konservativ: explicit field required)

   test_teacher_only_exports_external_llm_documents:
     doc mit analysis_source=AnalysisSource.EXTERNAL_LLM, is_analyzed=True, raw_text
     export_training_data([doc], path, teacher_only=True) → count == 1

   test_teacher_only_false_exports_all_sources:
     docs = [external_llm_doc, internal_doc, rule_doc] alle is_analyzed=True, mit text
     export_training_data(docs, path, teacher_only=False) → count == 3

Acceptance Criteria:
  - ruff check . sauber
  - pytest -q grün (alle bestehenden + 5 neue Tests)
  - export_training_data([rule_doc], path, teacher_only=True) → count == 0
  - export_training_data([openai_doc], path, teacher_only=True) → count == 1
  - default teacher_only=False: identical behavior to pre-5D (kein Breaking Change)
  - doc.analysis_source (nicht effective) wird für teacher_only geprüft (§16c letzte Zeile)
  - Kein Scope-Drift: nur datasets.py, cli/main.py, test_datasets.py
```

---

## Sprint 6 — Dataset Construction, Evaluation Harness, Distillation Readiness

**Ziel**: Den bestehenden Intelligence-Stack für Distillation vorbereiten, ohne neue Runtime-Architektur
zu bauen. Sprint 6 liefert klare Dataset-Rollen, einen offline Evaluation Harness und die
Readiness-Regeln für Teacher/Benchmark/Baseline.

**Contract-Basis**:
- `docs/dataset_evaluation_contract.md`
- `docs/contracts.md §17`
- `docs/intelligence_architecture.md §Distillation Path`

| # | Task | Agent | Status |
|---|---|---|---|
| 6.1 | Teacher-only dataset export — `export_training_data(teacher_only=True)` + I-27 function-level guard | Claude Code | ✅ |
| 6.2 | CLI: `dataset-export --teacher-only` flag + `--source-type internal/rule` für Benchmark/Baseline | Codex | ✅ |
| 6.3 | CLI: `research evaluate-datasets` — JSONL-Export vergleichen, Rich-Tabelle ausgeben | Codex | ✅ |
| 6.4 | `compare_datasets()` — JSONL-basierter Harness, Join über `document_id` | Claude Code | ✅ |
| 6.5 | Pflichtmetriken: `sentiment_agreement`, `priority_mae`, `relevance_mae`, `impact_mae`, `tag_overlap_mean` | Claude Code | ✅ |
| 6.6 | `load_jsonl()` Helper für offline JSONL-Vergleich | Claude Code | ✅ |
| 6.7 | Contract-Abnahme + Commit | Claude Code | ✅ |
| 6.8 | CLI: `research benchmark-companion` — Teacher-vs-Candidate Benchmark mit optionalem Report-/Artifact-Output | Codex | ✅ |
| 6.9 | Benchmark-Artifact-Hooks — `save_evaluation_report()` + `save_benchmark_artifact()` | Codex | ✅ |

**Codex-Spec für Sprint 6.2 — CLI-Erweiterung:**

```
## Task: Sprint 6.2 — Dataset Export CLI --teacher-only Flag

Agent: Codex
Phase: Sprint 6
Modul: app/cli/main.py
Typ: feature (minimal, kein Interface-Break)

Vollständige Spec: docs/dataset_evaluation_contract.md §CLI-Contract-6.2

Beschreibung:
  Ergänze research_dataset_export() um ein --teacher-only Flag.
  export_training_data(teacher_only=True) ist bereits implementiert und getestet.
  Nur der CLI-Hookup fehlt — 2 Zeilen Änderung.

Spec-Referenz: docs/dataset_evaluation_contract.md §CLI-Contract-6.2, I-27

Änderungen in app/cli/main.py:
  1. Neuer Parameter in research_dataset_export():
       teacher_only: bool = typer.Option(
           False,
           "--teacher-only",
           help="Strict teacher guard: only export analysis_source=EXTERNAL_LLM rows (I-27)",
       )

  2. Aufruf ändern von:
       count = export_training_data(docs, out_path)
     zu:
       count = export_training_data(docs, out_path, teacher_only=teacher_only)

Constraints:
  - NICHT: export_training_data() oder datasets.py ändern
  - NICHT: DB-Schema oder Repository ändern
  - NICHT: Neue Module anlegen
  - --teacher-only ist additiv — bestehende Callers ohne Flag bleiben unverändert

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/ grün (keine Regression, Basis: 547 Tests)
  - [ ] research dataset-export teacher.jsonl --teacher-only exportiert nur external_llm Rows
  - [ ] research dataset-export benchmark.jsonl --source-type internal exportiert interne Rows
  - [ ] research dataset-export baseline.jsonl --source-type rule exportiert rule Rows
  - [ ] --teacher-only ohne --source-type funktioniert (nur function-level guard)
```

**Codex-Spec für Sprint 6.3 — evaluate-datasets CLI:**

```
## Task: Sprint 6.3 — research evaluate-datasets CLI

Agent: Codex
Phase: Sprint 6
Modul: app/cli/main.py
Typ: feature

Vollständige Spec: docs/dataset_evaluation_contract.md §CLI-Contract-6.3

Beschreibung:
  Neuer CLI-Befehl research evaluate-datasets <teacher_file> <baseline_file>.
  Lädt zwei JSONL-Dateien (keine DB), ruft compare_datasets() auf, gibt Rich-Tabelle aus.
  compare_datasets() und load_jsonl() sind bereits fertig und getestet — nur CLI-Hookup.

Spec-Referenz: docs/dataset_evaluation_contract.md §CLI-Contract-6.3

Typer-Signatur (exakt):
  @research_app.command("evaluate-datasets")
  def research_evaluate_datasets(
      teacher_file: str = typer.Argument(
          ..., help="Path to teacher JSONL file (analysis_source=external_llm)"
      ),
      baseline_file: str = typer.Argument(
          ..., help="Path to baseline JSONL file (rule or internal tier)"
      ),
      dataset_type: str = typer.Option(
          "rule_baseline",
          help="Comparison type: rule_baseline | internal_benchmark | custom",
      ),
  ) -> None:
      """Compare two exported JSONL datasets offline. No DB required."""

Implementierungslogik: → docs/dataset_evaluation_contract.md §CLI-Contract-6.3

Constraints:
  - KEINE LLM-Calls, kein Model-Loading, kein build_session_factory
  - KEIN neues Modul anlegen
  - compare_datasets() und load_jsonl() nicht ändern
  - Exit 1 wenn teacher_file oder baseline_file nicht existiert (nicht silent skip)
  - Table-Import ist in main.py bereits vorhanden (rich.table)

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/ grün (Basis: 547 Tests)
  - [ ] research evaluate-datasets teacher.jsonl rule.jsonl — alle 5 Metriken in Tabelle
  - [ ] research --help zeigt evaluate-datasets in research-Gruppe
  - [ ] Exit 1 + Fehlermeldung wenn Datei nicht existiert
  - [ ] --dataset-type internal_benchmark erscheint im Tabellentitel
  - [ ] research evaluate (DB-basiert) weiterhin unverändert funktionsfähig
```

**Sprint-6 Abschlusskriterien (nach 6.2 + 6.3):**

```
Sprint 6 gilt als abgeschlossen wenn:
  - [ ] 6.2: dataset-export --teacher-only implementiert
  - [ ] 6.3: evaluate-datasets implementiert
  - [ ] ruff check . sauber
  - [ ] pytest passing (Basis 547, kein Rückschritt)
  - [ ] research evaluate (DB-basiert) unverändert
  - [ ] research evaluate-datasets in research --help sichtbar
  - [ ] docs/contracts.md §17 Status → ✅
  - [ ] TASKLIST.md 6.2, 6.3, 6.7 → ✅
  - [ ] AGENTS.md Test-Stand aktualisiert
```

---

## Sprint 5 — Intelligence Layer (Companion Model)

> **Startet erst nach Sprint 4C-Abschluss.**

**Ziel**: Lokale Analyse-Ebene ohne externe Provider — `InternalCompanionProvider` als eigenständige Option neben Tier 3.

**Architektur-Basis**: `docs/intelligence_architecture.md`, `docs/contracts.md §13`

| # | Task | Agent | Status |
|---|---|---|---|
| 5.1 | `InternalCompanionProvider` Skeleton — `app/analysis/providers/companion.py` | Codex | ⏳ |
| 5.2 | `ProviderSettings` Extension — `companion_model_endpoint`, `companion_model_name`, `companion_model_timeout` | Codex | ⏳ |
| 5.3 | Factory `"internal"` Branch — `create_provider()` | Codex | ⏳ |
| 5.4 | `AnalysisSource` Enum — `app/analysis/base/interfaces.py` | Codex | ⏳ |
| 5.5 | `AnalysisResult.analysis_source` Field + Alembic Migration | Codex | ⏳ |
| 5.6 | Tests: Companion Provider, Factory, AnalysisSource | Codex | ⏳ |
| 5.7 | Priority Fallback Chain — Tier 3 → Tier 2 → Tier 1 | Codex | ⏳ |

**Codex-Spec für 5.1–5.3 (Sprint 5A — Skeleton + Factory):**

```
## Task: Sprint 5A — InternalCompanionProvider Skeleton

Agent: Codex
Phase: Sprint 5A
Modul: app/analysis/providers/companion.py, app/analysis/factory.py, app/core/settings.py
Typ: feature (stub/skeleton)

Beschreibung:
  Führe den InternalCompanionProvider als vollständige Skeleton-Implementierung ein.
  Kein Training, kein echter Inference-Aufruf — nur sauberes Interface + HTTP-Stub.

Spec-Referenz: docs/intelligence_architecture.md §Tier 2, docs/contracts.md §13a–13b

Änderungen:
  1. app/core/settings.py — ProviderSettings Extension:
     companion_model_endpoint: str | None = None
     companion_model_name: str = "kai-analyst-v1"
     companion_model_timeout: int = 10

  2. app/analysis/providers/companion.py (NEU):
     class InternalCompanionProvider(BaseAnalysisProvider):
         provider_name = "internal"
         model: str
         endpoint: str
         timeout: int
         async def analyze(title, text, context) -> LLMAnalysisOutput:
             # Stub: POST to self.endpoint/analyze, return LLMAnalysisOutput
             # Falls endpoint nicht erreichbar → raise RuntimeError (kein silentes Fallback)

  3. app/analysis/factory.py — neuer case "internal":
     if not settings.companion_model_endpoint: return None
     return InternalCompanionProvider(...)

Constraints:
  - NICHT: echten Inference-Client bauen (HTTP-Stub reicht für Sprint 5A)
  - NICHT: Scoring-Formel ändern
  - NICHT: Pipeline ändern (BaseAnalysisProvider-Kompatibilität reicht)
  - Security: companion_model_endpoint validation — nur localhost oder allowlisted hosts

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_companion_provider.py grün (neu)
  - [ ] pytest tests/unit/test_factory.py weiterhin grün (neuer "internal"-Pfad abgedeckt)
  - [ ] InternalCompanionProvider ist kein Breaking Change für bestehende Tests
```

**Codex-Spec für 5.4–5.5 (Sprint 5B — AnalysisSource + Migration):**

```
## Task: Sprint 5B — AnalysisSource Enum + DB Migration

Agent: Codex
Phase: Sprint 5B
Modul: app/analysis/base/interfaces.py, app/storage/models.py, alembic/versions/
Typ: feature

Spec-Referenz: docs/contracts.md §13c, docs/intelligence_architecture.md §AnalysisSource

Änderungen:
  1. app/analysis/base/interfaces.py:
     class AnalysisSource(str, Enum):
         RULE = "rule"
         INTERNAL = "internal"
         EXTERNAL_LLM = "external_llm"

     AnalysisResult + optional field:
     analysis_source: AnalysisSource | None = None

  2. app/storage/models.py:
     canonical_documents.analysis_source: Mapped[str | None] = mapped_column(String(20), nullable=True)

  3. Alembic Migration (neu):
     ALTER TABLE canonical_documents ADD COLUMN analysis_source VARCHAR(20);

Constraints:
  - Invariant I-18: analysis_source ist nach apply_to_document() immutabel
  - Invariant I-19: RULE-Dokumente dürfen NIEMALS als Distillation-Teacher dienen
  - Nullable: bestehende Dokumente ohne analysis_source sind valid (vor Sprint 5)

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] Alembic-Migration läuft durch (alembic upgrade head)
  - [ ] Bestehende Tests weiterhin grün (keine Breaking Changes)
```

---

## Sprint 7 — Companion Benchmark Harness, Promotion Gate, Artifact Contract

> **Startet erst nach Sprint 6-Abschluss.** Sprint 6 ✅ (547 Tests, ruff clean)

**Ziel**: Sprint-6 Evaluation-Stubs testen, in CLI verdrahten, Promotion-Gate als manuell
prüfbaren Schritt sichtbar machen. Kein Training, keine neuen Provider, keine Auto-Promotion.

**Contract-Basis**:
- `docs/benchmark_promotion_contract.md` (neu, kanonische Sprint-7 Referenz)
- `docs/contracts.md §18` (I-34–I-39)

**Drei explizite Trennungen (nicht verhandelbar)**:
- `Benchmark ≠ Training`
- `Evaluation ≠ Promotion`
- `Promotion = manueller Gate-Schritt, kein automatischer Trigger`

| # | Task | Agent | Status |
|---|---|---|---|
| 7.1 | Tests: `validate_promotion()`, `save_evaluation_report()`, `save_benchmark_artifact()` — alle in `evaluation.py` vorhanden, aber null Test-Coverage | Codex | ✅ |
| 7.2 | CLI: `evaluate-datasets --save-report <path> [--save-artifact <path>]` — optionale Persistence-Flags, kein Behavior-Change ohne Flags | Codex | ✅ |
| 7.3 | CLI: `research check-promotion <report.json>` — liest gespeicherten Report, `validate_promotion()`, per-Gate-Tabelle, Exit 0/1 | Codex | ✅ |
| 7.4 | `benchmark_promotion_contract.md` + `contracts.md §18` + I-34–I-39 | Claude Code | ✅ |
| 7.5 | `intelligence_architecture.md` Sprint-7 Update | Claude Code | ✅ |
| 7.6 | `dataset_evaluation_contract.md` Sprint-7 Pointer | Claude Code | ✅ |
| 7.7 | Contract-Abnahme + Commit | Claude Code | ✅ |

**Codex-Spec für 7.1 — Tests:**

```
## Task: Sprint 7.1 — Tests für Evaluation Stubs

Agent: Codex
Phase: Sprint 7
Modul: tests/unit/test_evaluation.py (erweitern)
Typ: test (keine Implementierungsänderung)

Spec-Referenz: docs/benchmark_promotion_contract.md §Sprint-7 Acceptance Criteria 7.1

Ziel: Test-Coverage für drei bereits implementierte Funktionen in evaluation.py,
die bisher null Tests haben.

Tests für validate_promotion():

  test_validate_promotion_all_gates_pass:
    metrics = EvaluationMetrics(sentiment_agreement=0.90, priority_mae=1.0,
                                relevance_mae=0.10, impact_mae=0.15,
                                tag_overlap_mean=0.40, sample_count=50, missing_pairs=0)
    result = validate_promotion(metrics)
    assert result.is_promotable is True
    assert all([result.sentiment_pass, result.priority_pass, result.relevance_pass,
                result.impact_pass, result.tag_overlap_pass])

  test_validate_promotion_sentiment_fails:
    metrics mit sentiment_agreement=0.80 (< 0.85)
    assert result.sentiment_pass is False
    assert result.is_promotable is False

  test_validate_promotion_priority_fails:
    metrics mit priority_mae=2.0 (> 1.5)
    assert result.priority_pass is False

  test_validate_promotion_relevance_fails:
    metrics mit relevance_mae=0.20 (> 0.15)
    assert result.relevance_pass is False

  test_validate_promotion_impact_fails:
    metrics mit impact_mae=0.25 (> 0.20)
    assert result.impact_pass is False

  test_validate_promotion_tag_overlap_fails:
    metrics mit tag_overlap_mean=0.20 (< 0.30)
    assert result.tag_overlap_pass is False

  test_validate_promotion_boundary_values_are_passing:
    metrics = EvaluationMetrics(sentiment_agreement=0.85, priority_mae=1.5,
                                relevance_mae=0.15, impact_mae=0.20,
                                tag_overlap_mean=0.30, sample_count=10, missing_pairs=0)
    result = validate_promotion(metrics)
    assert result.is_promotable is True  # boundary = pass (>=, <=)

Tests für save_evaluation_report():

  test_save_evaluation_report_creates_valid_json(tmp_path):
    report = EvaluationReport(metrics=..., dataset_type="rule_baseline",
                              teacher_count=10, baseline_count=10, paired_count=8)
    path = save_evaluation_report(report, tmp_path / "report.json",
                                  teacher_dataset="/a/teacher.jsonl",
                                  candidate_dataset="/b/candidate.jsonl")
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["report_type"] == "dataset_evaluation"
    assert "generated_at" in data
    assert data["inputs"]["teacher_dataset"].endswith("teacher.jsonl")
    assert data["metrics"]["sentiment_agreement"] == report.metrics.sentiment_agreement

Tests für save_benchmark_artifact():

  test_save_benchmark_artifact_benchmark_ready(tmp_path):
    report mit paired_count=5
    path = save_benchmark_artifact(tmp_path / "artifact.json", ...)
    data = json.loads(path.read_text())
    assert data["artifact_type"] == "companion_benchmark"
    assert data["status"] == "benchmark_ready"

  test_save_benchmark_artifact_needs_more_data(tmp_path):
    report mit paired_count=0
    data["status"] == "needs_more_data"

Constraints:
  - NICHT: validate_promotion(), save_evaluation_report(), save_benchmark_artifact() ändern
  - NICHT: neue Module anlegen
  - Nur tests/unit/test_evaluation.py erweitern

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_evaluation.py grün (alle neuen + bestehenden Tests)
  - [ ] pytest tests/unit/ grün (547 Basis, kein Rückschritt)
```

**Codex-Spec für 7.2 — CLI `--save-report` / `--save-artifact`:**

```
## Task: Sprint 7.2 — evaluate-datasets Persistence-Flags

Agent: Codex
Phase: Sprint 7
Modul: app/cli/main.py
Typ: feature (additiv, kein Behavior-Change ohne Flags)

Spec-Referenz: docs/benchmark_promotion_contract.md §CLI-Contract-7.2

Änderungen in research_evaluate_datasets():

  Neue optionale Parameter:
    save_report: str | None = typer.Option(
        None, "--save-report",
        help="Persist EvaluationReport as JSON for audit trail and check-promotion",
    )
    save_artifact: str | None = typer.Option(
        None, "--save-artifact",
        help="Persist companion benchmark manifest JSON",
    )

  Nach console.print(table):
    from app.research.evaluation import save_evaluation_report, save_benchmark_artifact

    if save_report:
        saved = save_evaluation_report(
            report, save_report,
            teacher_dataset=teacher_file,
            candidate_dataset=candidate_file,
        )
        console.print(f"[dim]Evaluation report saved: {saved}[/dim]")

    if save_artifact:
        artifact = save_benchmark_artifact(
            save_artifact,
            teacher_dataset=teacher_file,
            candidate_dataset=candidate_file,
            report=report,
            report_path=save_report,
        )
        console.print(f"[dim]Benchmark artifact saved: {artifact}[/dim]")

Constraints:
  - NICHT: save_evaluation_report() oder save_benchmark_artifact() ändern
  - NICHT: bestehende Tabellen-/Metrik-Ausgabe ändern
  - Ohne --save-report / --save-artifact: Verhalten identisch zu Sprint-6

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest grün (547 Basis)
  - [ ] --save-report erstellt JSON-Datei mit allen EvaluationReport-Feldern
  - [ ] --save-artifact erstellt Benchmark-Manifest mit artifact_type = "companion_benchmark"
  - [ ] Beide Flags zusammen: beide Dateien erstellt
  - [ ] Ohne Flags: Verhalten unverändert (kein Regression)
```

**Codex-Spec für 7.3 — CLI `research check-promotion`:**

```
## Task: Sprint 7.3 — research check-promotion CLI

Agent: Codex
Phase: Sprint 7
Modul: app/cli/main.py
Typ: feature

Vollständige Implementierungslogik und Signatur:
→ docs/benchmark_promotion_contract.md §CLI-Contract-7.3

Typer-Signatur:
  @research_app.command("check-promotion")
  def research_check_promotion(
      report_file: str = typer.Argument(
          ..., help="Path to evaluation_report.json (from evaluate-datasets --save-report)"
      ),
  ) -> None:
      """Check whether a saved evaluation report meets all companion promotion gates.

      Exits 0 if all 6 quantitative gates pass.
      Exits 1 if any gate fails or report cannot be parsed.

      Gate I-34 is enforced via false_actionable_rate on paired rows only.
      """

Implementierung: exakt wie in docs/benchmark_promotion_contract.md §CLI-Contract-7.3 spezifiziert.

Constraints:
  - NICHT: validate_promotion() oder EvaluationMetrics ändern
  - KEIN DB-Aufruf, KEIN Model-Load, KEIN LLM-Call
  - Exit 0 = promotable (alle 6 quantitativen Gates pass)
  - Exit 1 = nicht promotable ODER Datei nicht gefunden ODER Parse-Fehler
  - I-34-Automationshinweis IMMER anzeigen, auch bei PASS

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest grün (547 Basis)
  - [ ] Exit 0 wenn alle 6 quantitativen Gates pass
  - [ ] Exit 1 wenn mindestens 1 Gate fail
  - [ ] Exit 1 + Fehlermeldung wenn Datei nicht gefunden
  - [ ] Exit 1 + Fehlermeldung bei ungültigem JSON
  - [ ] I-34-Automationshinweis in allen Fällen sichtbar
  - [ ] research --help zeigt check-promotion in research-Gruppe
```

**Sprint-7 Abschlusskriterien:**

```
Sprint 7 gilt als abgeschlossen wenn:
  - [x] 7.1: validate_promotion + save_* vollständig getestet
  - [x] 7.2: --save-report / --save-artifact CLI-Flags verdrahtet
  - [x] 7.3: check-promotion CLI implementiert
  - [x] ruff check . sauber
  - [x] pytest passing (561 Tests, kein Rückschritt)
  - [x] evaluate-datasets (bestehend) unverändert funktionsfähig
  - [x] evaluate (DB-basiert, Sprint 5) unverändert
  - [x] docs/contracts.md §18 + I-34–I-39 ✅
  - [x] TASKLIST.md Sprint-7 Tasks aktualisiert
  - [x] AGENTS.md Test-Stand aktualisiert
  - [x] benchmark_promotion_contract.md vollständig und konsistent
```

---

## Sprint 8 — Controlled Companion Inference, Tuning Artifact Flow, Manual Promotion

> **Startet erst nach Sprint 7-Abschluss.** Sprint 7 ⏳ (7.1–7.3 Codex pending, 7.7 sign-off pending)

**Ziel**: Tuning-Artifact-Flow und manuellen Promotion-Record definieren und implementieren.
Kein Training, kein automatischer Routing-Wechsel. Companion-Inferenz bleibt lokal und kontrolliert.

**Contract-Basis**:
- `docs/tuning_promotion_contract.md` (neu, kanonische Sprint-8 Referenz)
- `docs/contracts.md §19` (I-40–I-45)

**Vier explizite Trennungen (nicht verhandelbar)**:
- `Benchmark ≠ Tuning`
- `Tuning ≠ Training`
- `Training ≠ Promotion`
- `Promotion ≠ Deployment`

| # | Task | Agent | Status |
|---|---|---|---|
| 8.1 | `app/research/tuning.py` — `TuningArtifact`, `PromotionRecord`, `save_tuning_artifact()`, `save_promotion_record()` + vollständige Tests | Codex | ✅ |
| 8.2 | CLI: `research prepare-tuning-artifact <teacher_file> <model_base>` — Tuning-Manifest erstellen | Codex | ✅ |
| 8.3 | CLI: `research record-promotion <report_file> <model_id> --endpoint <url> --operator-note <text>` — Promotion-Record schreiben | Codex | ✅ |
| 8.4 | `tuning_promotion_contract.md` + `contracts.md §19` + I-40–I-45 | Claude Code | ✅ |
| 8.5 | `intelligence_architecture.md` Sprint-8 Update | Claude Code | ✅ |
| 8.6 | Contract-Abnahme + Commit | Claude Code | ✅ |

**Codex-Spec für 8.1 — `app/research/tuning.py` + Tests:**

```
## Task: Sprint 8.1 — tuning.py + Tests

Agent: Codex
Phase: Sprint 8
Modul: app/research/tuning.py (NEU), tests/unit/test_tuning.py (NEU)
Typ: feature + test

Spec-Referenz: docs/tuning_promotion_contract.md §NewModule + §AcceptanceCriteria-8.1

Implementiere exakt nach der Spezifikation in docs/tuning_promotion_contract.md §NewModule:
- TuningArtifact dataclass mit to_json_dict()
- PromotionRecord dataclass mit to_json_dict()
- save_tuning_artifact() — schreibt JSON, kein Training
- save_promotion_record() — schreibt JSON, validiert operator_note und eval-report-Existenz

Tests in tests/unit/test_tuning.py:
  test_save_tuning_artifact_creates_valid_json(tmp_path):
    path = save_tuning_artifact(tmp_path/"manifest.json",
                                teacher_dataset="/a/teacher.jsonl",
                                model_base="llama3.2:3b", row_count=50)
    data = json.loads(path.read_text())
    assert data["artifact_type"] == "tuning_manifest"
    assert data["model_base"] == "llama3.2:3b"
    assert data["training_format"] == "openai_chat"
    assert data["row_count"] == 50
    assert data["evaluation_report"] is None

  test_save_tuning_artifact_with_eval_report(tmp_path):
    eval_report = tmp_path/"report.json"
    eval_report.write_text("{}")
    path = save_tuning_artifact(tmp_path/"manifest.json",
                                teacher_dataset="/a/teacher.jsonl",
                                model_base="kai-v1", row_count=10,
                                evaluation_report=eval_report)
    data = json.loads(path.read_text())
    assert data["evaluation_report"] is not None

  test_save_promotion_record_creates_valid_json(tmp_path):
    eval_report = tmp_path/"report.json"
    eval_report.write_text("{}")
    path = save_promotion_record(tmp_path/"promo.json",
                                 promoted_model="kai-analyst-v1",
                                 promoted_endpoint="http://localhost:11434",
                                 evaluation_report=eval_report,
                                 operator_note="Operator approved after review")
    data = json.loads(path.read_text())
    assert data["record_type"] == "companion_promotion"
    assert "reversal_instructions" in data
    assert data["tuning_artifact"] is None

  test_save_promotion_record_blank_note_raises(tmp_path):
    eval_report = tmp_path/"report.json"
    eval_report.write_text("{}")
    with pytest.raises(ValueError, match="operator_note"):
        save_promotion_record(tmp_path/"promo.json",
                              promoted_model="m", promoted_endpoint="http://localhost:11434",
                              evaluation_report=eval_report, operator_note="   ")

  test_save_promotion_record_missing_report_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        save_promotion_record(tmp_path/"promo.json",
                              promoted_model="m", promoted_endpoint="http://localhost:11434",
                              evaluation_report=tmp_path/"nonexistent.json",
                              operator_note="ok")

Constraints:
  - NICHT: evaluation.py oder bestehende Module ändern
  - tuning.py importiert NICHT aus evaluation.py
  - Nur json, dataclasses, datetime, pathlib verwenden
  - Kein httpx, kein asyncio, kein torch

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_tuning.py grün (alle Tests)
  - [ ] pytest tests/unit/ grün (561 Basis, kein Rückschritt)
```

**Codex-Spec für 8.2 — CLI `prepare-tuning-artifact`:**

```
## Task: Sprint 8.2 — research prepare-tuning-artifact CLI

Agent: Codex
Phase: Sprint 8
Modul: app/cli/main.py
Typ: feature

Vollständige Implementierungslogik und Signatur:
→ docs/tuning_promotion_contract.md §CLI-Contract-8.2

Typer-Signatur:
  @research_app.command("prepare-tuning-artifact")
  def research_prepare_tuning_artifact(
      teacher_file: str = typer.Argument(...),
      model_base: str = typer.Argument(...),
      eval_report: str | None = typer.Option(None, "--eval-report"),
      out: str = typer.Option("tuning_manifest.json", "--out"),
  ) -> None:
      """Record a training-ready manifest. Does NOT train a model."""

Implementierung: exakt wie in docs/tuning_promotion_contract.md §CLI-Contract-8.2.

Constraints:
  - KEIN DB-Aufruf, KEIN Model-Load, KEIN LLM-Call
  - KEIN Training-Trigger
  - Exit 1 wenn teacher_file nicht gefunden
  - Exit 1 wenn teacher_file leer
  - Disclaimer immer drucken: "record only, run fine-tuning separately"

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest grün (561 Basis)
  - [ ] --out erstellt tuning_manifest.json mit korrekten Feldern
  - [ ] Exit 1 bei fehlendem/leerem teacher_file
  - [ ] research --help zeigt prepare-tuning-artifact in research-Gruppe
```

**Codex-Spec für 8.3 — CLI `record-promotion`:**

```
## Task: Sprint 8.3 — research record-promotion CLI

Agent: Codex
Phase: Sprint 8
Modul: app/cli/main.py
Typ: feature

Vollständige Implementierungslogik und Signatur:
→ docs/tuning_promotion_contract.md §CLI-Contract-8.3

Typer-Signatur:
  @research_app.command("record-promotion")
  def research_record_promotion(
      report_file: str = typer.Argument(...),
      model_id: str = typer.Argument(...),
      endpoint: str = typer.Option(..., "--endpoint"),
      operator_note: str = typer.Option(..., "--operator-note"),
      tuning_artifact: str | None = typer.Option(None, "--tuning-artifact"),
      out: str = typer.Option("promotion_record.json", "--out"),
  ) -> None:
      """Record a manual companion promotion decision. Does NOT change routing."""

Implementierung: exakt wie in docs/tuning_promotion_contract.md §CLI-Contract-8.3.

Constraints:
  - Verifikation: validate_promotion() auf den gelesenen Report VOR dem Schreiben
  - Exit 1 wenn Report nicht existiert oder Gates nicht bestehen
  - Exit 1 bei leerem --operator-note (propagiert ValueError aus save_promotion_record)
  - Druckt Aktivierungshinweis (APP_LLM_PROVIDER) + Reversierungshinweis
  - KEIN DB-Aufruf, KEIN LLM-Call, KEIN Routing-Wechsel

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest grün (561 Basis)
  - [ ] Exit 0 + promotion_record.json erstellt wenn Gates bestehen und note nicht leer
  - [ ] Exit 1 wenn Report-Gates nicht bestehen
  - [ ] Exit 1 wenn Report-Datei nicht gefunden
  - [ ] Exit 1 bei leerem --operator-note
  - [ ] Aktivierungs- und Reversierungshinweis ausgegeben
  - [ ] research --help zeigt record-promotion in research-Gruppe
```

**Sprint-8 Abschlusskriterien:**

```
Sprint 8 gilt als abgeschlossen wenn:
  - [x] 8.1: tuning.py vollständig implementiert + getestet
  - [x] 8.2: prepare-tuning-artifact CLI implementiert + getestet
  - [x] 8.3: record-promotion CLI implementiert + getestet
  - [x] ruff check . sauber
  - [x] pytest passing (571 Tests, kein Rückschritt)
  - [x] check-promotion, benchmark-companion, evaluate-datasets unverändert
  - [x] docs/contracts.md §19 + I-40–I-45 ✅
  - [x] TASKLIST.md Sprint-8 Tasks aktualisiert
  - [x] AGENTS.md Test-Stand aktualisiert
  - [x] tuning_promotion_contract.md vollständig und konsistent
```

---

## Sprint 9 — Promotion Audit Hardening: I-34 Automation, Artifact Consistency, Gate Summary

> **Startet erst nach Sprint 8-Abschluss.** Sprint 8 ✅ (571 Tests, ruff clean)

**Ziel**: Drei Härtungseigenschaften nach einer Companion-Promotion-Entscheidung sicherstellen:
1. I-34 formell als automatischer 6. Gate (G6: `false_actionable_rate ≤ 0.05`) in contracts.md verankert
2. `PromotionRecord` beinhaltet `gates_summary` — alle 6 Gate-Ergebnisse zum Schreibzeitpunkt
3. Wenn `--tuning-artifact` angegeben: Artifact muss auf denselben Eval-Report verweisen

**Contract-Basis**:
- `docs/sprint9_promotion_audit_contract.md` (kanonische Sprint-9 Referenz)
- `docs/contracts.md §20` (I-46–I-50)

**Context**: Codex hat Sprint 8 über den ursprünglichen Contract hinaus erweitert —
`false_actionable_rate` + `false_actionable_pass` sind bereits implementiert.
Sprint 9 formalisiert diese Erweiterung und vervollständigt den Audit-Trail.

| # | Task | Agent | Status |
|---|---|---|---|
| 9.1 | `PromotionRecord.gates_summary` + `save_promotion_record(gates_summary=...)` + Artifact-Linkage-Validation + Tests in `test_tuning.py` | Codex | done |
| 9.2 | CLI `record-promotion`: `gates_summary` aus `validate_promotion()` uebergeben | Codex | done |
| 9.3 | Tests: `test_cli.py` - G6 in `check-promotion` + `gates_summary` in `record-promotion` Output | Codex | done |
| 9.4 | `sprint9_promotion_audit_contract.md` + `contracts.md §20` + I-46–I-50 + I-34/I-45 Update | Claude Code | ✅ |
| 9.5 | `intelligence_architecture.md` Sprint-9 Update + AGENTS.md + TASKLIST.md | Claude Code | ✅ |
| 9.6 | Contract-Abnahme: benchmark_promotion_contract.md I-34 bereinigt, Baselines aktualisiert, Codex-Specs finalisiert | Claude Code | ✅ |

**Codex-Spec für 9.1 — PromotionRecord + save_promotion_record:**

→ Vollständige Spec: `docs/sprint9_promotion_audit_contract.md §Codex-Spec 9.1`

Kurzzusammenfassung:
```
Modul: app/research/tuning.py, tests/unit/test_tuning.py

Änderungen:
  1. PromotionRecord: + gates_summary: dict[str, bool] | None = None
  2. to_json_dict(): "gates_summary": self.gates_summary einbetten
  3. save_promotion_record(): + gates_summary Parameter
  4. Artifact-Linkage: wenn tuning_artifact.evaluation_report != eval_report → ValueError (I-49)

Tests (4 neue):
  - test_save_promotion_record_embeds_gates_summary
  - test_save_promotion_record_null_gates_summary
  - test_save_promotion_record_tuning_artifact_linkage_mismatch
  - test_save_promotion_record_tuning_artifact_missing_eval_report_field_raises

Constraints:
  - NICHT: evaluation.py oder andere Module ändern
  - gates_summary=None ist Default → rückwärtskompatibel
  - Nur tuning.py + test_tuning.py

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_tuning.py grün
  - [x] pytest tests/unit/ grün (598 Tests)
```

**Codex-Spec für 9.2 — record-promotion CLI:**

→ Vollständige Spec: `docs/sprint9_promotion_audit_contract.md §Codex-Spec 9.2`

Kurzzusammenfassung:
```
Modul: app/cli/main.py
Typ: feature (additiv, 5 Zeilen)

Nach validate_promotion(metrics):
  gates_summary = {
      "sentiment_pass": validation.sentiment_pass,
      "priority_pass": validation.priority_pass,
      "relevance_pass": validation.relevance_pass,
      "impact_pass": validation.impact_pass,
      "tag_overlap_pass": validation.tag_overlap_pass,
      "false_actionable_pass": validation.false_actionable_pass,
  }

save_promotion_record(..., gates_summary=gates_summary) — gates_summary hinzufügen.
Artifact-Linkage-Fehler propagiert bereits als ValueError (kein neuer CLI-Code).

Constraints:
  - Kein neues CLI-Flag, keine Verhaltensänderung ohne --tuning-artifact
  - Nur app/cli/main.py

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [x] pytest tests/unit/ grün (598 Tests)
  - [ ] Output-JSON enthält gates_summary dict
  - [ ] --tuning-artifact mit Mismatch → Exit 1
```

**Codex-Spec für 9.3 — Tests:**

→ Vollständige Spec: `docs/sprint9_promotion_audit_contract.md §Codex-Spec 9.3`

Kurzzusammenfassung:
```
Modul: tests/unit/test_cli.py (erweitern)
Typ: test (keine Implementierungsänderung)

Neue Tests:
  - test_research_check_promotion_g6_pass: FAR=0.02 → Exit 0
  - test_research_check_promotion_g6_fail: FAR=0.10 → Exit 1
  - test_research_record_promotion_embeds_gates_summary: Output-JSON hat gates_summary

Constraints:
  - NICHT: main.py oder tuning.py ändern
  - Runner: from typer.testing import CliRunner

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_cli.py grün
  - [x] pytest tests/unit/ grün (598 Tests)
```

**Sprint-9 Abschlusskriterien:**

```
Sprint 9 gilt als abgeschlossen wenn:
  - [x] 9.1: PromotionRecord.gates_summary + Artifact-Linkage implementiert + getestet
  - [x] 9.2: record-promotion CLI gibt gates_summary weiter
  - [x] 9.3: CLI-Tests für G6 (check-promotion) + gates_summary (record-promotion)
  - [x] ruff check . sauber
  - [x] pytest passing (598 Tests, kein Rückschritt)
  - [x] check-promotion, evaluate-datasets, benchmark-companion unverändert
  - [x] docs/contracts.md §20 + I-46–I-50 vollständig (9.4 ✅)
  - [x] I-34 in contracts.md + benchmark_promotion_contract.md als automatisiert markiert (9.6 ✅)
  - [x] TASKLIST.md Sprint-9 Tasks + Baselines aktualisiert (9.5/9.6 ✅)
  - [x] AGENTS.md Test-Stand aktualisiert (598 Tests) (9.5/9B ✅)
  - [x] sprint9_promotion_audit_contract.md vollständig und konsistent (9.4/9.6 ✅)
```

---

## Sprint 10 — Companion Shadow Run: Audit-Only Parallel Inference

> **Startet erst nach Sprint 9-Abschluss.** Sprint 9 ✅ (598 Tests, ruff clean)

**Ziel**: Companion laeuft parallel zum primaeren Provider auf echten analysierten Dokumenten.
Shadow-Ergebnis wird separat in JSONL gespeichert. Kein Einfluss auf Produktivpfade.

**Status**: 🔄 Teilweise implementiert — Compare-Evaluations live, restliche Sprint-13-Erweiterungen offen

**Context**: Sprint 9 hat den Promotion Audit Trail haertet (gates_summary, Artifact-Linkage).
Sprint 10 schliesst den naechsten Audit-Loop: Companion unter Realbedingungen beobachten,
ohne irgendeinen Produktionspfad zu veraendern.

| # | Task | Agent | Status |
|---|---|---|---|
| 10.1 | `app/research/shadow.py`: `ShadowRunRecord`, `DivergenceSummary`, `compute_divergence()`, `write_shadow_record()`, `run_shadow_batch()` + `DocumentRepository.get_recent_analyzed()` + `tests/unit/test_shadow.py` | Codex | ⬜ |
| 10.2 | CLI: `research shadow-run` + `research shadow-report` + `tests/unit/test_cli.py` Shadow-Tests | Codex | ⬜ |
| 10.3 | `docs/sprint10_shadow_run_contract.md` + `contracts.md §21` + I-51–I-55 | Claude Code | ✅ |
| 10.4 | `docs/intelligence_architecture.md` Sprint-10 Update + `AGENTS.md` + `TASKLIST.md` | Claude Code | ✅ |

**Codex-Spec fuer 10.1 — app/research/shadow.py + Repository + Tests:**

→ Vollstaendige Spec: `docs/sprint10_shadow_run_contract.md §Codex-Spec 10.1`

Kurzzusammenfassung:
```
Modul: app/research/shadow.py (NEU), app/storage/repositories/document_repo.py (ERWEITERN)
Testmodul: tests/unit/test_shadow.py (NEU)

ShadowRunRecord: document_id, run_at, primary_provider, primary_analysis_source,
  companion_endpoint, companion_model, primary_result (dict), companion_result (dict|None),
  divergence (dict|None)

DivergenceSummary: sentiment_match, priority_diff, relevance_diff, impact_diff,
  actionable_match, tag_overlap

Funktionen:
  compute_divergence(doc, companion_output) -> DivergenceSummary
  write_shadow_record(record, path)        → appends JSON line to JSONL
  run_shadow_batch(documents, companion, output_path) -> list[ShadowRunRecord]

+ DocumentRepository.get_recent_analyzed(limit) → list[CanonicalDocument]
  (SELECT ... WHERE status='analyzed' ORDER BY fetched_at DESC LIMIT :limit)

Tests (8 neue):
  test_compute_divergence_identical_results
  test_compute_divergence_full_mismatch
  test_compute_divergence_tag_overlap_partial
  test_compute_divergence_both_tags_empty
  test_write_shadow_record_creates_valid_jsonl
  test_write_shadow_record_appends_multiple
  test_run_shadow_batch_calls_companion_per_doc
  test_run_shadow_batch_handles_companion_error

Constraints:
  - NICHT: pipeline.py, apply_to_document(), update_analysis() aendern
  - NICHT: neue DB-Spalten oder Migrationen
  - NICHT: APP_LLM_PROVIDER auslesen oder veraendern

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_shadow.py gruen (8 neue Tests)
  - [ ] pytest tests/unit/ gruen (>= 598 Tests)
```

**Codex-Spec fuer 10.2 — CLI shadow-run + shadow-report:**

→ Vollstaendige Spec: `docs/sprint10_shadow_run_contract.md §Codex-Spec 10.2`

Kurzzusammenfassung:
```
Modul: app/cli/main.py (ERWEITERN)
Testmodul: tests/unit/test_cli.py (ERWEITERN)

Neue Commands (research subgroup):
  research shadow-run --count INT (default 20) --output PATH
  research shadow-report PATH

shadow-run Verhalten:
  1. companion_model_endpoint pruefen → falls None: info + exit 0
  2. repo.get_recent_analyzed(count) laden
  3. InternalCompanionProvider(endpoint, model) erstellen
  4. Fuer jedes doc: companion.analyze(title, raw_text or "")
     → Erfolg: compute_divergence + write_shadow_record
     → Fehler: record mit companion_result=None, divergence=None
  5. Summary ausgeben: N processed, M errors, output path
  6. Exit 0 (immer)

shadow-report Verhalten:
  1. JSONL lesen
  2. Rich Table: document_id, primary_provider, sentiment_match, priority_diff,
     relevance_diff, impact_diff, actionable_match, tag_overlap
  3. Aggregat: total, errors, sentiment_agreement_rate, actionable_agreement_rate,
     avg_priority_diff, avg_relevance_diff, avg_impact_diff
  4. Exit 0

CLI-Tests (5 neue):
  test_research_shadow_run_skips_when_no_endpoint
  test_research_shadow_run_writes_jsonl
  test_research_shadow_run_handles_companion_error
  test_research_shadow_report_prints_table
  test_research_shadow_report_missing_file_exits

Constraints:
  - NICHT: apply_to_document() oder update_analysis() aufrufen
  - Exit 0 auch bei Companion-Fehlern (nicht-fatal)

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_cli.py gruen
  - [ ] pytest tests/unit/ gruen (>= 598 + neue Tests)
  - [ ] shadow-run schreibt korrektes JSONL
  - [ ] shadow-report zeigt Tabelle + Aggregat
```

**Sprint-10 Abschlusskriterien:**

```
Sprint 10 gilt als abgeschlossen wenn:
  - [ ] 10.1: shadow.py + DocumentRepository.get_recent_analyzed() + test_shadow.py gruen
  - [ ] 10.2: shadow-run + shadow-report CLI + CLI-Tests gruen
  - [x] 10.3: sprint10_shadow_run_contract.md + contracts.md §21 + I-51–I-55 vollstaendig
  - [x] 10.4: intelligence_architecture.md + AGENTS.md + TASKLIST.md aktualisiert
  - [ ] ruff check . sauber
  - [ ] pytest passing (>= 598 + neue Shadow-Tests, kein Rueckschritt)
  - [ ] shadow-run schreibt JSONL ohne DB-Writes zu canonical_documents
  - [ ] Kein Einfluss auf primary analysis pipeline, research outputs, oder alert-Pfade
```

---

## Sprint 11 — Distillation Harness und Evaluation Engine

> **Startet erst nach Sprint 10-Abschluss.** Sprint 10 ⏳ (Contract ✅, Codex 10.1/10.2 ausstehend)

**Ziel**: Einheitlicher Distillation-Readiness-Harness, der Teacher-, Candidate- und Shadow-Daten
kombiniert. Evaluation Engine ohne Training. Distillation Manifest als strukturierter JSON-Audit.

**Status**: 🔄 Teilweise implementiert — Compare-Evaluations und Upgrade-Cycle live, Promotion-/Comparison-Follow-up offen

**Context**: Sprint 10 liefert Shadow-JSONL (offline batch + live inline). Sprint 11 baut
darauf auf: `compute_shadow_coverage()` liest beide Shadow-Formate. `build_distillation_report()`
kombiniert `compare_datasets()` + `validate_promotion()` + Shadow Coverage.

| # | Task | Agent | Status |
|---|---|---|---|
| 11.1 | `app/research/distillation.py`: `DistillationInputs`, `ShadowCoverageReport`, `DistillationReadinessReport`, `compute_shadow_coverage()`, `build_distillation_report()`, `save_distillation_manifest()` + `tests/unit/test_distillation.py` | Codex | ⬜ |
| 11.2 | CLI: `research distillation-check` + `tests/unit/test_cli.py` Distillation-Tests | Codex | ⬜ |
| 11.3 | `docs/sprint11_distillation_contract.md` + `contracts.md §22` + I-58–I-62 | Claude Code | ✅ |
| 11.4 | `docs/intelligence_architecture.md` Sprint-11 Update + `AGENTS.md` + `TASKLIST.md` | Claude Code | ✅ |

**Codex-Spec fuer 11.1 — app/research/distillation.py + Tests:**

→ Vollstaendige Spec: `docs/sprint11_distillation_contract.md §Codex-Spec 11.1`

Kurzzusammenfassung:
```
Modul: app/research/distillation.py (NEU)
Testmodul: tests/unit/test_distillation.py (NEU)

Datenklassen:
  DistillationInputs:  teacher_path|None, candidate_path|None, eval_report_path|None,
                       shadow_path|None, dataset_type="internal_benchmark"
  ShadowCoverageReport: total/error/valid_records + agreement rates + avg diffs
  DistillationReadinessReport: generated_at, inputs, evaluation (EvaluationReport),
                               promotion_validation (PromotionValidation),
                               shadow_coverage (ShadowCoverageReport|None), notes

Funktionen:
  compute_shadow_coverage(shadow_path) -> ShadowCoverageReport
    normalisiert BEIDE Shadow-Formate:
    - batch (shadow.py):     divergence.priority_diff / relevance_diff / impact_diff
    - live (evaluation.py):  deviations.priority_delta / relevance_delta / impact_delta

  build_distillation_report(inputs) -> DistillationReadinessReport
    wenn eval_report_path: JSON laden (kein compare_datasets)
    sonst: load_jsonl(teacher) + load_jsonl(candidate) -> compare_datasets()
    + validate_promotion(metrics) + optional compute_shadow_coverage()

  save_distillation_manifest(report, path) -> Path

Tests (>= 10):
  test_compute_shadow_coverage_batch_format
  test_compute_shadow_coverage_live_format
  test_compute_shadow_coverage_mixed_formats
  test_compute_shadow_coverage_error_records
  test_compute_shadow_coverage_file_not_found
  test_build_distillation_report_with_teacher_candidate
  test_build_distillation_report_with_eval_report_path
  test_build_distillation_report_with_shadow
  test_build_distillation_report_missing_inputs_raises
  test_save_distillation_manifest_creates_valid_json
  test_save_distillation_manifest_creates_parent_dirs

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_distillation.py gruen (>= 10 Tests)
  - [ ] pytest tests/unit/ gruen (>= 600 Tests)
```

**Codex-Spec fuer 11.2 — CLI distillation-check:**

→ Vollstaendige Spec: `docs/sprint11_distillation_contract.md §Codex-Spec 11.2`

Kurzzusammenfassung:
```
Modul: app/cli/main.py (ERWEITERN)
Testmodul: tests/unit/test_cli.py (ERWEITERN)

Command: research distillation-check
Optionen: --teacher PATH, --candidate PATH, --eval-report PATH,
          --shadow PATH, --dataset-type STR, --save-manifest PATH

Output-Sektionen:
  1. Evaluation Metrics Table  (bestehenden _build_dataset_evaluation_table WIEDERVERWENDEN)
  2. Shadow Coverage Table     (nur wenn --shadow gesetzt)
  3. Promotion Gate Summary    (bestehenden _print_companion_promotion_readiness WIEDERVERWENDEN)
  4. Manifest saved            (nur wenn --save-manifest gesetzt)

Exit 0: immer (informativer Output, kein Gate)
Exit 1: FileNotFoundError oder Parsing-Fehler

CLI-Tests (>= 5):
  test_research_distillation_check_with_teacher_candidate
  test_research_distillation_check_with_eval_report
  test_research_distillation_check_with_shadow
  test_research_distillation_check_missing_inputs_exits_1
  test_research_distillation_check_saves_manifest

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_cli.py gruen
  - [ ] pytest tests/unit/ gruen (>= 600 + neue Tests)
  - [ ] Alle 3 Output-Sektionen sichtbar
  - [ ] --save-manifest schreibt valides JSON
```

**Sprint-11 Abschlusskriterien:**

```
Sprint 11 gilt als abgeschlossen wenn:
  - [ ] 11.1: distillation.py + test_distillation.py gruen (>= 10 neue Tests)
  - [ ] 11.2: distillation-check CLI + CLI-Tests gruen
  - [x] 11.3: sprint11_distillation_contract.md + contracts.md §22 + I-58–I-62 vollstaendig
  - [x] 11.4: intelligence_architecture.md + AGENTS.md + TASKLIST.md aktualisiert
  - [ ] ruff check . sauber
  - [ ] pytest passing (>= 600 + neue Distillation-Tests, kein Rueckschritt)
  - [ ] compute_shadow_coverage akzeptiert batch- UND live-Shadow-Format
  - [ ] distillation-check zeigt Metrics + Shadow Coverage + Gate Summary
  - [ ] Kein Einfluss auf Routing, pipeline, apply_to_document()
```

---

## Sprint 12 — Training Job Record und Post-Training Evaluation

> **Startet erst nach Sprint 11-Abschluss.** Sprint 11 ✅ (642 Tests, ruff clean)

**Ziel**: Strukturierte Artefaktkette fuer den kontrollierten Trainingsschritt.
Platform erfasst Trainingsintent (TrainingJobRecord), verknuepft Job mit Post-Training
Evaluation (PostTrainingEvaluationSpec), erweitert Promotion-Audit-Trail.
Training bleibt ausschliesslich ein externer Operator-Prozess.

**Status**: ✅ abgeschlossen — 667 Tests, ruff clean

**Context**: Sprint 11 liefert vollstaendigen Distillation-Harness. Sprint 12 schliesst
den Kreis: Vom Teacher-Dataset ueber Training-Manifest zu Post-Training-Evaluation
bis hin zum vollstaendig dokumentierten Promotion-Record. Ausserdem: Shadow-Schema-
Kanonisierung (deviations.*_delta als kanonisch, divergence.*_diff deprecated).

| # | Task | Agent | Status |
|---|---|---|---|
| 12.1 | `app/research/training.py`: `TrainingJobRecord`, `PostTrainingEvaluationSpec`, `save_training_job_record()`, `save_post_training_eval_spec()` + `tests/unit/test_training.py` | Codex | ✅ |
| 12.2 | CLI: `research prepare-training-job` + `research link-training-evaluation` + `record-promotion --training-job` Extension + `tuning.py` PromotionRecord Extension + CLI-Tests | Codex | ✅ |
| 12.3 | `app/research/shadow.py`: canonical `deviations.*_delta` output + `divergence` deprecated alias + `test_shadow.py` Updates | Codex | ✅ |
| 12.4 | `docs/sprint12_training_job_contract.md` + `contracts.md §23` + I-63–I-69 | Claude Code | ✅ |
| 12.5 | `docs/intelligence_architecture.md` Sprint-12 Update + `AGENTS.md` + `TASKLIST.md` | Claude Code | ✅ |

**Codex-Spec fuer 12.1 — app/research/training.py + Tests:**

→ Vollstaendige Spec: `docs/sprint12_training_job_contract.md §Codex-Spec 12.1`

Kurzzusammenfassung:
```
Modul: app/research/training.py (NEU)
Testmodul: tests/unit/test_training.py (NEU)
Keine Imports aus evaluation.py, shadow.py, distillation.py

Datenklassen:
  TrainingJobRecord: teacher_dataset, model_base, target_model_id,
    training_format="openai_chat", row_count, job_id (uuid4), tuning_artifact_path|None, notes
    to_json_dict() → record_type="training_job", status="pending"

  PostTrainingEvaluationSpec: training_job_path, trained_model_id, trained_model_endpoint,
    eval_report_path|None, notes
    to_json_dict() → record_type="post_training_eval"

Funktionen:
  save_training_job_record(output_path, *, teacher_dataset, model_base, target_model_id,
    row_count, tuning_artifact_path=None, notes=None) -> Path
    Validiert: teacher_dataset exists, row_count >= 1, target_model_id non-empty,
               tuning_artifact_path exists wenn gesetzt

  save_post_training_eval_spec(output_path, *, training_job_path, trained_model_id,
    trained_model_endpoint, eval_report_path=None, notes=None) -> Path
    Validiert: training_job_path exists, eval_report_path exists wenn gesetzt,
               trained_model_id + trained_model_endpoint non-empty

Tests (>= 10):
  test_training_job_record_to_json_dict_structure
  test_training_job_record_status_always_pending
  test_training_job_record_training_format_always_openai_chat
  test_save_training_job_record_creates_file
  test_save_training_job_record_raises_on_missing_teacher
  test_save_training_job_record_raises_on_zero_rows
  test_save_training_job_record_raises_on_empty_target_model_id
  test_save_training_job_record_tuning_artifact_optional
  test_post_training_eval_spec_to_json_dict_structure
  test_save_post_training_eval_spec_creates_file
  test_save_post_training_eval_spec_raises_on_missing_job_record
  test_save_post_training_eval_spec_eval_report_optional

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_training.py gruen (>= 10 neue Tests)
  - [ ] pytest tests/unit/ gruen (>= 642 Tests, kein Rueckschritt)
  - [ ] training_format immer "openai_chat"
  - [ ] status immer "pending" bei Erstellung
```

**Codex-Spec fuer 12.2 — CLI + Tuning Extension + Tests:**

→ Vollstaendige Spec: `docs/sprint12_training_job_contract.md §Codex-Spec 12.2`

Kurzzusammenfassung:
```
Module: app/cli/main.py (ERWEITERN), app/research/tuning.py (ERWEITERN)
Testmodul: tests/unit/test_cli.py (ERWEITERN)

1. tuning.py PromotionRecord Extension (additiv):
   - Feld: training_job_record: str | None = None
   - to_json_dict() + save_promotion_record() entsprechend erweitern
   - Wenn gesetzt: Pfad muss existieren → FileNotFoundError

2. research prepare-training-job:
   - Args: teacher_file, model_base, target_model_id
   - Opts: --tuning-artifact, --out (default: training_job_record.json)
   - Laedt JSONL, zaehlt Rows, ruft save_training_job_record() auf
   - Gibt Tabelle + Training-Command-Hinweis aus
   - Exit 1: teacher nicht gefunden oder leer

3. research link-training-evaluation:
   - Args: job_record, eval_report, model_id, endpoint
   - Opt: --out (default: post_training_eval_spec.json)
   - Exit 1: job_record oder eval_report nicht gefunden
   - Gibt Tabelle + next-steps Hinweis aus

4. record-promotion --training-job (optional):
   - Wenn gesetzt: validiert Existenz, uebergibt an save_promotion_record()
   - Bestehende Tests bleiben unveraendert gruen

CLI-Tests (>= 5):
  test_research_prepare_training_job_creates_record
  test_research_prepare_training_job_exits_1_on_missing_teacher
  test_research_prepare_training_job_exits_1_on_empty_teacher
  test_research_link_training_evaluation_creates_spec
  test_research_link_training_evaluation_exits_1_on_missing_job
  test_research_record_promotion_with_training_job_flag

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/ gruen (>= 642 + neue Tests)
  - [ ] record-promotion bestehende Tests unveraendert gruen
  - [ ] PromotionRecord.training_job_record = None wenn --training-job nicht gesetzt
```

**Codex-Spec fuer 12.3 — Shadow Schema Canonicalization:**

→ Vollstaendige Spec: `docs/sprint12_training_job_contract.md §Codex-Spec 12.3`

Kurzzusammenfassung:
```
Modul: app/research/shadow.py (AENDERN)
Testmodul: tests/unit/test_shadow.py (AENDERN)

ShadowRunRecord.to_json_dict() schreibt BEIDE Keys:
  "deviations": { priority_delta, relevance_delta, impact_delta, ... }  ← kanonisch
  "divergence":  { priority_diff, relevance_diff, impact_diff, ... }    ← deprecated alias

DivergenceSummary interne Felder (priority_diff, etc.) bleiben unveraendert.
compute_shadow_coverage() in distillation.py normalisiert weiterhin beide Formate.

test_shadow.py Updates:
  test_shadow_record_has_deviations_key (neu)
  test_shadow_record_divergence_alias_present (neu)
  Bestehende Tests: um "deviations" Key erweitern

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_shadow.py gruen (alle + 2 neue Tests)
  - [ ] pytest tests/unit/ gruen (>= 642 + neue Tests)
  - [ ] "deviations" Key im JSONL output vorhanden
  - [ ] "divergence" Key als backward-compat alias vorhanden
```

**Sprint-12 Abschlusskriterien:**

```
Sprint 12 gilt als abgeschlossen wenn:
  - [x] 12.1: training.py + test_training.py gruen (>= 10 neue Tests)
  - [x] 12.2: CLI prepare-training-job + link-training-evaluation + record-promotion ext + Tests
  - [x] 12.3: shadow.py deviations-Kanonisierung + Tests gruen
  - [x] 12.4: sprint12_training_job_contract.md + contracts.md §23 + I-63–I-69 vollstaendig
  - [x] 12.5: intelligence_architecture.md + AGENTS.md + TASKLIST.md aktualisiert
  - [x] ruff check . sauber
  - [x] pytest passing (>= 642 + neue Training/Shadow-Tests, kein Rueckschritt)
  - [x] PromotionRecord rueckwaertskompatibel (training_job_record=None default)
  - [x] record-promotion bestehende Tests unveraendert gruen
  - [x] Kein Training ausgefuehrt, kein Routing geaendert, kein Auto-Deploy
```

---

## Sprint 13 — Evaluation Comparison und Regression Guard

> **Startet erst nach Sprint 12-Abschluss.** Sprint 12 ✅ (667 Tests, ruff clean)

**Ziel**: Ein neues Modellartefakt muss gegen einen Baseline-Stand verglichen werden.
Reine Einzelmetrik-Betrachtung (G1–G6) reicht nicht mehr — Regression-Sichtbarkeit
ist Pflicht vor Promotion. Vergleichsbericht als persistierbares Audit-Artefakt.

**Status**: ✅ abgeschlossen — 694 Tests, ruff clean. 13.1 superseded (evaluation.py kanonisch), 13.2 ✅ (PromotionRecord.comparison_report_path, record-promotion --comparison, I-72), 13.6 ✅ (upgrade_cycle.py, upgrade-cycle-status)

**Context**: Sprint 12 liefert TrainingJobRecord + PostTrainingEvaluationSpec.
Sprint 13 baut darauf auf: compare_evaluation_reports() verbindet Pre-Training-
Baseline mit Post-Training-Candidate. ComparisonMetrics + compare_metrics() aus
evaluation.py sind bereits implementiert (Codex-Vorarbeit). Sprint 13 formalisiert
mit EvaluationComparisonReport + Hard-Regression-Schwellen + Persistenz + Tests.

| # | Task | Agent | Status |
|---|---|---|---|
| 13.1 | ~~`app/research/comparison.py`~~: SUPERSEDED — `evaluation.py` ist kanonischer Ort; `EvaluationComparisonReport`, `compare_evaluation_reports()`, `save_evaluation_comparison_report()` bereits implementiert | — | ✅ |
| 13.2 | `tuning.py`: `PromotionRecord.comparison_report_path` + `save_promotion_record(comparison_report=None)` + `record-promotion --comparison PATH` CLI-Flag + 3 neue Tests | Codex | ✅ |
| 13.3 | `docs/sprint13_comparison_contract.md` + `contracts.md §24` + I-70–I-74 | Claude Code | ✅ |
| 13.4 | `docs/intelligence_architecture.md` Sprint-13 Update + `AGENTS.md` + `TASKLIST.md` | Claude Code | ✅ |
| 13.5 | `docs/sprint13_comparison_contract.md Part 2` + `contracts.md §25` + I-75–I-79: UpgradeCycleReport Contract | Claude Code | ✅ |
| 13.6 | `app/research/upgrade_cycle.py`: `UpgradeCycleReport`, `derive_cycle_status()`, `build_upgrade_cycle_report()`, `save_upgrade_cycle_report()` + `tests/unit/test_upgrade_cycle.py` (>= 10 Tests) + CLI `research upgrade-cycle-status` (explizite Pfade; bestehenden `cycle-summary`-Command durch `upgrade-cycle-status` ersetzen — `cycle-summary` nutzt Directory-Glob statt expliziter Pfade und entspricht NICHT dem Spec) + CLI-Tests | Codex | ✅ |

**~~Codex-Spec fuer 13.1~~: SUPERSEDED**

```
Task 13.1 (comparison.py) ist superseded.
Begruendung: evaluation.py enthaelt bereits EvaluationComparisonReport,
compare_evaluation_reports(), save_evaluation_comparison_report(),
RegressionSummary. Ein separates comparison.py wuerde Parallelarchitektur erzeugen.
evaluation.py ist kanonischer Ort fuer alle Comparison-Typen.
Vollstaendige Erklaerung: docs/sprint13_comparison_contract.md §Sprint 13C
```

**Codex-Spec fuer 13.2 — PromotionRecord Extension + record-promotion --comparison (Sprint 13C):**

→ Vollstaendige Spec: `docs/sprint13_comparison_contract.md §Codex-Spec 13.2`

Kurzzusammenfassung:
```
Module: app/cli/main.py (ERWEITERN), app/research/tuning.py (ERWEITERN)
Testmodul: tests/unit/test_cli.py (ERWEITERN)

ACHTUNG: compare-evaluations ist VOLLSTAENDIG — nicht anfassen.
  Bestehendes: --out FLAG, Regression-Anzeige, Exit-Codes alles implementiert.

1. tuning.py PromotionRecord Extension (additiv):
   - Neues Feld: comparison_report_path: str | None = None
   - to_json_dict(): "comparison_report_path": self.comparison_report_path (immer)
   - save_promotion_record(): Keyword-Param comparison_report_path: Path | str | None = None
   - Wenn nicht None: Path.exists() pruefen → FileNotFoundError
   - Wenn nicht None: str(path.resolve()) speichern
   - Rueckwaertskompatibel: alle bestehenden Tests bleiben gruen

2. record-promotion --comparison Extension:
   - Neues Option: --comparison PATH (optional, default None)
   - Pfad-Existenzpruefung → Exit 1 wenn nicht gefunden
   - JSON laden → regression_summary.has_regression auslesen (None-safe)
   - Wenn True: "[bold yellow]WARNING:[/bold yellow] Comparison report shows regressions.
     Review before promoting. Promotion proceeds on explicit operator decision."
     (KEIN Exit 1, KEIN Block — I-72)
   - comparison_report_path an save_promotion_record() uebergeben
   - Bestehende record-promotion Tests bleiben unveraendert gruen

CLI-Tests (>= 3):
  test_research_record_promotion_with_comparison_no_regression
  test_research_record_promotion_with_comparison_has_regression_prints_warning
  test_research_record_promotion_comparison_missing_file_exits_1

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/ gruen (>= 691 + neue Tests, kein Rueckschritt)
  - [ ] record-promotion bestehende Tests (ohne --comparison) unveraendert gruen
  - [ ] PromotionRecord.comparison_report_path = None wenn --comparison nicht gesetzt
  - [ ] PromotionRecord.to_json_dict() enthaelt "comparison_report_path" Key immer
  - [ ] Regression-Warning ausgegeben aber kein Exit-Block
  - [ ] comparison_report_path in promotion_record.json vorhanden wenn gesetzt
  - [ ] Exit 1 nur bei fehlender Datei
```

**Codex-Spec fuer 13.6 — app/research/upgrade_cycle.py + Tests + CLI:**

→ Vollstaendige Spec: `docs/sprint13_comparison_contract.md Part 2 §Codex-Spec 13.5`

Kurzzusammenfassung:
```
Modul: app/research/upgrade_cycle.py (NEU)
Testmodul: tests/unit/test_upgrade_cycle.py (NEU)
CLI: app/cli/main.py — research_app.command("upgrade-cycle-status") (NEU, ersetzt cycle-summary)

Imports DIREKT aus evaluation.py (NICHT duplizieren):
  from app.research.evaluation import EvaluationMetrics, validate_promotion

Status-Literal:
  UPGRADE_CYCLE_STATUSES = Literal[
      "prepared", "training_recorded", "evaluated",
      "compared", "promotable", "promoted_manual"
  ]

Datenklasse:
  UpgradeCycleReport: teacher_dataset_path, training_job_record_path,
    evaluation_report_path, comparison_report_path, promotion_readiness,
    promotion_record_path, status, notes
    to_json_dict() → report_type="upgrade_cycle_report"

Funktionen:
  derive_cycle_status(teacher_dataset_path, training_job_record_path,
      evaluation_report_path, comparison_report_path,
      promotion_record_path, promotion_readiness) → str
    - Reihenfolge: promoted_manual > promotable > compared > evaluated
                   > training_recorded > prepared
    - Nur Path.exists() prüfen — KEIN JSON lesen

  build_upgrade_cycle_report(teacher_dataset_path, *, ...) → UpgradeCycleReport
    - Raises FileNotFoundError wenn teacher_dataset_path nicht existiert
    - Wenn evaluation_report_path vorhanden: validate_promotion() aufrufen
    - derive_cycle_status() aufrufen
    - KEINE DB-Calls, LLM-Calls, Netzwerk (I-75, I-62)

  save_upgrade_cycle_report(report, output_path) → Path
    - JSON indent=2 sort_keys=True

CLI-Command research upgrade-cycle-status:
  TEACHER_FILE (Argument, required)
  --training-job PATH (optional)
  --eval-report PATH (optional)
  --comparison PATH (optional)
  --promotion-record PATH (optional)
  --out PATH (default: upgrade_cycle_report.json)
  Output: Artefakt-Status-Tabelle + aktueller Status + naechster Schritt

ACHTUNG: Bestehenden cycle-summary Command ERSETZEN (loeschen und durch
upgrade-cycle-status ersetzen). cycle-summary verwendet Directory-Glob-Scanning
— nicht spec-konform.

Tests (>= 10):
  test_build_upgrade_cycle_report_prepared_status
  test_build_upgrade_cycle_report_training_recorded_status
  test_build_upgrade_cycle_report_evaluated_status
  test_build_upgrade_cycle_report_compared_status
  test_build_upgrade_cycle_report_promotable_status
  test_build_upgrade_cycle_report_promoted_manual_status
  test_build_upgrade_cycle_report_raises_on_missing_teacher
  test_build_upgrade_cycle_report_promotion_readiness_from_eval
  test_save_upgrade_cycle_report_creates_valid_json
  test_save_upgrade_cycle_report_creates_parent_dirs
  test_derive_cycle_status_priority_order
  test_upgrade_cycle_report_to_json_dict_structure

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_upgrade_cycle.py gruen (>= 10 Tests)
  - [ ] pytest tests/unit/ gruen (kein Rueckschritt)
  - [ ] KEINE Duplikation von validate_promotion() oder EvaluationMetrics
  - [ ] build_upgrade_cycle_report() rein datei-basiert (I-75)
  - [ ] derive_cycle_status() KEIN JSON-Lesen, nur Path.exists()
  - [ ] cycle-summary Command nicht mehr vorhanden
  - [ ] upgrade-cycle-status Exit 1 nur bei fehlendem teacher_dataset_path
```

**Sprint-13 / Sprint-13C Abschlusskriterien:**

```
Sprint 13 gilt als abgeschlossen wenn:
  - [x] 13.1: SUPERSEDED — evaluation.py ist kanonischer Ort (kein comparison.py)
  - [x] 13.2: PromotionRecord.comparison_report_path + record-promotion --comparison + 3 Tests
  - [x] 13.3: sprint13_comparison_contract.md + contracts.md §24 + I-70–I-74 vollstaendig
  - [x] 13.4: intelligence_architecture.md + AGENTS.md + TASKLIST.md aktualisiert
  - [x] 13.5: sprint13_comparison_contract.md Part 2 + contracts.md §25 + I-75–I-79 vollstaendig
  - [x] 13.6: upgrade_cycle.py + test_upgrade_cycle.py (12 Tests) + upgrade-cycle-status CLI
  - [x] ruff check . sauber
  - [x] pytest passing (694 Tests, kein Rueckschritt)
  - [x] PromotionRecord.comparison_report_path rueckwaertskompatibel (None default)
  - [x] Regression-Warning ausgegeben wenn regression_summary.has_regression=True (kein Block)
  - [x] G1-G6 Gates unveraendert
  - [x] compare-evaluations --out funktioniert, Regression-Anzeige vorhanden
  - [x] upgrade-cycle-status zeigt Cycle-Status-Tabelle + naechste Schritte
  - [x] build_upgrade_cycle_report() pure computation — keine DB, kein LLM, kein Netzwerk
```

---

## Sprint 14 — Controlled A/B/C Inference Profiles und Signal Distribution

> **Startet erst nach Sprint 13-Abschluss.** Sprint 13 ✅ (701 Tests, ruff clean)

**Ziel**: Eine kleine, auditierbare A/B/C-Inferenzarchitektur definieren, die bestehende
Primary-, Shadow-, Comparison-, Promotion- und Upgrade-Cycle-Artefakte zusammenführt,
ohne Auto-Routing oder Auto-Promotion einzuführen.

**Status**: 🔄 Contract definiert — Runtime-Implementierung noch offen

**Contract-Basis**:
- `docs/sprint14_inference_distribution_contract.md`
- `docs/contracts.md §26`
- `docs/intelligence_architecture.md` Sprint-14 Abschnitt

| # | Task | Agent | Status |
|---|---|---|---|
| 14.1 | `docs/sprint14_inference_distribution_contract.md` + `contracts.md §26` + I-80–I-87 | Claude Code | ✅ |
| 14.2 | `docs/intelligence_architecture.md` + `TASKLIST.md` Sprint-14 Ausrichtung | Claude Code | ✅ |
| 14.3 | Route-Profile-Artefakt: deklaratives `InferenceRouteProfile` laden/validieren, ohne Live-Routing zu ändern | Claude Code | ✅ |
| 14.4 | A/B/C-Envelope-Builder über bestehende Primary-/Shadow-/Comparison-Artefakte | Claude Code | ✅ |
| 14.5 | Audit-sichere Distribution Targets für Briefs, Signals und Vergleichsartefakte | Claude Code | ✅ |

**Sprint-14 Kernregeln:**
- A = primary path (einziger Pfad mit Produktionspersistenz)
- B = shadow/trained companion path (InternalCompanionProvider, audit-only)
- C = control/rule path (RuleAnalyzer, immer verfuegbar)
- Distribution != Decision (I-83)
- Routing configuration != activation (I-84)
- Kein Auto-Routing, kein Auto-Promote, kein produktives Ueberschreiben durch B/C
- ABCInferenceEnvelope ist pure composition (I-88)
- create-inference-profile ist pure file output (I-89)

**Codex-Spec fuer 14.3 — inference_profile.py:**

→ Vollstaendige Spec: `docs/sprint14_inference_distribution_contract.md Part C §Codex-Spec 14.3`

Kurzzusammenfassung:
```
Modul: app/research/inference_profile.py (NEU)
       tests/unit/test_inference_profile.py (NEU)

Zu implementieren:
  - DistributionTarget dataclass + to_dict()
  - InferenceRouteProfile dataclass + to_json_dict()
  - VALID_ROUTE_PROFILES frozenset
  - save_inference_route_profile(profile, output_path) -> Path
  - load_inference_route_profile(path) -> InferenceRouteProfile

Tests (>= 8):
  test_inference_route_profile_to_json_dict_structure
  test_inference_route_profile_report_type_always_present
  test_save_inference_route_profile_creates_file
  test_save_inference_route_profile_invalid_route_raises_value_error
  test_load_inference_route_profile_roundtrip
  + 3 weitere

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_inference_profile.py gruen (>= 8 Tests)
  - [ ] pytest tests/unit/ gruen (>= 701 Tests, kein Rueckschritt)
  - [ ] Kein Import von app/analysis/ oder app/storage/
```

**Codex-Spec fuer 14.4 — abc_result.py:**

→ Vollstaendige Spec: `docs/sprint14_inference_distribution_contract.md Part C §Codex-Spec 14.4`

Kurzzusammenfassung:
```
Modul: app/research/abc_result.py (NEU)
       tests/unit/test_abc_result.py (NEU)

Zu implementieren:
  - PathResultEnvelope dataclass
  - PathComparisonSummary dataclass
  - DistributionMetadata dataclass (decision_owner="operator", activation_state="audit_only")
  - ABCInferenceEnvelope dataclass + to_json_dict()
  - save_abc_inference_envelope(envelope, output_path) -> Path
  - save_abc_inference_envelope_jsonl(envelopes, output_path) -> Path (APPEND)

Tests (>= 10):
  test_abc_inference_envelope_to_json_dict_structure
  test_abc_inference_envelope_report_type_always_present
  test_save_abc_inference_envelope_creates_file
  test_save_abc_inference_envelope_jsonl_appends
  test_distribution_metadata_decision_owner_default
  + 5 weitere

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_abc_result.py gruen (>= 10 Tests)
  - [ ] pytest tests/unit/ gruen (kein Rueckschritt)
  - [ ] KEIN DB-Import in abc_result.py
  - [ ] JSONL-Append (nicht overwrite)
```

**Codex-Spec fuer 14.5 — CLI create-inference-profile + abc-run:**

→ Vollstaendige Spec: `docs/sprint14_inference_distribution_contract.md Part C §Codex-Spec 14.5`

Kurzzusammenfassung:
```
Modul: app/cli/main.py (ERWEITERN)
       tests/unit/test_cli.py (ERWEITERN)

VORAUSSETZUNG: Tasks 14.3 + 14.4 gruen.

1. research create-inference-profile:
   - Validiert --route-profile Wert -> Exit 1 wenn ungueltig
   - Erstellt InferenceRouteProfile, ruft save_inference_route_profile()
   - Druckt Tabelle + Disclaimer (I-80, I-84, I-89)
   - Exit 0 bei Erfolg, kein DB-Aufruf

2. research abc-run:
   - Laedt InferenceRouteProfile aus --profile
   - Liest shadow JSONL bei --shadow-jsonl
   - Laedt comparison_report wenn --comparison-report angegeben
   - Baut ABCInferenceEnvelope, schreibt nach --out
   - Exit 1: --profile nicht gefunden
   - Exit 1: document_id nicht in shadow JSONL
   - Kein apply_to_document(), kein DB-Write (I-88)

CLI-Tests (>= 6):
  test_research_create_inference_profile_primary_only
  test_research_create_inference_profile_invalid_route_exits_1
  test_research_create_inference_profile_creates_json_file
  test_research_abc_run_builds_envelope
  test_research_abc_run_missing_profile_exits_1
  test_research_abc_run_with_comparison_report

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_cli.py gruen (bestehende + neue Tests)
  - [ ] pytest tests/unit/ gruen (kein Rueckschritt)
  - [ ] research --help zeigt create-inference-profile und abc-run
  - [ ] Kein Auto-Routing, kein DB-Write in beiden Commands
```

**Sprint-14 Abschlusskriterien:**

```
Sprint 14 gilt als abgeschlossen wenn:
  - [x] 14.1: sprint14_inference_distribution_contract.md vollstaendig
              contracts.md §26 + I-80–I-89 vollstaendig
  - [x] 14.2: intelligence_architecture.md Sprint-14 + Implementierungstabelle
              AGENTS.md P19 eingetragen
  - [x] 14.3: inference_profile.py + test_inference_profile.py (11 Tests) gruen ✅
  - [x] 14.4: abc_result.py + test_abc_result.py (11 Tests) gruen ✅
  - [x] 14.5: CLI create-inference-profile + abc-run + 6 Tests gruen ✅ (2026-03-20)
  - [x] ruff check . sauber ✅
  - [x] pytest passing (801 Tests, kein Rueckschritt) ✅
  - [x] Kein Auto-Routing eingebaut ✅
  - [x] Kein Auto-Promote eingebaut ✅
  - [x] Keine produktive Ueberschreibung durch Shadow/Control (I-81, I-82) ✅
  - [x] Jeder A/B/C-Output auditierbar: document_id + path_label + provider + analysis_source (I-85) ✅
```

---

## Sprint 15 — Newsdata.io Integration ✅ (vor 2026-03-20)

**Sprint 15 = Newsdata.io Integration** (war bereits abgeschlossen):
- `app/integrations/newsdata/` — NewsdataClient, NewsdataAdapter
- `ProviderSettings.newsdata_api_key`, FetchResult → CanonicalDocument, validate()
- `tests/unit/test_newsdata_adapter.py` (19 Tests)

---

## Sprint 14C — Runtime Route Activation (2026-03-20)

Sprint 14C liefert den in I-84 genannten "explicit future runtime hook":
Das `InferenceRouteProfile` kann jetzt persistiert aktiviert werden —
`route-activate` schreibt eine State-Datei, die `analyze-pending` liest.

**Neue Komponenten:**
- `app/research/active_route.py` — `ActiveRouteState`, `activate_route_profile()`,
  `load_active_route_state()`, `deactivate_route_profile()`
- CLI: `research route-activate`, `research route-deactivate`
- `test_active_route.py` (20 Tests), 7 neue CLI-Tests

**Neue Invarianten I-90–I-93** (siehe `docs/contracts.md`):
- I-90: ActiveRouteStore schreibt nur in eine dedizierte State-Datei, NIEMALS in .env/settings
- I-91: `route-activate` aendert APP_LLM_PROVIDER NICHT (I-80)
- I-92: `analyze-pending` mit aktivem Shadow schreibt Primaer-Ergebnisse nur in DB (I-51, I-82)
- I-93: ABCInferenceEnvelope wird nur als Audit-JSONL geschrieben — keine DB-Writes

**Sprint-14C Abschlusskriterien:**

```
Sprint 14C gilt als abgeschlossen wenn:
  - [x] 14C.1: active_route.py + test_active_route.py (20 Tests) gruen ✅ (2026-03-20)
  - [x] 14C.2: CLI route-activate + route-deactivate (7 Tests) gruen ✅ (2026-03-20)
  - [x] ruff check . sauber ✅
  - [x] pytest passing (830 Tests, kein Rueckschritt) ✅
  - [x] APP_LLM_PROVIDER unveraendert nach route-activate (I-80, I-91) ✅
  - [x] I-91 explizit per monkeypatch-Test verifiziert ✅
  - [x] Kein Auto-Routing: route-activate ist explizite Operator-Aktion (I-84) ✅
  - [x] ABCInferenceEnvelope bleibt Audit-JSONL only (I-93) ✅
  - [x] AGENTS.md P21 eingetragen ✅
  - [x] contracts.md I-90–I-93 vollstaendig ✅
  - [x] sprint14_inference_distribution_contract.md Sprint-14C Abschnitt ✅
  - [x] intelligence_architecture.md Sprint-14C Zeile ✅
```

---

## Sprint 17 — analyze-pending Route Integration (2026-03-20)

Sprint 17 schließt den Kreis der A/B/C-Architektur: `analyze-pending` liest den
aktiven Route-State (Sprint 16) und führt Shadow- und Control-Pfade aus. Ergebnisse
gehen ausschließlich als `ABCInferenceEnvelope` in die Audit-JSONL (I-92, I-93).

**Neue Komponenten:**
- `app/research/route_runner.py` — `map_path_to_provider_name()`,
  `build_path_result_from_llm_output()`, `build_path_result_from_analysis_result()`,
  `build_comparison_summaries()`, `build_abc_envelope()`, `run_route_provider()`
- `analyze-pending` (modified): liest ActiveRouteState, erstellt Shadow/Control-Provider,
  baut ABCInferenceEnvelopes, schreibt in `abc_envelope_output` JSONL
- `tests/unit/test_route_runner.py` (25 Tests)
- 6 neue CLI-Tests in `test_cli.py`

**Invarianten:**
- I-92: Primary → DB, Shadow/Control → JSONL audit only
- I-93: ABCInferenceEnvelope → JSONL only, kein DB-Write
- I-90/I-91: analyze-pending ändert APP_LLM_PROVIDER nicht
- `--shadow-companion` wird ignoriert wenn aktiver Route-State vorhanden

**Sprint-17 Abschlusskriterien:**

```
Sprint 17 gilt als abgeschlossen wenn:
  - [x] 17.1: route_runner.py + test_route_runner.py (25 Tests) gruen ✅ (2026-03-20)
  - [x] 17.2: analyze-pending route integration (6 CLI-Tests) gruen ✅ (2026-03-20)
  - [x] ruff check . sauber ✅
  - [x] pytest passing (836 Tests, kein Rueckschritt) ✅
  - [x] Primary → DB only (I-92), Shadow/Control → JSONL only (I-93) ✅
  - [x] APP_LLM_PROVIDER unveraendert (I-90, I-91) ✅
  - [x] ABCInferenceEnvelope.to_json_dict() serialisierbar ✅
  - [x] run_route_provider() never raises (exception captured) ✅
  - [x] activation_state="active" in DistributionMetadata ✅
  - [x] --shadow-companion suppressed by active route (I-84) ✅
  - [x] AGENTS.md P22 eingetragen ✅
  - [x] TASKLIST.md Sprint-17 vollstaendig ✅
  - [x] intelligence_architecture.md Sprint-17 Zeile ✅
  - [x] docs/sprint17_route_integration_contract.md vollstaendig ✅
  - [x] I-90–I-93 in docs/contracts.md §27 ✅
```

---

## Sprint 18 — Controlled MCP Server Integration (2026-03-20)

Sprint 18 definiert und dokumentiert die kontrollierte MCP-Schnittstelle:
read-first Zugriff auf KAI Research-Surface + streng begrenzte, auditierbare Write-Aktionen.

**Neue/bestehende Komponenten:**
- `app/agents/mcp_server.py` — 8 Read-Tools + 3 Guarded-Write-Tools + `_resolve_workspace_path()` Workspace-Guard
- `docs/sprint18_mcp_contract.md` — vollstaendiger MCP Contract
- `tests/unit/test_mcp_server.py` — 19 Tests

**Neue Invarianten I-94–I-100** (siehe `docs/contracts.md §29`):
- I-94: Workspace-Confinement — kein Path-Traversal via MCP
- I-95: Read-Tools sind seiteneffektfrei (kein DB-Write, kein Routing-Change)
- I-96: Write-Tools produzieren genau eine Artifact-Datei — kein APP_LLM_PROVIDER Change
- I-97: Jede Write-Aktion gibt `app_llm_provider_unchanged: true` zurueck
- I-98: Keine Trading-Execution via MCP
- I-99: Kein Auto-Routing, kein Auto-Promotion via MCP
- I-100: Dataset-Export, Training, Promotion-Recording bleiben CLI-only

**Read Surface:**
- `get_watchlists`, `get_research_brief`, `get_signal_candidates`
- `get_route_profile_report`, `get_inference_route_profile`, `get_active_route_status`
- `get_upgrade_cycle_status`, `get_mcp_capabilities`

**Guarded Write Surface:**
- `create_inference_profile`, `activate_route_profile`, `deactivate_route_profile`

**Sprint-18 Abschlusskriterien:**

```
Sprint 18 gilt als abgeschlossen wenn:
  - [x] 18.1: mcp_server.py — 11 Tools, _resolve_workspace_path() ✅ (2026-03-20)
  - [x] 18.2: test_mcp_server.py — 19 Tests gruen ✅ (2026-03-20)
  - [x] 18.3: I-94–I-100 in docs/contracts.md §29 ✅
  - [x] 18.4: docs/sprint18_mcp_contract.md vollstaendig ✅
  - [x] 18.5: app_llm_provider_unchanged in activate_route_profile return (I-97) ✅
  - [x] 18.6: _require_artifacts_subpath() — Write-Pfade auf artifacts/ beschraenkt (I-95) ✅
  - [x] 18.7: _append_mcp_write_audit() → artifacts/mcp_write_audit.jsonl (I-94) ✅
  - [x] ruff check . sauber ✅
  - [x] pytest passing (864 Tests, kein Rueckschritt) ✅
  - [x] Kein Auto-Routing eingebaut ✅
  - [x] Kein Auto-Promotion eingebaut ✅
  - [x] Keine Trading-Execution ✅
  - [x] Workspace-Confinement getestet (2 path-traversal Tests) ✅
  - [x] AGENTS.md P23 eingetragen ✅
  - [x] TASKLIST.md Sprint-18 vollstaendig ✅
  - [x] intelligence_architecture.md Sprint-18 Zeile ✅
```

---

## Sprint 16 — Immutable Signal Handoff Layer (2026-03-20)

Sprint 16 definiert die kontrollierte External Execution Interface / Signal Consumption Layer:
`execution_handoff.py` liefert ein immutables `SignalHandoff`-Artefakt (frozen dataclass)
als kanonisches Delivery-Objekt für externe Konsumenten.

**Kernprinzip (I-101):** KAI produziert Signale. KAI FÜHRT KEINE TRADES AUS.
Signal-Delivery ≠ Execution. Consumption ≠ Trade-Bestätigung. Externer Agent ≠ Trusted Control Plane.

```
Sprint 16 gilt als abgeschlossen wenn:
  - [x] 16.1: app/research/execution_handoff.py — SignalHandoff frozen dataclass ✅
  - [x] 16.2: create_signal_handoff(), save_signal_handoff(), save_signal_handoff_batch_jsonl() ✅
  - [x] 16.3: test_execution_handoff.py — 22 Tests gruen ✅
  - [x] 16.4: CLI: research signal-handoff --out/--out-json ✅
  - [x] 16.5: test_cli.py — 3 signal-handoff Tests gruen ✅
  - [x] 16.6: I-105–I-108 in docs/contracts.md §30 ✅
  - [x] 16.7: docs/sprint16_execution_handoff_contract.md vollstaendig ✅
  - [x] 16.8: AGENTS.md P24 eingetragen ✅
  - [x] 16.9: TASKLIST.md Sprint-16 vollstaendig ✅
  - [x] 16.10: intelligence_architecture.md Sprint-16 Zeile ✅
  - [x] ruff check . sauber ✅
  - [x] pytest passing (897 Tests, kein Rueckschritt) ✅
  - [x] recommended_next_step ausgeschlossen ✅
  - [x] Evidence auf 500 chars begrenzt ✅
  - [x] consumer_note immer gesetzt ✅
  - [x] provenance_complete korrekt berechnet ✅
```

---

## Sprint 19 — Route-Aware Signal Distribution (2026-03-20)

Sprint 19 formalises delivery-class separation across the A/B/C route architecture.
Primary signals (A.*) remain the sole productive handoff surface.
Shadow (B.*) and control (C.*) are permanently audit-only (I-113).

**Kernprinzip (I-112):** Route-aware delivery reports sind read-only.
Kein Write-back, keine Trade-Submission, kein Auto-Routing, kein Auto-Promotion.

```
Sprint 19 gilt als abgeschlossen wenn:
  - [x] 19.1: DeliveryClassification + classify_delivery_for_route() in execution_handoff.py ✅
  - [x] 19.2: SignalHandoff +4 Felder: path_type, delivery_class, consumer_visibility, audit_visibility ✅
  - [x] 19.3: classify_delivery_class() + _DELIVERY_CLASS_* Constants in distribution.py ✅
  - [x] 19.4: RouteAwareDistributionSummary + build_route_aware_distribution_summary() ✅
  - [x] 19.5: DistributionAuditRecord + DistributionClassificationReport + build_distribution_classification_report() ✅
  - [x] 19.6: save_distribution_classification_report() ✅
  - [x] 19.7: test_distribution.py — 21 neue Sprint-19 Tests gruen ✅
  - [x] 19.8: I-109–I-115 in docs/contracts.md §31 ✅
  - [x] 19.9: docs/sprint19_distribution_contract.md vollstaendig ✅
  - [x] 19.10: AGENTS.md P25 eingetragen ✅
  - [x] 19.11: TASKLIST.md Sprint-19 vollstaendig ✅
  - [x] 19.12: intelligence_architecture.md Sprint-19 Zeile ✅
  - [x] ruff check . sauber ✅
  - [x] pytest passing (911 Tests, kein Rueckschritt) ✅
  - [x] Kein Auto-Routing eingebaut ✅
  - [x] Kein Auto-Promotion eingebaut ✅
  - [x] Keine Trading-Execution ✅
  - [x] shadow/control niemals in production-visible gemischt (I-113) ✅
```

---

## Grundregel

> Ein Sprint beginnt erst, wenn der vorherige **vollständig** abgeschlossen ist:
> - alle Tasks ✅
> - `pytest` grün
> - `ruff check` sauber
> - AGENTS.md + contracts.md aktuell



### Sprint 20 — External Consumer Collector & Acknowledgement Orchestration

```
**Sprint 20C Konsolidierung (finaler technischer Stand):**
- Kanonischer Ack-/Collector-Pfad: `app/research/execution_handoff.py` + `app/research/distribution.py`
- Finale MCP-Namen: `acknowledge_signal_handoff(handoff_path, handoff_id, consumer_agent_id, notes)` und `get_handoff_collector_summary(handoff_path, acknowledgement_path)`
- Finale CLI-Namen: `research handoff-acknowledge <handoff_file> --handoff-id --consumer-agent-id` und `research handoff-collector-summary <handoff_file>`
- Nur rückwärtskompatibel: `get_handoff_summary(...)` als Alias auf die Collector Summary
- Kompatibilitätsalias im CLI: `research consumer-ack` und `research handoff-summary`
- Superseded/entfernt: `app/research/consumer_collection.py`
- [x] 20.1: `HandoffAcknowledgement` in `execution_handoff.py` ist der finale Audit-Record ✅
- [x] 20.2: `create_handoff_acknowledgement()` + `append_handoff_acknowledgement_jsonl()` + `load_handoff_acknowledgements()` ✅
- [x] 20.3: `HandoffCollectorSummaryReport` + `build_handoff_collector_summary()` in `distribution.py` ✅
- [x] 20.4: MCP auf finale Ack-/Collector-Namen konsolidiert ✅
- [x] 20.5: CLI auf `handoff-acknowledge` + `handoff-collector-summary` konsolidiert; `consumer-ack` + `handoff-summary` bleiben als Kompatibilitätsaliasse ✅
- [x] 20.9: I-116–I-122 in docs/contracts.md §32 ✅
- [x] 20.10: Sprint-20-Doku auf kanonischen Ack-/Collector-Pfad bereinigt ✅
- [x] 20.11: AGENTS.md P26 eingetragen ✅
- [x] 20.12: TASKLIST.md Sprint-20 vollstaendig ✅
- [x] 20.13: intelligence_architecture.md Sprint-20 Zeile ✅
- [x] ruff check . sauber ✅
- [x] pytest passing (946 Tests, kein Rueckschritt) ✅
- [x] Kein Auto-Routing eingebaut ✅
- [x] Kein Trading-Execution eingebaut ✅
- [x] Acknowledgement is audit-only — kein Reverse-Channel (I-118, I-120) ✅
- [x] Consumer state ≠ routing decision (I-121) ✅
```


---

## Sprint 21 — Operational Readiness Surface

**Ziel**: Observational-only readiness surface für Route Health, Collector Backlog, Artifact State und Shadow/Control Visibility. Kein Auto-Remediation, kein Auto-Routing, kein Auto-Promote.

**Status**: ✅ abgeschlossen — 934 Tests passing, ruff clean

### Sprint 21A — Governance Freeze (Sprint 20C-Abschluss)

- [x] 21A.1: contracts.md §32 bereinigt — kanonischer Runtime-Pfad (execution_handoff + distribution), korrekte CLI-Namen (handoff-acknowledge, handoff-collector-summary)
- [x] 21A.2: AGENTS.md P26 korrigiert — execution_handoff + distribution als Runtime-Canonical, consumer_collection.py als non-canonical/backward-compat
- [x] 21A.3: intelligence_architecture.md Sprint-20-Zeile aktualisiert

### Sprint 21B — Operational Readiness Surface

| # | Task | Status |
|---|---|---|
| 21.1 | `OperationalReadinessReport`, `ReadinessIssue`, `RouteReadinessSummary`, `AlertDispatchSummary`, `OperationalArtifactRefs` | ✅ |
| 21.2 | Severity-/Category-Struktur: backlog, acknowledgement_audit, artifact_state, route_provider, shadow_control_failure, stale_state | ✅ |
| 21.3 | `build_operational_readiness_report(...)` adaptiert nur bestehende Artefakte | ✅ |
| 21.4 | `save_operational_readiness_report()` schreibt strukturiertes JSON | ✅ |
| 21.5 | Collector backlog / orphaned ack / stale pending visibility | ✅ |
| 21.6 | Missing artifact / stale route state / shadow-control failure visibility | ✅ |
| 21.7 | MCP read tool: `get_operational_readiness_summary(...)` | ✅ |
| 21.8 | CLI: `research readiness-summary [--handoff-file] [--ack-file] [--state] [--abc-output] [--alert-audit-dir] [--out]` | ✅ |
| 21.9 | `research operational-alerts` superseded/entfernt; `research readiness-summary` bleibt final | ✅ |
| 21.10 | `alerts audit-summary` bleibt read-only Audit-Helfer | ✅ |
| 21.11 | Tests: `test_operational_readiness.py` + MCP/CLI-Readiness-Fälle | ✅ |
| 21.15 | I-123–I-130 in contracts.md §33 | ✅ |
| 21.16 | Sprint-21-Doku auf Readiness statt AlertBundle konsolidiert | ✅ |
| 21.17 | AGENTS.md P27 | ✅ |
| 21.18 | TASKLIST.md Sprint-21-Block | ✅ |
| 21.19 | intelligence_architecture.md Sprint-21-Zeile | ✅ |

**Quality Checks:**
- [x] ruff check passes ✅
- [x] pytest 934 Tests passing, kein Regression ✅
- [x] Kein Auto-Routing implementiert ✅
- [x] Keine Trading-Execution implementiert ✅
- [x] Kein Auto-Remediation implementiert ✅
- [x] Readiness observational only — kein Trigger für State-Änderungen (I-123) ✅
- [x] Kein zweiter Monitoring-Stack / kein Alert-Writeback ✅

---

## Sprint 22 — Provider Health & Distribution Drift Monitoring

**Ziel**: Den kanonischen Readiness-Stack beibehalten und darin Provider-Health- sowie Distribution-Drift-Monitoring ergänzen. Keine zweite Monitoring-Architektur, keine Auto-Remediation, kein Auto-Routing.

**Status**: ✅ abgeschlossen — 944 Tests passing, ruff clean

| # | Task | Status |
|---|---|---|
| 22A.1 | AGENTS.md P27 korrigiert — operational_readiness.py bleibt der einzige kanonische Monitoring-Stack | ✅ |
| 22A.2 | contracts.md §33 Titel korrigiert: "Operational Readiness Surface" | ✅ |
| 22A.3 | contracts.md §33/§34 auf einen Monitoring-Stack konsolidiert: operational_alerts.py ist superseded | ✅ |
| 22A.4 | TASKLIST.md Sprint-21-Teststand korrigiert: 934 (war 951/973 in Altständen) | ✅ |
| 22B.1 | `get_provider_health(...)` MCP liefert nur den Readiness-abgeleiteten Provider-Health-Slice | ✅ |
| 22B.2 | `get_distribution_drift(...)` MCP liefert nur den Readiness-abgeleiteten Drift-Slice | ✅ |
| 22B.3 | CLI: `research provider-health` bleibt als Readiness-View auf denselben Artefakten | ✅ |
| 22B.4 | CLI: `research drift-summary` bleibt als Readiness-View auf denselben Artefakten | ✅ |
| 22B.5 | `ProviderHealthSummary` + `DistributionDriftSummary` in operational_readiness.py integriert | ✅ |
| 22B.6 | Tests: `test_get_provider_health_*` (4 Tests) in test_mcp_server.py | ✅ |
| 22B.7 | Tests: `test_get_distribution_drift_*` (4 Tests) in test_mcp_server.py | ✅ |
| 22B.8 | I-131–I-138 in contracts.md §34 | ✅ |
| 22B.9 | AGENTS.md P28 — Provider Health & Drift Monitoring Surface | ✅ |
| 22B.10 | Kein Trading-Zugriff, keine Core-DB-Mutation, kein Auto-Routing, kein Auto-Remediation | ✅ |
| 22C.1 | `operational_readiness.py` ist der einzige kanonische Monitoring-/Readiness-Backend-Pfad für MCP und CLI | ✅ |
| 22C.2 | `operational_alerts.py` existiert als Standalone-Check-Library; NICHT im MCP/CLI-Pfad (als Produktionsoberfläche superseded, aber nicht gelöscht) | ✅ |
| 22C.3 | Finale CLI-/MCP-Signaturen: `research readiness-summary`, `research provider-health [--handoff-file] [--state] [--abc-output]`, `research drift-summary [--handoff-file] [--state] [--abc-output]`, `get_provider_health(handoff_path, state_path, abc_output_path)`, `get_distribution_drift(handoff_path, state_path, abc_output_path)` | ✅ |
| 22C.4 | contracts.md §33/§34 auf tatsächliche Implementierung korrigiert: operational_alerts.py nicht "removed", korrekte MCP/CLI-Signaturen, korrekte Artifact-Contract-Felder | ✅ |
| 22C.5 | AGENTS.md P28 korrigiert: operational_alerts.py "standalone, nicht im MCP/CLI-Pfad", korrekte MCP-Signaturen | ✅ |
| 22C.6 | intelligence_architecture.md Sprint-22-Zeile: korrekte MCP-Signaturen, operational_readiness.py als einziger Pfad bestätigt | ✅ |
| 22C.7 | Kanonischer Test-Stand Sprint 22C: 944 passed, 0 failed, ruff clean | ✅ |

---

## Sprint 23 — Protective Gates & Remediation Recommendations

**Ziel**: Den kanonischen Readiness-Stack um eine kleine Protective-Gate- und
Recommendation-Schicht erweitern. Keine zweite Monitoring-Architektur, keine
Auto-Remediation, kein Auto-Routing, keine Trading-Execution.

**Status**: ✅ abgeschlossen — 975 Tests passing, ruff clean

| # | Task | Status |
|---|---|---|
| 23.1 | `operational_readiness.py` bleibt der einzige interne Gate-Backend-Pfad (ProtectiveGateSummary, ProtectiveGateItem in OperationalReadinessReport) | ✅ |
| 23.2 | `app/research/protective_gates.py` ist superseded; der kanonische Gate-Contract bleibt vollständig in `operational_readiness.py` | ✅ |
| 23.3 | MCP-Tools `get_protective_gate_summary(...)` und `get_remediation_recommendations(...)` — read-only, readiness-derived | ✅ |
| 23.4 | CLI-Commands `research gate-summary` und `research remediation-recommendations` — operator-facing read views | ✅ |
| 23.5 | Kanonische Tests liegen in `test_operational_readiness.py`, `test_mcp_server.py` und `test_cli.py`; kein separater `test_protective_gates.py`-Pfad bleibt aktiv | ✅ |
| 23.6 | Invariants I-139–I-145 in contracts.md §35 dokumentiert; AGENTS.md P29 ergänzt; intelligence_architecture.md Sprint 23 row | ✅ |
| 23.7 | `python -m pytest` (949 passed) + `python -m ruff check .` grün | ✅ |

### Sprint 23C — Dokumentations-Konsolidierung (2026-03-20)

```
Kanonischer technischer Stand nach Sprint 23C:
- Einziger Gate-Backend-Pfad: app/research/operational_readiness.py
  - ProtectiveGateSummary (frozen=True): gate_status, blocking_count, warning_count,
    advisory_count, items, execution_enabled=False, write_back_allowed=False
  - ProtectiveGateItem (frozen=True): gate_status, severity, category, summary,
    subsystem, blocking_reason, recommended_actions, evidence_refs
  - _build_protective_gate_summary() intern
  - Eingebettet in OperationalReadinessReport
- app/research/protective_gates.py: EXISTIERT NICHT — war geplant, unmittelbar superseded (I-145)
- Keine GateStatus-StrEnum, keine ActionableRecommendation, kein evaluate_protective_gates() public
- MCP: get_protective_gate_summary() + get_remediation_recommendations() — beide in mcp_server.py via
  _build_protective_gate_payload() / _build_remediation_recommendation_payload() aus OperationalReadinessReport
- CLI: research gate-summary + research remediation-recommendations — beide in cli/main.py implementiert
- Test-Pfade: test_operational_readiness.py (6 Tests), test_mcp_server.py (7 Gate/Readiness-Tests),
  test_cli.py (6 Gate/Readiness-Tests). Kein separater test_protective_gates.py.
- [x] 23C.1: AGENTS.md P29 auf kanonischen Pfad korrigiert (operational_readiness.py statt
         protective_gates.py als Hauptreferenz) ✅
- [x] 23C.2: contracts.md §35 / I-145 bereits korrekt — kein weiterer Change nötig ✅
- [x] 23C.3: intelligence_architecture.md Sprint-23-Zeile bereits korrekt ✅
- [x] 23C.4: TASKLIST.md Sprint-23.2 bereits korrekt (superseded) ✅
- [x] 23C.5: F811-Code-Fix — doppelte get_protective_gate_summary / get_remediation_recommendations Definitionen
         in mcp_server.py (Zeilen 1004–1068, inline-Variante) entfernt; kanonische _build_*-Helper-Variante bleibt ✅
- [x] 23C.6: python -m pytest (975 passed) + ruff clean — kein Regression ✅
- [x] Kein Auto-Routing, kein Auto-Promote, keine Trading-Execution ✅
- [x] Keine zweite Gate-Architektur, kein Parallel-Stack ✅
```

---

## Sprint 24 — Artifact Lifecycle Management Surface (2026-03-20)

**Ziel**: Schließt den operativen Loop aus Sprints 21–23 (detect stale → report stale → archive stale). Keine Auto-Remediation, keine automatischen Löschoperationen, kein Auto-Routing, keine Trading-Execution.

**Status**: ✅ abgeschlossen — 975 Tests passing, ruff clean

| # | Task | Status |
|---|---|---|
| 24.1 | `app/research/artifact_lifecycle.py` — `ArtifactEntry`, `ArtifactInventoryReport` (execution_enabled=False), `ArtifactRotationSummary` | ✅ |
| 24.2 | `build_artifact_inventory(artifacts_dir, stale_after_days=30.0)` — scant top-level .json/.jsonl, excludes archive/ subdir | ✅ |
| 24.3 | `rotate_stale_artifacts(artifacts_dir, stale_after_days=30.0, *, dry_run=True)` — archive-only, never deletes (I-148) | ✅ |
| 24.4 | `save_artifact_inventory()` + `save_artifact_rotation_summary()` — JSON persistence | ✅ |
| 24.5 | MCP: `get_artifact_inventory(artifacts_dir, stale_after_days)` — read-only, workspace-confined (I-149) | ✅ |
| 24.6 | CLI: `research artifact-inventory [--artifacts-dir] [--stale-after-days] [--out]` | ✅ |
| 24.7 | CLI: `research artifact-rotate [--artifacts-dir] [--stale-after-days] [--dry-run/--no-dry-run] [--out]` (default --dry-run, I-152) | ✅ |
| 24.8 | Tests: `test_artifact_lifecycle.py` — 21 Unit-Tests grün | ✅ |
| 24.9 | CLI-Tests in `test_cli.py` — 5 neue Sprint-24-Tests grün | ✅ |
| 24.10 | I-146–I-152 in contracts.md §36 dokumentiert | ✅ |
| 24.11 | `docs/sprint24_artifact_lifecycle_contract.md` vollständig | ✅ |
| 24.12 | AGENTS.md P30 eingetragen | ✅ |
| 24.13 | TASKLIST.md Sprint-24-Block vollständig | ✅ |
| 24.14 | intelligence_architecture.md Sprint-24-Zeile + I-146–I-152 | ✅ |

```
Sprint 24 gilt als abgeschlossen wenn:
  - [x] 24.1: artifact_lifecycle.py — ArtifactEntry, ArtifactInventoryReport, ArtifactRotationSummary ✅
  - [x] 24.2-4: build/rotate/save Funktionen implementiert ✅
  - [x] 24.5: MCP get_artifact_inventory (read-only) ✅
  - [x] 24.6-7: CLI artifact-inventory + artifact-rotate ✅
  - [x] 24.8: test_artifact_lifecycle.py — 21 Tests grün ✅
  - [x] 24.9: test_cli.py Sprint-24-Tests grün ✅
  - [x] 24.10: I-146–I-152 in contracts.md §36 ✅
  - [x] 24.11: sprint24_artifact_lifecycle_contract.md vollständig ✅
  - [x] 24.12: AGENTS.md P30 ✅
  - [x] 24.13: TASKLIST.md vollständig ✅
  - [x] 24.14: intelligence_architecture.md Sprint-24-Zeile ✅
  - [x] ruff check . sauber ✅
  - [x] pytest passing (975 Tests, kein Rückschritt) ✅
  - [x] Kein Auto-Routing ✅
  - [x] Kein Auto-Remediation ✅
  - [x] Keine Trading-Execution ✅
  - [x] dry_run=True ist Default (I-147, I-152) ✅
  - [x] archive/ Subdir ist einziger Schreibpfad (I-148) ✅
  - [x] ArtifactInventoryReport.execution_enabled always False (I-150) ✅
```

---

## Sprint 25 — Safe Artifact Retention & Cleanup Policy (2026-03-20)

**Ziel**: Bestehenden Artifact-Lifecycle-Stack kontrolliert erweitern. Keine zweite Lifecycle-Architektur, keine Auto-Deletion, keine Trading-Execution. Cleanup bleibt archivierende Eligibility im Dry-Run-Modell.

**Status**: ✅ abgeschlossen — 1008 Tests passing, ruff clean

| # | Task | Status |
|---|---|---|
| 25.1 | `artifact_lifecycle.py` erweitert: `ArtifactRetentionEntry`, `ArtifactRetentionReport`, rationale/guidance | ✅ |
| 25.2 | `build_cleanup_eligibility_summary()` + `build_protected_artifact_summary()` auf demselben Retention-Report | ✅ |
| 25.3 | `rotate_stale_artifacts()` archiviert nur noch `rotatable=True`; protected/review-required bleiben fail-closed unberührt | ✅ |
| 25.4 | MCP: `get_artifact_retention_report`, `get_cleanup_eligibility_summary`, `get_protected_artifact_summary` | ✅ |
| 25.5 | CLI: `research artifact-retention`, `research cleanup-eligibility-summary`, `research protected-artifact-summary` | ✅ |
| 25.6 | Tests: Artifact-/MCP-/CLI-Coverage für protected flags, cleanup eligibility, dry-run-default und non-destructive behavior | ✅ |
| 25.7 | `docs/contracts.md` §37 und Modul-AGENTS auf kanonischen Retention-/Cleanup-Pfad aktualisiert | ✅ |

```
Sprint 25 gilt als abgeschlossen wenn:
  - [x] Retention bleibt classification-only; kein Auto-Cleanup, keine Auto-Deletion ✅
  - [x] Protected artifacts bleiben bei Rotation geschützt ✅
  - [x] Cleanup eligibility ist read-only, archive-only und dry-run-first ✅
  - [x] MCP/CLI nutzen denselben kanonischen Lifecycle-Stack ✅
  - [x] python -m pytest (1008 passed) grün ✅
  - [x] python -m ruff check . grün ✅
  - [x] I-153–I-161 in contracts.md §37 ✅
  - [x] AGENTS.md P31 ✅
  - [x] intelligence_architecture.md Sprint-25-Zeile ✅
  - [x] Kein Auto-Routing, kein Auto-Remediation, keine Trading-Execution ✅
  - [x] ArtifactRetentionEntry.delete_eligible always False (I-154) ✅
  - [x] rotate_stale_artifacts() policy-aware: protected + review_required übersprungen (I-155) ✅
```

---

## Sprint 26 — Artifact Governance Surfaces & Operator Review Flow (2026-03-20)

**Ziel**: Governance- und Review-Sichten ausschließlich aus dem kanonischen Retention-Report ableiten. Keine zweite Lifecycle-Architektur, keine Auto-Deletion, keine Trading-Execution.

**Status**: ✅ abgeschlossen — 1014 Tests, ruff clean

| # | Task | Status |
|---|---|---|
| 26.1 | `ReviewRequiredArtifactSummary` + `build_review_required_summary()` als einzige dedizierte Review-Sicht auf dem Retention-Report | ✅ |
| 26.2 | Finale Governance-Summaries bleiben `ArtifactRetentionReport`, `ArtifactCleanupEligibilitySummary`, `ProtectedArtifactSummary`, `ReviewRequiredArtifactSummary` | ✅ |
| 26.3 | MCP: `get_artifact_retention_report` + `get_cleanup_eligibility_summary` + `get_protected_artifact_summary` + `get_review_required_summary` (read-only, workspace-confined) | ✅ |
| 26.4 | CLI: `research artifact-retention` + `research cleanup-eligibility-summary` + `research protected-artifact-summary` + `research review-required-summary` | ✅ |
| 26.5 | Superseded: `ArtifactGovernanceSummary`, `ArtifactPolicyRationaleSummary`, `get_governance_summary`, `get_policy_rationale_summary`, `research governance-summary` | ✅ |
| 26.6 | Tests: Governance-/Review-Coverage läuft nur noch über Retention-/Protected-/Review-required-/CLI-/MCP-Surfaces | ✅ |
| 26.7 | Keine Auto-Deletion, keine Trading-Execution, kein zweiter Governance-Stack | ✅ |
| 26.8 | `docs/contracts.md` §38, `docs/intelligence_architecture.md` Sprint-26-Zeile, `AGENTS.md` P32 | ✅ |

**Quality Checks:**
- 1014 Tests ✅
- ruff clean ✅
- contracts.md §38 ✅
- AGENTS.md P32 ✅


## Sprint 26C — Governance Contract Consolidation (2026-03-20)

**Ziel**: Exakt eine kanonische Governance-/Review-Oberfläche herstellen. Keine doppelten Wahrheiten, keine konkurrierenden Surface-Namen.

**Status**: ✅ abgeschlossen — 1014 Tests, ruff clean

| # | Task | Status |
|---|---|---|
| 26C.1 | Audit bestätigt: öffentlicher Governance-/Review-Pfad wird ausschließlich aus dem Retention-Stack abgeleitet | ✅ |
| 26C.2 | `get_governance_summary` + `get_policy_rationale_summary` aus MCP entfernt; Capabilities auf finalen Surface reduziert | ✅ |
| 26C.3 | `research governance-summary` aus CLI entfernt; `review-required-summary` bleibt mit sichtbarer Rationale/Guidance | ✅ |
| 26C.4 | Governance-/Policy-Rationale-Alt-Modelle und zugehörige Tests aus dem kanonischen Pfad entfernt | ✅ |
| 26C.5 | contracts.md §38, intelligence_architecture.md, AGENTS.md P32 und TASKLIST.md auf denselben Endstand konsolidiert | ✅ |
| 26C.6 | Teststand und Lint nach Konsolidierung erneut vollständig grün | ✅ |

**Kanonische Oberfläche nach Sprint 26C:**

MCP (read-only): get_artifact_retention_report · get_cleanup_eligibility_summary · get_protected_artifact_summary · get_review_required_summary
CLI: research artifact-retention · research cleanup-eligibility-summary · research protected-artifact-summary · research review-required-summary
Modelle: ArtifactRetentionReport · ArtifactCleanupEligibilitySummary · ProtectedArtifactSummary · ReviewRequiredArtifactSummary

**Quality Checks:**
- 1014 Tests ✅
- ruff clean ✅
- contracts.md §38 konsolidiert ✅
- AGENTS.md P32 aktuell ✅


## Sprint 26D — Governance Surface Finalization (2026-03-20)

**Ziel**: Den operativen Governance-/Review-Finalstand ohne Meta-Surfaces absichern. Genau eine produktive Oberfläche bleibt: Retention, Cleanup Eligibility, Protected Artifacts, Review Required.

**Status**: ✅ abgeschlossen — 1014 Tests, ruff clean

| # | Task | Status |
|---|---|---|
| 26D.1 | Audit bestätigt: keine funktionale Doppel-Logik mehr in `artifact_lifecycle.py`, `mcp_server.py`, `main.py`, `test_artifact_lifecycle.py`, `test_mcp_server.py`, `test_cli.py` | ✅ |
| 26D.2 | Operativer Finalstil bestätigt: `artifact-retention`, `cleanup-eligibility-summary`, `protected-artifact-summary`, `review-required-summary` | ✅ |
| 26D.3 | Redundante Meta-Surfaces bleiben superseded: `ArtifactGovernanceSummary`, `ArtifactPolicyRationaleSummary`, `get_governance_summary`, `get_policy_rationale_summary`, `research governance-summary` | ✅ |
| 26D.4 | AGENTS.md und TASKLIST.md auf denselben finalen Sprint-26-Endstand gezogen | ✅ |
| 26D.5 | Vollvalidierung erneut grün: `python -m pytest -q` + `python -m ruff check .` | ✅ |

**Finale Governance-/Review-Oberfläche nach Sprint 26D:**

MCP (read-only): get_artifact_retention_report · get_cleanup_eligibility_summary · get_protected_artifact_summary · get_review_required_summary
CLI: research artifact-retention · research cleanup-eligibility-summary · research protected-artifact-summary · research review-required-summary
Modelle: ArtifactRetentionReport · ArtifactCleanupEligibilitySummary · ProtectedArtifactSummary · ReviewRequiredArtifactSummary

**Quality Checks:**
- 1014 Tests ✅
- ruff clean ✅
- Kein Auto-Routing ✅
- Kein Auto-Promote ✅
- Keine Trading-Execution ✅
- Keine Scope-Explosion ✅

---

## Sprint 27 — Safe Operational Escalation Surface (2026-03-20)

**Ziel**: Auf dem kanonischen Readiness-, Gate- und Governance-Stack eine kleine, sichere, rein read-only Escalation-Oberfläche bereitstellen. Keine zweite Monitoring-Architektur, keine Auto-Remediation, keine Trading-Execution.

**Status**: ✅ abgeschlossen — Vollvalidierung grün

| # | Task | Status |
|---|---|---|
| 27.1 | `operational_readiness.py` bleibt der einzige kanonische Backend-Pfad; Escalation ist nur eine Projektion aus Readiness + ReviewRequired | ✅ |
| 27.2 | `OperationalEscalationItem`, `OperationalEscalationSummary`, `BlockingSummary`, `OperatorActionSummary` in `operational_readiness.py` | ✅ |
| 27.3 | `build_operational_escalation_summary()`, `build_blocking_summary()`, `build_operator_action_summary()` als read-only Projektionen | ✅ |
| 27.4 | MCP: `get_escalation_summary`, `get_blocking_summary`, `get_operator_action_summary` als finale Surface-Namen | ✅ |
| 27.5 | CLI: `research escalation-summary`, `research blocking-summary`, `research operator-action-summary` als finale operator-facing Read-Views | ✅ |
| 27.6 | Retention-Klassifikation konsolidiert: kanonische Signal-Handoff-Artefakte bleiben `audit_trail` und eskalieren nicht fälschlich als `review_required` | ✅ |
| 27.7 | Tests: operative Escalation-, MCP-, CLI- und Retention-Regressionen auf den kanonischen Typen/Surface-Namen konsolidiert | ✅ |
| 27.8 | `docs/contracts.md` §39 sowie Modul-/Repo-AGENTS auf denselben Endstand gezogen | ✅ |

**Kanonische Oberfläche nach Sprint 27:**

MCP (read-only): get_escalation_summary · get_blocking_summary · get_operator_action_summary
CLI: research escalation-summary · research blocking-summary · research operator-action-summary
Modelle: OperationalEscalationSummary · BlockingSummary · OperatorActionSummary

**Quality Checks:**
- Read-only only — kein Auto-Remediation-Pfad ✅
- Kein Auto-Routing, kein Auto-Promote, keine Trading-Execution ✅
- Keine Core-DB-Mutation, keine Lifecycle-/Ack-Writebacks ✅
- `python -m pytest` grün ✅
- `python -m ruff check .` grün ✅

---

## Sprint 27C — CLI-Stabilisierung Escalation + Artifact-Lifecycle (2026-03-20)

**Ziel**: Pre-existing Bugs im Working-Copy-Stand fixieren, die durch `.pyc`-Cache verdeckt waren; CLI-Befehle auf direkte Backend-Calls umstellen (kein Workspace-Guard für CLI).

**Status**: ✅ abgeschlossen — 1052 Tests grün

| # | Task | Status |
|---|---|---|
| 27C.1 | `research_escalation_summary`: doppeltes `out`-Parameter entfernt, fehlendes `state`-Parameter wiederhergestellt (NameError + SyntaxError behoben) | ✅ |
| 27C.2 | `artifact-rotate`: Async/MCP-Call durch direkten `rotate_stale_artifacts()` Call ersetzt; `--dry-run/--no-dry-run` Flag korrigiert | ✅ |
| 27C.3 | `artifact-retention`: Async/MCP-Call durch direkten `build_retention_report()` Call ersetzt; Output auf "Artifact Retention Policy" + per-entry-Details korrigiert | ✅ |
| 27C.4 | `cleanup-eligibility-summary`: Async/MCP-Call durch direkten Stack-Call ersetzt; Output `eligible=N` + `dry_run_default=True` korrigiert | ✅ |
| 27C.5 | `protected-artifact-summary`: Async/MCP-Call durch direkten Stack-Call ersetzt; Output `protected=N` korrigiert | ✅ |
| 27C.6 | `review-required-summary`: Async/MCP-Call durch direkten Stack-Call ersetzt; per-entry `Rationale:` Output ergänzt | ✅ |
| 27C.7 | AGENTS.md P33 Test-Stand auf 1052 aktualisiert | ✅ |
| 27C.8 | docs/contracts.md §39 + TASKLIST.md aktualisiert | ✅ |

**Invariante**: CLI ruft `artifact_lifecycle`-Funktionen direkt auf; MCP-Workspace-Guard (I-95) gilt ausschließlich für MCP-Protokoll-Kontext, nicht für CLI-Nutzung.

**Quality Checks:**
- `python -m pytest` → 1052 passed, 0 failed ✅
- `python -m ruff check app/cli/main.py` → All checks passed ✅

---

## Sprint 28 — Safe Operator Action Queue (2026-03-20)

**Ziel**: Auf dem kanonischen Escalation- und Governance-Stack eine kleine, sichere, rein read-only Operator-Action-Queue bereitstellen. Keine zweite Escalation-Architektur, keine Auto-Remediation, keine Trading-Execution.

**Status**: ✅ abgeschlossen — Vollvalidierung grün

| # | Task | Status |
|---|---|---|
| 28.1 | `operational_readiness.py` bleibt der einzige kanonische Backend-Pfad; Action Queue ist nur eine Projektion aus `OperationalEscalationSummary` | ✅ |
| 28.2 | `ActionQueueItem`, `ActionQueueSummary`, `BlockingActionsSummary`, `PrioritizedActionsSummary`, `ReviewRequiredActionsSummary` in `operational_readiness.py` | ✅ |
| 28.3 | `build_action_queue_summary()`, `build_blocking_actions()`, `build_prioritized_actions()`, `build_review_required_actions()` als read-only Projektionen | ✅ |
| 28.4 | MCP: `get_action_queue_summary`, `get_blocking_actions`, `get_prioritized_actions`, `get_review_required_actions` als finale Surface-Namen | ✅ |
| 28.5 | CLI: `research action-queue-summary`, `research blocking-actions`, `research prioritized-actions`, `research review-required-actions` als finale operator-facing Read-Views | ✅ |
| 28.6 | Queue-Felder konsolidiert: `action_id`, `priority`, `queue_status`, `subsystem`, `operator_action_required`, `evidence_refs` | ✅ |
| 28.7 | Tests: Queue-Bildung, Priorisierung, Blocking-/Review-Slices, MCP-/CLI-Surfaces und Read-only-Invarianten abgesichert | ✅ |
| 28.8 | `docs/contracts.md` §40 sowie Modul-AGENTS auf denselben Endstand gezogen | ✅ |

**Kanonische Oberfläche nach Sprint 28:**

MCP (read-only): get_action_queue_summary · get_blocking_actions · get_prioritized_actions · get_review_required_actions
CLI: research action-queue-summary · research blocking-actions · research prioritized-actions · research review-required-actions
Modelle: ActionQueueSummary · BlockingActionsSummary · PrioritizedActionsSummary · ReviewRequiredActionsSummary

**Quality Checks:**
- Read-only only — kein Auto-Remediation-Pfad ✅
- Kein Auto-Routing, kein Auto-Promote, keine Trading-Execution ✅
- Keine Core-DB-Mutation, keine Lifecycle-/Ack-Writebacks ✅
- `python -m pytest -q` grün ✅
- `python -m ruff check .` grün ✅

---

## Sprint 29 — Read-Only Operator Decision Pack (2026-03-20)

**Ziel**: Auf dem kanonischen Escalation- und Action-Queue-Stack eine kleine, sichere, rein read-only Operator-Decision-Pack-Oberfläche bereitstellen. Kein zweiter Readiness-/Gate-/Governance-Pfad, keine Auto-Remediation, keine Trading-Execution.

**Status**: ✅ abgeschlossen — Vollvalidierung grün

| # | Task | Status |
|---|---|---|
| 29.1 | `operational_readiness.py` bleibt der einzige kanonische Backend-Pfad; Decision Pack bündelt vorhandene kanonische Summaries | ✅ |
| 29.2 | `OperatorDecisionPack` (frozen dataclass): `overall_status`, `blocking_count`, `review_required_count`, `action_queue_count`, `affected_subsystems`, `operator_guidance`, `evidence_refs`, `readiness_summary`, `blocking_summary`, `action_queue_summary`, `review_required_summary` | ✅ |
| 29.3 | `build_operator_decision_pack()` als read-only Aggregation aus allen 4 Sub-Summaries; keyword-only API | ✅ |
| 29.4 | Keine Sprint-29-Sub-Surfaces: keine zweite Overview-/Focus-/Affected-Subsystem-Architektur; nur das kanonische `OperatorDecisionPack` | ✅ |
| 29.5 | MCP: `get_decision_pack_summary` + `get_operator_decision_pack` (Alias) als finale Surface-Namen; keine weiteren Decision-Pack-Nebenpfade | ✅ |
| 29.6 | CLI: `research decision-pack-summary` + `research operator-decision-pack` (Alias) als finale operator-facing Read-Views | ✅ |
| 29.7 | Tests: Pack-Bildung, Status-Ableitung, Blocking-/Review-Counts, MCP-/CLI-Surfaces und Read-only-Invarianten abgesichert (37 Tests in test_operational_escalation.py, 133 in test_cli.py) | ✅ |
| 29.8 | `docs/contracts.md` §41 (I-185–I-192), `AGENTS.md` P35 sowie Modul-AGENTS auf denselben Endstand gezogen | ✅ |

**Kanonische Oberfläche nach Sprint 29:**

MCP (read-only): get_decision_pack_summary · get_operator_decision_pack
CLI: research decision-pack-summary · research operator-decision-pack
Modelle: OperatorDecisionPack

**Quality Checks:**
- Read-only only — kein Auto-Remediation-Pfad ✅
- Kein Auto-Routing, kein Auto-Promote, keine Trading-Execution ✅
- Keine Core-DB-Mutation, keine Lifecycle-/Ack-Writebacks ✅
- `python -m pytest -q` → 1126 passed ✅
- `python -m ruff check .` grün ✅

---

## Sprint 30 — Read-Only Operator Runbook & Command Safety (2026-03-20)

**Ziel**: Auf dem kanonischen Decision-Pack-, Escalation- und Governance-Stack eine kleine, sichere, rein read-only Operator-Runbook-Oberfläche bereitstellen. Keine neue Business-Logik, keine Auto-Ausführung, keine veralteten oder superseded Command-Referenzen.

**Status**: ✅ abgeschlossen — Vollvalidierung grün

| # | Task | Status |
|---|---|---|
| 30.1 | `operational_readiness.py` bleibt der einzige kanonische Backend-Pfad; `build_operator_runbook()` bündelt vorhandene Decision-Pack-/Queue-/Governance-Summaries | ✅ |
| 30.2 | `RunbookStep` + `OperatorRunbookSummary` (frozen) als read-only Modelle mit `command_refs`, `steps` und `next_steps` | ✅ |
| 30.3 | Runbook-Ableitung bleibt rein projektional aus `OperatorDecisionPack`; keine zweite Readiness-, Escalation-, Queue- oder Governance-Architektur | ✅ |
| 30.4 | CLI: `research runbook-summary`, `research runbook-next-steps` und `research operator-runbook` als drei eigenständige operator-facing Read-Views | ✅ |
| 30.5 | MCP: `get_operator_runbook` als read-only Surface mit fail-closed Command-Referenzvalidierung gegen echte registrierte `research`-Commands | ✅ |
| 30.6 | Command Safety Guardrails: keine superseded Befehle im Runbook; Referenzen müssen auf tatsächlich vorhandene kanonische Commands zeigen | ✅ |
| 30.7 | Tests: Runbook-Bildung, geordnete next steps, gültige Command-Referenzen, read-only Verhalten und keine Trading-Semantik präzise abgesichert | ✅ |
| 30.8 | `docs/contracts.md` §42 sowie Root-/Modul-AGENTS auf denselben Endstand gezogen | ✅ |

**Kanonische Oberfläche nach Sprint 30:**

MCP (read-only): get_operator_runbook
CLI: research runbook-summary · research runbook-next-steps · research operator-runbook
Modelle: OperatorRunbookSummary · RunbookStep

**Quality Checks:**
- Read-only only — keine Auto-Ausführung, kein Auto-Routing, kein Auto-Promote, keine Trading-Execution ✅
- Keine Core-DB-Mutation, keine Lifecycle-/Ack-Writebacks, keine destruktiven Seiteneffekte ✅
- Command-Referenzen fail-closed gegen tatsächlich registrierte `research`-Commands validiert ✅
- `python -m pytest -q` → 1075 passed ✅
- `python -m ruff check .` grün ✅

---

## Sprint 31 — CLI Contract Lock & Coverage Recovery (2026-03-21)

**Ziel**: Den kanonischen CLI- und MCP-Surface nach Sprint 30/30C einzufrieren, 6 offene Coverage-Lücken zu schließen und Contract-Klarheit durch §43, I-201–I-210 und P37 herzustellen. Keine neuen Business-Features.

**Status**: ✅ abgeschlossen — Vollvalidierung grün

| # | Task | Status |
|---|---|---|
| 31.1 | `docs/contracts.md` §43 — Canonical CLI & MCP Surface Lock definiert | ✅ |
| 31.2 | `docs/intelligence_architecture.md` I-201–I-210 — CLI/MCP Contract Invarianten | ✅ |
| 31.3 | `AGENTS.md` P37 — Sprint 31 Protokolleintrag | ✅ |
| 31.4 | `app/agents/mcp_server.py` — `get_narrative_clusters` in read_tools hinzugefügt (I-205); `get_operational_escalation_summary` explizit als deprecated dokumentiert (I-204) | ✅ |
| 31.5 | Coverage Recovery: 6 CLI-Commands mit targeted Tests versehen (`signals`, `benchmark-companion-run`, `check-promotion`, `prepare-tuning-artifact`, `record-promotion`, `evaluate`) | ✅ |
| 31.6 | TASKLIST.md Sprint 30.4 korrigiert (kein Alias, drei eigenständige Commands) | ✅ |
| 31.7 | Keine neue Business-Logik, keine neue Monitoring-Architektur, keine Trading-Execution | ✅ |

**Kanonische Oberfläche nach Sprint 31:**

CLI (32 Commands): 4 query_app + 28 research_app — authoritative via `get_registered_research_command_names()`
MCP (38 registriert): 32 read_tools + 4 write_tools + 1 workflow_helper + 2 deprecated Aliase
Coverage: 0 ungetestete CLI-Commands nach Sprint 31

**Quality Checks:**
- Read-only only — keine Auto-Ausführung, kein Auto-Routing, kein Auto-Promote, keine Trading-Execution ✅
- Command-Referenzen fail-closed gegen tatsächlich registrierte `research`-Commands validiert ✅
- `python -m pytest -q` → 1093 passed ✅
- `python -m ruff check .` grün ✅

---

## Sprint 32 — MCP Contract Lock & Coverage Completion (2026-03-21)

**Ziel**: Den MCP-Surface vollständig klassifizieren (canonical/active_alias/superseded/workflow_helper), Coverage auf 100% bringen und Sprint-31-Dokumentationsinkonsistenzen korrigieren. Keine neuen Business-Features.

**Status**: ✅ abgeschlossen — Vollvalidierung grün

| # | Task | Status |
|---|---|---|
| 32.1 | Sprint-31-Inkonsistenzen korrigiert: I-204 (get_handoff_summary ist aktiver Alias, nicht deprecated), I-208 (44 CLI Commands, nicht 32), I-209 (38 total MCP, nicht 39), contracts.md §43 (40 research_app Commands, nicht 28) | ✅ |
| 32.2 | `docs/contracts.md` §44 — MCP Tool Classification Contract mit vollständiger 38-Tool-Tabelle (canonical/active_alias/superseded/workflow_helper) | ✅ |
| 32.3 | `docs/intelligence_architecture.md` I-211–I-220 — MCP Contract Invarianten (Tool-Klassifikation, Superseded-Policy, Coverage-Pflicht) | ✅ |
| 32.4 | `AGENTS.md` P38 — Sprint 32 Protokolleintrag | ✅ |
| 32.5 | Coverage Completion: `get_narrative_clusters` (canonical read-only, I-214) + `get_operational_escalation_summary` (superseded, I-215) targeted Tests | ✅ |
| 32.6 | Keine neue Business-Logik, keine neue Monitoring-Architektur, keine Trading-Execution | ✅ |

**Kanonische MCP-Oberfläche nach Sprint 32:**

Tool-Klassifikation: 34 canonical + 2 active_alias + 1 superseded + 1 workflow_helper = 38 total
read_tools: 32 | write_tools: 4 | workflow_helper: 1 | superseded (nicht in read_tools): 1
Coverage: 0 ungetestete Tools

**Quality Checks:**
- Read-only only — keine Auto-Ausführung, kein Auto-Routing, kein Auto-Promote, keine Trading-Execution ✅
- Superseded tools korrekt aus read_tools ausgeschlossen, test-verifiziert ✅
- `python -m pytest -q` → 1101 passed ✅
- `python -m ruff check .` grün ✅


---

## Sprint 33 — Append-Only Operator Review Journal & Resolution Tracking (2026-03-21)

**Ziel**: Append-only Audit-Schicht für Operator-Review und Resolution-Tracking auf Basis des bestehenden Runbook/Decision-Pack/Governance-Stacks. Keine neue Governance-Architektur, keine Trading-Execution, kein zweites Action-Queue-System.

**Status**: ✅ abgeschlossen — Vollvalidierung grün

| # | Task | Status |
|---|---|---|
| 33.1 | `ReviewJournalEntry` (frozen, append-only), `ReviewJournalSummary`, `ReviewResolutionSummary` in `app/research/operational_readiness.py` — kanonische Implementierung, kein zweites Modul | ✅ |
| 33.2 | `append_review_journal_entry_jsonl()`, `load_review_journal_entries()`, `build_review_journal_summary()`, `build_review_resolution_summary()` — append-only Write-Boundary, keine Core-DB-Mutation | ✅ |
| 33.3 | MCP: `append_review_journal_entry` (guarded_write), `get_review_journal_summary` (read), `get_resolution_summary` (read) — MCP-Surface auf 41 Tools erweitert | ✅ |
| 33.4 | CLI: `research review-journal-append`, `research review-journal-summary`, `research resolution-summary` — 44 Commands bleiben canonical | ✅ |
| 33.5 | `operator_review_journal.jsonl` als `protected` audit_trail in `artifact_lifecycle.py` registriert (I-228) | ✅ |
| 33.6 | Tests: `tests/unit/test_review_journal.py` (8 Tests), `tests/unit/test_cli.py` (3 Tests), `tests/unit/test_mcp_server.py` (3 MCP Tests) — Coverage 100% für Sprint 33 | ✅ |
| 33.7 | `docs/contracts.md` §45, `docs/intelligence_architecture.md` I-221–I-230 (inkl. Update I-209/I-211/I-218), `AGENTS.md` P39, `TASKLIST.md` Sprint 33 | ✅ |
| 33.8 | Keine parallele Architektur: app/research/review_journal.py nicht vorhanden — einziger Backend-Pfad ist operational_readiness.py | ✅ |

**Kanonische Oberfläche nach Sprint 33:**

MCP: 32 read_tools + 5 write_tools + 1 workflow_helper + 2 active_aliases + 1 superseded = 41 total @mcp.tool()
CLI: 44 Commands (4 query_app + 40 research_app) — unverändert
Review Journal Invarianten: I-221–I-230

**Quality Checks:**
- Append-only — kein Rollback, keine Mutation bestehender Journal-Einträge ✅
- Kein Auto-Routing, kein Auto-Promote, keine Trading-Execution, keine Core-DB-Mutation ✅
- `core_state_unchanged=True` in MCP guarded-write Response ✅
- `operator_review_journal.jsonl` rotation-geschützt (I-228) ✅
- `python -m pytest -q` → 1116 passed ✅
- `python -m ruff check .` grün ✅

## Sprint 34 — KAI Phase 1: Risk Engine, Paper Execution, Market Data, Operator Telegram (2026-03-21)

**Ziel**: Minimale, sichere, lauffähige KAI-Basisversion (Phase 1 des KAI Master Prompts). Keine Trading-Execution in Live-Modus, kein freies Margin-Risiko. Alle Komponenten paper-first und audit-fähig.

**Status**: ✅ abgeschlossen — 1158 Tests, ruff clean

| # | Task | Status |
|---|---|---|
| 34.1 | `app/core/settings.py`: `RiskSettings` (RISK_*), `ExecutionSettings` (EXECUTION_*), `OperatorSettings` (OPERATOR_*) + 3 neue Felder in `AppSettings` | ✅ |
| 34.2 | `app/risk/models.py`: `RiskLimits`, `RiskCheckResult`, `PositionSizeResult`, `DailyLossState` (frozen dataclasses) | ✅ |
| 34.3 | `app/risk/engine.py`: `RiskEngine` — 8 Pre-Order-Gates, Kill-Switch, Pause/Resume, auto-trigger bei Limit-Breach, risk-based Position-Sizing | ✅ |
| 34.4 | `app/execution/models.py`: `PaperOrder`, `PaperFill`, `PaperPosition`, `PaperPortfolio` | ✅ |
| 34.5 | `app/execution/paper_engine.py`: `PaperExecutionEngine` — Idempotency-Key-Dedup, JSONL-Audit, Slippage/Fee-Simulation; live_enabled Guard | ✅ |
| 34.6 | `app/market_data/`: `BaseMarketDataAdapter` ABC + `MockMarketDataAdapter` (sinusoidal, deterministisch, keine externen Deps) | ✅ |
| 34.7 | `app/messaging/telegram_bot.py`: `TelegramOperatorBot` — Admin-Whitelist, Double-Confirm-Kill, dry_run, JSONL-Audit; getrennt von `TelegramAlertChannel` | ✅ |
| 34.8 | Tests: 16 Risk + 11 Execution + 7 MarketData + 9 TelegramBot = 42 neue Tests | ✅ |
| 34.9 | `ASSUMPTIONS.md` (A-001–A-011) + `RISK_POLICY.md` (binding policy, 8-Gate-Tabelle, Kill-Switch-Protokoll) | ✅ |

**Kanonische Oberfläche nach Sprint 34:**

Neue Module: `app/risk/`, `app/execution/`, `app/market_data/`, `app/messaging/`
Settings: `RiskSettings`, `ExecutionSettings`, `OperatorSettings` in `AppSettings`
Audit-Trails: `artifacts/paper_execution_audit.jsonl`, `artifacts/operator_commands.jsonl`
Safety: Live-Execution gesperrt (live_enabled=False default), Kill-Switch, Pause/Resume

**Quality Checks:**
- Keine Live-Execution — `paper_engine.py` raises `ValueError` wenn `live_enabled=True` ✅
- Kill-Switch fail-closed (alle Orders blockiert bis explizit reset) ✅
- Admin-Whitelist: nicht-autorisierte Chat-IDs silent-ignored + geloggt ✅
- MockAdapter: zero externe Dependencies, deterministisch ✅
- `python -m pytest -q` → 1158 passed ✅
- `python -m ruff check .` grün ✅

---

## Sprint 35 — KAI Backtest Engine: Signal→Risk→Paper Loop (2026-03-21)

**Ziel**: Den KAI-Execution-Core-Loop schließen: SignalCandidates → RiskEngine → PaperExecutionEngine. Backtest ist paper-only, kill-switch-aware, deterministisch und audit-safe. Keine Live-Execution, kein Gate-Bypass.

**Status**: ✅ abgeschlossen — 1212 Tests, ruff clean

| # | Task | Status |
|---|---|---|
| 35.1 | `app/execution/backtest_engine.py`: `BacktestConfig` (frozen), `SignalExecutionRecord` (frozen), `BacktestResult` (frozen), `BacktestEngine.run(signals, prices)` | ✅ |
| 35.2 | Signal→Order Mapping: bullish→buy, bearish→skip (long_only=True), neutral→skip (I-236); Slippage/Fee-Buffer für fill_rejected-Prävention | ✅ |
| 35.3 | Volle RiskEngine-Integration: check_order() + calculate_position_size() pro Signal (I-232); Kill-Switch Halt (I-237) | ✅ |
| 35.4 | CLI: `research backtest-run` mit --signals-path, --out, --min-confidence, --stop-loss-pct, --audit-path | ✅ |
| 35.5 | Audit: append-only JSONL nach `artifacts/backtest_audit.jsonl` (I-240) | ✅ |
| 35.6 | Tests: 22 BacktestEngine-Tests + 3 CLI-Tests = 25 neue Tests | ✅ |
| 35.7 | `ASSUMPTIONS.md` A-012–A-015, `docs/contracts.md` §46, `docs/intelligence_architecture.md` I-231–I-240, `AGENTS.md` P40, `TASKLIST.md` Sprint 35 | ✅ |
| 35.8 | `Out of Scope`-Eintrag "Historical backtesting engine" aus ASSUMPTIONS.md entfernt — nun implementiert | ✅ |

**Kanonische Oberfläche nach Sprint 35:**

Neue Datei: `app/execution/backtest_engine.py`
CLI: 41 Commands (4 query_app + 41 research_app: backtest-run hinzugefügt) — **CLI-Count auf 41 research_app erhöht**
Outcome-Typen: filled | risk_rejected | skipped_neutral | skipped_bearish | no_price | no_quantity | kill_switch_halted
Audit: `artifacts/backtest_audit.jsonl` (protected, I-240 family)

**Quality Checks:**
- Paper-only: `live_enabled=False` hardcoded, keine Live-Execution-Route (I-231) ✅
- Kein Gate-Bypass: jedes Signal durch alle RiskEngine-Gates (I-232) ✅
- Kill-Switch: sofortiger Halt aller weiteren Fills (I-237) ✅
- Determinismus: gleiche Inputs → gleiche Outputs (I-235) ✅
- BacktestResult immutable (I-233), to_json_dict() ohne interne Pfade (I-239) ✅
- `python -m pytest -q` → 1212 passed ✅
- `python -m ruff check .` grün ✅

---

## Sprint 36 — KAI Core Orchestrator: Signal Engine + Paper Trading Loop (2026-03-21)

**Ziel**: Schließung des Kernpfads: AnalysisResult → SignalCandidate → RiskEngine → PaperExecutionEngine → JSONL-Audit. Kein Live-Execution. Kein freies Margin-Risiko. Alle Komponenten paper-first, immutable, audit-fähig.

**Status**: ✅ abgeschlossen — 1406 Tests, ruff clean, mypy 0 Fehler

| # | Task | Status |
|---|---|---|
| 35.1 | `app/signals/models.py`: `SignalDirection` (StrEnum), `SignalState` (StrEnum), `SignalCandidate` (frozen dataclass) mit allen KAI-Pflichtfeldern (decision_id, thesis, supporting_factors, contradictory_factors, confidence, confluence, market_regime, volatility_state, liquidity_state, entry/exit/sl/tp, invalidation, risk_assessment, traceability, approval/execution state) | ✅ |
| 35.2 | `app/signals/generator.py`: `SignalGenerator` — 6 Filter-Gates (market data, price, stale, confidence, actionable, sentiment, confluence), Confluence-Berechnung (max 5 Punkte: impact, relevance, novelty, assets, sentiment), Regime/Volatility-Ableitung aus change_pct_24h, SL/TP-Berechnung (2:1 R/R), never raises | ✅ |
| 35.3 | `app/orchestrator/models.py`: `CycleStatus` (StrEnum), `LoopCycle` (frozen dataclass) als immutabler Audit-Record jedes Zyklus (7 Status-Werte, alle Step-Flags, Traceability-IDs, notes) | ✅ |
| 35.4 | `app/orchestrator/trading_loop.py`: `TradingLoop.run_cycle()` — 7-Schritt-Pipeline: MarketData → Signal → RiskGate → PositionSize → Order+Fill → DailyLossUpdate → JSONL-Audit; direction→side-Mapping (long→buy, short→sell); never raises; alle Cycles auditiert | ✅ |
| 35.5 | Tests: 23 Signal-Tests (`test_signals.py`) + 14 Trading-Loop-Tests (`test_trading_loop.py`) = 37 neue Tests | ✅ |
| 35.6 | Bugfix: `SignalDirection.value` ("long"/"short") → Order-Side-Mapping ("buy"/"sell") vor `PaperExecutionEngine.create_order()` | ✅ |
| 35.7 | `TASKLIST.md` Sprint 35 + `AGENTS.md` P40 | ✅ |

**Kanonische Oberfläche nach Sprint 35:**

Neue Module: `app/signals/` (models, generator), `app/orchestrator/` (models, trading_loop)
Kernpfad geschlossen: AnalysisResult → SignalCandidate → RiskEngine → PaperExecutionEngine → LoopCycle
Audit-Trail: `artifacts/trading_loop_audit.jsonl` (alle Zyklen, inkl. No-Signal und Risk-Rejected)
Safety: Live-Execution gesperrt, Kill-Switch-Gate, Position-Limit-Gate, Confluence-Gate

**Quality Checks:**
- Signal-Generator: 6 Reject-Gates, never raises, alle Filter unit-getestet ✅
- TradingLoop: never raises, alle Zyklusergebnisse JSONL-auditiert ✅
- Direction→Side-Mapping: long→buy, short→sell (korrekt getrennt von Konzept und Protokoll) ✅
- Immutable: SignalCandidate, LoopCycle, RiskCheckResult, PaperOrder — alle frozen dataclasses ✅
- `python -m pytest -q` → 1406 passed ✅
- `python -m ruff check .` grün ✅
- `python -m mypy app --ignore-missing-imports` → 0 Fehler ✅

---

## Sprint 36 — Decision Journal & TradingLoop CLI/MCP Surface

**Ziel**: Operator-Observability für DecisionJournal und TradingLoop-Audit-Trail über typed CLI und MCP-Tools.

**Status**: ✅ abgeschlossen — 1315 Tests passing, ruff clean

| # | Task | Status |
|---|---|---|
| 36.1 | CLI `research decision-journal-append`: Append validated DecisionInstance via `create_decision_instance()`, append-only JSONL, prints decision_id + execution_enabled=False | ✅ |
| 36.2 | CLI `research decision-journal-summary`: Read-only summary (total_count, by_mode, by_approval, avg_confidence, symbols), execution_enabled=False | ✅ |
| 36.3 | CLI `research loop-cycle-summary`: Read-only JSONL audit table (status_counts, last_n cycles, sig/risk/fill columns), execution_enabled=False | ✅ |
| 36.4 | MCP `get_decision_journal_summary` (canonical_read): Delegates to `build_decision_journal_summary()`, always execution_enabled=False | ✅ |
| 36.5 | MCP `get_loop_cycle_summary` (canonical_read): Reads trading_loop_audit.jsonl, returns status_counts + recent_cycles, always execution_enabled=False | ✅ |
| 36.6 | MCP `append_decision_instance` (guarded_write): Workspace-confined, artifacts/-restricted, write-audit logged, never triggers trade | ✅ |
| 36.7 | `_CANONICAL_MCP_READ_TOOL_NAMES` +2, `_GUARDED_MCP_WRITE_TOOL_NAMES` +1; inventory-matches-registered test ✅ | ✅ |
| 36.8 | Tests: 14 CLI tests (`test_cli_decision_journal.py`) + 20 MCP tests (`test_mcp_sprint36.py`) = 34 neue Tests | ✅ |
| 36.9 | Docs: contracts.md §47, intelligence_architecture.md I-241–I-250, AGENTS.md P42, TASKLIST Sprint 36 | ✅ |

**Kanonische Oberfläche nach Sprint 36:**

Neue CLI-Commands: decision-journal-append, decision-journal-summary, loop-cycle-summary
Neue MCP-Tools: get_decision_journal_summary, get_loop_cycle_summary, append_decision_instance
Sicherheit: Recording ≠ Execution, alle Antworten mit execution_enabled=False, workspace-confined

**Quality Checks:**
- Alle neuen CLI-Commands: inputs validiert, safety-flags sichtbar, JSONL append-only ✅
- Alle neuen MCP-Tools: workspace-confined, artifacts/-restricted, write-audit, read-only ✅
- MCP-Inventory: registered == classified (Pflichttest grün) ✅
- `python -m pytest -q` → 1315 passed ✅
- `python -m ruff check .` grün ✅

---

## Sprint 37+37C — Runtime Schema Binding & Decision Backbone Convergence (konsolidiert)
**Status**: ✅ Abgeschlossen | **Datum**: 2026-03-21 | **Tests**: 1356 | **Ruff**: clean

### Ziel
DECISION_SCHEMA.json und CONFIG_SCHEMA.json zu echten Runtime-Verträgen machen.
DecisionInstance/DecisionRecord-Divergenz schließen. Kanonische Schema-Validation auf jedem Persistenz-Pfad.
Zwei-Schichten-Architektur dokumentieren: Schema-Integrität (schema_binding.py) + Payload-Validierung (runtime_validator.py).

### Deliverables

**Neue Dateien:**
- `app/schemas/__init__.py` — Package-Marker
- `app/schemas/runtime_validator.py` — Kanonische Implementierung: `validate_json_schema_payload()`, `validate_runtime_config_payload()`, `validate_decision_schema_payload()`, `SchemaValidationError`; Kompatibilitäts-Aliases `validate_decision_payload()`, `validate_config_payload()`
- `app/core/schema_binding.py` — Schema-Integrität: `validate_config_schema()`, `validate_decision_schema()`, `validate_decision_schema_alignment()`, `run_all_schema_validations()`, `SchemaValidationResult`; prüft 10 Safety-Consts in CONFIG_SCHEMA.json
- `tests/unit/test_schema_runtime_binding.py` — 25 Tests (Payload-Validierung)
- `tests/unit/test_schema_binding.py` — 14 Tests (Schema-Integrität, Safety-Consts, Alignment)

**Geänderte Dateien:**
- `app/decisions/journal.py` — `DecisionInstance = TypeAlias[DecisionRecord]`; Legacy-Enum-Mapping; `_normalize_legacy_decision_payload()`; vollständige Delegation auf `DecisionRecord`
- `app/decisions/__init__.py` — Re-export von `DecisionRecord` als kanonisches Modell
- `app/execution/models.py` — `DecisionRecord._validate_safe_state()` ruft `validate_json_schema_payload()` (via settings-Wrapper) auf; `@field_validator("timestamp_utc")` für ISO 8601; `DecisionRiskAssessment` um kompatible optionale Felder erweitert; `contradictory_factors` optional (default=`()`); `max_loss_estimate` auf `ge=0.0`
- `app/core/settings.py` — `validate_json_schema_payload()` als Kompatibilitäts-Wrapper; delegiert an `runtime_validator.py`; `AppSettings.validate_runtime_contract()` ruft Config-Validator auf
- `DECISION_SCHEMA.json` — `report_type` als optionale Property hinzugefügt (nicht required)
- `docs/contracts.md` — §48 (vollständig inkl. Zwei-Schichten-Architektur, Safety-Consts-Tabelle)
- `docs/intelligence_architecture.md` — I-251–I-265
- `AGENTS.md` — P43
- `ASSUMPTIONS.md` — A-024–A-025
- `TASKLIST.md` — Sprint 37C-Konsolidierung

**Neue Tests (Sprint 37):**
- `test_schema_binding.py` — 14 Tests: Schema-Integrität, Safety-Consts, Alignment, fail-closed, Immutability
- `test_schema_runtime_binding.py` — 25 Tests: valid payloads, invalid enums, missing fields, legacy enum rejection, config validation
- `test_decision_journal.py` — 20 Tests: Konvergenz, Legacy-Normalisierung, Round-trip, Summary
- `test_decision_record.py` — 9 Tests: Runtime-Schema-Binding, Safe-State-Validator, Append/Load

### Sprint 37 Acceptance Criteria

- Alle alten Tests grün (Regression-Sicherheit) ✅
- `DECISION_SCHEMA.json` wird bei jeder `DecisionRecord`-Instanziierung validiert ✅
- `CONFIG_SCHEMA.json` wird bei AppSettings-Instanziierung validiert ✅
- `DecisionInstance` ist `TypeAlias` für `DecisionRecord` — kein eigenständiges Dataclass ✅
- Legacy-Enum-Werte werden normalisiert (nicht abgelehnt) beim Laden alter Journal-Rows ✅
- Neue Records werden immer im kanonischen Schema-Format gespeichert ✅
- `SchemaValidationError` ist Subclass von `ValueError` ✅
- `app/schemas/runtime_validator.py` ist die kanonische Implementierung ✅
- `app/core/settings.py::validate_json_schema_payload()` ist ein Kompatibilitäts-Wrapper ✅
- `app/core/schema_binding.py` prüft 10 Safety-Consts in CONFIG_SCHEMA.json ✅
- Zwei-Schichten-Architektur dokumentiert und konsistent ✅
- `python -m pytest -q` → 1356 passed ✅
- `python -m ruff check .` grün ✅

---

## Sprint 38+38C — Telegram Command Hardening & Canonical Read Surfaces (konsolidiert)

**Status**: ✅ Abgeschlossen | **Datum**: 2026-03-21 | **Tests**: 1362 | **Ruff**: clean

### Ziel

Telegram-Kommandos auf kanonische MCP-Read-Surfaces mappen, Sicherheitsgrenzen festziehen, Klassifikationskonflikt bereinigen.
Keine neuen Produktivfeatures. Ausschließlich Härtung und Kanonisierung des Operator-Surface.

### Leitprinzip

Telegram = Operator-Surface. Niemals Execution-Surface. Niemals Live-Bypass.

### Deliverables

**Geänderte Dateien:**
- `app/messaging/telegram_bot.py` — `_READ_ONLY_COMMANDS` (7 Einträge, disjunkt), `_GUARDED_AUDIT_COMMANDS` (3 Einträge), alle MCP-Bindings via `_load_canonical_surface()`, `_validate_decision_ref()`, `get_telegram_command_inventory()`; Sprint 38C: `incident` aus `_READ_ONLY_COMMANDS` entfernt
- `TELEGRAM_INTERFACE.md` — kanonischer Operator-Surface-Contract (Sprint 38+38C)
- `docs/contracts.md` — §49 (Sprint 38+38C, final)
- `docs/intelligence_architecture.md` — I-266–I-280
- `ASSUMPTIONS.md` — A-027–A-031; A-028 Sprint 38C praezisiert
- `AGENTS.md` — P44

**Neue Dateien:**
- `tests/unit/test_telegram_bot.py` — 28 Tests (alle grün)

### Sprint 38+38C Acceptance Criteria

- Alle alten Tests grün ✅
- `TELEGRAM_INTERFACE.md` kanonischer Contract ✅
- `§49` in contracts.md final ✅
- I-266–I-280 in intelligence_architecture.md ✅
- A-027–A-031 in ASSUMPTIONS.md ✅
- Alle MCP-Bindings produktiv (read_only via `_load_canonical_surface()`) ✅
- `_cmd_risk` via `get_protective_gate_summary()` (MCP) — keine private attrs ✅
- `/signals` via `get_signals_for_execution()` (MCP) ✅
- `/journal` via `get_review_journal_summary()` (MCP) ✅
- `/daily_summary` via `get_decision_pack_summary()` (MCP) ✅
- `decision_ref` Format-Validierung (`^dec_[0-9a-f]{12}$`) ✅
- `test_telegram_bot.py` — 28 Tests grün ✅
- `_READ_ONLY_COMMANDS` und `_GUARDED_AUDIT_COMMANDS` disjunkt ✅ (Sprint 38C)
- `incident` korrekt als `guarded_audit` — nicht in `_READ_ONLY_COMMANDS` ✅ (Sprint 38C)
- Kein Trading, kein Auto-Routing, kein Auto-Promote, keine Live-Pfade ✅
- `python -m pytest -q` → 1362 passed ✅
- `python -m ruff check .` grün ✅

### Verbotene Seiteneffekte (nicht verhandelbar)

- Kein Trading über Telegram
- Kein Auto-Routing über Telegram
- Kein Auto-Promote über Telegram
- Keine Telegram-Aktion mit Live-Execution-Wirkung
- Kein automatisches /approve das eine Order auslöst
- Kein Telegram-Bypass des Approval-Gates

---

## Sprint 39 — Market Data Layer: Read-Only Adapter Contract

**Status**: 🔵 Definition abgeschlossen — Implementierung ausstehend (Codex)
**Datum**: 2026-03-21
**Ziel**: Ersten kanonischen read-only Market-Data-Contract implementieren und testen. Adapter-Layer ist passiv, read-only, fail-closed. Kein Execution-Pfad.

### Sprint 39 Nicht-Verhandelbar (identisch Sprint 38)

1. **Security First**: Adapter darf niemals Schreibzugriff auf Broker-Systeme haben
2. **Fail-Closed**: None/is_stale → Zyklus überspringen, kein Auto-Routing
3. **Live default-off**: MockAdapter ist Default — kein echter Provider ohne explizite Konfiguration
4. **Kein neues Produkt-Feature**: Nur Market-Data-Layer — kein Signal-Gen, kein Backtest-Umbau, kein Telegram-Hook
5. **Keine parallele Architektur**: BaseMarketDataAdapter ist der einzige erlaubte Einstiegspunkt

### Sprint 39 Architektur-Tasks (Claude Code — abgeschlossen ✅)

- [x] **39.A**: `app/market_data/models.py` gelesen und Datenmodell-Contract definiert (MarketDataPoint, Ticker, OHLCV, OrderBook)
- [x] **39.B**: `app/market_data/base.py` gelesen und Adapter-Interface-Contract definiert (never-raise, read-only)
- [x] **39.C**: `app/market_data/mock_adapter.py` gelesen und Mock-Verhalten dokumentiert (deterministisch, sinusoidal)
- [x] **39.D**: TradingLoop- und BacktestEngine-Integration gelesen und dokumentiert
- [x] **39.E**: `docs/contracts.md §50` geschrieben — kanonischer Market-Data-Contract
- [x] **39.F**: `docs/intelligence_architecture.md` I-281–I-290 geschrieben
- [x] **39.G**: `ASSUMPTIONS.md` A-032–A-036 geschrieben
- [x] **39.H**: `AGENTS.md` P45 geschrieben
- [x] **39.I**: `TASKLIST.md` Sprint-39-Block geschrieben

### Sprint 39 Implementierungs-Tasks (Codex — ausstehend)

- [ ] **39.1**: `tests/unit/test_mock_adapter.py` erstellen
  - Determinismus-Test: gleiche Inputs → gleiche Preise (cross-call)
  - `get_ticker()` → Ticker-Felder vollständig und valid
  - `get_market_data_point()` → MarketDataPoint-Felder vollständig (source="mock", is_stale=False)
  - `get_ohlcv()` → list[OHLCV], len=limit, alle Felder gesetzt
  - `health_check()` → True (kein Netzwerk)
  - `get_ticker()` für unbekanntes Symbol → None (kein raise)
  - `adapter_name` → "mock"
  - Ziel: ≥ 8 Tests

- [ ] **39.2**: `tests/unit/test_market_data_models.py` erstellen
  - `MarketDataPoint` ist frozen (FrozenInstanceError bei Mutation-Versuch)
  - `Ticker` ist frozen
  - `OHLCV` ist frozen
  - `OrderBook` ist frozen
  - `MarketDataPoint(is_stale=True)` → is_stale Feld korrekt
  - `MarketDataPoint(freshness_seconds=5.2)` → freshness_seconds korrekt
  - Ziel: ≥ 6 Tests

- [ ] **39.3**: `tests/unit/test_base_adapter.py` erstellen
  - `BaseMarketDataAdapter` ist ABC → kann nicht direkt instanziiert werden
  - Minimale Implementierung (get_ticker/get_ohlcv/get_price/adapter_name) → health_check() default-Verhalten
  - `get_market_data_point()` default → delegiert an get_ticker()
  - Ziel: ≥ 3 Tests

- [ ] **39.4**: Verifikation `MockMarketDataAdapter` Timestamp UTC-awareness
  - Alle zurückgegebenen Timestamps (Ticker.timestamp_utc, OHLCV.timestamp_utc, MarketDataPoint.timestamp_utc) MÜSSEN UTC-aware sein
  - Falls nicht: `mock_adapter.py` korrigieren (datetime.now(tz=timezone.utc) statt datetime.utcnow())
  - Test: `timestamp_utc.tzinfo is not None`
  - Ziel: ≥ 1 Test, ggf. Code-Fix in mock_adapter.py

- [ ] **39.5**: `tests/unit/test_trading_loop_market_data.py` — Integration TradingLoop+MockAdapter
  - `run_cycle()` mit MockAdapter → kein Fehler, CycleStatus gesetzt
  - `run_cycle()` mit Adapter der None zurückgibt → CycleStatus enthält "no_market_data"
  - Ziel: ≥ 3 Tests (falls TradingLoop unit-testable ohne DB)

- [ ] **39.6**: Ruff + vollständiger Test-Run nach allen Änderungen
  - `python -m pytest -q` → alle Tests grün (Ziel: 1377+ = 1362 + ≥15 neue)
  - `python -m ruff check .` → clean
  - Kein bestehender Test gebrochen

### Sprint 39 Akzeptanz-Kriterien

- `app/market_data/models.py` — MarketDataPoint, Ticker, OHLCV, OrderBook: frozen, UTC-aware ✅ (bereits implementiert)
- `app/market_data/base.py` — BaseMarketDataAdapter ABC: never-raise, read-only-Invariante ✅ (bereits implementiert)
- `app/market_data/mock_adapter.py` — deterministisch, kein random(), adapter_name="mock" ✅ (bereits implementiert)
- `docs/contracts.md §50` — kanonischer Contract (vollständig) ✅
- `docs/intelligence_architecture.md` I-281–I-290 ✅
- `ASSUMPTIONS.md` A-032–A-036 ✅
- `AGENTS.md` P45 ✅
- `tests/unit/test_mock_adapter.py` — ≥ 8 Tests 🔲 (Codex)
- `tests/unit/test_market_data_models.py` — ≥ 6 Tests 🔲 (Codex)
- `tests/unit/test_base_adapter.py` — ≥ 3 Tests 🔲 (Codex)
- UTC-awareness aller Timestamps verifiziert 🔲 (Codex)
- `python -m pytest -q` → ≥ 1377 passed 🔲 (Codex)
- `python -m ruff check .` → clean 🔲 (Codex)
- Kein Trading, kein Auto-Routing, kein Broker-Schreibzugriff, keine Live-Pfade ✅

### Sprint 39 Verbotene Seiteneffekte (nicht verhandelbar)

- Kein Broker-Schreibzugriff im Adapter
- Kein Auto-Routing zwischen Providern
- Kein `random()` im MockAdapter
- Kein Adapter-Aufruf innerhalb `BacktestEngine.run()`
- Keine Änderung an SignalGenerator, RiskEngine oder TradingLoop-Logik (außer ggf. UTC-Fix)
- Kein neues CLI-Command, kein neues MCP-Tool

---

## Sprint 40 — Paper Portfolio Read Surface & Exposure Summary

**Status**: 🔵 Definition abgeschlossen — Implementierung ausstehend (Codex)
**Datum**: 2026-03-21
**Ziel**: Kanonischen read-only Portfolio-/Positions-/Exposure-Contract implementieren. /positions und /exposure auf echte MCP-Surfaces legen. Kein Mutations-Pfad, kein Execution-Feature.

### Sprint 40 Nicht-Verhandelbar

1. **Security First**: Portfolio Surface = read-only — kein Mutations-Pfad
2. **Fail-Closed**: Audit-JSONL fehlt = leeres Portfolio. MtM-Fehler = Fallback entry_price
3. **Live default-off**: Kein Provider-Wechsel ohne explizite Konfiguration
4. **Kein neues Execution-Feature**: Kein Order-Trigger, kein Rebalancing
5. **Kein eigenstaendiger ExposureSummary-Datenpfad**: Projektion von PaperPortfolioSnapshot

### Sprint 40 Architektur-Tasks (Claude Code — abgeschlossen)

- [x] 40.A: paper_engine.py + models.py gelesen (PaperPortfolio, PaperPosition)
- [x] 40.B: market_data/models.py gelesen (MarketDataSnapshot existent)
- [x] 40.C: coingecko_adapter.py + service.py gelesen (Sprint 39 impl. abgeschlossen)
- [x] 40.D: telegram_bot.py gelesen (/positions=Proxy, /exposure=Stub identifiziert)
- [x] 40.E: mcp_server.py gelesen (get_paper_portfolio_snapshot fehlt)
- [x] 40.F: docs/contracts.md §51 geschrieben
- [x] 40.G: docs/intelligence_architecture.md I-291–I-300 geschrieben
- [x] 40.H: ASSUMPTIONS.md A-040–A-044 geschrieben
- [x] 40.I: AGENTS.md P46 geschrieben
- [x] 40.J: TASKLIST.md Sprint-40-Block geschrieben

### Sprint 40 Implementierungs-Tasks (Codex — ausstehend)

- [ ] **40.1**: `app/research/portfolio_surface.py` erstellen
  - `PositionSnapshot` frozen dataclass (lt. contracts.md §51.1)
  - `PaperPortfolioSnapshot` frozen dataclass (lt. §51.2)
  - `ExposureSummary` frozen dataclass (lt. §51.3)
  - `build_position_snapshot(pos, *, snapshot, as_of)`:
    - position_id = "pos_" + hashlib.sha1(f"{symbol}{opened_at}".encode()).hexdigest()[:12]
    - MtM fail-closed: stale/unavailable -> is_mark_to_market=False, current_price=None
    - position_value_usd = qty * (current_price or entry_price)
  - `build_paper_portfolio_snapshot_from_audit(audit_path, *, market_data_snapshots=None)`:
    - Replay order_filled events, buy = add/average, sell = reduce/close
    - Audit fehlt/leer = leerer PaperPortfolioSnapshot (cash=0, initial_equity=0)
    - Malformed Zeile = ueberspringen + WARNING
  - `build_exposure_summary(portfolio)`:
    - largest_position = max(positions, key=position_value_usd)
    - cash_pct = 100.0 wenn total_equity_usd <= 0

- [ ] **40.2**: `app/agents/mcp_server.py` erweitern
  - `get_paper_portfolio_snapshot` hinzufuegen (audit_log_path, provider, freshness_threshold_seconds)
  - `get_portfolio_exposure_summary` hinzufuegen (delegiert intern an Portfolio-Snapshot)
  - Beide in `_CANONICAL_MCP_READ_TOOL_NAMES` eintragen
  - execution_enabled=False, write_back_allowed=False in allen Antworten

- [ ] **40.3**: `app/cli/main.py` erweitern
  - `research paper-portfolio-snapshot` Command
  - `research portfolio-exposure` Command
  - Beide in `get_registered_research_command_names()` registriert
  - --provider (default: mock), --audit-log Parameter

- [ ] **40.4**: `app/messaging/telegram_bot.py` aktualisieren
  - "exposure" zu `_READ_ONLY_COMMANDS` hinzufuegen
  - `TELEGRAM_CANONICAL_RESEARCH_REFS["positions"]` = ("research paper-portfolio-snapshot",)
  - `TELEGRAM_CANONICAL_RESEARCH_REFS["exposure"]` = ("research portfolio-exposure",)
  - `_get_paper_portfolio_snapshot()` Loader-Methode
  - `_get_portfolio_exposure_summary()` Loader-Methode
  - `_cmd_positions()` nutzt `_get_paper_portfolio_snapshot` (nicht mehr handoff_collector)
  - `_cmd_exposure()` nutzt `_get_portfolio_exposure_summary` (Stub entfernen)

- [ ] **40.5**: `tests/unit/test_portfolio_surface.py` erstellen (>= 12 Tests)
  - PositionSnapshot frozen (FrozenInstanceError)
  - PaperPortfolioSnapshot frozen, positions ist tuple
  - ExposureSummary frozen
  - execution_enabled=False + write_back_allowed=False aller drei Modelle
  - build_position_snapshot() ohne MtM -> is_mark_to_market=False
  - build_position_snapshot() mit validem MtM -> is_mark_to_market=True
  - build_position_snapshot() mit stale MtM -> is_mark_to_market=False
  - build_paper_portfolio_snapshot_from_audit() fehlende JSONL -> leerer Snapshot
  - build_paper_portfolio_snapshot_from_audit() buy-fill replay -> Position korrekt
  - build_paper_portfolio_snapshot_from_audit() buy+sell -> Position entfernt
  - build_exposure_summary() 0 Positionen -> cash_pct=100.0
  - build_exposure_summary() mit Positionen -> largest_position_symbol korrekt

- [ ] **40.6**: `tests/unit/test_telegram_bot.py` aktualisieren
  - Test: "exposure" in _READ_ONLY_COMMANDS
  - Test: _cmd_positions nutzt get_paper_portfolio_snapshot (nicht handoff_collector)
  - Test: _cmd_exposure ist nicht mehr Stub
  - Bestehende 28 Tests weiterhin gruen

- [ ] **40.7**: Ruff + vollstaendiger Test-Run
  - python -m pytest -q -> alle Tests gruen (Ziel: Basisstand + >= 14 neue)
  - python -m ruff check . -> clean

### Sprint 40 Akzeptanz-Kriterien

- app/research/portfolio_surface.py: 3 Modelle + 3 Builder-Funktionen 🔲
- PositionSnapshot, PaperPortfolioSnapshot, ExposureSummary frozen, execution_enabled=False 🔲
- build_paper_portfolio_snapshot_from_audit() Fill-Replay korrekt 🔲
- MtM fail-closed per Position 🔲
- get_paper_portfolio_snapshot + get_portfolio_exposure_summary in _CANONICAL_MCP_READ_TOOL_NAMES 🔲
- research paper-portfolio-snapshot + research portfolio-exposure in CLI registriert 🔲
- "exposure" in _READ_ONLY_COMMANDS 🔲
- /positions -> get_paper_portfolio_snapshot (kein Handoff-Proxy mehr) 🔲
- TELEGRAM_CANONICAL_RESEARCH_REFS korrekt aktualisiert 🔲
- tests/unit/test_portfolio_surface.py >= 12 Tests gruen 🔲
- test_telegram_bot.py 28+ Tests gruen 🔲
- python -m pytest -q -> alles gruen 🔲
- python -m ruff check . -> clean 🔲
- Kein Trading, kein Rebalancing, keine Portfolio-Mutation, keine Live-Pfade

### Sprint 40 Verbotene Seiteneffekte (nicht verhandelbar)

- Kein Schreibzugriff auf PaperPortfolio-State via Portfolio-Surface
- Kein direkter Zugriff auf laufende PaperExecutionEngine-Instanz
- Kein Order-Trigger aus /positions oder /exposure
- Kein Rebalancing-Signal aus ExposureSummary
- Kein eigenstaendiger Datenpfad fuer ExposureSummary
- Kein Breaking Change an get_handoff_collector_summary (bleibt erhalten)
- Keine neuen Live-/Broker-/Routing-Features
