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

---

## Phase-1-Evidenz-Review 2026-06-29 → **Phase-2-Verdikt: NO-GO** (bleibt gated)

Die Phase-1-Evidenz ist jetzt belastbar akkumuliert. Auswertung des vollständigen
Drift-Datensatzes (`artifacts/counterfactual_comparison.jsonl`, **7144 Records**,
frisch bis 2026-06-29 12:08, alle `threshold_bps=30`) + Gate-G0-Abgleich
(`trading canonical-edge`) am 2026-06-29 (alles gegen Pi-Mainline `e717054` verifiziert):

### Drift-Befund (quellen-stratifiziert — das ist der Kern)
- **Gesamt:** 33,9 % `drift_exceeded` (Live-Entry > 30 bps von gesettelter Kline-Range);
  nur **15,5 % `in_settled_range`** (Live-Entry innerhalb der gesettelten 1m-Kline).
- **Nach Quelle** (der entscheidende Schnitt):
  | source | n | drift_exceeded |
  |---|---|---|
  | `technical_screener` | 5752 | **41,4 %** |
  | `autonomous_loop` | 552 | 6,5 % |
  | `autonomous_generator` | 840 | **0,7 %** |
- Die Gesamt-„33,9 %" sind also **fast vollständig vom `technical_screener` getragen**,
  nicht von der Edge-tragenden `autonomous_generator`-Quelle.
- **Screener-Divergenz ist überwiegend echt:** 2294 plausible Exceedances (entry_live
  im Sinn-Bereich), **Median 56 bps, Mittel 72 bps, max 781 bps** — der Screener-Entry-Preis
  weicht material von der gesettelten Binance-Perp-Kline ab.
- **Datenqualitäts-Kontaminante (klein, aber real):** 92 absurde Records mit
  `drift_to_range_bps` > 1000 bps (1,29 %; bis 10,7 Mio bps) + ein
  `entry_live ≈ 100–102`-Cluster (108 Records = 1,9 % des Screeners; ENA/XLM/WLD/ZEC,
  z. B. ENA `entry_live=101,98` vs echte 0,094). Das sind **kein Slippage, sondern ein
  Preis-/Einheiten-/Platzhalter-Bug im Screener-Entry-Pfad**, der die Drift-Statistik
  nach oben verzerrt. Bereinigt (ohne >1000 bps): Gesamt 33,1 %; generator 0,1 %;
  loop 6,5 %; screener 40,5 %.
- **Ehrliches Caveat:** Die 0,1–0,7 % des `autonomous_generator` sind teils
  **tautologisch** — der Generator speist bereits aus Binance-Klines, die der Replay
  erneut holt. Es belegt „kein Feed-Mismatch", ist aber **schwache** Evidenz für echte
  Ausführungs-Treue. Nicht überinterpretieren.

### Gate G0 (Edge) — NICHT bestanden, fragil
`trading canonical-edge` (2026-06-29): n=62, P(mu_net>0)=56,5 % — **aber vollständig von
einem einzigen +2799 bps-Ausreißer getragen**: ohne Best-Trade fällt P auf **25,0 %**
(mean −32 bps). Median −89 bps, net/notional −8,1 bps, Bootstrap-CI95 [−107; +146]
überspannt Null. **Kein robuster Edge → Gate-G0-Vorbedingung für jeden Live-Flip nicht erfüllt.**

### Gate-Zustand verifiziert (nichts ist offen)
`EXECUTION_ENTRY_MODE=paper` (Live AUS), `_run_once_guard` erlaubt nur PAPER/SHADOW,
Engine `live_enabled=False`, `EXECUTION_DUAL_STREAM_DIAGNOSTICS=true` (Phase-1-Logger an).

### Verdikt: **NO-GO auf Phase-2-Auto-Switch** — drei unabhängige, je hinreichende Gründe
1. **Gate G0 nicht bestanden** (Edge fragil/Einzel-Trade). Master-Regel: kein Live vor Gates.
2. **Drift-Evidenz** belegt materielle Live∥Replay-Divergenz auf dem Breit-Input-Pfad
   (Screener 40,5 %, Median 56 bps echt); per-Zyklus-Auto-Switch würde diese Divergenz
   in Kapital-Entscheidungen injizieren.
3. **Die Drift-Metrik selbst ist noch kontaminiert** (Screener-Entry-Preis-Bug) → muss
   bereinigt werden, bevor die Zahl voll vertrauenswürdig ist.

### Revisit-Bedingungen (wann „Go" überhaupt erwägbar wird)
- **Gate G0 bestanden:** robuster Edge — P(mu_net>0) hoch **auch ohne** Best-Trade,
  positiver Median **und** net/notional, n≥100–200 (Edge-Validierungs-Doktrin).
- **Drift-Metrik bereinigt** (Screener-Entry-Preis-Bug behoben) + neu baselined; danach
  zielführend pro Quelle/Symbol/Regime statt global lesen.
- **Selbst dann:** Switching-Policy = **nie automatisch**, immer Operator + signiert
  (ADR-§3-Default bestätigt).

### Kleiner Folgeschritt — **RESOLVED 2026-06-30** (war: separat, gated NICHT das NO-GO)
Root-Cause des `entry_live ≈ 100`-Kontaminanten + Plausibilitäts-Guard. Audit 2026-06-30
(gegen Pi-Artifact `counterfactual_comparison.jsonl`, 7727 Records) ergab: **beide Hälften
sind durch frühere PRs bereits erledigt** — kein neuer Code, sonst redundant.

- **Wurzel (strukturell gefixt):** Die ~100-Werte stammten aus dem `technical_screener`,
  als der Decision-Entry noch der **letzte 1h-Close des `fallback`-Providers** war — der
  für (dynamic-universe-)Symbole eine normalisierte ~100-Indexreihe statt des echten
  Preises lieferte. **#498** zieht den Decision-Entry jetzt venue-konsistent aus der
  **Binance-1m-Kline** (gleiche Quelle wie der Shadow-Resolver), **#503** prunt das
  Dynamic-Universe auf Binance-spot-resolvebare Symbole. Beleg: alle 89 ~100-Kontaminanten
  sind `schema_version=v1`, ts **2026-06-16…06-26** (vor #498/#503); die 568 **v2**-Records
  (seit 06-29, Screener weiterhin `enabled`) haben **0 Platzhalter** und echte Preise
  (RE/ENA: 0,006…4045 statt ~100) → Screener *gefixt*, nicht nur aus.
- **Plausi-Guard (live):** `counterfactual_replay_logger.build_comparison` (#516, `v2`)
  markiert `|drift_to_range_bps| > SUSPECT_RANGE_BPS` (3000) als `data_quality_suspect`
  und zählt es **nicht** als `drift_exceeded`; `trading counterfactual-report` wendet die
  Plausibilität auch **read-time** auf v1-Altzeilen an → Report zeigt `suspect=92`,
  `drift_exceeded=2335`, `max=780 bps` (statt roh 1,5 Mio). Die Phase-1-Evidenz ist
  damit beim Lesen sauber.
- **Bewusste Entscheidung — Roh-Artifact bleibt immutable:** Die 7159 v1-Roh-Zeilen tragen
  weiterhin `data_quality_suspect=None`/v1-`drift_exceeded`, werden aber read-time
  neutralisiert und von **keinem** Konsumenten literal gelesen (nur Writer + ein Kommentar).
  Das Evidenz-Log wird nicht nachträglich umgeschrieben (Audit-Integrität); die saubere
  Sicht liefert der Report. Eine optionale Einmal-Backfill-Migration ist möglich, aber
  nicht nötig.
- **Folge:** Die in „Drift-Befund"/Grund 3 zitierten Roh-Prozentsätze sind über den vollen
  v1+v2-Stream gerechnet; die **kanonische, bereinigte** Drift-Sicht ist `counterfactual-report`
  (read-time-plausibel). Grund 3 ist damit adressiert; das **NO-GO bleibt unverändert**
  (Grund 1 Gate-G0 + Grund 2 echte Screener-Divergenz tragen es allein).
