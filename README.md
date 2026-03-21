# KAI (Robotron)

> **Leitmotiv: SIMPLE BUT POWERFUL**

**Repository**: [https://github.com/CP-Srinivasa/KAI](https://github.com/CP-Srinivasa/KAI)

KAI ist ein modularer, sicherheitsorientierter AI-Analyse- und Entscheidungsstack für
Krypto- und klassische Märkte. Der Kern ist standardmäßig research/paper-first,
auditierbar, fail-closed und für kontrollierte agentische Erweiterung ausgelegt.

## Sicherheitsbaseline

- `paper` als Default-Betriebsmodus
- `live` standardmäßig deaktiviert
- keine ungeprüfte Modellausgabe im kritischen Pfad
- Risk Engine und Kill Switch sind harte Gates
- Operator-Surfaces sind read-only oder audit-only, solange keine explizite Freigabe existiert

## Pflichtdokumente

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [ASSUMPTIONS.md](ASSUMPTIONS.md)
- [SECURITY.md](SECURITY.md)
- [RISK_POLICY.md](RISK_POLICY.md)
- [RUNBOOK.md](RUNBOOK.md)
- [TELEGRAM_INTERFACE.md](TELEGRAM_INTERFACE.md)
- [CONFIG_SCHEMA.json](CONFIG_SCHEMA.json)
- [DECISION_SCHEMA.json](DECISION_SCHEMA.json)
- [CHANGELOG.md](CHANGELOG.md)

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
# 1. Konfiguration
cp .env.example .env
# .env mit API-Keys befüllen

# 2. Mit Docker starten
docker-compose up -d

# 3. Oder lokal
pip install -e ".[dev]"
uvicorn app.api.main:app --reload

# 4. API-Docs
open http://localhost:8000/docs
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

Alle Settings via `.env`. Siehe `.env.example` für alle Optionen.

Wichtigste Keys:
- `OPENAI_API_KEY` — Für LLM-Analyse erforderlich
- `DATABASE_URL` — PostgreSQL Connection String
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — Für Telegram-Alerts
- `YOUTUBE_API_KEY` — Für YouTube-Kanal-Monitoring

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
