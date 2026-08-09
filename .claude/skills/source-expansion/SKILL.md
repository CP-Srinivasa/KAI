---
name: source-expansion
description: Systematische Quellen-Erschließung nach KAI Directive §3-§5. Matrix A/B/C/D, Gap-Analyse, Integrationskosten-Score, ranked Vorschlagsliste.
trigger: User sagt "Sources", "Neue Quellen", "Source-Expansion", "Datenquellen prüfen", oder `daily-strategy-review` signalisiert Gap in Kategorie A/B/C/D.
---

# Source Expansion (KAI)

Operationalisiert §3 (Verbot künstlicher Begrenzung) + §4 (Erweiterungsauftrag) + §5 (Keine Denkfaulheit bei fehlenden APIs) der Master Execution Directive.

## Zweck

Kein Verharren auf aktuell integrierten Quellen. Jede Session prüft aktiv, welche neuen Quellen (Daten/News/Social/Markt/Kontrolle) sinnvoll sind — inklusive API-loser Erschließung via RSS, Crawl, MCP oder Cross-Signal.

## Trigger

- User-Befehl: "Source-Expansion", "Neue Quellen", "Sources prüfen"
- Daily Review signalisiert **fehlende Coverage** in einer §4-Kategorie
- Neuer Use-Case / Asset / Narrativ taucht auf, für den aktuelle Quellen unzureichend sind
- Monatlich als Routine-Check (Kategorien-Audit)

## Input-Quellen

**Ist-Zustand der Coverage:**
- `monitor/*.txt` (aktive Quellen-Listen)
- `config/sources/*.json`, `config/feeds/*.yaml` (falls vorhanden)
- `app/ingestion/` (aktive Adapter/Resolver)
- `app/integrations/` (aktive Provider)
- `artifacts/source_registry.json` (falls implementiert)

**Gap-Indikatoren:**
- Alerts mit `source: unknown` oder fehlender Primärquelle
- Narrative/Themen in Alerts ohne verifizierbare Grundquelle
- DECISION_LOG-Einträge die auf fehlende Daten verweisen

## 4-Kategorien-Matrix (Directive §4)

### A. News/Web
| Quelle | Typ | Integrationsweg | Kosten | Relevanz | Status |
|--------|-----|-----------------|--------|----------|--------|
| ... | rss_feed / news_api / scrape / manual | URL/API/RSS | low/med/high | low/med/high | active/planned/disabled |

**Pflicht-Check pro Lauf:**
- Krypto: Coindesk, Block, Decrypt, Bankless, The Defiant, Messari, CoinMarketCap News, Bitcoin Magazine, Cointelegraph, Crypto Briefing, Wu Blockchain (CN), DL News
- Exchange-Announcements: Binance, Coinbase, Kraken, OKX, Bybit, Bitget, Upbit (Listings/Delistings/Maintenance)
- Regulatorik: SEC filings (EDGAR), BaFin, FCA, MAS, CFTC, ESMA press
- Research: Glassnode Insights, Delphi Digital, Arcane Research, Kaiko Research, IntoTheBlock, CryptoQuant blog, Nansen blog
- GitHub: releases/commits/issues relevanter Protocol-Repos (Ethereum, Bitcoin Core, Lightning, Uniswap v4, etc.)
- Governance: Snapshot, Tally, MakerDAO forum, Uniswap forum, Optimism Governance

### B. Social/Community
| Plattform | Zugang | Integrationsweg | Kosten | Relevanz |
|-----------|--------|-----------------|--------|----------|
| X/Twitter | API (bearer) | bereits integriert | med | high |
| Reddit | api.reddit.com | public JSON | low | med |
| Telegram | Bot/API | bereits integriert | low | high |
| Discord | Webhook/Gateway | bot-account | med | med-high |
| YouTube | Data API v3 | bereits integriert | low | med |
| Mirror | RSS/sitemap | Crawl | low | med |
| Substack | RSS pro Author | RSS | low | med |
| Bitcointalk | subforum RSS | RSS/Crawl | low | low-med |
| LinkedIn | — | kein robuster Crawl | high | low |
| TikTok | inoffiziell | hoch-fragil | high | low-med |

### C. Markt/Struktur
- **Orderbuch/OI/Funding/Liquidations:** Coinalyze, Laevitas, Velo Data, CoinGlass (API oder Crawl)
- **On-Chain:** Glassnode API, Nansen API, Dune (SQL), Arkham Intel, Etherscan (free tier), Chainalysis (if licensed), DefiLlama (TVL/yields/bridges/fees — free API)
- **Derivate:** Deribit, CME (CoT reports), Bybit Futures, Binance Futures (bereits Spot integriert)
- **Stablecoin-Flows:** Circle, Tether monthly reports, DefiLlama stablecoins
- **ETF:** Farside Investors (BTC/ETH spot ETF flows, RSS/CSV), Bitwise, iShares product pages
- **Sentiment:** Alternative.me Fear&Greed, Santiment, LunarCrush, CoinGecko sentiment
- **Korrelation:** The Tie, Kaiko, eigene Compute auf OHLCV

### D. Kontrolle (Crosscheck/Validierung)
- Konkurrierende Datenanbieter für Price: CoinGecko vs Binance vs Kraken vs CryptoCompare
- Konkurrierende AI: OpenAI vs Gemini vs Anthropic (bereits teilweise) → für Dissens-Erkennung
- Reputations-Quellen: Messari Governor-Scores, Token Terminal fundamentals
- Dedup-Quellen: Duplicate-Detection über hash(title)+publish_window bei News
- Verifikation: Offizielle Projekt-Blogs als Ground Truth vs Aggregator-News

## Output-Format (verbindlich)

### § 1 Coverage-Gap-Analyse
Pro Kategorie A/B/C/D: Was fehlt? Warum wichtig? Welche Alerts/Analysen scheitern ohne?

### § 2 Ranked Proposal-List
Jede vorgeschlagene Quelle im Directive-§11-Format:
- Vorschlag
- Warum jetzt?
- Erwarteter Nutzen (konkret: welcher Alert-Typ verbessert, welche Latenz-Reduktion)
- Datenquellen/Systeme
- Umsetzungsweg (API-Endpoint / RSS-URL / Crawl-Selector / MCP-Konfiguration)
- Parallel möglich?
- Aufwand (minimal/realistisch): in Stunden, keine Dramatik
- Risiken (legal/technisch/qualitativ)
- Priorität P0..P3

### § 3 API-los verwertbar (§5 Directive)
Explizite Sektion für Quellen ohne offizielle API:
- RSS-Substitut?
- Sitemap-basiert?
- DOM-strukturiert (cheerio/beautifulsoup)?
- MCP-Connector?
- Cross-Signal (z.B. X-Mentions als Proxy für unzugängliche Quelle)?
- **Stabilitäts-Label:** stabil / experimentell / riskant

### § 4 Quick-Win Shortlist (≤4h Aufwand)
Top-5 Quellen mit höchstem Nutzen-pro-Stunde-Verhältnis.

### § 5 Strategic Shortlist
Top-3 Quellen mit größtem strukturellen Impact (auch wenn Aufwand höher).

## Bewertungsmatrix (pro Quelle)

```
Integrationskosten × Erwartete Relevanz = Score

Kosten:     low=1, med=3, high=6
Relevanz:   low=1, med=3, high=5
Stabilität: stabil=×1.0, experimentell=×0.7, riskant=×0.4

Score = (Relevanz / Kosten) × Stabilität
Higher is better. Score ≥ 1.5 → P0/P1. Score 0.8-1.5 → P2. Score < 0.8 → P3.
```

## Anti-Pattern

- Quellen-Liste ohne Integrationsweg (nur „X wäre gut" reicht nicht)
- Fragile Scrapes ohne Stabilitäts-Kennzeichnung
- Paywall/Login-Umgehungen
- Duplikate zu bestehenden Quellen ohne Mehrwert
- „Alle RSS-Feeds von Kategorie X" — braucht Selektion
- Quellen ohne klaren Use-Case („interessant" ≠ „nützlich")

## Post-Run Pflicht

1. Artifact `artifacts/agents/source_expansion/YYYY-MM-DD.md` schreiben
2. Update `monitor/source_proposals.jsonl` mit P0/P1-Kandidaten
3. Wenn P0 → TaskCreate für Integration
4. Gap-Kategorien markieren die weiter zu prüfen sind

## Referenz
- KAI Master Execution Directive §3, §4, §5
- Verwandter Skill: `daily-strategy-review` (für Gap-Detection-Trigger), `research-crosscheck` (für Kategorie D)
- Bestehende Infrastruktur: `app/ingestion/`, `app/integrations/`, `monitor/`
