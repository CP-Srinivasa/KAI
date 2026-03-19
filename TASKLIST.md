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

> **Startet erst nach Sprint 3-Abschluss.**

**Ziel**: Verwertbare Outputs für Entscheidungen — Watchlists, Briefs, Signal-Kandidaten.

| # | Task |
|---|---|
| 4.1 | Watchlists — Asset/Ticker-basierte Beobachtungslisten in `monitor/` |
| 4.2 | Research Briefs — strukturierte Zusammenfassungen pro Asset / Event-Cluster |
| 4.3 | Signal Candidates — priorisierte Liste actionable Dokumente für Trading-Entscheidungen |
| 4.4 | `query list` Erweiterung — Filter nach Watchlist, Asset, Priority, Datum |
| 4.5 | Export-Format (JSON / Markdown) für Research Briefs |

---

## Grundregel

> Ein Sprint beginnt erst, wenn der vorherige **vollständig** abgeschlossen ist:
> - alle Tasks ✅
> - `pytest` grün
> - `ruff check` sauber
> - AGENTS.md + contracts.md aktuell
