# TASKLIST.md — KAI Platform Sprint Plan

> Sprints sind **streng sequenziell**. Sprint N startet erst, wenn Sprint N-1 vollständig abgeschlossen ist.
> Letzte Aktualisierung: 2026-03-20

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
  - [ ] ruff check . sauber
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
  - [ ] ruff check . sauber
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
  - [ ] ruff check . sauber
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

      Exits 0 if all 5 quantitative gates pass.
      Exits 1 if any gate fails or report cannot be parsed.

      Gate I-34 (false-actionable rate) requires separate manual verification
      via `research evaluate`. See docs/benchmark_promotion_contract.md.
      """

Implementierung: exakt wie in docs/benchmark_promotion_contract.md §CLI-Contract-7.3 spezifiziert.

Constraints:
  - NICHT: validate_promotion() oder EvaluationMetrics ändern
  - KEIN DB-Aufruf, KEIN Model-Load, KEIN LLM-Call
  - Exit 0 = promotable (alle 5 Gates pass)
  - Exit 1 = nicht promotable ODER Datei nicht gefunden ODER Parse-Fehler
  - I-34-Hinweis IMMER anzeigen, auch bei PASS

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest grün (547 Basis)
  - [ ] Exit 0 wenn alle 5 Gates pass
  - [ ] Exit 1 wenn mindestens 1 Gate fail
  - [ ] Exit 1 + Fehlermeldung wenn Datei nicht gefunden
  - [ ] Exit 1 + Fehlermeldung bei ungültigem JSON
  - [ ] I-34-Hinweis in allen Fällen sichtbar
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

## Grundregel

> Ein Sprint beginnt erst, wenn der vorherige **vollständig** abgeschlossen ist:
> - alle Tasks ✅
> - `pytest` grün
> - `ruff check` sauber
> - AGENTS.md + contracts.md aktuell
