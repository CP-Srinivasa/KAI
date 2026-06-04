# NEO-P-002-r3 Phase 1 — Source-Inventory (read-only, data-grounded, 2026-06-05)

**Status: INVENTORY, kein Runtime-Code.** Grundregel: preserve existing architecture, extension over replacement. P0-Gate des /goal: echten Generator-Edge messen, bevor neue Signal-Intelligenz gebaut wird.

## Bestehende Edge-Mess-Module — NICHT neu bauen, nur nutzen
- `app/observability/edge_report.py`: `compute_trade_edge`, `aggregate_cohort` (`CohortEdge`), `bootstrap_p_mean_positive`, `ClosedTrade`/`OpenPosition`/`TradeEdge`, `SymbolChurn`, side-adjusted bps. → Kosten-/Slippage-adjustierte Edge-Statistik **existiert**.
- `app/observability/shadow_candidate_ledger.py`: `resolve_pending` (MAE/MFE/forward-returns aus Klines), `build_shadow_report` (real/canary/unattributed/primary_class), `REAL_SOURCES={autonomous_generator}`, V2 `ShadowCandidate`. → Read-only Forward-Measurement **existiert**.
- `app/observability/shadow_resolver.py`, `evidence_window.py`, `outcome_dedupe_report.py`. → Resolver/Evidence/Dedup **existieren**.
**Konsequenz:** r3 baut KEINE neue Edge-Engine. Es fehlt nur der **Input-Strom** echter Kandidaten.

## Datenmodell (was der Generator konsumiert)
`AnalysisResult` (`app/core/domain/document.py:215`) + `app/analysis/base/interfaces.py`: trägt `sentiment_label`, `directional_confidence` (0..1), `recommended_priority` (1..10, default 5), `actionable`, `document_id`. → Alle Feeder-Pflichtfelder sind im Domänenmodell vorhanden.

## Feeder-Quellen-Kandidat: alert_audit.jsonl (data-grounded, lokales Sample n=200)
| Feld | Abdeckung | Feeder-tauglich |
|---|---|---|
| `document_id` | 200/200 (100%) | ✓ dedup/provenance-key |
| `priority` | 188/200 (94%) | ✓ priority-gate-Input |
| `sentiment_label` | 188/200 (94%) | ✓ |
| `actionable` | 188/200 (94%) | ✓ eligibility |
| `directional_confidence` | 92/200 (46%) | ✓ (nur directional; Rest non_directional → Funnel-Zähler) |
Schema-Top-Keys: `channel, dispatched_at, document_id, is_digest, message_id, provenance` (Analyse-Felder im `provenance`-Block). **Verdict: alert_audit ist eine tragfähige, document_id-attribuierte, dedup-bare Feeder-Quelle.** (Live-Volumen pro 24h/48h auf Pi noch zu sampeln — lokales Artefakt ist stale-Schema-Beleg, kein Live-Count.)

## Präzise P0-Lücke
Der autonome Shadow-Loop fährt ausschließlich `build_loop_trigger_analysis` (`trading_loop.py:1454` → `loop_control_*`-Canary) → `derive_autonomous_signal_source` → `canary_probe`. Die **echten** `AnalysisResult`-Objekte (die in `alert_audit` landen) werden **nie** in `SignalGenerator.generate` → Shadow-Ledger gespeist. Daher `real_resolved=0` = „kein Real-Sample", nicht „kein Edge".

## Delta (nur das — keine Doppelung)
`RealAnalysisProvider.fetch_pending()`: read-only, idempotent, dedup nach `document_id`, stale-aware, bounded batch, source-attribuiert → echte Analyse → bestehender `SignalGenerator` → bestehendes `record_candidate` (`source=autonomous_generator`). Hinter `EXECUTION_SHADOW_REAL_GENERATOR=false` (default). Funnel-Zähler (`raw_alerts`/`priority_rejected`/`not_actionable`/`non_directional`/`directional_accepted`/`reached_signal_generator`/`shadow_candidates_written`). Harte No-Execution-Invariante (Fill/Order/Position==0).

## Injektions-Naht GEFUNDEN (vereinfacht das Delta erheblich)
Loop-Driver = `app/cli/commands/trading.py` (loop-once Command, `PAPER_CRON_PROFILE`/`analysis_profile`) → `run_trading_loop_once` (`trading_loop.py:1668`). Bei `:1707`:
```
analysis = analysis_result or build_loop_trigger_analysis(symbol=symbol, analysis_profile=analysis_profile)
return await loop.run_cycle(analysis, symbol)
```
⇒ **Es gibt bereits einen `analysis_result`-Override-Seam.** Der Feeder muss NICHT in `run_cycle` eingreifen: ein Shadow-Feed-Treiber iteriert `RealAnalysisProvider.fetch_pending()` und ruft `run_trading_loop_once(symbol, analysis_result=real_analysis)` pro Kandidat — der bestehende `shadow_only`-Pfad (entry_mode=disabled → `_record_shadow_candidate`, keine Execution) bleibt unverändert. Delta = Provider + Feed-Treiber + Flag + Funnel, **kein** Orchestrator-Umbau.

## Offen vor Code
- alert_audit `provenance`-Block exakt mappen auf `AnalysisResult` — oder direkt den Analyse-Store/`run-all`-Output lesen, falls alert_audit nicht genug für `generate` trägt (entry-Preis kommt aus market_data im Loop, nicht aus der Analyse — also vermutlich ausreichend).
- Funnel-Einspeisungspunkt: pro `run_trading_loop_once`-Rückgabe den `CycleStatus` in Funnel-Zähler mappen (PRIORITY_REJECTED/NO_SIGNAL/ENTRY_MODE_BLOCKED+shadow_recorded/…).
- alert_audit `provenance`-Block exakt mappen auf `AnalysisResult` (führt es genug für `generate`, oder muss der Analysis-Store statt alert_audit gelesen werden?).
- Live-Volumen/Eligibility-Quote (priority≥gate, directional) auf Pi sampeln.

## Akzeptanz (unverändert aus Spec #141 + design-note)
Default-OFF schreibt nichts · ON+real→autonomous_generator · ON+keine eligible→Funnel, keine Kandidaten · keine Fills/Positionen/Orders · entry_mode bleibt disabled · legacy/canary aus real_resolved · real_resolved=0⇒INSUFFICIENT_DATA.
