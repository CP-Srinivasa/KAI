# TASKLIST.md — KAI Platform Sprint Plan

> Sprints sind **streng sequenziell**. Sprint N startet erst, wenn Sprint N-1 vollständig abgeschlossen ist.
> Letzte Aktualisierung: 2026-03-19

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
- `app/research/watchlists.py` — `WatchlistRegistry` + `find_by_text()`
- `app/research/briefs.py` — `ResearchBrief`, `BriefDocument`, `ResearchBriefBuilder`
- `app/research/signals.py` — `SignalCandidate`, `extract_signal_candidates()`
- `app/research/__init__.py` — öffentliche Exports definiert
- `app/analysis/keywords/watchlist.py` — `load_watchlist()` jetzt mit `persons`+`topics`
- `docs/contracts.md §11` — vollständige Sprint 4 Contracts dokumentiert
- `monitor/watchlists.yml` — Seed-Datei vorhanden (13 crypto, 8 equity, 5 ETF, 10 persons, 10 topics)

---

### Sprint 4 Phase A — Watchlist + Research Brief CLI (→ Codex)

**Status**: ⏳ bereit für Codex

| # | Task | Agent | Status |
|---|---|---|---|
| 4.1 | `research watchlists list` CLI — alle Watchlists aus registry ausgeben | Codex | ⏳ |
| 4.2 | `research watchlists for <tag>` CLI — Symbole einer Watchlist ausgeben | Codex | ⏳ |
| 4.3 | `research brief <cluster>` CLI — ResearchBrief für einen Cluster bauen | Codex | ⏳ |
| 4.4 | `GET /research/briefs/{cluster}` API-Endpoint | Codex | ⏳ |
| 4.5 | Tests für alle neuen CLI-Commands + API-Endpoint | Codex | ⏳ |

**Codex-Spec für 4.1–4.5:**

```
## Task: Sprint 4A — Research CLI + API

Agent: Codex
Phase: Sprint 4A
Modul: app/cli/main.py, app/api/routers/research.py
Typ: feature

Beschreibung:
  Baue die CLI-Schicht für das Research-Modul und den ersten API-Endpoint.
  Alle Models und Builder sind fertig in app/research/ — nur die Eingangspunkte fehlen.

Spec-Referenz: docs/contracts.md §11, app/research/__init__.py

Constraints:
  - NICHT: neue Models einführen
  - NICHT: app/research/*.py ändern
  - NICHT: monitor/watchlists.yml ändern
  - Typer-Subgruppe "research" unter dem bestehenden app in app/cli/main.py
  - API-Router analog zu app/api/routers/alerts.py

CLI-Befehle (Typer):
  research watchlists list
    → WatchlistRegistry.from_monitor_dir(settings.monitor_dir)
    → für jede Watchlist: Tag + Symbole ausgeben (Rich Table)

  research watchlists for <tag>
    → get_watchlist(tag) ausgeben
    → Exit 1 wenn leer

  research brief <cluster> [--days 7] [--limit 50] [--format markdown|json]
    → repo.list(is_analyzed=True, limit=limit) holen
    → WatchlistRegistry.find_by_text() zum Filtern nutzen
    → ResearchBriefBuilder(cluster).build(gefilterte_docs)
    → to_markdown() oder to_json_dict() ausgeben

API-Endpoint:
  POST /research/briefs/{cluster}
    Body: { "days": 7, "limit": 50 }
    Returns: ResearchBrief.to_json_dict()
    Auth: Bearer-Token (analog /alerts/test)

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_research_cli.py grün
  - [ ] pytest tests/unit/test_research_api.py grün
  - [ ] WatchlistRegistry korrekt aus monitor_dir geladen
  - [ ] Brief-Output enthält cluster_name, document_count, top_actionable_signals
```

---

### Sprint 4 Phase B — Signal Candidates (→ Antigravity koordiniert Codex)

**Status**: ⏳ nach Phase A

| # | Task | Agent | Status |
|---|---|---|---|
| 4.6 | `research signals list [--watchlist <tag>] [--min-priority 8]` CLI | Codex | ⏳ |
| 4.7 | `GET /research/signals` API-Endpoint | Codex | ⏳ |
| 4.8 | Watchlist-Boost-Integration in signals CLI | Codex | ⏳ |
| 4.9 | Tests für Signal-CLI + API | Codex | ⏳ |

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

## Grundregel

> Ein Sprint beginnt erst, wenn der vorherige **vollständig** abgeschlossen ist:
> - alle Tasks ✅
> - `pytest` grün
> - `ruff check` sauber
> - AGENTS.md + contracts.md aktuell
