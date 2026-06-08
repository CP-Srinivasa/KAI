
## 2026-06-08 - D-227 Outcome-Report: persistenter Artifact-Emitter (read-only)

Folge-Block zum D-227-Outcome-Report-Kern (in p7 via #196). Macht den Report als Zeitreihe pullbar — wie der Shadow-Report. Rein read-only, kein Runtime/Env/Flag.

- **`app/alerts/blocked_outcome_report.py`**: neuer reiner Writer `write_blocked_outcome_report(report, out_path=BLOCKED_OUTCOME_REPORT_PATH)` schreibt den gebauten Report als Pretty-JSON (mkdir parents), kanonischer Pfad `artifacts/blocked_outcome_report.json`. Schreibt nur ein Report-Artefakt — kein Execution-/Env-Touch.
- **`app/cli/main.py`**: `alerts blocked-outcome-report --out-json <path>` persistiert zusätzlich; bei `--json` bleibt **stdout reines JSON** (die „wrote …"-Notiz geht nach stderr).
- **Tests**: Writer round-trippt zu validem JSON ohne Fabrikation, Default-Pfad unter `artifacts/`. 3 grün; ruff + format + mypy clean.

## 2026-06-08 - Premium-Fastlane fail-closed Bypass-Defaults + Entry-Mode-Override-Preflight (Issue #181, P0)

Follow-up zum #179-Incident (ADR 0006). Die Fastlane-Bypass-Kaskade defaultete vollständig `True`, sodass `PREMIUM_FASTLANE_ENABLED=true` den globalen Kill-Switch `entry_mode=disabled` durch einen einzigen Flag-Flip neutralisierte. Behoben: fail-closed Defaults + zweistufiger expliziter Override.

- **`app/core/settings.py`**: Alle sieben `bypass_*`-Defaults von `True` → **`False`** (fail-closed). Neuer unabhängiger Arm `PREMIUM_FASTLANE_ALLOW_ENTRY_MODE_DISABLED_OVERRIDE` (default `False`). Enabling der Fastlane relaxt für sich genommen kein Gate mehr.
- **`app/execution/premium_fastlane.py`**: Neue reine SSOT-Funktion `fastlane_entry_mode_override(settings) -> (allowed, refusal_code)`. `fastlane_status.overrides_classic_block` meldet nur noch `True`, wenn der zweistufige Override real scharf ist (Dashboard-Wahrheit == Laufzeit).
- **`app/execution/envelope_to_paper_bridge.py`**: Preflight-Gate am Entry-Mode-Bypass — Bypass nur bei vollständig scharfem Zwei-Flag-Arm; sonst fail-closed in `rejected_entry_mode` + Refusal-Record `premium_fastlane_entry_mode_override_refused` mit Reason-Code. Neuer Result-Counter `fastlane_entry_mode_override_refused`.
- **`app/risk/reason_codes.py`**: Neuer `ExecutionBlockerCode.FASTLANE_ENTRY_MODE_OVERRIDE_NOT_ARMED`.
- **Tests**: `test_premium_fastlane_settings` (Defaults jetzt fail-closed), zwei neue Bridge-Tests — `disabled`+`FASTLANE_ENABLED=true` ohne Arm → 0 Fills/0 Orders/0 Positionen (Issue §4); einzelnes Bypass-Flag ohne Override → fail-closed + Refusal-Record (Issue §7). Abhängige Tests auf explizites Arming umgestellt. 44 Premium-Fastlane-Tests + 80 angrenzende grün; ruff + ruff-format + mypy clean.
- **Bewusst nicht in diesem PR** (Issue §5/§8, durch fail-closed-Posture nicht-dringlich): Per-Source-Limits/max trades-h/notional-day; Remodelling als expliziter `entry_mode`-Enum (`premium_paper_limited`). Siehe ADR 0006.

## 2026-06-08 - NEO-P-002-r3 Phase 3: In-Loop-Funnel-Achsen (Issue #175)

Folge zu Phase 2 (Real-Analysen-Feeder): die Phase-2-Funnel zeigte nur Pre-Loop-Selektion + terminale `by_cycle_status`. Sie konnte nicht erklären, **wo im Loop/Generator** ein Real-Kandidat starb. Damit `real_resolved=0` *erklärbar* bleibt (nie still als `EDGE_NEGATIVE`).

- **`app/observability/shadow_inloop_funnel.py`** (neu, rein): `classify_cycle(status, notes)` mappt jeden injizierten Zyklus-Terminal (`CycleStatus`) auf eine In-Loop-Achse; `build_inloop_funnel(cycles)` liefert die kumulativen Achsen (`real_analyses_seen`/`eligible_for_shadow`/`priority_rejected`/`sentiment_rejected`/`non_directional`/`directional_accepted`/`reached_signal_generator`/`generator_returned_none`/`shadow_candidate_written`/`resolver_resolved_real`) + eine `rejected_funnel`-Aufschlüsselung. **Reine Instrumentierung** — kein Loop-Verhalten geändert (keine Directional-Gate-Lockerung, keine Priority-Threshold-Änderung, kein D-182-Bypass).
- **`app/observability/shadow_real_feed.py`**: sammelt `(status, notes)` je injiziertem Zyklus und ergänzt einen `in_loop`-Block im Funnel-Record — **getrennt** von den Feeder-Level-`counts`.
- **`app/observability/shadow_candidate_ledger.py`**: `build_shadow_report(..., inloop_funnel=None)` surfaced `in_loop_funnel` + `rejected_funnel`; **diagnostisch only**, ändert `primary_class` nicht → `real_resolved=0` bleibt `INSUFFICIENT_DATA`.
- **Invarianten**: Default-OFF (Flag unverändert), keine Fills/Positionen/Orders, `entry_mode` disabled; Report trennt Feeder-Level vs In-Loop.
- **Tests**: `test_shadow_inloop_funnel.py` (15: Klassifizierer je Achse, Zero-Candidate/rejected/success-Pfade, Mixed-Counts, Report surfaced `rejected_funnel` ∧ bleibt INSUFFICIENT, Report ohne Funnel unverändert). 86 Shadow-Tests grün; ruff + format + mypy clean.

## 2026-06-08 - Truth-Layer v2: Dashboard-MetricRegistry-Verdrahtung (Issue #170 Part A)

Folge-Verdrahtung zu #162 (formale `MetricRegistry`, ohne Live-Read-Pfad): die kanonischen skalaren Dashboard-Metriken werden jetzt **additiv** über die Registry serviert — eine Berechnungsquelle, Frontend rendert nur, nie selbst rechnen.

- **`app/observability/dashboard_metric_registry.py`** (neu, rein/IO-frei): `build_dashboard_metric_registry(values)` deklariert die kanonischen skalaren Truth-Metriken (live-sourced: `paper_fills_with_pnl`, `paper_fills_recent_24h`, `priority_tier_lift_pct`, `source_reliability_trusted_count`) + die noch ungebundenen Risiko-Skalare (`pnl_realized/unrealized`, `fees`, `exposure_gross/net`, `drawdown_max`, `var`, `cvar`, `sharpe`, `sortino`, `win_rate`). Jede Definition `frontend_calculation_allowed=False`. `reconcile_dashboard_snapshot()` vergleicht Contract-Werte gegen die SSOT → Drift = Warning, kein Hard-Fail.
- **`app/api/routers/dashboard.py`**: Truth-Contract-Endpoint baut die Registry aus **denselben** bereits berechneten Werten (kein zweiter Pfad), serviert `metric_registry` (verbatim `MetricResponse` je Metrik) + `metric_registry_reconciliation`; Contract-Version `1` → **`2`**. Ungebundene Risiko-Skalare servieren ehrlich `degraded` (value withheld), nie eine Fantasiezahl.
- **Tests**: `test_dashboard_metric_registry.py` (Frontend-Guard fail-closed, live-sourced servt Wert, unsourced → degraded, Builder pur, Reconcile within/drift/unsourced-never-ok) + erweiterter `test_api_dashboard` (Version 2, Registry-Wert == Contract-Wert, `var_usd` degraded, Reconciliation within-tolerance). 27 grün; ruff + format + mypy clean.
- **Bewusst nicht (Issue #170 Teil B)**: Generator-Edge-Collector (Side-Channel-Feeder für IC/Brier) bleibt geparkt — sinnvoll **erst nach dem NEO-P-002-r3-Feeder** (sonst `real_resolved≈0`, Canary-Artefakt). Die Risiko-Skalar-Bindings (var/cvar/sharpe/sortino aus Equity-Return-Serie) sind als `degraded` deklariert und folgen mit der Plumbing.

## 2026-06-08 - SENTR Governance-Gates produktiv verdrahtet (Issue #165)

Folge-Sprint zu PR #164 (Gate-Primitive standalone): Verdrahtung in den Decision-Journal-Pfad — additiv, fail-closed, kein `entry_mode`-Change.

- **`app/security/governance/registry_store.py`** (neu): append-only JSONL-Persistenz unter `artifacts/governance/` für Model-/Prompt-Registry (keyed `(id, version)`, last-write-wins) + Decision-Governance-Audit-Sidecar. Loader fail-closed (unbekannt → `None` → Gate refuse; malformed Row übersprungen). `save_*`-Writer **operator/CLI-only** — Agenten haben keinen Import-Pfad, `mutate_registry` bleibt forbidden.
- **`app/orchestrator/governed_decision.py`** (neu): `authorize_and_append_decision(...)` führt `authorize_productive_decision` + `validate_decision_audit` als **Hard-Gate vor dem Append** aus. Pass → Journal-Record + `DecisionRegistryReference` (inkl. `registry_hash`) in den Sidecar (keyed `decision_id`). Fail → Refusal-Audit-Record, **kein** Journal-Record, `GovernanceRejectedError`. `resolve_and_append_decision(...)` löst Entries aus den persistierten Registries auf. Sidecar statt Record-Mutation, weil `DecisionRecord` `extra=forbid, frozen` ist → additiv + Legacy-tolerant.
- **`app/agents/worker.py`**: SENTR-Worker-Mode `governance-audit` (read-only Report über Journal + Sidecar: governed/refused/ungoverned-legacy-Counts, Severity trackt Refusals; analog `sentr kyt-review`).
- **Invarianten**: Agenten kein Registry-Mutationsrecht (Test pinnt `mutate_registry` forbidden + Capability-Gate denied); Gates pure/read-only; `EXECUTION_ENTRY_MODE` unberührt; bestehendes `append_decision_jsonl` unverändert (Back-compat).
- **Tests**: `test_governance_registry_store` + `test_governed_decision` + `test_worker_governance_audit` (18 neu) + bestehende 38 Gate-Cases grün = 56; ruff + format + mypy clean. Doku `docs/security/governance_gates.md` § Integration aktualisiert (follow-up → wired).

## 2026-06-08 - Cross-Exchange Per-Venue-Quote-Plumbing (Issue #169)

Folge-Sprint zu PR #168 (pure Weighted-Median-Validierung, nicht verdrahtet, weil `MarketDataPoint` keine Mikrostruktur trägt). Diese PR liefert den Plumbing-Kern — additiv, default-OFF, **keine Execution-Beeinflussung**.

- **`app/market_data/venue_trust.py`** (neu): statischer Venue-Trust-SSOT (`venue_trust_score(provider_id) → [0,1]`), fail-closed (unbekannte Venue → konservativ niedrig `0.3`, nie hoher Default). Item #2.
- **`app/market_data/quote_builder.py`** (neu): `build_provider_quote(point, microstructure, now_ms)` mappt `MarketDataPoint` + optionale `Microstructure` (bid/ask/depth/latency) auf `ProviderQuote`. Trust aus dem SSOT, `timestamp_ms` aus ISO oder Freshness (nie als „now" erfunden). **Keine gefakte Mikrostruktur**: fehlt bid/ask/depth → Venue wird *ausgeschlossen* (None), nicht mit Zero-Spread-Full-Credit reingemogelt. Item #1.
- **`app/market_data/cross_exchange_aggregator.py`** (neu): `aggregate_and_validate(asset_id, venue_inputs, ...)` baut N Venue-Quotes für *dasselbe* Symbol, droppt Venues ohne Mikrostruktur, ruft `validate_cross_exchange` und liefert Funnel-Zähler (providers_in/quotes_built/excluded). `run_cross_exchange_validation(..., settings)` gated hinter Default-OFF-Flag → `disabled`-Envelope ohne Median-Run solange aus. **No-Execution-Invariante**: importiert nichts aus execution/orchestrator/risk, `influences_execution=False`. Items #3+#4.
- **`app/core/settings.py`**: `cross_exchange_validation_enabled` (default False, env `APP_CROSS_EXCHANGE_VALIDATION_ENABLED`).
- **Tests**: `test_cross_exchange_plumbing.py` (13: Trust-SSOT known/unknown, Quote-Mapping + honest exclusion + Timestamp-Ableitung, Aggregation + Funnel, Flag-OFF/ON-Invariante) + 14 bestehende Validierungs-Tests grün = 27; ruff + format + mypy clean.
- **Bewusst nicht (Issue #169 §1-Adapter / §5)**: reale bid/ask/depth aus den Live-Exchange-APIs in `bybit`/`okx`/`binance_futures`-Adaptern (Network-Layer-Umbau) + Kalibrierung der `CrossExchangeConfig`-Defaults gegen reale Tick/Spread/Depth-Verteilungen — beides braucht Live-API-Arbeit + reale Daten und ist nicht im sicheren Scope dieser PR. Bis dahin schließt die Aggregation Venues ohne Mikrostruktur ehrlich aus (heute alle → inert), `entry_mode` bleibt disabled.

## 2026-06-01 - Entry-Safety-Mode + cost-adjusted Edge-Release-Gate (/goal sprint, A–F)

Negative kostenbereinigte Live-Edge bestätigt (Pi 2026-06-01: P(mu_net>0)=0%, net ≈ −69 bps/notional, n=22). Antwort: messbares Entry-Gate statt Bauchgefühl. Default-Verhalten im Paper-Betrieb unverändert ausser dem Churn-Throttle; vollständig über Env rückrollbar; nie Auto-Live. Siehe `DECISION_LOG.md` D-229 für die volle Begründung.

- **Sprint A — Entry-Safety-Mode** (`app/core/enums.py`, `app/core/settings.py`, `app/orchestrator/trading_loop.py`, `app/orchestrator/models.py`): `EntryMode`-Enum (DISABLED/PAPER/PROBE/LIVE_LIMITED/LIVE_NORMAL), env `EXECUTION_ENTRY_MODE`, Default **PAPER** (nie live). DISABLED stoppt autonome Loop-Entries vor dem Market-Data-Fetch (`CycleStatus.ENTRY_MODE_BLOCKED`). Fail-closed: ein Live-Entry-Mode auf nicht-live Venue wird abgelehnt. Promoted/Operator-Signale sind bewusst NICHT vom Gate erfasst.
- **Sprint B — CostModel single source** (`app/execution/cost_model.py`, `app/execution/fees.py`, `app/risk/engine.py`): per-side Fees als Quelle, round-trip stets abgeleitet (kein driftbarer Standalone-Wert), maker/taker pro Seite. Paper-Default realistische 10 bp/Seite (statt Worst-Case 60); Worst-Case bleibt als separate Fallback-Schicht bei korruptem/fehlendem YAML. V1-Cost-Geometry-Gate, paper-Engine und Backtest leiten dieselbe round-trip-Kostenbasis aus EINEM Modell ab. Open-Fill-Fees sind von closed-round-trip-Fees getrennt (fixt den +433-vs-−283-Accounting-Bug).
- **Sprint C — Edge-Diagnostik** (`app/observability/edge_report.py`): side-adjusted return (long/short), net_bps = gross − CostModel-round-trip, bootstrap `P(mu_net>0)` (winrate ist nicht das Urteil), getrennte Buckets closed-PnL / open-MTM / fees_open / fees_closed, Churn-Metriken, ehrliche forward-return-Coverage (Gaps als `no_historical_minute_bars`, nie erfunden).
- **Sprint D — Edge-Release-Policy** (`app/risk/edge_release_policy.py`): mappt P/net auf die EntryMode-Leiter, DISABLED bei insufficient/n<min_n/P<0.5, PAPER ohne realen net-Edge, LIVE_* nur mit Operator-Sign-off, LIVE_NORMAL zusätzlich OOS-stabil und **nie** auto-promotet. JSON-serialisierbares Decision-Objekt + Operator-Render.
- **Sprint E — Churn-Killer** (`app/orchestrator/trading_loop.py`): Entry-Throttle (per-Symbol Trades/h, Notional-Turnover/h, Cooldown nach jedem Close inkl. `take`), tighter PROBE-Cap, `RISK_CHURN_*`-Envs (alle 0 = inert). **Hard-Invariante**: Exits (`monitor_positions`/`close_position`) werden NIE vom Churn-Killer oder Entry-Gate blockiert.
- **Sprint F — Akzeptanz + Doku**: a–g-Traceability gegen bestehende Sprint-Tests verifiziert (keine Redundanz erfunden); neuer Integrations-Akzeptanztest `tests/unit/test_goal_acceptance_20260601.py` (E2E: disabled→ENTRY_MODE_BLOCKED, paper+Churn→CHURN_REJECTED während Exit schliesst, negative Verteilung→DISABLED via `trading edge-gate`, CostModel single-source gate-fee==report-fee); DECISION_LOG D-229 + dieser CHANGELOG + Runbook `docs/strategy/entry_safety_runbook_20260601.md`.
- **CLI**: `trading edge-report` (Sprint-C-Diagnostik, read-only) und `trading edge-gate` (Sprint-D-Verdict DISABLED/PAPER/PROBE/LIVE_LIMITED/LIVE_NORMAL + Begründung, read-only — ändert `entry_mode` NICHT).

## 2026-05-28 - Premium-Signal Analytics (/goal sprint)

- **Created** `app/observability/premium_signal_analytics.py` (pure, IO-free): eingesetztes Kapital + Anteil am freien Kapital, PnL absolut/prozentual, Per-Target-Status (hit/missed/pending/unknown), Entry-Status + Wartezeit, Trade-Ergebnis, Source-Quality (Wilson-LB über das Trail-Fenster), Analyse-Hinweise.
- **Extended** `premium_signal_trail.TrailEntry` mit optionalem `analytics`-Block; `build_trail` berechnet ihn pro Signal + Source-Quality im 2. Pass. `/api/premium-signals/trail` reicht ihn unverändert durch (backward-compatible).
- **Frontend**: neue Komponente `web/src/components/panels/PremiumSignalAnalytics.tsx` (Kapital-/Ergebnis-/Entry-/Quellen-Kacheln, Target-Stepper, Hinweise), eingebunden in `PremiumSignalTrail` mit Fallback auf die alte Detail-Row. TS-Typen in `api.ts` erweitert.
- **Annahmen** (dokumentiert im Modul-Docstring): `available_capital_at_entry` = freies Cash vor erstem Entry-Fill (aus `portfolio_cash`); Entry-Timing-Schwellen 300s/3600s; Target-„hit" nur bei belastbarer Preis-Evidenz; fehlende Daten werden NIE erfunden, sondern als „nicht verfügbar"/„nicht bewertbar" gezeigt.
- **Tests**: 34 neue Unit-Tests in `test_premium_signal_analytics.py` (Kapital, %, fehlende/0-Basis, Targets hit/missed/pending/unknown, Entry on-time/waited/late/missed, win/loss/break_even/unknown PnL, internal/external, Source-Quality, incomplete data). Premium-Bereich: `55 passed`; ruff + mypy clean; `tsc -b` + `vite build` grün.
- **Follow-up (Live-Daten)**: per-Trade-PnL-Fallback aus Fill-Preisen, wenn die Paper-Engine kein `trade_pnl_usd` liefert (Legacy-Close-Pfad) — nur bei vollständigem Close, transparent via `final_pnl_source: engine|fills`. Behebt „unknown"-Ergebnisse für reale abgeschlossene Premium-Trades (z.B. CYS/TRUTH/US: alle Targets hit, PnL war zuvor nicht ausgewiesen). +3 Tests.

---

## 2026-03-24 – 2026-05-28 — Sprint history consolidated in DECISION_LOG (gap notice)

> **Doku-Hinweis (AUDIT-A17):** Zwischen `2026-03-24` und `2026-05-28` wurde der CHANGELOG nicht fortlaufend gepflegt. Die vollständige, evidenzbelegte Entscheidungs- und Sprint-Historie für diesen Zeitraum steht im **`DECISION_LOG.md`** (D-125 … D-228-S3) sowie in den Operator-Memos. Wichtigste Meilensteine dieser Periode: Pi-5-Cutover + Re-Entry (2026-05-07), Phase-0-Security-Stack, Premium-Signal-Pipeline (P0–P2), DuckDB-Pivot, Adaptive-Learning/Regime/Bayes-Stack, Asset-Diversification enforce (D-226), Dispatch-Recall-Proxy (D-227), Asset-Reserve/Fokusfeld-Layer (D-228/S3). Ab hier ist der CHANGELOG wieder append-only.

---

## 2026-03-24 - Alert hit-rate metric (first quality metric)

- **Enriched** `AlertAuditRecord` with prediction fields (sentiment_label, affected_assets, priority, actionable)
- **Created** `app/alerts/hit_rate.py`: AlertOutcome, classify_hit, build_outcomes_from_records, compute_hit_rate
- **Added** CLI command: `kai alerts hit-rate` with per-sentiment/per-asset breakdowns
- **Tests**: 20 new tests in `test_alert_hit_rate.py`
- Baseline: `1079 passed, ruff clean`

---

## 2026-03-24 - Companion code extraction to companion-ml branch

- **Extracted**: ~80 companion-only files removed from main dev path.
- **Archived**: `companion-ml` branch preserves all extracted code.
- **Scope**: 16 research modules, 4 CLI command modules, 5 agent tool modules, `mcp_server.py`, 2 API routers, 26+ test files.
- **Keepers**: `signals.py`, `watchlists.py`, `briefs.py` in `app/core/`, re-exported via `app/research/__init__.py`.
- Baseline: `1046 passed, ruff clean`.

---

## 2026-03-24 - PH5C executed; strategic hold active (D-97)

- **Strategic hold imposed (D-97)**: No new companion-ML sprint, decision, or invariant until alert-precision + paper-trading metrics are clearly positive.
- Baseline: `1046 passed, ruff clean`.

---

## 2026-03-24 - PH5C filter baseline set to pending final freeze

- Canonical state set to `current_sprint = PH5C_FILTER_BEFORE_LLM_BASELINE (pending final freeze)`.
- Next required step set to `PH5C_STATUS_FREEZE`.
- PH5B findings accepted; PH5B remains closed.
- `EMPTY_MANUAL` confirmed as root cause of the PH5B low-signal cluster.
- Governance conflict remains between execution-ready and status-freeze states; execution stays blocked.
- Baseline unified to `1609 passed, ruff clean`.

---


- Baseline: `1609 passed, ruff clean`.

---

## 2026-03-24 - CI hardened (N-8); all 5 jobs green

- `hypothesis>=6.0.0` + `pytest-mock>=3.14.0` added to `[dev]` extras (were installed locally but missing from CI).
- ruff format pass over 138 files (no logic changes).
- Duplicate `asyncio.run(run())` in `send_digest` CLI command removed (pre-existing copy/paste bug).
- Baseline: `1609 passed, ruff clean`. CI: 5/5 green.

---

## 2026-03-24 - Alert Integration wired into analyze-pending (N-7)

- `--no-alerts` flag suppresses Phase 4 entirely.
- `Alerts dispatched: N` printed when alerts fire.
- 3 new tests: `tests/unit/cli/test_analyze_pending_alerts.py` (dispatch, --no-alerts suppression, fail-open).

---

## 2026-03-24 - MCP compat.py extraction complete (N-6)

- Last 5 inline `@mcp.tool()` definitions extracted from `mcp_server.py` into `app/agents/tools/compat.py`.
- `test_canonical_read.py` + `test_guarded_write.py` upgraded: trivial alias checks replaced with `mcp.list_tools()` registration verification.

---

## 2026-03-24 - PH5A execution complete; results-review mode active

- PH5A execution has completed and artifacts are ready for review.
- Canonical state remains: `current_phase = PHASE 5 (active)`, `current_sprint = PH5A_BASELINE_RELIABILITY_AND_SIGNAL_TRUST`.
- Next required step set to `PH5A_RESULTS_REVIEW_AND_CLOSE`.
- Working tree is clean and status report is in-repo (`status_report.md`).
- Baseline remains `1609 passed, ruff clean`.
- PH5B stays blocked until PH5A review is formally closed.

---

## 2026-03-23 - PH4K contract freeze completed; execution gate opened

- Canonical state advanced to: `current_sprint = PH4K_TAG_SIGNAL_UTILITY_REVIEW (definition frozen)`.
- Canonical next step set to: `PH4K_EXECUTION_START`.
- PH4K remains diagnostic-only; no scoring/threshold/provider/actionability changes.
- Acceptance criteria locked before execution.
- DB failures remain on a separate track and are excluded from PH4K utility interpretation.

---


- PH4J_CLOSE_AND_PH4K_DEFINITION sprint executed: governance sync complete.
- All 10 governance docs aligned: PH4J=closed, PH4K=candidate.
- No PH4K execution before `PH4K_DEFINITION_AND_CONTRACT_FREEZE`.
- Baseline unchanged: `1554 passed, ruff clean`.

---

## 2026-03-23 - PH4J governance state set to ready-to-close (pre-closeout gate)

- Canonical state set to: `current_sprint = PH4J_FALLBACK_TAGS_ENRICHMENT (ready to close)`.
- Canonical next step set to: `PH4J_CLOSE_AND_PH4K_DEFINITION`.
- PH4J verification evidence remains unchanged: keyword-hit 4->7, zero-hit 1->4, assets-only 0->4, 29/29 tests, I-13 intact.
- DB failures remain on a separate track and are excluded from PH4J closeout semantics.

---


- DB test failures remain on a separate track.
- Governance state: `current_sprint = PH4K_TAG_SIGNAL_UTILITY_REVIEW (candidate)`, `next_required_step = PH4K_DEFINITION_AND_CONTRACT_FREEZE`.

---

## 2026-03-23 - PH4J live verification passed; sprint moved to ready-to-close

- `PH4J_FALLBACK_TAGS_ENRICHMENT` moved to `ready to close`.
- Live verification passed.
- Tag coverage improved in verified scenarios:
  - keyword-hit: `4 -> 7`
  - zero-hit: `1 -> 4`
  - assets-only: `0 -> 4`
- `29/29` pipeline tests passed.
- `I-13` remained intact.
- DB test failures are tracked separately from PH4J closeout.
- Next required step set to `PH4J_CLOSE_AND_PH4K_DEFINITION`.

---

## 2026-03-23 - PH4I formally closed (D-78); PH4J candidate defined

- PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT formally closed.
- S77 is now a frozen immutable anchor.
- PH4J_FALLBACK_TAGS_ENRICHMENT defined as next sprint candidate (PH4F: tags empty 69/69).
- New baseline: 1554 passed, ruff clean.
- Governance state advanced to:
  - current_sprint = PH4J_FALLBACK_TAGS_ENRICHMENT (candidate)
  - next_required_step = PH4J_DEFINITION_AND_CONTRACT_FREEZE

---

## 2026-03-23 - PH4I execution complete (I3+I4); market_scope enriched in fallback path

- `_fallback_market_scope()` extended with PH4I enrichment signals:
  - `document.crypto_assets` length -> CRYPTO votes
  - `document.tickers` length -> EQUITIES votes
  - Title keyword scan (bitcoin, ethereum, crypto, defi, etc.) -> CRYPTO signal
- 13 new tests added (1551 total; +13 from 1538 baseline).
- ruff clean confirmed.
- Before: market_scope UNKNOWN 69/69 (PH4F finding).
- After: crypto_assets/tickers/title keywords now resolve UNKNOWN to CRYPTO/EQUITIES where signals exist.
- Governance state advanced to:
  - `current_sprint = PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT (ready to close)`
  - `next_required_step = PH4I_CLOSE_AND_PH4J_DEFINITION`

---


- No scoring changes, no I-13 conflict, no actionable expansion.
- Acceptance criteria locked: market_scope > 0/69; 1538+ passed; ruff clean.
- Governance state advanced to:
  - `current_sprint = PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT (execution-ready)`
  - `next_required_step = PH4I_EXECUTION`
- Baseline unchanged: `1538 passed`, `ruff clean`.

---

## 2026-03-23 - PH4H policy review complete; I-13 confirmed permanent; PH4I defined

- PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW formally closed (D-74/D-75).
- Policy decision (D-74): `actionable` is an LLM-exclusive semantic judgment -- Option B selected.
  - Option 1 (relax I-13): rejected -- weakens fail-closed guarantee; no semantic basis.
  - Option 3 (hybrid gate): rejected -- arbitrary threshold; complexity without evidence.
- I-13 invariant confirmed as permanent: `test_rule_only_priority_ceiling_is_at_most_five`.
- S76 frozen as immutable anchor.
- PH4I defined (D-76): `PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT` -- market_scope unknown 69/69 (PH4F finding).
- S77 opened as active-definition contract.
- Governance state advanced to:
  - `current_sprint = PH4I_FALLBACK_MARKET_SCOPE_ENRICHMENT (active definition)`
  - `next_required_step = PH4I_CONTRACT_AND_ACCEPTANCE_FREEZE`
- Baseline unchanged: `1538 passed`, `ruff clean`.

---

## 2026-03-23 - PH4G formally closed; PH4H opened in active definition mode

- PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW opened as active definition sprint.
- Governance state advanced to:
  - `current_sprint = PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW (active definition)`
  - `next_required_step = PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE`
- Central policy question fixed for freeze: `I-13` rule-only ceiling vs fallback actionability.
- Baseline reconfirmed: `1538 passed`, `ruff clean`.

---

## 2026-03-23 - PH4G execution complete and moved to ready-to-close gate

- PH4G execution completed; sprint remains active in closeout mode.
- Relevance-floor fallback intervention retained.
- Actionable-heuristic intervention reverted due `I-13` ceiling constraint.
- Governance state set to:
  - `current_sprint = PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (ready to close)`
  - `next_required_step = PH4G_CLOSE_AND_PH4H_POLICY_REVIEW`
- PH4H remains candidate-only until PH4G formal closeout is recorded.
- Baseline reconfirmed: `1538 passed`, `ruff clean`.

---

## 2026-03-23 - [superseded] Premature PH4G closeout/opening record

- PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE formally closed.
- Retained: relevance-floor fallback intervention.
- PH4H is review-only: no code changes, no I-13 relaxation before policy decision.
- Governance state advanced to:
  - `current_sprint = PH4H_RULE_ONLY_CEILING_AND_ACTIONABILITY_POLICY_REVIEW (superseded draft state)`
  - `next_required_step = PH4H_CONTRACT_AND_ACCEPTANCE_FREEZE (superseded draft state)`
- Baseline confirmed: `1538 passed`, `ruff clean`.

---

## 2026-03-23 - PH4G execution complete; moved to closeout and PH4H policy review gate

- PH4G execution completed and sprint moved to `ready to close`.
- Relevance-floor fallback intervention is retained.
- Actionable-heuristic intervention was reverted.
- `I-13` policy constraint remains active (`rule-only priority <= 5`).
- Governance state advanced to:
  - `current_sprint = PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (ready to close)`
  - `next_required_step = PH4G_CLOSE_AND_PH4H_POLICY_REVIEW`
- Baseline reconfirmed: `1538 passed`, `ruff clean`.

---

## 2026-03-23 - PH4F closed and PH4G moved to contract-freeze definition

- PH4F formally closed after results review; findings frozen as PH4G intervention anchor.
- Governance state advanced to:
  - `current_sprint = PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE`
  - `next_required_step = PH4G_CONTRACT_AND_ACCEPTANCE_FREEZE`
- PH4G remains definition-only pending contract/acceptance freeze.
- Baseline updated and reconfirmed: `1538 passed`, `ruff clean`.

---

## 2026-03-23 - Refactoring findings RF-1..RF-7 implemented

### RF-1 CLI/MCP Split
- `app/cli/research.py` extracted from monolithic `main.py` (3400+ lines)
- `app/cli/commands/trading.py`: new `trading_app` Typer group
- `app/agents/tools/canonical_read.py` + `guarded_write.py`: MCP inventory modules
- `main.py` is now a thin registration layer

### RF-2 Working Tree committed
- 3 snapshot commits created for governance docs, code changes, and config files

### RF-3 CORS configurable (prior sprint)
- `APP_CORS_ALLOWED_ORIGINS` env var, `AppSettings.cors_allowed_origins`

### RF-4 DB-based aggregation (Phase 1)
- `TradingCycleRecord` + `PortfolioStateRecord` SQLAlchemy models
- Alembic migration `0007_create_trading_tables.py`
- Dual-write integration pending (Phase 2)

### RF-5 README/Docs Phase-4 update
- README phase status block updated to PH4F closed / PH4G pending
- Sprint and CoinGecko default documented

### RF-6 CoinGecko as default market data provider
- `APP_MARKET_DATA_PROVIDER=coingecko` documented in `.env.example`
- `create_market_data_adapter()` logs WARNING when mock provider used

### RF-7 Test-file splitting
- `tests/unit/cli/` and `tests/unit/mcp/` subpackages created
- 19 new tests added (1538 total, +19 from 1519 baseline)

---

## 2026-03-23 - PH4F execution complete and moved to closeout review

- PH4F execution artifacts generated from frozen paired set (`69` docs).
- Confirmed production Tier-1 path: fallback analysis in `app/analysis/pipeline.py` (not `RuleAnalyzer.analyze()`).
- Confirmed PH4F field gaps:
  - `actionable` missing in `69/69`
  - `market_scope` unknown in `69/69`
  - `tags` empty in `69/69`
  - `relevance_score` default-floor in `56/69`
- Governance state updated to:
  - `current_sprint = PH4F_RULE_INPUT_COMPLETENESS_AUDIT (ready to close)`
  - `next_required_step = PH4F_RESULTS_REVIEW_AND_PH4G_SELECTION`
- Baseline unchanged: `1519 passed`, `ruff clean`.

## 2026-03-23 - PH4E closed and PH4F opened (historical)

- PH4E scoring calibration audit formally closed (D-67).
- PH4F (`RULE_INPUT_COMPLETENESS_AUDIT`) opened as diagnostic-only sprint (D-68).


## 2026-03-24 - V-4 Dual-Write + DB-primary closeout (D-86)

- `app/orchestrator/trading_loop.py`: `run_cycle()` writes `TradingCycleRecord` + `PortfolioStateRecord` to DB via `session_factory`; DB error is non-fatal.
- `app/execution/portfolio_read.py`: `build_portfolio_snapshot()` queries `PortfolioStateRecord` as primary source when `session_factory` provided; falls back to JSONL on no-record or DB error.
- 6 new tests in `tests/unit/test_trading_loop_dual_write.py` (dual-write path).
- 8 new tests in `tests/unit/test_portfolio_snapshot_db_primary.py` (DB-primary path).
- RF-4 promoted to `phase-3-complete`. Baseline: 1604 passed, ruff clean, mypy 0 errors.

## 2026-03-24 - Phase-4 closeout draft recorded (D-87, superseded by final closeout sync gate)

- Phase 4 arc PH4A-PH4K (11 sprints) + V-4 Phase 1-3 documented as closeout-ready.
- Cumulative signal quality improvements: priority +28%, tags empty -62.3%, relevance=0 -43.5%.
- I-13 policy permanent: `actionable` = LLM-only, no rule-only fallback.
- V-4 technical hardening complete: DB-primary portfolio snapshot, dual-write in run_cycle().
- This claim is superseded by the conservative canonical gate: `PHASE4_FINAL_CANONICAL_CLOSEOUT`.
- Phase 5 remains blocked until final closeout sync is complete.


## 2026-03-24 - PH5A execution complete (D-89)

- PH5A diagnostic script executed against 69-doc paired set.
- Key findings:
  - Keyword coverage: 62.3% (43/69)
  - Watchlist overlap: 52.2% (36/69)
- Artifacts: `artifacts/ph5a_reliability_baseline.json` + `artifacts/ph5a_operator_summary.md`
- PH5A moved to results-review; PH5A-7 (governance closeout) pending.

## 2026-03-24 - PH5A closeout draft note (superseded by active review gate)

- This earlier closeout claim is superseded by the canonical review state.
- PH5A remains in results-review mode until `PH5A_RESULTS_REVIEW_AND_CLOSE` is completed.
- PH5B stays blocked until PH5A review is formally closed.

## 2026-03-24 - PH5B closed; PH5C opened (D-94, D-95)

- PH5B cluster analysis complete: all 19 LLM-error-proxy docs classified as EMPTY_MANUAL.
- Root cause: source=Manual with content='Comments' (8 bytes placeholder) -- data quality gap.
- LLM behaviour is correct; no model failure.
- Recommendation: FILTER_BEFORE_LLM (skip LLM for stub documents).
- PH5C sprint opened: PH5C_FILTER_BEFORE_LLM_BASELINE (D-95, par85).
- Artifacts: artifacts/ph5b/ph5b_cluster_analysis.json + artifacts/ph5b/ph5b_operator_summary.md

## 2026-03-24 - PH5C closed; strategic hold imposed (D-97)

- PH5C stub filter baseline: conservative rule recommended (FP=0, recall=58%).
- Strategic hold: no new Phase-5 sprints until alert-precision and paper-trading metrics positive.
- Artifacts: artifacts/ph5c/ph5c_stub_filter_baseline.json + ph5c_operator_summary.md
