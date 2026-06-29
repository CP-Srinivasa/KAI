# Universe-Eligibility-Gate — Daten-Integritäts-SSOT

**Status:** Spec / genehmigt 2026-06-29 (Operator-Freigabe im Brainstorming)
**Kontext-Anker:** NORTH_STAR-Pivot (ADR 0012) — KAI = auditierbare Truth-/Research-Plattform.
Die Asset-Rotation-FSM des (per `falsify_momentum.py`, n=178 widerlegten) Momentum-Programms
wird **nicht** für ein totes Signal weiterbetrieben, sondern ihre Hygiene-Idee wird zu einem
wiederverwendbaren Daten-Qualitäts-Gate umgewidmet.

## Problem

Das auto-generierte Universe (`scripts/momentum_universe_refresh.py` → `BybitAdapter`) umgeht die
vorhandene Qualitäts-/Tradability-Infrastruktur (`app/trading/asset_universe.py`) komplett.
Folge im Live-Ledger `artifacts/momentum_universe_candidates.jsonl` (2026-06-28):
`VELVET/USDT` (Rang 1), `ACT`, `SLX/USDT` (Rang 3), `O`, `BEAT`, `RAVE`, `HYPE`, `PUMPFUN` —
obskure Microcaps mit Bybit-Top-Volumen, die auf der **kanonischen Resolve-Venue Binance** gar
nicht oder kaum existieren.

Das ist keine Kosmetik: derselbe `SLX`-Ausreißer (+2799 bps, off-Binance) hat real
**Edge-Messungen kontaminiert** (siehe `kai_momentum_falsified_20260629`). `falsify_momentum.py`
misst gegen `BinanceAdapter().get_ohlcv` und muss off-Binance-Symbole als `no_data` verwerfen —
die Kontamination wird *nachgelagert* abgefangen statt *vorgelagert* verhindert.

**Venue-Diskrepanz als Wurzel:** Universe kommt von Bybit, gemessen/resolved wird gegen Binance.
Symbole, die auf Bybit handelbar sind aber auf Binance nicht, sind strukturell unbrauchbar für
jede Binance-basierte Analyse.

## Ziel

Eine **wiederverwendbare Eligibility-SSOT**, die jede Symbol-Quelle (Universe-Builder, später
Edge-Discovery, `falsify`-Skripte, künftige Feeder) aufrufen kann, um strukturell unbrauchbare
Symbole **vorgelagert** auszuschließen — gemessen gegen die kanonische Venue Binance, mit dem
KAI-Honesty-Contract (fehlende Daten = nicht bewertbar = ausgeschlossen, nie geschätzt).

Nicht-Ziel: kein Richtungs-/Momentum-/Edge-Urteil. Das Gate trennt **strukturell verwendbar** von
**strukturell unbrauchbar**, nichts weiter.

## Design

### Architektur-Entscheidung

Neues **pures** Modul `app/trading/symbol_eligibility.py`, bewusst **getrennt** von
`asset_universe.py`:
- `asset_universe.py` = operator-**kuratiert** (YAML-Watchlist + Overlay).
- `symbol_eligibility.py` = **auto-berechnet** aus Live-Binance-Daten.

Es folgt denselben Mustern wie die bestehenden Policies (`asset_rotation_policy`,
`source_rotation_policy`): frozen Verdict-Dataclass, reine deterministische Entscheidung, I/O im
Wrapper. Wiederverwendet `base_symbol()` aus `app/trading/diversification.py`/`asset_universe.py`.

**Verworfen:** Erweiterung von `asset_universe` — würde kuratierte und auto-berechnete Logik
vermischen und die Honesty-Trennung (kuratiert vs. gemessen) brechen.

### Units (jede isoliert testbar)

1. **Pure Decision Core** — `app/trading/symbol_eligibility.py`
   ```
   evaluate_eligibility(symbol, metrics: SymbolMetrics) -> EligibilityVerdict
   ```
   - `SymbolMetrics(turnover_24h_usd: float|None, history_days: int|None, base: str, quote: str)`
   - `EligibilityVerdict(symbol, eligible: bool, reasons: list[str], flags: dict)`
   - Kriterien (alle pure, schwellwert-parametrisiert):
     - `liquidity_ok` — `turnover_24h_usd >= min_turnover_usd`
     - `history_ok` — `history_days >= min_history_days`
     - `not_duplicate` — wird vom Resolver gesetzt (s.u.), Core liest das Flag
   - **Honesty-Contract:** `turnover_24h_usd is None` **oder** `history_days is None`
     → `eligible=False, reasons=["no_canonical_venue_data"]`. So fallen off-Binance-Symbole
     automatisch raus — **kein separates exchangeInfo-Gate nötig.**

2. **Metrics-Fetcher (I/O)** — holt pro Symbol Binance-24h-Turnover + OHLCV-Historie-Länge
   via vorhandenem `BinanceAdapter`. **Fail-soft:** Symbol fehlt/Fehler → `None`-Metriken
   (→ nicht bewertbar → ausgeschlossen). Batcht/cached pro Refresh-Lauf (~50 Symbole), darf
   bei Binance-Ausfall **nie** den Refresh crashen (gleicher Vertrag wie `build_universe`).

3. **Doppel-Paar-Resolver** — gruppiert Kandidaten per `base_symbol()` nach Underlying,
   wählt **eine** kanonische Variante je Underlying, markiert den Rest `duplicate`.
   - Quote-Präferenz: `USDT` > `USDC` > sonstige.
   - Spot vor Perp (Edge-Resolve nutzt Spot-OHLCV).

4. **Audit-Ledger** — `artifacts/symbol_eligibility_audit.jsonl`: je Lauf je Symbol eine Zeile
   mit `symbol, eligible, reasons, metrics, ts`. Reversibel/nachvollziehbar, append-only.

### Datenfluss & Shadow→Enforce (KAI-Doktrin)

- **Phase 1 — shadow (dieser PR):** `momentum_universe_refresh` ruft das Gate auf und schreibt
  das Verdikt **als Flag** in jede Universe-Ledger-Zeile (`eligible`, `reasons`). **Filtert
  nicht.** Neues CLI `universe eligibility` (`--json`) zum Inspizieren. Erwartung an echten Daten:
  `VELVET/SLX/ACT/O/BEAT/RAVE/HYPE/PUMPFUN` werden als `eligible=false` markiert.
- **Phase 2 — enforce (späterer PR, nach Verifikation):** Konsumenten schalten auf tatsächliches
  Filtern via Flag `UNIVERSE_ELIGIBILITY_ENFORCE` (default OFF). Erst Universe-Builder, dann
  Edge-Discovery/`falsify` nachziehen.

### Default-Schwellwerte (operator-konfigurierbar, `config/`)

- `min_history_days = 30` (= bestehender Builder-Lookback `lookback_days=31`).
- `min_turnover_usd = 10_000_000` (Vorschlag; nach Sichtung der Shadow-Verteilung kalibrieren —
  Schwelle so setzen, dass die etablierten Namen drinbleiben und Microcaps rausfallen).

### Bewusst draußen (YAGNI)

- Kein Binance-`exchangeInfo`-Gate (implizit über Datenverfügbarkeit gelöst).
- Kein Momentum-/Richtungs-Kriterium (Signal widerlegt; Operator-Korrektur im Brainstorming).
- Kein Dashboard-Panel in v1 (CLI + Ledger reichen zur Verifikation; Panel später).
- Keine Enforce-Verdrahtung in v1 (shadow-first).

## Akzeptanzkriterien

1. `evaluate_eligibility` pure + voll unit-getestet (liquidity/history/duplicate/no-data-Pfade).
2. Doppel-Paar-Resolver: deterministische kanonische Wahl, getestet (USDT/USDC/Spot-Perp).
3. Metrics-Fetcher fail-soft: Binance-Ausfall → leere Metriken, kein Crash (getestet mit Mock).
4. `momentum_universe_refresh` schreibt `eligible`/`reasons` ins Ledger, **ohne** zu filtern.
5. CLI `universe eligibility --json` listet je Symbol Verdikt + Gründe.
6. Audit-Ledger wird geschrieben, append-only.
7. `kai_preflight` grün (ruff/mypy/pytest/godfile-ratchet).

## Verifikation (vor „fertig")

- Shadow-Lauf auf dem Pi gegen echtes Universe → manuell prüfen, dass die bekannten
  Off-Venue/Microcap-Namen als `eligible=false` markiert sind und etablierte Namen
  (`BTC/ETH/SOL/XRP`) als `eligible=true`.
- Gegen-Check: kein `eligible=true`-Symbol ohne Binance-Daten (Honesty-Contract hält).
