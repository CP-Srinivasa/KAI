# KAI CODE-MAP — Anker-Index (Verify-Pointer, Stand 2026-06-22)

Zweck: die meist-gesuchten Code-Pfade an EINEM Ort, damit Agenten/Helfer den Worktree nicht jedes Mal neu durchsuchen müssen.

**Regeln:**
1. Einträge sind **Verify-Pointer** — vor dem Zitieren als Fakt gegen den aktuellen Code prüfen (Code bewegt sich).
2. Wer einen gemappten Pfad ändert/verschiebt, **aktualisiert diese Datei im SELBEN PR** (kein separater Pflegeaufwand, kein Verrotten).
3. Nur **Hochfrequenz-Anker** — bewusst NICHT die ganze Codebase.
4. **Keine Live-Werte hier** (die driften) — Flag-LIVE-Werte stehen im Pi-`.env`; aktueller Betriebs-Stand im Memory-Block „AKTUELLER STAND".

## Code-Anker

### Orchestrierung / Loop
- `app/orchestrator/trading_loop.py` → `TradingLoop`, `run_trading_loop_once` — 7-Step-Pipeline + Cycle-Audit

### Entry / Modi / Gates
- `app/core/enums.py` → `EntryMode` (disabled/paper/paper_premium_limited/paper_learning/probe/live_limited/live_normal); `.allows_autonomous_loop_entry` = True NUR für paper/probe/live_*
- `app/execution/entry_policy.py` → `EntryRoute`, `detect_contradictions`, Route-Verdicts (autonomous_loop/premium_paper/real_analysis_paper/fastlane/technical)
- `app/risk/engine.py` → `RiskEngine` (Sizing/Drawdown/Veto) · `app/risk/promotion_gate.py` → Bleed-Breaker · `app/security/governance/gates.py` → `authorize_productive_decision`

### Execution / Paper
- `app/execution/paper_engine.py` → `PaperExecutionEngine` (Fills/Close/MTM/Slippage) · `app/execution/models.py` → `PaperFill/PaperOrder/PaperPortfolio`
- `app/execution/audit_replay.py` → `replay_paper_audit`

### Edge / Shadow / Resolver
- `app/observability/shadow_candidate_ledger.py` → `build_shadow_report`, `_median`/`_split` (median-only, `fwd_*_bps`)
- `app/observability/shadow_resolver.py` → `resolve_with_binance` (Kline-Forward-Returns)
- `app/observability/edge_report.py` → Cohort-Edge + `_median`/`_winsorized_mean` (`WINSOR_LIMIT_BPS=500`, Median-GO-Gate)
- `app/observability/generator_edge_collector.py` → `collect_edge_inputs_from_resolved` (IC/Brier-Paare) · `generator_edge.py`
- **Edge-Discovery-Engine** (systematische Hypothesen-Suche auf eigenem OHLCV, NICHT live-Shadow): `app/research/{runner,evaluate,samples,stats,multiple_testing,ledger}.py` (Benjamini-Hochberg-FDR + Survival-Gates) ← `app/analysis/features/{feature_matrix,forward_returns}.py` (kausale Features + Forward-Label, No-Lookahead) ← `app/market_data/{history_loader,kline_windows}.py` (paginierter Backfill). Lauf: `python -m app.research.runner` → `artifacts/research/edge_search_*.json`; **Hypothesen-Ledger** (kumulativ, nie blind re-testen) → `artifacts/research/hypothesis_ledger.jsonl` (`ledger.hypothesis_key` = datenfenster-agnostische Config-ID). ⚠ Features kausal ≤i, Label vorwärts i+h — nie vermischen.

### Signal / Evidence
- `app/signals/generator.py` → `SignalGenerator` (6 Filter) · `app/signals/models.py` → `SignalCandidate`
- `app/signals/bayesian_confidence.py` → `BayesianConfidenceEngine`; **`direction_aligned` = pro/contra-Signal, NICHT realisiertes Outcome**
- Evidence-Settings: `app/core/evidence_settings.py` (`HypeEvidenceSettings` u.a.) + Wiring `app/signals/*_wiring.py`

### Markets / Sources
- `app/market_data/{momentum,oi_zscore,sentiment,liquidations,coingecko_adapter,binance_adapter}.py` (`binance_adapter.get_ohlcv(start_time_ms=…)` = historischer Backfill-Anker; Payload-Validierung in `_parse_kline_rows`, fail-closed vs NaN/Inf)
- `app/ingestion/rss/adapter.py` → `RSSFeedAdapter` (published_at via `calendar.timegm`, NICHT `mktime` — TZ-Bug #362) · `app/ingestion/classifier.py` · API-Adapter `app/integration/{cryptopanic,messari}/adapter.py`

### Digest / CLI / API / Audit / Regime
- `scripts/operator_digest.py` (tägl. Telegram-Digest; `collect_*`→`compose_digest_message`; inkl. `collect_edge_discovery` = jüngster `artifacts/research/edge_search_*.json` als 🔎-Sektion) · `app/cli/commands/daily_strategy.py` → `daily_strategy_app`
- `app/cli/main.py` → Typer-Entry (Gruppen: ingest / pipeline / signals / alerts / analyze / trading / audit / learning)
- `app/api/routers/` → `dashboard`, `signals`, `premium_signals`, `operator`, `alerts`, `health`, `tradingview`, `kyt`, `agents` …
- `app/audit/kai_audit_service.py` → `KaiAuditService` (Tamper-evident Hash-Chain)
- `app/regime/classifier.py` → `classify_raw` · `app/regime/models.py` → `RegimeClass` (TREND_UP/DOWN/BREAKOUT/CHOP/UNKNOWN)

## Kern-Env-Flags (Definition; LIVE-Werte = Pi-`.env`, NICHT hier)
- `EXECUTION_ENTRY_MODE` → `settings.execution.entry_mode` (`EntryMode`) — Master-Entry-Kill-Switch
- `EXECUTION_PAPER_MIN_PRIORITY` — Paper-Fill-Prioritätsschwelle
- `SOURCE_CRYPTO_RELEVANCE_GATE_MODE` (off/shadow/enforce) — Pre-Analyse-Relevanz-Gate
- `APP_HYPE_EVIDENCE_ENABLED` (env_prefix `APP_HYPE_EVIDENCE_`, `evidence_settings.HypeEvidenceSettings`) — HYPE-Evidence an/aus
- `PREMIUM_FASTLANE_ENABLED` (`PremiumFastlaneSettings`) — Fastlane (dauerhaft false)
- `RISK_MAX_OPEN_POSITIONS` — Positionslimit (nur Pi-`.env`)

## Kern-Artefakte (`artifacts/*.jsonl`)
- `paper_execution_audit.jsonl` — Paper-Fills/Closes/PnL (Replay-SSOT)
- `shadow_candidate_resolved.jsonl` — resolved Generator-Kandidaten + `fwd_*_bps`
- `shadow_real_feed_funnel.jsonl` — Funnel seen→eligible→injected→candidate
- `blocked_outcomes.jsonl` — geblockte Alerts + ~28h-Outcome (asset/dir/move im `note`)
- `blocked_alerts.jsonl` — geblockte Alerts (reason; KEIN Symbol/Dir)
- `alert_outcomes.jsonl` — resolved directional alerts (hit/miss)
- `funding_evidence_shadow.jsonl` / `oi_evidence_shadow.jsonl` / `hype_evidence_shadow.jsonl` — V5-Evidence (shadow)
- `bridge_pending_orders.jsonl` — TV-Bridge pending/promoted
- `trading_loop_audit.jsonl` — Cycle-Trace · `decision_journal.jsonl` — Operator-Entscheide · `risk_gate_audit.jsonl` — Gate-Decisions
