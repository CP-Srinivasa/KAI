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

## Grundregel

> Ein Sprint beginnt erst, wenn der vorherige **vollständig** abgeschlossen ist:
> - alle Tasks ✅
> - `pytest` grün
> - `ruff check` sauber
> - AGENTS.md + contracts.md aktuell
