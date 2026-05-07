# Source-Expansion-Recherche — 2026-05-07

**Operator-Auftrag:** B-3 aus dem Cutover-Strategie-Track 2026-05-07.
**Subagent:** `source-scout` (gefuehrt von Claude-Main).
**Ist-Zustand:** 14 aktive Sources im `provenance.by_source`-Endpoint, ~2.5 active resolutions/Tag.
**Status:** Recherche done. Implementation = eigener Folge-Sprint.

---

## Bilanz

CLAUDE.md §4 Pflicht-Erweiterungs-Auftrag definiert 4 Source-Kategorien. Aktuelle Coverage:

| Kategorie | Coverage heute | Befund |
|---|---|---|
| **A** News/Web | beincrypto, bitcoin_magazine, cointelegraph, decrypt, theblock, newsdata | mittel — fehlt Regulatorik, GitHub-Releases, Exchange-Announcements |
| **B** Social/Community | telegram_premium_channel, youtube, X/Twitter (watchlist) | mittel — kein Reddit, kein Substack, kein Forum |
| **C** Markt/Struktur | coingecko, binance, tradingview | **schwach** — null On-Chain, null Derivate, null Stablecoin-Flows |
| **D** Kontrolle/Crosscheck | — | **leer** — kein Sentiment-Baseline, kein Shadow-Modell |

---

## Top-3 Quick Wins (P0, < 4h zusammen)

### 1. CryptoPanic aktivieren (schlafender Adapter)

**Befund:** `app/integrations/cryptopanic/{client.py, adapter.py}` existiert vollstaendig im Repo, wird aber von keinem Scheduler referenziert und erscheint nicht in `provenance.by_source`. Aktivierung = Scheduler-Einbindung, **kein Neubau**.

**Warum jetzt:** verschenktes Potenzial. CryptoPanic aggregiert 500+ Krypto-News-Quellen plus Vote-basiertes Sentiment (`bullish`/`bearish`/`important`/`hot`).

**Erwarteter Nutzen:** +30-50 Items/Tag, Sentiment-Flag-Layer, geschaetzt **+0.8-1.5 active resolutions/Tag** Velocity-Beitrag.

**Endpoint:** `https://cryptopanic.com/api/v1/posts/` — Free Tier ~50-200 Req/h, max 20 Items/Request, Filter via `filter=hot|bullish|bearish|important`.

**Umsetzungsweg:** Einbinden in `app/ingestion/schedulers/rss_scheduler.py` oder neuer `cryptopanic_scheduler.py`, Adapter aus existierender `adapter.py` reuse, `source_id="cryptopanic"` in Provenance.

**Aufwand:** S (2-4h). **Risiken:** Rate-Limit, Duplikat-Risiko mit bestehenden News-Quellen (Dedup-Layer pruefen). **Prioritaet:** **P0**.

### 2. Alternative.me Fear & Greed Index

**Vorschlag:** Taeglicher Sentiment-Score (0-100) als Markt-Regime-Kontext-Layer.

**Warum jetzt:** Kategorie D (Kontrolle/Crosscheck) komplett leer. FGI ist die meistgenutzte oeffentliche Sentiment-Baseline im Krypto-Markt.

**Erwarteter Nutzen:** Markt-Regime-Kontext (Extreme Fear / Extreme Greed) fuer Konfluenz-Bewertung, Divergenz-Signale (News bullish + FGI=15 → kontra).

**Endpoint:** `https://api.alternative.me/fng/?limit=1` (aktuell), `?limit=30` (History). Kein Auth, Rate-Limit 60 Req/min.

**Umsetzungsweg:** Mini-Adapter `app/integrations/feargreed/client.py`, taeglicher Snapshot-Poll, Wert in DB persistieren, Kontext-Feld im Alert-Formatter.

**Aufwand:** S (2-3h). **Risiken:** Single-Source-Index, Methodik proprietaer (fuer Kontext-Layer akzeptabel). **Prioritaet:** **P0**.

### 3. Reddit RSS (r/cryptocurrency + r/bitcoin + r/ethfinance)

**Vorschlag:** Reddit-Subreddits via nativem RSS-Feed via bestehendem `RSSFeedAdapter`. **Kein neuer Code**, nur Konfiguration in `monitor/website_sources.txt`.

**Warum jetzt:** Kategorie B hat kein strukturiertes Community-Forum. Reddit ist die groesste englischsprachige Krypto-Community ausser X. Narrative entstehen oft zuerst in Reddit-Threads (Exploit, Rugpull-Warnung, Governance).

**Erwarteter Nutzen:** Frueh-Signal-Diversitaet, +20-40 Items/Tag.

**Endpoints:**
- `https://www.reddit.com/r/cryptocurrency/new/.rss?sort=new`
- `https://www.reddit.com/r/bitcoin/new/.rss?sort=new`
- `https://www.reddit.com/r/ethfinance/new/.rss?sort=new` (qualitativ hoch)

**Umsetzungsweg:** Feeds in `monitor/website_sources.txt` mit Typ `rss_feed`. Spam-Filter/Keyword-Gate zwingend (hohe Noise-Rate r/cryptocurrency).

**Aufwand:** S (1-2h). **Risiken:** Noise-Rate, User-Agent-Rate-Limit max 1 Req/min. **Prioritaet:** **P1**.

---

## Strategic Gaps (P1, mehr Aufwand, groesserer struktureller Impact)

### 4. SEC EDGAR RSS — Regulatorik-Primaerquelle

**Vorschlag:** Offizielle US-SEC-Filings via Atom-RSS, Update-Delay <1 min.

**Warum jetzt:** KAI hat keine Primaer-Regulatorik-Quelle. ETF-Filings (IBIT, FBTC, ETHW), 8-K-Meldungen von Coinbase/MicroStrategy/Marathon kommen aktuell nur 15-60 min spaeter via Newsmedien.

**Endpoints:**
- `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=8-K&output=atom`
- CIK-spezifisch filtern: Coinbase 1679788, MicroStrategy 1050446, Marathon 1507605
- `https://efts.sec.gov/LATEST/search-index?q=%22bitcoin%22&forms=8-K` (Full-Text-Search JSON)

**Umsetzungsweg:** EDGAR-RSS in `monitor/website_sources.txt`, bestehender RSSFeedAdapter parst Atom nativ, Entity-Extraktion (Filer/Filing-Type) → Alert bei 8-K von relevanten CIKs.

**Aufwand:** M (4-6h). **Risiken:** sehr hoher Filing-Volumen, CIK-Whitelist + Pre-Filter zwingend. **Prioritaet:** **P1**.

### 5. DefiLlama Free API — TVL & Stablecoin-Flows

**Vorschlag:** REST-API fuer DeFi-TVL, Stablecoin-Supply, Bridge-Flows. **Kostenlos, kein Auth.**

**Warum jetzt:** Kategorie C On-Chain komplett leer. TVL-Delta = Risk-On/Risk-Off Frueh-Indikator. Protokoll-spezifische TVL-Anomalien = Exploit-Verdacht.

**Endpoints:**
- `https://api.llama.fi/protocols`
- `https://api.llama.fi/v2/historicalChainTvl/Ethereum`
- `https://stablecoins.llama.fi/stablecoins?includePrices=true`
- `https://bridges.llama.fi/bridges`

**Umsetzungsweg:** Adapter `app/integrations/defillama/client.py`, taeglicher Snapshot, Delta-Berechnung TVL-Vortag → bei >3% Anomalie → Alert-Kandidat.

**Aufwand:** M (4-6h). **Risiken:** kein formales SLA, TVL-Methodik nicht 100% einheitlich pro Protokoll. **Prioritaet:** **P1**.

---

## Backlog (P2, Konditionen-abhaengig)

### 6. GitHub Releases Atom (P2, S-Aufwand)

Atom-Feeds fuer Geth, Bitcoin Core, Solana, Prysm. `monitor/website_sources.txt`-Eintrag, RSSFeedAdapter parst nativ. Niedrige Frequenz, hoher Signal-Wert bei Major-Upgrades. Pre-Release-Filter setzen.

### 7. Coinglass API — Derivate (P2, M-Aufwand, $29/mo)

Liquidationen, Open Interest, Funding-Rate. Bezahl-API, daher erst nach Velocity-Effekt-Messung von 1-5 evaluieren. Schliesst Kategorie-C-Sub-Gap "Derivate".

---

## Coverage-Gaps (weiter zu pruefen, **kein** Adapter-Vorschlag jetzt)

- **Whale Alert API**: kein echter Free Tier nach 7-Tage-Trial. Alternative: `etherscan.io`-Whale-Transfer-Webhook pruefen.
- **Nansen / Glassnode**: nur mit Bezahl-Plan. Erst nach DefiLlama-Free-Tier-Evaluation.
- **Coinbase/Binance Announcements**: Binance kein RSS, JSON-CMS-API (`developers.binance.com/docs/cms/announcement`) pruefen. Coinbase: `status.exchange.coinbase.com` Atom-Feed.
- **LinkedIn / Substack**: LinkedIn kein RSS-API. Substack-Feeds fuer Lyn Alden, Bankless-Newsletter, etc. pruefen.

---

## Nicht vorgeschlagen (mit Begruendung)

- **Lookonchain**: kein API/RSS, nur X-Feed → kein eigener Adapter sinnvoll, Cross-Signal via Twitter-Watchlist.
- **Dune Analytics**: Query-basiert, kein Push-Modell.
- **Nansen Smart Money**: paid-only, kein nutzbarer Free Tier.

---

## Implementierungsreihenfolge (empfohlen)

1. **Tag 1** (parallel): CryptoPanic + Fear & Greed + Reddit RSS — alle in einem Tag schaffbar.
2. **Tag 2-3**: SEC EDGAR + DefiLlama — strukturelle Gaps der Kategorien A/C.
3. **Backlog**: GitHub Releases (low-effort), Coinglass nach Budget-Entscheidung.

Velocity-Effekt nach Tag 1 in `provenance.by_source` ablesbar — neue Sources erscheinen mit `resolved=0` und akkumulieren ueber 24-72h.

## Cross-Refs

- KAI Master Execution Directive §4 (Pflicht-Erweiterungs-Auftrag) + §5 (legal/respectful/no scraping bypass).
- B-1 Forward-Replay (`docs/pi_migration/pi5_cutover_postmortem_20260507.md`) als komplementaerer Sample-Velocity-Hebel.
- Existierender CryptoPanic-Code: `app/integrations/cryptopanic/`.
