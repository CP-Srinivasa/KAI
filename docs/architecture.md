# Architektur: AI Analyst Trading Bot

## Leitmotiv

**SIMPLE BUT POWERFUL** — Modular, produktionsorientiert, erweiterbar.

## Design-Prinzipien

1. **Modulare Service-Architektur** — Jede Komponente ist isoliert und unabhängig testbar
2. **Deterministische Datenflüsse** — Ingestion → Normalisierung → Analyse → Alerting → Storage
3. **Provider-Abstraktion** — LLM-Provider, Alert-Kanäle und Marktdaten-Quellen sind austauschbar
4. **Konfigurationsgetrieben** — Alle Settings via `.env` und YAML
5. **Observability First** — Strukturiertes JSON-Logging, Health Checks, Usage-Tracking

## System-Schichten

```
┌─────────────────────────────────────────────────┐
│              API / CLI Layer                     │
│       FastAPI REST API + Typer CLI               │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│           Orchestration Layer                    │
│     APScheduler Jobs + Worker Processes          │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│             Ingestion Layer                      │
│  RSS | News API | YouTube | Podcasts | Social    │
│  BaseSourceAdapter → fetch() → normalize()       │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│        Normalization + Enrichment                │
│  CanonicalDocument ← Dedup ← Entity Extract     │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│             Analysis Layer                       │
│  Rule-based Scoring → LLM (OpenAI/Anthropic)    │
│  Sentiment | Impact | Novelty | Historical       │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│             Alerting Layer                       │
│     Telegram | Email | Webhook                  │
│     Rules → Breaking | Digest | Watchlist       │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│         Trading Preparation Layer                │
│  Signal Candidates | Watchlists | Event→Asset   │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│              Storage Layer                       │
│   PostgreSQL (SQLAlchemy 2.x) | Alembic         │
└─────────────────────────────────────────────────┘
```

## Kernmodelle

### CanonicalDocument
Zentrales normalisiertes Dokumentenschema. Alle Adapter produzieren dieses Format.
Datei: `app/core/domain/document.py`

### AnalysisResult
Strukturierter LLM-Output. Vor Storage via `LLMAnalysisOutput` (Pydantic) validiert.
Datei: `app/analysis/llm/base.py`

### QuerySpec
Such-/Filter-DSL Schema mit Boolean-Operator-Unterstützung.
Datei: `app/core/query/schema.py`

## Modul-Übersicht

| Modul | Zweck |
|-------|-------|
| `app/core/` | Settings, Logging, Errors, Enums, Domain-Modelle, Query DSL |
| `app/ingestion/` | Source-Adapter (RSS, News, YouTube, Podcasts, Social) |
| `app/normalization/` | Text-Bereinigung, Spracherkennung |
| `app/enrichment/` | Deduplication, Entity-Extraction, Tagging |
| `app/analysis/` | Scoring, LLM-Analyse, Sentiment, Narratives |
| `app/alerts/` | Telegram, Email, Webhook-Benachrichtigungen |
| `app/storage/` | DB-Modelle, Repositories, Migrations |
| `app/trading/` | Signal-Vorbereitung, Watchlists, Event-Mapping |
| `app/integrations/` | OpenAI, Anthropic, YouTube, Reddit Adapter |
| `app/api/` | FastAPI REST Endpoints |
| `app/cli/` | Typer CLI Commands |

## Implementierungsphasen

| Phase | Status | Beschreibung |
|-------|--------|-------------|
| Phase 1 – Foundation | ✅ | Repo, Settings, Logging, DB, Base Adapters, Query DSL, CI, Docker |
| Phase 2 – Ingestion Core | 🔄 | RSS-Scheduling, News APIs, Podcast-Resolver, YouTube-Registry |
| Phase 3 – Analysis Core | ⏳ | Keyword-Analyse, Sentiment-Pipeline, LLM-Provider |
| Phase 4 – Alerting | ⏳ | Telegram, Email, Alert-Rules, Digest |
| Phase 5 – Research/Trading | ⏳ | Watchlists, Signal-Kandidaten, Historical Analogs |
| Phase 6 – Advanced | ⏳ | Transcripts, Narrative Clustering, MCP Adapter |
