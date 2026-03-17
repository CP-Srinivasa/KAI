# Architecture — AI Analyst Trading Bot

## Overview

Modular, production-oriented AI-powered market intelligence platform.  
**Motto: Simple but Powerful**

## High-Level Flow

```
Source Registry
   ↓
Source Classification / Resolution
   ↓
Ingestion
   ↓
Normalization / Canonicalization
   ↓
Deduplication / Enrichment
   ↓
Rule-Based Analysis
   ↓
LLM Analysis
   ↓
Scoring / Ranking / Historical Linking
   ↓
Alerts / Research / Signal Candidates
```

## Module Map

| Module | Responsibility |
|--------|---------------|
| `app/core/` | Settings, logging, domain types, enums, errors |
| `app/ingestion/` | Source adapters, resolvers, schedulers |
| `app/normalization/` | Content cleaning, metadata alignment (Phase 2) |
| `app/enrichment/` | Entities, tags, dedup helpers (Phase 2) |
| `app/analysis/` | Keyword logic, scoring, LLM pipeline (Phase 3) |
| `app/integrations/` | Provider-specific clients (Phase 3) |
| `app/alerts/` | Telegram, email, alert rules (Phase 4) |
| `app/research/` | Briefs, summaries, watchlists (Phase 5) |
| `app/trading/` | Signal candidates (Phase 5) |
| `app/api/` | FastAPI endpoints |
| `app/cli/` | Typer commands |
| `app/storage/` | DB models, repositories, migrations |
| `monitor/` | User-editable source lists and watchlists |

## Source Taxonomy

Every source is classified as one of:
- `rss_feed` — validated RSS/Atom feed
- `website` — monitored website
- `news_api` — API-based news provider
- `youtube_channel` — YouTube channel
- `podcast_feed` — resolved podcast RSS
- `podcast_page` — podcast landing page (not a feed)
- `reference_page` — educational/reference resource
- `social_api` — social media API source
- `manual_source` — manually curated content
- `unresolved_source` — URL not yet classified

## Source Status Lifecycle

`active` → `disabled` | `requires_api` | `manual_resolution` | `unresolved`

## Key Design Rules

1. Classify first, resolve second, ingest third
2. Never fake RSS feeds
3. No business logic in adapters or controllers
4. LLM output always validated against `LLMAnalysisOutput` schema
5. All secrets via environment variables — never hardcoded

## Delivery Phases

| Phase | Content |
|-------|---------|
| 1 | Foundation: settings, logging, models, API, CLI, Docker, CI |
| 2 | Ingestion: RSS, podcasts, YouTube, websites, dedup |
| 3 | Analysis: query DSL, keywords, LLM, scoring |
| 4 | Alerting: Telegram, email, rules |
| 5 | Research & signals: watchlists, briefs, signal candidates |
| 6 | Advanced: transcripts, social connectors, narrative clustering |
