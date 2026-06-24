# ADR 0010 — Live/Replay-Umschaltung + Shadow/Live-Vergleich (#318)

**Status:** Phase 1 IMPLEMENTIERT (default-off, read-only); Phase 2 (Live-Switching) GATED
**Datum:** 2026-06-24
**Bezug:** TODO #318 (Folge-Architektur-Sprint)

> **Update 2026-06-24:** Phase 1 gebaut — `app/observability/counterfactual_replay_logger.py`
> (pure: `bar_covering`/`build_comparison`/`run_counterfactual_pass`) +
> `scripts/counterfactual_replay.py` (flag-gated, reuse `binance_kline_fetcher`).
> Vergleicht je Shadow-Kandidat den **Live**-Entry-Preis gegen die **gesettelte**
> 1m-Kline (Replay) der Entry-Minute → `in_settled_range` + Drift-bps →
> `artifacts/counterfactual_comparison.jsonl`. Flag `EXECUTION_DUAL_STREAM_DIAGNOSTICS`
> (default off), Schwelle `EXECUTION_DUAL_STREAM_DRIFT_BPS` (default 30). Read-only,
> kein Live-/Paper-Pfad. Konservativer Default „Binance-Klines re-fetch" gewählt
> (Entscheidung 1+2 mit gekennzeichnetem Default belegt). Aktivierung = Operator
> setzt das Flag. Phase 2 (per-Zyklus-Live-Switching) bleibt gated.

## Kontext

#318 will (a) einen Trading-Zyklus alternativ gegen **Replay**-Daten statt Live fahren
können und (b) **Shadow vs. Live** vergleichen („was wäre passiert"). Verifizierter
Ist-Zustand (Worktree-Audit 2026-06-24):

- **`entry_mode` ist GLOBAL**, nicht per-Zyklus: `app/execution/entry_policy.py:241`
  (`resolve_entry_policy`) liest `settings.execution.entry_mode`; der Orchestrator
  snapshotet ihn EINMAL pro Lauf (`app/orchestrator/trading_loop.py:204`).
- **Loop liest nur Live-Marktdaten:** `trading_loop.py:388–430`
  (`get_market_data_point`). Kein Replay-Daten-Seam.
- **Vorhandene Shadow-Infra (read-only):** `app/observability/shadow_candidate_ledger.py`
  (hypothetische Kandidaten + Forward-Resolution MAE/MFE), `app/observability/shadow_real_feed.py`
  (reale Analyse im erzwungenen SHADOW-Modus, flag-gated), `app/execution/audit_replay.py`
  (Paper-Audit-Replay). **Aber:** kein paralleler Live∥Replay-Vergleich, kein
  Counterfactual-Log das Live-Entscheidung gegen Replay-Entscheidung stellt.
- **Live-Guard:** `trading_loop.py:_run_once_guard` erlaubt nur PAPER/SHADOW
  (`_ALLOWED_CONTROL_MODES`), Engine `live_enabled=False`.

Fehlend für #318: (1) Dual-Stream-Erfassung (Live ∥ Replay parallel), (2) per-Zyklus
`entry_mode` statt global, (3) Orchestrator-Replay-Seam, (4) Counterfactual-Log.

## Entscheidung

Strikt phasiert, weil der wertvolle Teil (per-Zyklus-Mode-Switching) den
**kapital-nahen Live-Orchestrator** berührt:

### Phase 1 — Counterfactual-Logger (read-only, freigabefähig, KEIN Live-Pfad)
Neue `app/observability/counterfactual_replay_logger.py`: läuft **nach** dem echten
Zyklus (`trading_loop.run_cycle`, hinter `await self._write_db(cycle)`), liest die im
Zyklus bereits gewählte Geometrie + die Live-Marktdaten, holt **dieselbe** Datenquelle
zum Resolutionszeitpunkt erneut (Replay-Sicht) und schreibt einen Diff nach
`artifacts/counterfactual_comparison.jsonl` (`cycle_id, symbol, entry_live,
entry_replay, mfe/mae_diff, drift`). Flag `EXECUTION_DUAL_STREAM_DIAGNOSTICS=false`
(default-off, fail-closed). Mutiert **nichts** am Live-/Paper-Pfad, kein Eintrag in
`paper_execution_audit.jsonl`. Rollback = Flag aus. Reuse der vorhandenen
Forward-Resolution aus `shadow_candidate_ledger.resolve_pending`.

### Phase 2 — GATED: per-Zyklus-Live/Replay-Switching
`run_cycle(execution_mode_override=…)` + per-Zyklus statt globaler `entry_mode`,
verschärfter Guard (Live komplett blockiert wenn Replay aktiv), Idempotency-Schutz
gegen Doppel-Fills. **Nicht autonom baubar** — berührt echtes Kapital, irreversibel.

## Offene Operator-Entscheidungen (Voraussetzung für Phase 1-Wert + ganz Phase 2)

1. **Replay-Datenquelle:** Binance-Spot-Klines (1m/5m) re-fetch? Perp-Funding/OI?
   Eigenes Realtime-Archiv? (Bestimmt, was „Replay" konkret vergleicht.)
2. **Drift-Toleranz:** Ab welcher Abweichung Live↔Replay ein `DRIFT_EXCEEDED`-Signal?
3. **Switching-Policy (Phase 2):** nie automatisch (immer Operator + signiert) vs.
   regelbasiert (z. B. Vol > X → Replay)? Default-Empfehlung: **nie automatisch**.
4. **Mess-Horizont:** täglich re-resolved, welche Symbole/Regime, Backtest-Frage
   „was hätte Replay-statt-Live gebracht".

## Konsequenzen

- **Positiv:** Phase 1 liefert sofort eine ehrliche Drift-/Counterfactual-Sicht ohne
  Kapital-Risiko; macht die Architektur für Phase 2 messbar begründbar.
- **Risiko (Phase 2):** Race-Conditions Live↔Replay, Audit-Desync, Operator-Fehlbedienung
  (`override=LIVE` während Replay). Darum gated + signiert.
- **Doktrin:** Kein Feature ohne klaren Operator-Mehrwert → Phase 1 erst sinnvoll, wenn
  Entscheidung (1) gefallen ist; sonst Leerschale. Daher: ADR zuerst, Bau nach Freigabe.

## Nächster konkreter Schritt
Operator beantwortet Entscheidung (1)+(2) → dann Phase-1-Logger bauen (≈150 Z. +
Tests, default-off). Phase 2 bleibt bis zu echtem Kapital/Live separat gegated.
