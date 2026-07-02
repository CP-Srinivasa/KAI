# NEO-P-002-r3 — Design-Note (Discovery-Befund, 2026-06-04)

**Status: SPRINT-VORBEREITUNG, kein Code.** Option A (Operator 04.06.): erst Feeder-Design + Source-Inventory, dann implementieren mit Default-OFF + No-Execution-Invariante + CI, dann erst Pi-Sign-off.

## Entscheidender Discovery-Befund (Code-verifiziert auf p7-reentry `fa2adce`)
- **Recording-Schicht ist NICHT mehr der Fehler.** `_record_shadow_candidate` (`trading_loop.py:987`) schreibt bereits korrekt `source=autonomous_generator` / `candidate_kind=signal_candidate` / `source_stage=signal_generator`, sobald eine echte doc-id durchkommt. Ledger ist V2, `REAL_SOURCES={autonomous_generator}`, `_is_real_row` verlangt source∈REAL_SOURCES ∧ schema≥2.
- **Echte Crux:** `build_loop_trigger_analysis` (`trading_loop.py:1454-1476`) erzeugt **immer** eine `loop_control_*`-Canary-Analyse (conservative priority=1, bullish/bearish priority=10). `derive_autonomous_signal_source` (`:72`) mappt `loop_control_*` → `canary_probe`. ⇒ Der echte SignalGenerator läuft im autonomen Shadow-Pfad **nie auf echten News-/RSS-Analysen** → `real_resolved=0` ist ehrlich (kein Real-Sample-Strom), nicht „kein Edge".
- Passt zum Tagesbefund: degenerierter Ledger (441/441 conf=0.85/rr=2.0/gate=False) = Canary-/Scan-Rauschen, kein echter Signalstrom.
- **Risiko bei blindem Feeder:** ohne geklärte Quelle/Semantik schreibt r3 wieder „irgendwelche Events" als Real-Kandidaten → nächster Messfehler. Deshalb erst Inventory.

## r3 = Real Analysis Feeder + Funnel Visibility (4 Phasen)
1. **Source Inventory (read-only, Memo/JSON):** Welche echten Analysen existieren? Pro 24h/48h/7d? Felder (document_id/priority/sentiment/directional_state/source/ts/symbol)? Welche erreichen priority≥10 / sind directional / erreichen den Generator? Kandidaten-Quellen: Analysis-Store / RSS-News-Pipeline / alert_audit / dispatch records / Premium-Pipeline / bestehender Generator-Input-Store — je Quelle: dedup-bar? replay-bar? stale-sicher?
2. **`RealAnalysisProvider.fetch_pending(...)`:** read-only, idempotent, dedup nach document_id/event_id, stale-aware, bounded batch, keine Side-Effects.
3. **Shadow Injection (nur bei `EXECUTION_SHADOW_REAL_GENERATOR=true`):** echte Analyse → SignalGenerator → ShadowCandidate (`source=autonomous_generator`, candidate_kind signal_candidate/rejected_candidate/gate_candidate, source_stage signal_generator/sentiment_gate/risk_gate). Weiterhin: keine Fills/Positionen/Orders, entry_mode bleibt disabled.
4. **Funnel-Zähler (auch bei 0 Kandidaten):** raw_alerts, priority_rejected, not_actionable, low_directional_confidence, bearish_disabled, sentiment_rejected, non_directional, directional_accepted, reached_signal_generator, shadow_candidates_written, resolved_real_candidates → `rejected_funnel`-Bucket im Report, damit `real_resolved=0` erklärbar wird (Engpass = Quelle/Priority/Sentiment/Eligibility/Wiring?).

## Akzeptanzkriterien (Tests grün vor Akzeptanz)
1 Default-OFF schreibt keine Real-Kandidaten · 2 ON+echte Analyse→source=autonomous_generator · 3 ON+keine eligible→Funnel-Zähler, keine Kandidaten · 4 priority_rejected gezählt statt geloggt · 5 non_directional gezählt statt would_have_traded · 6 Records tragen document_id/source/sentiment/priority/directional_state · 7-9 keine Fills/Positionen/Orders · 10 entry_mode bleibt disabled · 11 legacy/canary/unattributed bleiben aus real_resolved · 12 real_resolved=0 ⇒ INSUFFICIENT_DATA.

## Hartes Nicht-Tun
D-182 umgehen · `service.py:61` Directional-Gate lockern · priority-threshold senken · mixed/neutral produktiv · Premium-Bridge nebenbei in Shadow verdrahten · entry_mode=paper · Risk-Gates enforce · Bau aus beweglichem Haupt-Worktree (isolierter Worktree Pflicht).

## Cross-Ref
Spec #141 `docs/strategy/neo_p002_r3_shadow_real_generator_spec_20260603.md`; `app/orchestrator/trading_loop.py` (`build_loop_trigger_analysis:1437`, `derive_autonomous_signal_source:72`, `_record_shadow_candidate:987`, run_cycle shadow_only:163-337); `app/observability/shadow_candidate_ledger.py` (`REAL_SOURCES`, `build_shadow_report`).
