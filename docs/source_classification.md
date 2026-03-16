# Source Classification Guide

## Source Types

| Typ | Beschreibung | Beispiel |
|-----|-------------|---------|
| `rss_feed` | Direkter RSS/Atom-Feed | `https://feeds.example.com/rss` |
| `website` | News-/Editorial-Website | `cointelegraph.com` |
| `news_api` | Strukturierte News-API | NewsAPI.org, Bing News |
| `social_api` | Social Media Plattform API | Twitter/X API, Reddit API |
| `youtube_channel` | YouTube-Kanal | `@Bankless` |
| `podcast_feed` | Aufgelöster Podcast RSS-Feed | Podigee, direktes RSS |
| `podcast_page` | Podcast Landing Page (kein Feed) | `btc-echo.de/podcasts/` |
| `reference_page` | Bildungs-/Referenzressource | `coinledger.io/guides/crypto-tax` |
| `market_data` | Preis-/OHLCV-Datenprovider | CoinGecko, CoinMarketCap |
| `unresolved_source` | Benötigt Klassifikation | Unbekannter URL-Typ |

## Source Status

| Status | Bedeutung |
|--------|-----------|
| `active` | Operativ, wird abgerufen |
| `planned` | Implementierung geplant |
| `disabled` | Absichtlich deaktiviert |
| `requires_api` | Benötigt API-Key-Konfiguration |
| `manual_resolution` | Menschliche Überprüfung erforderlich |
| `rss_resolution_needed` | Wahrscheinlich RSS vorhanden, noch nicht gefunden |

## Wichtige Regel: Spotify & Apple Podcasts ≠ RSS Feeds

Apple Podcasts und Spotify URLs sind **Landing Pages**, keine RSS-Feeds.
Sie erfordern externe APIs zur Auflösung:

- **Apple Podcasts**: iTunes Search API → `https://itunes.apple.com/lookup?id=<ID>`
- **Spotify**: Kein öffentliches RSS. Spotify API oder manuelle Recherche erforderlich.

## Podigee-Feeds (Pattern-basiert auflösbar)

```
{handle}.podigee.io → https://{handle}.podigee.io/feed/mp3
```

Beispiel: `saschahuber.podigee.io` → `https://saschahuber.podigee.io/feed/mp3`

## YouTube URL-Normalisierung

YouTube-Kanäle existieren in mehreren URL-Formaten:
- `youtube.com/@Handle` (aktueller Standard)
- `youtube.com/c/ChannelName` (Legacy)
- `youtube.com/channel/UCxxxxxx` (Channel-ID)

Resolver: `app/ingestion/resolvers/youtube_resolver.py`

**Deduplication**: `@CoinBureau` erschien zweimal in der Quellliste → bereinigt.

## Referenzseiten (keine News-Quellen)

Diese Ressourcen sind als `reference_page` klassifiziert und werden **nicht als News** ingested:

- `a16zcrypto.com/posts/article/crypto-readings-resources/` → reference
- `coinledger.io/bitcoin-rainbow-chart` → reference
- `coinledger.io/guides/crypto-tax` → reference
- `coinbase.com/learn` → reference
- `coinledger.io/crypto-profit-calculator` → reference
- `tradingview.com` → market data platform (kein News-Feed)

## Status-Dateien

- `monitor/podcast_feeds_resolved.txt` — Bestätigte RSS-Feeds, bereit zur Ingestion
- `monitor/podcast_sources_unresolved.txt` — Benötigt API-Key oder manuelle Auflösung
- `monitor/website_sources.txt` — News-/Reference-Websites
- `monitor/news_domains.txt` — Domains mit Credibility-Scores
