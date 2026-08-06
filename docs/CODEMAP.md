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
- `app/api/main.py` → `create_app`; **Middleware-Reihenfolge ist heikel**: `GZipMiddleware` MUSS als erstes registriert (= innerste Schicht) bleiben, sonst streamen die `BaseHTTPMiddleware`-Schichten (RequestGovernance/SecurityHeaders) die Antwort und `minimum_size` verpufft
- `app/observability/operator_board_live.py` → `build_live_board`/`open_preregs`/`curated_is_stale` (rein, kein I/O) — LIVE-Hälfte von `/dashboard/api/operator-board`: `prereg_ledger.jsonl` minus `prereg_verdicts.jsonl`, ergänzt um Reife + terminale Resolution aus `research/prereg_maturity.py`. Der Reife-Reader vertraut Resolutionen ausschließlich nach Vollverifikation von `artifacts/truth/attestation_ledger.jsonl`; `RESOLVED` verschwindet aus offen, kaputte/widersprüchliche Evidenz bleibt sichtbar als HOLD. ⚠ `INSUFFICIENT_N` ist NICHT terminal; Stale-Alarm nur bei OFFENEN kuratierten Punkten, Chronik (`status: done`) veraltet nie
- Dashboard-TTL-Caches in `routers/dashboard.py` (`_quality_`/`_priority_gate_`/`_n_overview_`/`_operator_board_cache`): Pflicht, weil die Endpoints Multi-MB-JSONL full-scannen; neue Cache-Dicts IMMER auch in `tests/unit/test_api_dashboard.py::_patch_artifacts` zurücksetzen, sonst antwortet ein Test mit den Artefakten eines anderen
- `app/audit/kai_audit_service.py` → `KaiAuditService` (Tamper-evident Hash-Chain)
- `app/regime/classifier.py` → `classify_raw` · `app/regime/models.py` → `RegimeClass` (TREND_UP/DOWN/BREAKOUT/CHOP/UNKNOWN)

### Lightning-Credentials + Geldjournal (Capability-Split W0/PR-A, Cutover W0/PR-C)
- `app/core/lightning_settings.py` → `LightningSettings.macaroon_credentials(scope)` mit `read|invoice|payment|onchain|channel`; **kein Write-Fallback** aufs Read-Credential (fehlender Scope → `LightningUnavailableError`). `validate_lightning_boot` bricht seit PR-C den START ab, wenn eine EINGESCHALTETE Capability kein Credential hat (C-1: `l402_enabled`/`receive_enabled`→invoice, `pay_enabled`→payment) — statt still 503 pro anonymer Anfrage
- `app/lightning/adapter.py::_build_client(cfg, credential_scope="read")` → seit PR-C fordert JEDER Konsument seinen eigenen Scope an: `create_invoice`+`earnings_booking`→`invoice`, `pay_invoice`/`keysend`→`payment`, `send_coins`→`onchain`, `open/close_channel`→`channel`, Lesepfade→`read`; der `payment`-Client ist zusätzlich strukturell an `APP_LN_PAY_ENABLED=true` gebunden, sodass auch Rohhelfer/Preflight ihn disarmed nicht materialisieren können (Tests: `test_lightning.py::test_every_money_path_requests_its_own_capability_scope`, `test_payment_client_requires_the_value_layer_kill_switch`)
- `app/lightning/value_layer.py::pay_invoice` → vor Intent und Send dekodiert derselbe scope-minimale Payment-Client die BOLT11 via LND `GET /v1/payreq/{pay_req}`. HRP-Betrag und Decode-Betrag müssen übereinstimmen; der normalisierte 32-Byte-`payment_hash` sowie `amount_sat` werden in den v2-Intent gebunden. `payment_error`, ein gesetzter `failure_reason`, fehlender oder abweichender Response-Hash schließen den Intent als `error` — HTTP 200 allein bedeutet nie „executed“ (M-4/B-12)
- `app/lightning/client.py::list_payments` → read-only LND-`GET /v1/payments` mit `include_incomplete`, `omit_hops=true`, redigierten `LndPayment`-Zeilen (kein Preimage/BOLT11/Route/Hop) und richtungsabhängig validierter Pagination als `LndPaymentPage`; noch ohne Konsument (T6a-Voraussetzung für den separaten Reconciliation-Timer)
- `app/lightning/reconciliation.py` + `scripts/ln_reconcile.py` + `kai-ln-reconcile.timer` → PR-D outcome-only Crash-Gap-Abgleich: verifizierter v2-Snapshot und B-3-Truth-Tip-Containment VOR vollstaendigem read-only `list_payments`-Scan, danach beide lokalen Beweise erneut; nur eindeutiges `SUCCEEDED`/`FAILED` mit passendem Hash+Betrag schliesst einen BOLT11-Intent. Kein Retry/Send/Payment-Credential; Timer install-only, nicht Boot-/Server-gekoppelt. Runbook: `docs/runbooks/ln_reconciliation.md`
- **Zwei Journale, bewusst asymmetrisch (PR-C):** `app/lightning/ops_ledger.py` (v2, verkettet, write-ahead) ist Vorbedingung für JEDEN Spend — nicht schreibbar/verifizierbar ⇒ Spend denied, Node unberührt (`value_layer.money_journal_status`). `spent_today_sat_v2` liest unter Lock + Vollverifikation: missing/unlesbar/korrupt ⇒ `None` (Cap UNKNOWN) ⇒ `ln_control` denied; nur eine vorhandene valide Leerdatei bedeutet bekannte 0 sat. `app/lightning/receive_ledger.py` auditiert Invoice-Mints in eigener Datei, ohne Lock, best-effort — der `/oracle`-Einnahmepfad darf nie an einem Journal scheitern (BL-2/M-9). v1 (`append_ln_op`/`spent_today_sat`) ist eingefroren = Rollback-Fläche, Guard-Test verbietet neue Aufrufer
- `app/lightning/policy.py::ACTION_RISK_CLASSES` + `is_capital_action` = **einzige** Risiko-Taxonomie; `ln_control._ACTIONS` leitet daraus ab (M-8, Reflection-Test beidseitig)
- `app/lightning/golive_preflight.py` + `scripts/ln_golive_preflight.py` → Bake-Gate: prüft Read- und Invoice-Credential getrennt, probt `pay_invoice` gegen **beide** Empfangs-Credentials; armed zusätzlich gegen `APP_LN_PAYMENT_MACAROON_*`. Matrix: `docs/lightning_macaroon_matrix.md`
- Der Armed-Send-Probe ist dreiwertig: Permission-Deny=`True` (nicht sendefähig), explizite Invalid-Invoice-Antwort nach Permission=`False` (sendefähig), Transport/TLS/Timeout/unklar=`None` (unbewiesen ⇒ NO-GO). Ein Transportfehler darf nie als sendefähiges Credential gelten (B-6)
- `app/security/hotp_auth.py` + `scripts/hotp_bootstrap.py` → HOTP-Read→Verify→Append unter strict Lock + `fsync`; fehlendes/leeres/korruptes Journal ist **deny**, Erst-Inbetriebnahme nur explizit mit `--next-counter` (bestehende Journale werden nie überschrieben)
- `app/security/auth.py::_LN_LOCAL_BYPASS_READS` → `/dashboard/api/ln/ops` ist bewusst NICHT drin (Geldpfad-Audit ≠ lokale Dashboard-Bequemlichkeit); einziger Konsument = Browser-Panel via CF-Access

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
- `app/intelligence/` — Local Intelligence Layer (ADR 0015): TaskRouter/Provider/ContextBuilder/Audit; Flags `KAI_LLM_*` (default-off); Trail `artifacts/intelligence_audit.jsonl`; CLI `intelligence {daily-summary,anomaly-explain,doc-qa}`
- `funding_evidence_shadow.jsonl` / `oi_evidence_shadow.jsonl` / `hype_evidence_shadow.jsonl` — V5-Evidence (shadow)
- `bridge_pending_orders.jsonl` — TV-Bridge pending/promoted
- `trading_loop_audit.jsonl` — Cycle-Trace · `decision_journal.jsonl` — Operator-Entscheide · `risk_gate_audit.jsonl` — Gate-Decisions
- **Wachstum/Lesekosten (2026-07-30):** `app/storage/jsonl_io.py::iter_jsonl_since` = Fenster-Read für zeitsortierte append-only Streams (rückwärts in 256-KiB-Blöcken, Stop erst nach `_DISORDER_MARGIN_RECORDS=64` älteren Datensätzen). Genutzt von `build_priority_gate_summary` + `compute_loop_idle_signal`; gemessen 0,74 s → 0,03 s bei identischem Ergebnis. ⚠ Setzt LEXIKOGRAFISCH vergleichbare ISO-Stempel voraus — nur bei Call-Sites einsetzen, die ohnehin so verglichen haben (`daily_briefing`/`canonical_read` parsen zu `datetime` → NICHT umgestellt). ⚠ Streams sind append-only, aber nicht streng monoton (143 Verletzungen / 88.321 Zeilen, max. 7 Zeilen Versatz) — daher die Marge.
- **Rotation:** `scripts/audit_rotate.py` (`kai-audit-rotate.timer`, täglich 04:40, LIVE) archiviert allowlistete Streams nach `artifacts/archive/`, löscht nie. Ein Stream kommt nur auf die Allowlist, wenn JEDER Konsument höchstens ein Fenster braucht. `paper_execution_audit.jsonl` (Replay-SSOT) + `blocked_outcomes.jsonl` hart ausgeschlossen; `trading_loop_audit.jsonl` bleibt trotz Grösse aussen vor (Voll-Historie-Leser: `daily_briefing`, `health_check`, `loop_idle_signal`, `canonical_read`) — dort ist der Fenster-Read der Hebel, nicht das Kürzen.
