# AI Analyst Trading Bot

> **Leitmotiv: SIMPLE BUT POWERFUL**

Modulares, produktionsreifes AI-Analyse- und Trading-Bot-System für Krypto- und klassische Finanzmärkte.

## Features

- **Multi-Source Monitoring**: RSS, News APIs, YouTube, Podcasts, Social Media, Marktdaten
- **Multi-Market**: Crypto + Aktien/ETFs + Makro/Forex
- **Flexible Suche**: Boolean Query DSL, Feldsuche, Datum/Region/Sprache-Filter
- **KI-Analyse**: Sentiment, Impact, Novelty, Credibility via OpenAI/ChatGPT
- **Deduplication**: Hash + URL + Title-Similarity über Quellen hinweg
- **Alerting**: Telegram, Email, Webhook mit konfigurierbaren Schwellenwerten
- **Trading-Ready**: Signal-Kandidaten, Watchlists, Event-to-Asset-Mapping

## Quick Start

```bash
# --- #
```

## CLI

```bash
# Query validieren
trading-bot query validate "bitcoin AND (DeFi OR NFT) NOT scam"

# Quellen klassifizieren
trading-bot sources classify --file monitor/podcast_feeds_raw.txt

# Ingestion starten
trading-bot ingest run

# Pending Dokumente analysieren
trading-bot analyze pending --limit 50

# Digest senden
trading-bot alerts send-digest --channel telegram
```

## Tech Stack

| Komponente | Technologie |
|-----------|-------------|
| Sprache | Python 3.12+ |
| API | FastAPI |
| Validation | Pydantic v2 |
| Datenbank | PostgreSQL + SQLAlchemy 2.x |
| Migrations | Alembic |
| HTTP | httpx (async) |
| LLM | OpenAI (gpt-4o), Anthropic (optional) |
| Scheduler | APScheduler |
| CLI | Typer + Rich |
| RSS | feedparser |
| Logging | structlog (JSON) |
| Tests | pytest + pytest-asyncio |
| Linting | ruff |
| Container | Docker + docker-compose |

## Projektstruktur

```
ai_analyst_trading_bot/
├── app/
│   ├── api/            # FastAPI Routes
│   ├── cli/            # Typer CLI
│   ├── core/           # Settings, Logging, Errors, Domain, Query DSL
│   ├── ingestion/      # Source Adapter (RSS, News, YouTube, etc.)
│   ├── enrichment/     # Deduplication, Entity Extraction
│   ├── analysis/       # Scoring, LLM-Analyse, Sentiment
│   ├── alerts/         # Telegram, Email, Webhook
│   ├── storage/        # DB Models, Repositories
│   ├── trading/        # Signal-Vorbereitung
│   └── integrations/   # OpenAI, Anthropic, YouTube, Reddit Adapter
├── monitor/            # Keyword-, Channel-, Domain-Watchlists
├── tests/              # Unit, Integration, E2E Tests
├── docs/               # Architektur, Source Classification
└── docker/             # Dockerfile
```

## Konfiguration

----------------

## Development

```bash
pip install -e ".[dev]"
pytest           # Tests ausführen
ruff check app/  # Linting
ruff format app/ # Formatierung
```

## Implementierungsphasen

| Phase | Status | Beschreibung |
|-------|--------|-------------|
| 1 – Foundation | ✅ | Repo, Settings, DB, Adapter, Query DSL, CI |
| 2 – Ingestion Core | 🔄 | RSS-Scheduler, News APIs, Dedup-Pipeline |
| 3 – Analysis Core | ⏳ | LLM-Pipeline, Keyword-Analyse, Scoring |
| 4 – Alerting | ⏳ | Telegram, Email, Alert-Rules |
| 5 – Research/Trading | ⏳ | Watchlists, Signal-Kandidaten |
| 6 – Advanced | ⏳ | Transcripts, Narrative Clustering, MCP |

## License

MIT
