# ADR 0006 — Source-Intake Exploration (Graubereich, isolierte Sandbox)

- Status: accepted
- Datum: 2026-06-15
- Decision-ID: DEC-SRC-EXPLORE-001
- Kontext-Goal: „Integration von Scraper + Wrapper für CoinMarketCap, CoinGecko,
  Glassnode, Dune, CoinGlass, Messari, Nansen" (Operator-Auftrag)
- Plan-SSOT: `docs/strategy/source_intake_exploration_plan.md`

## Problem

KAI soll seine Input-Breite über sieben Krypto-Datenquellen prüfen. Die meisten
haben offizielle APIs (oft mit Free-Tier), aber Free-Tiers sind häufig
beschnitten — ohne praktischen Vollbetrieb der Wrapper UND der Scraper lässt
sich nicht seriös beurteilen, welche Quelle real welche nutzbaren Daten liefert.
`CLAUDE.md §5` + Non-Negotiable Rules verbieten jedoch „scraping-first" und
fordern „API-first".

## Entscheidung

Der „API-first / no-scraping-foundation"-Teil von §5 wird **zeitlich begrenzt
und eng begrenzt ausgesetzt** — Scraper-Wrapper dürfen in vollem Umfang gebaut
und betrieben werden, **ausschließlich** in einer isolierten Explorations-Sandbox
`app/exploration/`. Ziel ist Messen (Coverage-Report), nicht Produktion.

Isolation (test-erzwungen, `tests/unit/test_exploration_import_isolation.py`):
- Kein Produktiv-Runtime-Modul (signals, orchestrator, execution, trading,
  alerts, risk, market_data, pipeline, ingestion) importiert `app.exploration`.
- `app.exploration` importiert NUR aus `app.exploration` / `app.security` /
  `app.core` — nie aus den genannten Runtime-Modulen.
- Eigene Settings (`ExplorationSettings`, NICHT in `AppSettings`) und eigener
  CLI-Entrypoint (`python -m app.exploration.cli`). Vollständig reversibel via
  `rm -rf app/exploration`.

## Was NICHT ausgesetzt wird (harte Linien, bleiben in Kraft)

- Kein Login-/Paywall-/Auth-Bypass, kein CAPTCHA-Bruch.
- Keine DoS-nahen Raten — Throttle (`min_request_interval_seconds`) + Capture-
  Cache Pflicht; ehrlicher User-Agent.
- Keine Secrets im Repo.
- SSRF-Guard (`app.security.ssrf.validate_url`) vor jedem Outbound-Call.

## Folgen

- default-off: ohne `EXPLORATION_ENABLED=true` + Quell-Flag läuft nur die
  netzwerklose Dummy-Probe. Key-pflichtige Quellen ohne Key melden ehrlich
  `disabled_no_api_key`.
- Produktiv-Pfad unverändert; null Edits an Produktiv-Modulen.
- Graduation (Sandbox → `ingestion`/`market_data`/`signals`) re-aktiviert die
  vollen Regeln: Coverage = GO, produktionsfähiger Zugangsweg, definiertes
  Mapping, default-off/shadow-only Start, Tests + Doku.

## Re-Evaluation

Nach Vorliegen des Coverage-Reports v2, spätestens 4 Wochen nach Start
(~2026-07-13): pro Quelle graduieren / verwerfen / weiter beobachten;
Override-Status erneut bewerten.

## Rückrollbarkeit

Vollständig. Verzeichnis-Löschung oder `git revert` entfernt die Schicht ohne
Produktiv-Folgen.
