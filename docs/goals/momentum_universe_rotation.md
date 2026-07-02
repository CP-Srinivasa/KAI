# Goal: Momentum-Universe-Rotation + externe Cross-Check-Intelligence

> Status (2026-06-26): **G0–G5 gebaut, getestet, gemergt, auf Pi deployed & live.** G0 Universe-Panel
> + Refresh-Timer · G1 Rotations-FSM (Hysterese auf echten Daten bewiesen) · G2 Feeder→Paper
> (cohort-getaggt, G1→G2-Loop greift) · G3 Momentum-Bayes-Evidence (shadow/inert) + Eval-Pipeline ·
> G4 Cross-Check (eigenes TA-Rating, ToS-konform) · G5 Cohort-Edge→Release-Recommendation (gated).
> Edge-/Eval-Verdikte stehen ehrlich auf `insufficient`, bis genug `momentum_universe`-Trades
> schließen. Promote zu Live-Sizing bleibt operator+edge-gegated. Genehmigt vom Operator 2026-06-26.

## Context (warum dieser Plan)
KAI soll regelmäßig mit „den besten Coins zum Handeln" gefüttert werden: ein Universe der
meist-gehandelten / best-performenden Kryptos bilden, paper ausprobieren, **Gewinner halten,
Verlierer rotieren**, laufend frisch halten (Vorbild war die TradingView-Seite *prices-most-traded*).
Zusätzlich sollen externe Daten (Performance, technische Ratings, Derivate, On-Chain-Transaktionen/
Adressen, Sentiment) **zum Abgleich** danebenstehen.

**Strategische Einordnung (ehrlich):** Der Kern ist sinnvoll und ~90 % der Infrastruktur existiert
schon. Zwei harte Leitplanken sind nicht verhandelbar:
1. **Kein TradingView-Scraping als Kern.** HTML-/`scanner.tradingview.com`-Pull verstößt gegen die
   TV-ToS und ist fragil. „Most-traded/best-performer" rechnen wir rechtssicher aus **eigenen
   Börsendaten** (Bybit-Volumen + Binance/OHLCV-Returns) — faktisch dieselbe Liste. Externe Quellen
   kommen als **opt-in, default-off, isolierte, fail-soft Cross-Check-Lane** dazu (legitime Provider).
2. **Kein Live-Kapital ohne bewiesenen Edge.** Naive Momentum-„Best-Performer-Jagd" ist netto (nach
   Gebühren) typischerweise negativ und churn-anfällig. Alles läuft **mess-/paper-first**, kosten-/
   churn-bewusst, durch das vorhandene Edge-Gate. Ein ehrliches `NO_GO` ist ein **valides Ergebnis**.

**Operator-Entscheidungen:** Datenbasis = eigene Börsendaten **+** externe Cross-Check-Lane (gated).
Umfang = **(1) Rotations-Feeder + (2) Momentum-als-Bayes-Evidence**. Rotations-Trigger = **Mittelweg:
Netto-PnL-über-Fenster + Hysterese × Wilson-Score**.

## Leitprinzipien
- **Zwei Bahnen, getrennt:** Lane A (eigene Daten, rechtssicher, produktiver Kern, immer an) ·
  Lane B (externe Intelligence, nur Cross-Check, gated). Lane A funktioniert ohne Lane B.
- **Wiederverwenden statt duplizieren:** eine Fee-SSOT (`cost_model.py`), ein Edge-Gate
  (`generator_edge`/`edge_release_policy`/`promotion_gate`), ein Evidence-Muster
  (`evidence_settings`+`*_wiring`+`evaluate_*`).
- **Default-off + fail-soft.** **Cohort-Tagging** (`cohort=momentum_universe`) für isolierte Messung.

## Wiederverwendbare Seams (Code-Sweep verifiziert)
| Zweck | Datei |
|---|---|
| Dyn. Universe / Volumen-Rang | `app/market_data/bybit_adapter.py` `top_symbols_by_volume()` · `app/observability/technical_screener_feed.py` |
| Returns | `app/market_data/base.py` `get_ohlcv()` (24h/7d/30d selbst rechnen) · `momentum.py` (nur 24h, 3 Symbole) |
| Feeder-/Scheduler-Muster | `app/observability/technical_paper_feeder.py` · `app/orchestrator/technical_paper_scheduler.py` |
| Rotations-Vorbild | `app/learning/source_lifecycle.py` · `source_rotation_policy.py` · `source_graduation.py` · `source_reliability.py` (Wilson) |
| Per-Symbol-PnL | `app/observability/paper_quality_snapshot.py` `by_symbol` |
| Fee-SSOT | `app/execution/cost_model.py` · `config/venue_fees.yaml` |
| Edge-Gate | `app/observability/generator_edge.py` (n≥30, net-Median>0, P≥0.60, IC≥2, ECE≤0.10, DD≤2000, ≥2 Regime) |
| Release/Promote | `app/risk/edge_release_policy.py` · `app/risk/promotion_gate.py` (fail-closed) |
| Churn/Fee | `app/observability/churn_report.py` · `/dashboard/api/churn` |
| Evidence-Muster | `app/core/evidence_settings.py` · `app/signals/*_wiring.py` · `scripts/evaluate_v5_evidence.py` |

## Lane A (eigene Daten, produktiver Kern)
Datenfluss: Universe-Builder → Kandidaten-Ledger → Asset-Rotation-FSM (Scoring) →
Feeder → `run_trading_loop_once()` → Paper-Fill → Per-Symbol-PnL → zurück ins Scoring.
- `app/observability/momentum_universe.py` — **pure** Ranking (Volumen-Percentile + Mehrfenster-
  Return-Percentile → Universe-Score; robust gegen Ausreißer/NaN). **[G0 — fertig]**
- `artifacts/momentum_universe_candidates.jsonl` — Kandidaten-Persistenz (JSONL).
- `app/learning/asset_lifecycle.py` + `asset_rotation_policy.py` + `asset_performance_score.py` —
  Asset-FSM + Mittelweg-Trigger (Netto-PnL-Fenster × Wilson-LB, Hysterese/Min-Hold, replace-only-
  when-ready, `PINNED` nie rotiert).
- `app/observability/momentum_universe_feeder.py` + `app/orchestrator/momentum_universe_scheduler.py`
  — Feeder analog technical-paper, `max_per_run`-Cap, Dedup, cohort-getaggt; systemd-Notifier.
- Settings `MOMENTUM_UNIVERSE_*` (default-konservativ, enabled=false).

## Momentum-als-Bayes-Evidence (Umfang Teil 2)
- `MomentumUniverseEvidenceSettings` (enabled=False, source_trust=0.5, direction_aligned=0 inert,
  shadow_log `artifacts/momentum_evidence_shadow.jsonl`).
- `app/signals/momentum_wiring.py::build_momentum_evidence_provider()` (Struktur von funding_wiring).
- in `composite_evidence_wiring.py` registrieren · `scripts/evaluate_momentum_evidence.py`
  (PIT-Join + Bootstrap; Richtung gelernt, nie auto-geflippt).

## Lane B (externe Cross-Check-Intelligence, gated)
Provider-Interface `app/integrations/market_intel/` (fail-soft, gecacht, nie kritischer Pfad).
**TradingView:** kein ToS-konformer Pull — wir bauen **keinen** Scraper; TV bleibt webhook-push-only
(`integrations/tradingview/`). Cross-Check-Wert kommt über legitime Provider + **eigene TA-Ratings**
(`pandas-ta` auf `app/market_data/indicators.py`).

| Prio | Quelle | Liefert | Zugang | Legal |
|---|---|---|---|---|
| P0 | Bybit `allLiquidation` WS | Liquidations (Derivate) | keyless WS | ToS-OK |
| P0 | Binance Futures `!forceOrder` | Liquidations (3. Venue) | keyless REST+WS | ToS-OK |
| P1 | Mempool.space | BTC On-Chain (Mempool/Fees) | keyless, MIT | ToS-OK |
| P1 | Messari (Graduation) | Sektor/Kategorie, Research | Free-Key 20/min | ToS-OK* |
| P1 | CryptoCompare/CoinDesk | News-Kategorien + Social-Volume | Free-Key | ToS-prüfen* |
| P2 | Eigene TA (`pandas-ta`) | „Technische Ratings" (TV-Ersatz) | lokal | ToS-OK |
| P2 | OKX `rubik` OI + LS | Derivate (3. Venue) | keyless REST | ToS-OK |
| P2 | LunarCrush Free | AltRank/Galaxy (social) | Free-Key, experimentell | ToS-prüfen* |

\*Key-Secrets nie ins Repo (Classifier-Block), nur Pi-`.env`; Attribution einhalten.
Lücke (eigene Scouting-Runde): On-Chain-Altcoins (ETH/Etherscan-Free, SOL/Solscan).

## Edge-/Kosten-Disziplin (Gate jedes Feeders)
1. Mess-/paper-first, cohort-getaggt. 2. `edge-report`/`generator_edge` misst kosten-netto.
3. `churn-report` muss `trades_per_day × fee_drag < Edge` zeigen, bevor Kadenz steigt.
4. `edge_release_policy` → Modus, `promotion_gate` fail-closed; **Live nur bei `GO` + OOS≥2 Tage +
   Operator-Freigabe.**

## Phasen (klein & reversibel)
- **G0** — Universe-Builder (pure Ranking) + dieses Goal-Doc + (folgt) Kandidaten-Ledger + Dashboard.
- **G1** — Rotation-FSM + Scoring (shadow, kein Feed).
- **G2** — Feeder → Paper (cohort-getaggt, cap).
- **G3** — Momentum-als-Bayes-Evidence (shadow) + Eval-Script.
- **G4** — externe Cross-Check-Lane (legitime Provider scharf, TV push-only) + Cross-Check-Panel.
- **G5** — Edge-Messung → Release (gated; GO **oder** ehrliches NO_GO).

## Hauptrisiken & Gegenmaßnahmen
- Gebühren-/Churn-Bleed → Mittelweg-Trigger + Hysterese/Min-Hold + `churn-report`-Gate.
- Naive Momentum = kein Edge → mess-first + Edge-Gate; NO_GO akzeptiert.
- TV-ToS → compliant-first, kein Scraper, legitime Provider.
- Pi-Last → `max_per_run`-Cap, konservative Intervalle, fail-soft.
- Universe-Instabilität → Hysterese + replace-only-when-ready + `PINNED`.

## Bewusst NICHT in diesem Goal
Live-Kapital/Live-Trading (separates Gate); TV-Scraping als Default-Kern; zweiter Kostenpfad/Gate;
verteilte Compute. Edge-Promotion bleibt manueller Operator-Akt.
