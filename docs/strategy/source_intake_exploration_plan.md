# Source-Intake Explorationsplan — CMC · CoinGecko · Glassnode · Dune · CoinGlass · Messari · Nansen

**Status:** Plan (Dokument-only, noch kein Code)
**Datum:** 2026-06-15
**Operator-Autorisierung:** Ja (siehe DECISION-Eintrag unten)
**Owner:** Claude Code (Hauptagent) + Operator (Sascha)
**Bezug:** KAI Master Execution Directive §3–§5 (Datenquellen-Breite), Input-Breite-Sprint (#248–#254), V5 Funding/OI-Evidence (#211)

---

## 0. Kurzfassung (TL;DR)

Wir bauen **eine isolierte, abtrennbare Explorations-/Benchmark-Schicht** (`app/exploration/sources/`),
in der pro Quelle **API-Wrapper UND Scraper-Wrapper nebeneinander** laufen. Sie schreibt
**Roh-Captures nach `artifacts/exploration/`** und erzeugt einen **Vergleichs-/Coverage-Report**
(Abdeckung, Frische, Felder, Nutzbarkeit, Kosten, Stabilität). Ziel ist **Lernen, nicht Produktion**:
herausfinden, welche Quelle real welche nutzbaren Daten liefert — bevor irgendetwas in den
Produktiv-Pfad (`ingestion`/`market_data`/`signals`) wandert.

Die Schicht ist **default-off, sandboxed, reversibel** (`rm -rf app/exploration` entfernt alles).
Kein Explorationsadapter darf Signale, Trades oder Gates beeinflussen.

---

## 1. DECISION — temporärer, eng begrenzter Override von CLAUDE.md §5

> **decision_id:** DEC-SRC-EXPLORE-001
> **datum:** 2026-06-15
> **entscheidung:** Für eine zeitlich begrenzte Explorationsphase wird der „API-first /
> no-scraping-foundation"-Teil von CLAUDE.md §5 + Non-Negotiable Rules **ausgesetzt** —
> Scraper-Wrapper dürfen in vollem Umfang gebaut und betrieben werden, **ausschließlich
> innerhalb des isolierten Moduls `app/exploration/sources/`**.
> **begruendung:** Operator-Entscheid. Realer Erkenntnisgewinn („was liefert jede Quelle
> wirklich, was ist nutzbar") ist ohne praktischen Vollbetrieb der Wrapper nicht seriös
> beurteilbar. Free-Tier-APIs sind oft beschnitten; Scrapen deckt auf, was die API verschweigt.
> **scope:** NUR Exploration-Sandbox. Produktiv-Layer (`app/ingestion`, `app/market_data`,
> `app/signals`) bleiben unter den vollen Regeln.
> **was NICHT ausgesetzt wird (harte Linien, bleiben in Kraft):**
>   - kein Login-/Paywall-/Auth-Bypass
>   - keine Credential-Umgehung, keine CAPTCHA-Bruch-Dienste
>   - keine DoS-nahen Request-Raten; respektvolle Throttles + Caching Pflicht
>   - keine Secrets im Repo
> **rueckrollbarkeit:** Vollständig. Modul ist abtrennbar; `git revert` / Verzeichnis-Löschung
>   entfernt die gesamte Schicht ohne Produktiv-Folgen.
> **zieltermin Re-Evaluation:** nach Abschluss Sprint EXPLORE-S2 (Coverage-Report liegt vor),
>   spätestens 4 Wochen nach Start. Dann Entscheidung pro Quelle: graduieren / verwerfen /
>   weiter beobachten.
> **betroffene_dokumente:** DECISION_LOG.md, ASSUMPTIONS.md, ARCHITECTURE.md, SECURITY.md

**Wichtig:** Dieser Override macht die Sandbox nicht zum Produkt. Der Übergang von Sandbox →
Produktiv-Pfad re-aktiviert die vollen Regeln (siehe §9 „Graduation-Gate").

---

## 2. Ziel der Explorationsphase

Pro Quelle belastbar beantworten:

1. **Coverage** — welche Felder/Metriken kommen real an? (Liste, nicht Marketing-Versprechen)
2. **Frische** — wie aktuell sind die Daten? (Latenz, Update-Cadence)
3. **Free-Tier-Realität** — was geht ohne Geld, wo beginnt die Beschneidung?
4. **API vs. Scrape** — liefert der Scrape mehr/anderes als die API? Lohnt der Graubereich?
5. **Nutzbarkeit für KAI** — mappt das auf Signal-Evidence / Narrative / Marktdaten?
6. **Stabilität** — wie oft bricht es? (Rate-Limits, DOM-Änderungen, Bans)
7. **Kosten** — echter Preis bei Skalierung auf KAIs Bedarf.

Output: **ein Coverage-Report** (`artifacts/exploration/coverage_report.md` + JSONL-Rohdaten),
der für jede Quelle ein ehrliches GO / NO-GO / CONDITIONAL gibt.

---

## 3. Architektur der Exploration-Sandbox

```
app/exploration/
  __init__.py
  sources/
    __init__.py
    base.py                 # ExplorationProbe-Kontrakt (siehe §4)
    coinmarketcap/
      api.py                # offizieller API-Wrapper (Free-Tier-Key)
      scraper.py            # Scraper-Wrapper (Graubereich, isoliert)
    coingecko/
      api.py                # nutzt vorhandenen Free/Pro-Pfad, aber VOLL ausgereizt
      scraper.py
    glassnode/
      api.py
      scraper.py
    dune/
      api.py
    coinglass/
      api.py
      scraper.py
    messari/
      api.py
      scraper.py
    nansen/
      api.py
      scraper.py
  runner.py                 # führt alle aktivierten Probes aus, schreibt Captures
  report.py                 # erzeugt coverage_report.md aus den Captures
  cli.py                    # Typer: `exploration run|report|probe <quelle>`

artifacts/exploration/
  raw/<quelle>/<timestamp>.json     # Roh-Capture (unverändert, audit)
  normalized/<quelle>.jsonl         # auf ExplorationRecord normalisiert
  coverage_report.md                # menschenlesbarer Vergleich
```

**Isolations-Regeln (bindend):**
- `app/exploration/` importiert NICHT aus `app/signals`, `app/orchestrator`, `app/execution`.
- Kein Produktiv-Modul importiert aus `app/exploration/`.
- Eigener CLI-Namespace (`exploration ...`), nicht in `ingestion`/`trading` gemischt.
- Eigener Settings-Block `ExplorationSettings`, alle Flags default-off.
- Ein optionaler Import-Lint (CI) verbietet Cross-Importe in beide Richtungen.

---

## 4. Gemeinsame Kontrakte

### ExplorationProbe (ABC)
```python
class ExplorationProbe(ABC):
    source_name: str
    access_mode: Literal["api", "scrape"]
    @abstractmethod
    async def probe(self) -> ExplorationResult: ...  # darf NIE raisen
```

### ExplorationResult / ExplorationRecord
- `source_name`, `access_mode`, `fetched_at`, `success`, `error`
- `raw` (unveränderter Payload → audit), `records: list[dict]` (flach normalisiert)
- `meta`: `http_status`, `rate_limit_remaining`, `latency_ms`, `bytes`, `field_count`

Der Report (§7) aggregiert über `records` + `meta`: Feld-Abdeckung, Frische, Fehlerquote,
API-vs-Scrape-Delta.

**Begründung für den gemeinsamen Kontrakt:** verhindert 7 Sonderformen und macht den
Vergleich überhaupt erst fair (gleiche Metriken über alle Quellen).

---

## 5. Quellen im Detail — was, wie, womit

Legende Free-Tier-Einschätzung = vor der Messung; die Sandbox prüft genau das nach.

### 5.1 CoinGlass — **P0** (Funding / OI / Liquidations)
- **API:** Free-Tier vorhanden (Funding-Rates, Open Interest, Liquidationen, Long/Short-Ratio).
- **Scrape:** Web-Heatmaps/Liquidation-Maps als Ergänzung.
- **Edge:** verlängert die **bereits live verdrahtete V5-Evidence** (`funding_evidence_cache.py`)
  um eine zweite, breitere Derivate-Quelle. Höchster Nutzen pro Aufwand.
- **Mapping:** Bayes-Evidence (Funding-Vorzeichen, OI-Δ, Liquidation-Cluster).

### 5.2 Messari — **P1** (Research-News + Metrics)
- **API:** Free-Tier (Asset-Metrics, News-Feed). Zwei Layer auf einmal.
- **Scrape:** Research-Artikel/Intel-Seiten als Narrative-Ergänzung.
- **Mapping:** Metrics → Evidence; News → `CanonicalDocument` (später `ingestion`-Adapter).

### 5.3 Dune Analytics — **P1** (beliebige On-Chain-Queries)
- **API:** Query-Execution-API mit Free-Credits. Kein Scrape nötig/sinnvoll.
- **Edge:** kuratierte SQL-Queries (Stablecoin-Flows, DEX-Volumen, Bridge-Flows) → Evidence.
- **Mapping:** je Query ein Evidence-Kanal; 1–2 Queries zum Start.

### 5.4 Glassnode — **P2** (On-Chain-Metriken)
- **API:** Tier-1 frei, Kern-Metriken paid. Scrape der Studio-Charts ist Graubereich + fragil.
- **Edge:** hoch, aber Kern-Wert hinter Paywall. Erst Tier-1 messen, dann entscheiden.

### 5.5 CoinMarketCap — **P3** (Preise/MarketCap/Listings)
- **API:** Free-Tier (Listings, Quotes). Scrape der Coin-Seiten möglich (deine Beispiel-Repos).
- **Edge:** **niedrig** — weitgehend redundant zu CoinGecko. Wert v.a. als Cross-Check/Dedup
  und für CMC-spezifische Felder (z.B. CMC-Rank, „trending").

### 5.6 Nansen — **P3** (Smart-Money / Wallet-Flows)
- **API:** vorhanden, aber teuer; Free-Tier dünn. Scrape stark eingeschränkt (Auth-Wall → harte
  Linie, NICHT umgehen).
- **Edge:** hoch (Smart-Money-Labels), aber Kosten-/Zugangs-Gate. Zuletzt.

### 5.7 CoinGecko — **bereits live, aber Kosten-Befund**
- **Befund:** Pro-Key nur für Preisabgleich = Verschwendung. Zwei Optionen im Report bewerten:
  - **(a) Downgrade auf Free-Tier** für den Preis-Use-Case, oder
  - **(b) Pro-Key ausreizen:** /trending, /categories, /derivatives, /global, /coins/{id}
    (Community-/Developer-/Sentiment-Daten), GeckoTerminal-DEX-Daten.
- Die Sandbox zieht GENAU diese zusätzlichen Endpunkte und zeigt, ob (b) den Pro-Preis
  rechtfertigt oder (a) reicht.

---

## 6. Referenz-Repos (aus deinen Links) — Einordnung

| Repo / Quelle | Rolle im Plan |
|---|---|
| `prouast/coinmarketcap-scraper` | Referenz für CMC-Scraper (`coinmarketcap/scraper.py`) |
| `python-coinmarketcap` (PyPI) | Referenz für CMC-API-Wrapper (`coinmarketcap/api.py`) |
| crawlbase-Blog CMC | Pattern für robustes Scraping (Retry/Headers) |
| `nsmle/cmc-api` | Alternative CMC-API-Lib (Endpunkt-Mapping) |
| `mdugan8186/coingecko-scraper` | Referenz CoinGecko-Scraper |
| codereview „pycryptoscraper" | generisches Scrape-Pattern (mehrere Quellen) |
| kodigy/pyflyde-Scraper | Pipeline-/Flow-Pattern (nur Inspiration, kein Framework-Zwang) |
| `madmaze/pytesseract` | **bewusst-nicht** als Fundament — siehe §11 |

Wir **kopieren nicht blind**, sondern adaptieren auf den `ExplorationProbe`-Kontrakt
(einheitliche Captures + nie-raisen + Throttle/Cache).

---

## 7. Coverage-Report (das eigentliche Produkt der Phase)

`exploration report` erzeugt pro Quelle:
- **Feld-Inventar:** welche Keys/Metriken real geliefert wurden (mit Nicht-Null-Quote).
- **Frische:** Median-Latenz Quelle→Capture, Update-Cadence.
- **API-vs-Scrape-Delta:** welche Felder NUR der Scrape liefert.
- **Stabilität:** Erfolgsquote über N Läufe, beobachtete Rate-Limits/Bans.
- **Kosten-Hochrechnung:** Calls/Tag bei KAI-Bedarf → Tier/Preis.
- **Verdict:** GO / CONDITIONAL / NO-GO + 1 Satz Begründung.

Das ist die Grundlage, um „was bringt was wirklich" ehrlich zu entscheiden.

---

## 8. Phasen & Reihenfolge

- **EXPLORE-S0 (Fundament):** `app/exploration/`-Skelett, `ExplorationProbe`, runner, report,
  CLI, Settings-Block, Import-Lint. Eine Dummy-Probe als Durchstich.
- **EXPLORE-S1 (P0/P1):** CoinGlass + Messari + Dune (API + ggf. Scrape) + CoinGecko-Endpunkt-
  Ausreizung. Erster Coverage-Report.
- **EXPLORE-S2 (P2/P3):** Glassnode (Tier-1 + Scrape-Test) + CMC (API + Scrape) + Nansen (API,
  nur Free). Coverage-Report v2 + Re-Evaluation des Overrides (DEC-SRC-EXPLORE-001).
- **GRADUATE (laufend):** Quellen mit GO wandern einzeln in den Produktiv-Pfad (§9).

---

## 9. Graduation-Gate (Sandbox → Produktiv) — Regeln gelten wieder voll

Eine Quelle darf erst aus der Sandbox in `ingestion`/`market_data`/`signals`, wenn:
1. Coverage-Report = GO (nutzbare Felder, akzeptable Frische/Stabilität).
2. **Zugangsweg produktionsfähig:** offizielle API ODER expliziter, dokumentierter,
   ToS-/Risiko-bewerteter Scrape mit Stabilitäts-Plan (kein fragiles Bastelwerk im Kernpfad).
3. Mapping definiert: market_data-Adapter / ingestion-Adapter / Bayes-Evidence.
4. default-off + shadow-only beim Produktiv-Start (wie V5), trust=0.5 bis gemessen.
5. Tests + SSRF-Guard + stale-gating + Doku.

**D.h.:** der Graubereich ist Lern-Werkzeug, nicht Lizenz für fragile Produktion.

---

## 10. Arbeitspakete

```yaml
ARBEITSPAKET:
  task_id: EXPLORE-S0
  phase_id: PHASE-4
  sprint_id: EXPLORE-S0
  titel: Exploration-Sandbox-Fundament (isoliert, default-off, reversibel)
  warum_jetzt: Ohne gemeinsamen Kontrakt + Isolation wird die Phase zu 7 Insellösungen.
  ziel: Lauffähiges Skelett mit Dummy-Probe, Captures, Report, CLI, Import-Lint.
  in_scope:
    - app/exploration/ Struktur + ExplorationProbe + Result/Record
    - runner.py, report.py, cli.py (Typer: exploration run|report|probe)
    - ExplorationSettings (alle Flags default-off, Keys als secret-Fields)
    - artifacts/exploration/ Schreibpfade
    - CI-Import-Lint: keine Cross-Importe exploration<->produktiv
  out_of_scope: [echte Quellen-Adapter, jede Produktiv-Verdrahtung]
  betroffene_module: [app/exploration, app/core/settings.py, app/cli, CI]
  betroffene_dokumente: [ARCHITECTURE, DECISION_LOG, ASSUMPTIONS, feature_flags]
  tests_erforderlich:
    - Dummy-Probe: success + raises-nie + Capture geschrieben
    - report.py: aggregiert Feld-Coverage korrekt
    - Import-Lint schlägt bei Cross-Import an
  validierung: [pytest, ruff, mypy]
  akzeptanzkriterien:
    - `exploration run` läuft mit Dummy-Probe, schreibt Capture + Report
    - nichts im Produktiv-Pfad importiert exploration
  risiken: [gering — isolierte Neuanlage]
  doku_sync_pflicht: [DECISION_LOG(DEC-SRC-EXPLORE-001), ARCHITECTURE(neuer Layer)]
  naechster_folgeschritt: EXPLORE-S1 (CoinGlass/Messari/Dune/CoinGecko)

ARBEITSPAKET:
  task_id: EXPLORE-S1
  phase_id: PHASE-4
  sprint_id: EXPLORE-S1
  titel: P0/P1-Quellen-Probes + erster Coverage-Report
  warum_jetzt: Höchster Edge pro Aufwand; CoinGlass dockt an live V5-Evidence an.
  ziel: API+Scrape-Probes für CoinGlass, Messari, Dune; CoinGecko-Endpunkt-Ausreizung;
    Coverage-Report v1 mit GO/CONDITIONAL/NO-GO.
  in_scope:
    - coinglass/{api,scraper}, messari/{api,scraper}, dune/api, coingecko/api(erweitert)
    - Free-Tier-Keys via Settings (nie im Repo)
    - Throttle + Cache + nie-raisen pro Probe
    - coverage_report.md v1
  out_of_scope: [Produktiv-Graduation, harte Gates, Glassnode/CMC/Nansen]
  betroffene_module: [app/exploration/sources/*]
  tests_erforderlich:
    - pro Probe: Parsing, leerer/Fehler-Payload, fehlender Key → disabled
    - Report-Verdict-Logik
  validierung: [pytest, ruff, mypy, Smoke gegen Free-Tier]
  akzeptanzkriterien:
    - Report listet reale Feld-Coverage + API-vs-Scrape-Delta je Quelle
    - keine Probe raised; Rate-Limits respektiert
  risiken:
    - rechtlich: ToS-Graubereich (durch Sandbox+Throttle+keine-Auth-Bypass begrenzt)
    - operativ: Free-Tier-Limits/Bans (Throttle+Cache)
  doku_sync_pflicht: [coverage_report, ASSUMPTIONS(Free-Tier-Realität je Quelle)]
  naechster_folgeschritt: EXPLORE-S2 + erste Graduation-Kandidaten

ARBEITSPAKET:
  task_id: EXPLORE-S2
  phase_id: PHASE-4
  sprint_id: EXPLORE-S2
  titel: P2/P3-Quellen + Override-Re-Evaluation
  warum_jetzt: Vervollständigt die Vergleichsbasis; teure/zugangsbeschränkte Quellen ehrlich bewerten.
  ziel: Glassnode(Tier-1+Scrape-Test), CMC(API+Scrape), Nansen(API Free); Coverage-Report v2;
    Entscheidung pro Quelle graduieren/verwerfen + Override-Status prüfen.
  in_scope: [glassnode/*, coinmarketcap/*, nansen/api, coverage_report v2]
  out_of_scope: [Auth-Bypass jeder Art (harte Linie)]
  betroffene_module: [app/exploration/sources/*]
  tests_erforderlich: [pro Probe analog S1]
  validierung: [pytest, ruff, mypy]
  akzeptanzkriterien: [Report v2 vollständig; DEC-SRC-EXPLORE-001 re-evaluiert + dokumentiert]
  risiken: [Nansen/Glassnode-Auth-Walls — NICHT umgehen, als NO-GO/CONDITIONAL führen]
  doku_sync_pflicht: [DECISION_LOG(Re-Eval), coverage_report v2]
  naechster_folgeschritt: GRADUATE einzelner GO-Quellen in Produktiv-Pfad
```

---

## 11. pytesseract / OCR — bewusst-nicht (jetzt)

OCR ist nur nötig, um Zahlen aus **Chart-Bildern** zu lesen (z.B. Glassnode-Studio-Screenshots).
Das ist die fragilste denkbare Quelle: bricht bei jedem UI-Pixel-Shift, langsam, fehleranfällig.
**Empfehlung:** nicht im Plan-Scope. Falls eine Quelle NUR als Bild existiert und sich als
hoch-relevant erweist (Report-Befund), dann separater, klar als `experimental` markierter
Spike — niemals im Kernpfad.

---

## 12. Risiken (ehrlich)

| Risiko | Einstufung | Gegenmaßnahme |
|---|---|---|
| ToS-Verletzung durch Scrape | mittel | Sandbox-Isolation, Throttle, Cache, keine Auth-Bypass, zeitbegrenzt |
| IP-Ban / Rate-Limit | mittel | konservative Raten, Backoff, Capture-Cache statt Re-Fetch |
| Free-Tier liefert weniger als gedacht | hoch (erwartet) | genau das misst der Report — kein Schaden, nur Erkenntnis |
| Sandbox sickert in Produktiv-Pfad | mittel | Import-Lint (CI) in beide Richtungen, eigener CLI/Settings-Namespace |
| Kosten-Überraschung (Glassnode/Nansen) | mittel | Kosten-Hochrechnung im Report VOR Graduation |
| Override wird Dauerzustand | mittel | DEC-SRC-EXPLORE-001 zeitbegrenzt + Pflicht-Re-Eval in S2 |

---

## 13. Doku-Sync-Pflicht

- `DECISION_LOG.md` ← DEC-SRC-EXPLORE-001 (Override + Re-Eval)
- `ARCHITECTURE.md` ← neuer isolierter Layer `app/exploration/`
- `ASSUMPTIONS.md` ← Free-Tier-Realität je Quelle (nach Report)
- `SECURITY.md` ← harte Linien (keine Auth-Bypass), Scrape-Risiko-Posture
- `feature_flags.md` ← `ExplorationSettings`-Flags (alle default-off)
- `artifacts/exploration/coverage_report.md` ← das eigentliche Ergebnis
```
