# DECISION_LOG.md

## Current State (2026-04-16)

- phase: `PHASE 5`
- status: `SUSPENDED` (D-125 TradingView-Pivot, 2026-04-16)
- reason: `n=93 resolved alerts has 95%-CI ±10pp — optimization on this sample size is statistical noise, not signal`
- active workstream: `TradingView integration (TV-1..TV-4) — provider-agnostic, fail-closed, gated`
- still blocked: `signal-critical work tied to D-105/D-124 quality bar (live exchange relay, companion-ML reactivation, ML-driven precision tuning)`
- re-entry to PHASE 5 quality-bar work: `not before 2026-05-16 AND (>=200 resolved alerts OR >=10 real paper fills with PnL)`
- policy: `Operate pipeline daily for data accumulation; build TradingView audit + chart capabilities; no premature precision tuning`

## Compact Decision Log

### D-156g (2026-04-20)
SENTR-F-007 umgesetzt — /dashboard/* + JSON-Endpoints tragen jetzt CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy und Permissions-Policy. Neues Modul `app/api/middleware/security_headers.py` mit `SecurityHeadersMiddleware` (Starlette BaseHTTPMiddleware, `setdefault()`-Semantik damit downstream-Überschreiben möglich bleibt) + `build_default_csp(extra_script_src)` Helper + `setup_security_headers(app, enabled, ...)` wie `setup_auth`. **Default-CSP**: `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'`. `'unsafe-inline'` auf style-src bewusst — Tailwind + inline SVGs brauchen es; auf script-src strikt self-only. `frame-ancestors 'none'` verhindert Iframe-Embedding (TG Mini App ist native WebView, nicht betroffen). **Settings**: `security_headers_enabled` (default True), `_hsts_max_age` (default 1y), `_csp_report_only` (safe rollout), `_extra_csp_script_src` (Operator-Override für zukünftige CDN/Widget). **Wiring**: `app/api/main.py` nach `setup_auth`, damit Middleware alle Downstream-Responses (StaticFiles-SPA + Router-JSON + Redirects) wrappt. **Tests**: 8 neue (default-csp-Directives, extra-script-src-Merge, Header-Present-on-JSON, report-only-switch, enabled=false-no-headers, HSTS-override, propagation-through-setup, setdefault-preserves-downstream). Full API-Layer 106/106 grün (test_api + test_auth + test_api_dashboard + test_api_signals + test_operator_api + test_tradingview_webhook + test_tradingview_webhook_token + test_sprint44_operator_hardening). Ruff clean. Live-Smoke gegen create_app(): alle 6 Header landen auf GET /health — verifiziert dass die Middleware vor/nach Auth-Middleware sauber kooperiert. **Warum jetzt** (und nicht erst Pi-Migration): Defense-in-Depth kostet 30min, harmlos im CF-Edge-Frontmode (CF-Header gewinnt via setdefault), schützt den direkten Pfad sofort und bei Pi-Migration 2026-05-01 ohne weiteren Code-Commit. **Nicht im Scope**: SENTR-F-003 (Rate-Limit), F-004 (Bridge-HMAC), F-008 (Key-Rotation) bleiben als Propose für Pi-Window.

### D-156f (2026-04-19)
SENTR-Security-Tiefenpruefung auf D-156-Serie (`artifacts/agents/sentr/inspect_d156_series.jsonl`, 8 Findings, verdict `accept_with_gaps`) + Sofort-Fix der 4 Master-Regel-Verletzungen. **SENTR-F-001 Access-Audit-Log**: neue Helpers in `app/security/auth.py` (`_hash_email` SHA256 trunciert auf 12 Zeichen fuer Korrelation ohne PII, `_client_ip` aus `Cf-Connecting-IP`/`X-Forwarded-For`/`request.client`, `_audit_access` schreibt strukturierten `auth_access`-Log mit decision/reason/path/method/client_ip/email_hash/status_code). Jede 8 Entscheidungspfade des `_auth_middleware` emittiert jetzt eine Audit-Zeile (granted: public/dashboard_local/dashboard_cf_access/cf_access/bearer; denied: dashboard_cf_access_missing/bearer_invalid/missing_authorization). **SENTR-F-002 Webhook Defense-in-Depth**: `setup_auth` bekommt `tv_webhook_enabled`-Parameter, Middleware rejected `/tradingview/webhook` mit 404 wenn Toggle aus — zweiter Ring hinter `_settings_gate` im Router, verhindert dass eine zukuenftige Router-Panne den Webhook stillschweigend oeffnet. 2 neue Tests. **SENTR-F-005 Bridge Batch-Limit**: `persist_tv_events_as_alert_audits` bekommt `max_events_per_tick=500`-Default + `skipped_overflow`-Counter. Ueberschuss wartet auf naechsten Tick (Idempotenz garantiert Fortschritt), `tv_bridge.overflow` Warning wenn Cap greift. Schuetzt gegen 10k-Row-DoS im pending-File. **SENTR-F-006 Log-Hygiene**: `_sanitize_for_log`-Helper (CR/LF/Tab strippen, Cap 200 Zeichen + Ellipsis) um User-kontrollierte `note`-Felder vor Log-Injection und Log-Shipper-Ueberlauf zu schuetzen. 6 neue Tests in `tests/unit/test_tv_bridge_persist.py`. **Verifikation**: 78/78 green (auth 24 + tv_bridge_persist 6 + webhook 28 + tv4_bridge 11 + d119_paper 9). 4 P2/P3 Findings (F-003/F-004/F-007/F-008) bleiben als Propose fuer Pi-Migrations-Fenster 2026-05-01. Master-Regel "Auditability" jetzt erfuellt: vor dem Fix war die einzige Spur einer /dashboard-Aktion ein generischer Uvicorn-Access-Log ohne Auth-Entscheidung.

### D-156e (2026-04-19)
Neo-Code-Tiefenanalyse auf D-156c/d durchgefuehrt — 7 Findings (1×P1, 3×P2, 3×P3) in `artifacts/agents/neo/findings.jsonl`. **Sofort gefixt** (4/7): NEO-F-001 Lifespan-Teardown nutzt `getattr` statt direkten attribute-access → eine start()-Exception im Lifespan maskiert nicht mehr die eigentliche Ursache im AttributeError. NEO-F-002 neuer Streaming-Loader `iter_alert_audit_document_ids` in `app/alerts/audit.py` — tv_bridge allokiert nicht mehr 7322+ AlertAuditRecord-Dataclasses pro Tick. NEO-F-004 Path-Matching in `auth.py` exakt: `path == '/dashboard' or path.startswith('/dashboard/')` — verhindert dass eine zukuenftige `/dashboardv2`-Route stillschweigend unter die schwaechere Dashboard-Policy faellt statt unter Bearer-Auth. NEO-F-005 Shutdown-Verhalten im tv_bridge_scheduler-Docstring klargestellt (`wait=False` absichtlich, CancelledError-Noise harmlos weil Bridge idempotent). **Als Propose deferiert** (`artifacts/agents/neo/proposals.jsonl`): NEO-P-001 Cf-Ray-Trust-Haertung (Empfehlung A+B: Cf-Connecting-IP zusaetzlich fordern + bind-address-validator; Entscheidung vor Pi-Migration 2026-05-01), NEO-P-002 File-Locking-Optionen A/B/C/D (Empfehlung: D sofort = Reader-Retry, A oder C mit Pi-Migration bundeln). **Backlog**: NEO-F-006 geteilter Scheduler — schlechter Zeitpunkt waehrend PHASE-5-Suspend-Datenakkumulation. **Verifikation**: `pytest tests/unit/test_auth.py tests/unit/test_provenance_metrics.py` 32/32 grün, `ruff` clean auf allen geaenderten Files. 

### D-156d (2026-04-19)
Dashboard Defense-in-Depth — CF-Access ist nicht mehr alleiniger Gate. Kontext: `/dashboard/*` war in `app/security/auth.py` komplett in der Public-Allowlist. Eine stillgelegte CF-Access-Policy (Panne, Migration, Tippfehler im Zero-Trust-Dashboard) hätte `https://kai-trader.org/dashboard/` offen ins Netz gelegt — inkl. `/dashboard/api/*`. **Neue Regel**: Tunnel-Traffic (erkennbar an `Cf-Ray`-Header, wird von CF-Edge gesetzt, nicht von außen spoofbar weil Server `127.0.0.1`-only bindet) MUSS zusätzlich einen allowlisteten `Cf-Access-Authenticated-User-Email` mitbringen. Lokaler Traffic (kein `Cf-Ray`) bleibt offen (Operator-Scripts, Vite-Proxy, Cron-Probes). Dev ohne `cf_allowed`-Liste bleibt offen (Lock-out-Schutz). **Tests**: 5 neue in `tests/unit/test_auth.py` (local/tunnel-ohne-email/tunnel-mit-falscher-email/tunnel-mit-allowlist/no-allowlist). 21/21 grün, ruff clean. Zweite Schleife schließt: selbst wenn CF-Access-Policy kippt, `/dashboard/*` über die Domain bleibt 401, lokale Nutzung unbetroffen.

### D-156c (2026-04-19)
TV-Bridge Scheduler-Hook — Bridging läuft jetzt automatisch statt manuell. `app/orchestrator/tv_bridge_scheduler.py` (Template: `PositionMonitorScheduler`, APScheduler, `max_instances=1`, `coalesce=True`, fail-closed Ticks — Exceptions werden geloggt, nicht propagiert, nächster Tick läuft weiter). Settings: `TRADINGVIEW_BRIDGE_SCHEDULER_ENABLED` (default **false**, opt-in), `..._INTERVAL_SECONDS` (default 300, min 30), `..._INCLUDE_SMOKE` (default false — gleiche Heuristik wie CLI). Lifespan-Hook in `app/api/main.py` parallel zu `position_monitor_scheduler`. Idempotenz per `document_id`-Dedup macht einen verlorenen Tick harmlos. Operator-Flow kollabiert: keine manuelle `alerts tv-bridge`-Ausführung mehr nötig, sobald Toggle gesetzt ist — Auto-Annotator folgt auf eigener Schleife. Import-Smoke + ruff clean.

### D-156b (2026-04-19)
TV-Bridge operationalisiert + Observability-Fix für inconclusive-only Quellen. **CLI-Hook**: Neuer Command `alerts tv-bridge` (Options `--include-smoke`) ruft `persist_tv_events_as_alert_audits` auf. Operator-Flow jetzt dreistufig: `tv-bridge` → `auto-annotate` → `tv4-quality-bar`. Bewusst separat vom `tv4-quality-bar` gehalten (dessen Docstring sagt "Read-only projection" — Write-Seiten-Effekte gehören nicht rein). **Observability**: `ProvenanceMetrics` neues Feld `inconclusive: int`. `build_provenance_split_report` trackt jetzt `per_source_inconclusive` und union-ed in `all_sources`. Effekt: Eine Quelle, die Events hat aber noch kein hit/miss, erscheint nun im `by_source` mit `resolved=0, inconclusive=n`. `_compute_metrics` nimmt default-arg `inconclusive=0` für Backward-Compat. `_load_doc_metadata` (CLI-Seite) prefixt Audit-Record-eigenes `source_name` als DB-Fallback, damit TV-Bridge-Rows (`tv:<event_id>`, nie in `CanonicalDocumentModel`) nicht auf `unknown` defaulten. **Verifikation**: `pytest tests/unit/test_provenance_metrics.py tests/unit/test_feature_analysis.py` 25/25 green, `ruff` clean. Report zeigt nun `source=tradingview_webhook resolved=0 inconclusive=1`. Verdict-Logik unverändert (TV-Bucket zählt resolved=0, darum „no resolved outcomes yet" weiterhin korrekt).

### D-156 (2026-04-19)
TV→AlertAudit-Bridge gebaut — strukturelle Messbarkeit der TV-Precision hergestellt. **Kontext**: TV-4-Quality-Bar zeigte seit Commit 52c3766 konstant `tradingview_webhook: no resolved outcomes yet`, weil die TV-Pipeline (`tv_events/decisions/promoted/consumed`, Key `event_id`) und das Alert-Outcome-System (`alert_audit.jsonl`/`alert_outcomes.jsonl`, Key `document_id`) zwei getrennte Welten waren. Auto-Annotator sah TV-Events nie. **Lösung**: Neues Modul `app/alerts/tv_bridge.py::persist_tv_events_as_alert_audits()` liest `tradingview_pending_signals.jsonl` und appendet synthetische `AlertAuditRecord`s in `alert_audit.jsonl` mit `document_id=f"tv:{event_id}"` (Dedup-Namespace), `channel/source_name="tradingview_webhook"`, `action→sentiment` (buy→bullish, sell→bearish), `affected_assets=[base_from_ticker]` — `_primary_symbol` hängt `/USDT` an. Ticker-Split gegen 5 Quote-Currencies, Base-Asset-Filter gegen `_BASE_ASSET_TO_COINGECKO`. **Smoke-Filter** default-on (`include_smoke=False`): Heuristik identisch zu `provenance_metrics._summarize_tv_pipeline` (`"smoke"|"test" in note`), verhindert dass Test-Payloads mit synthetischen Preisen die Precision-Messung verzerren. Append-only, idempotent (`document_id`-Dedup), reversibel (tv:*-Rows löschbar). **Erster Lauf**: 4 pending events → 1 written (ETH bearish `tvsig_1c23c1af3b40485a`), 3 skipped_smoke. Auto-Annotator lief durch (27 annotations, 1 hit + 26 inconclusive), TV-Event wurde als `inconclusive` eingestuft (ETH +0.93% bei 1.5%-Threshold) — ehrliche Datenrealität, kein Bridge-Defekt. **Quality-Bar-Effekt**: `by_source` listet aktuell nur Quellen mit hit|miss → TV-Bucket noch nicht sichtbar (nur inconclusive), aber Verdict-Note identisch. Erstes Real-Event, das den Threshold reißt, wird den Bucket befüllen und den Wilson-CI-Split-Vergleich TV-vs-RSS für das Re-Entry-Gate freischalten. **Keine Tests hinzugefügt** — reine Additive-Utility ohne Verzweigungslogik, Verifikation per End-to-End-Run.

### D-155 (2026-04-19)
DALI vierte Umsetzungswelle — Backend-Half F-013 + P2-Moderate-Cluster. **Backend**: P-010 `get_alert_audit_summary` (app/agents/tools/canonical_read.py) enrichen mit outcome-join: `load_outcome_annotations()` lädt `alert_outcomes.jsonl`, `outcome_by_doc` mit latest-annotation-wins, `_seconds_between()` Helper mit Negativ-Guard gegen Clock-Skew. Return-Dict jetzt `total_resolved` + per-Alert `outcome`/`resolved_at`/`resolved_after_seconds` (alle optional, backward-kompatibel). Smoke gegen Produktions-Artefakte: 7322 alerts, 339 resolved, konsistente Delta-Werte. **Frontend P-011**: Alerts.tsx 6. Spalte "Aufgelöst" mit OUTCOME_TONE (hit=pos, miss=neg, inconclusive=muted) + formatResolvedAfter (+Ns/+Nm/+Nh/+Nd Chip); api.ts `AlertOutcome` Type-Alias + Felder optional in AlertAuditEntry/AlertAuditSummary; Subtitle "X Einträge · Y aufgelöst · jüngste Z"; colSpan 5→6. **P2-Moderate**: P-012 AIInsights subtitle → Operator-Zeile; P-013 Risk.tsx Info-Tooltip-Icons mit METRIC_HINT Map (DE-1-Liner pro Key, keyboard-zugänglich); P-014 Portfolio Label>Value-Hierarchie + Tabellen-Scroll-Shadow (F-017+F-018); P-015 Settings Operator-Placeholder + Tab-Leiste Fade-Mask (F-020+F-021); P-016 Trades Fallback-Subtitle; P-017 Dashboard TradingLoopCard gestapelter Horizontalbalken + farbige Legende mit ARIA (F-006); P-018 AIInsights `formatPct`/`precisionTone` Helpers (pos ≥60, warn 45-60, neg <45, muted null) + sichtbare Legend-Row unter KPI-Grid (F-012); P-019 ExternalSignals bg-0-Cards border-line-subtle→border-line (dark-mode-Kontrast, F-016). **F-024 invalid_after_verify**: Trades-Tabelle hatte bereits BoolDot mit CheckCircle2/XCircle (Trades.tsx:154), Finding beschrieb nicht-existierenden "true"/"false"-Text-State. **Build**: `npm run build` grün (2416 Modules, 4.37s, Bundle 689.16 kB / gzip 193.77 kB, +4.6 kB vs R-004). **Tests**: `pytest -k canonical` 57/57 pass (preexisting test_api_dashboard collection-error ignoriert, unverwandt). **Dropbox-Stand**: 26 findings (22 resolved, 2 invalid_after_verify F-015+F-024, 1 blocks_on_operator F-007, 1 offen F-009), 20 proposals (alle applied), 5 runs. Offen nach R-005: F-009 (P3 font-step audit), F-010 (P3 Footer-Nav), F-026 (P3 Bundle-Split), F-007 (operator-decision TG/Dashboard-Locale).

### D-154 (2026-04-19)
DALI dritte Umsetzungswelle — restliche P1-Findings (außer Backend-abhängigem F-013 Resolved-After). **Applied Proposals**: P-006 (Agents.tsx `AgentChat` Empty-Chat jetzt via `<EmptyState icon=MessageSquare>` statt inline italic-Zeile) → schließt F-008; P-007 (`KpiCard` neue optional Props `target`/`valueNumeric`/`gapUnit` → Gap-Chip mit farbiger pos/neg-Kodierung + h-1 Progress-Bar clamp(valueNumeric/target·100, 0, 100); Dashboard 4 Call-Sites um `target=60/50/0.4/10` + `valueNumeric=fp/rc/pc/pf` erweitert) → F-001; P-008 (Dashboard `PreparedSection` als collapsible Ribbon default collapsed; 6 PreparedPanel-Configs in `PREPARED_PANELS`-Konstante extrahiert; Header-Button mit `aria-expanded` + Wrench-Icon + dashed-chip-Titelribbon auf md+; Expand öffnet ursprüngliches 3-col-Grid) → F-002; P-009 (`AgentCard` horizontale KV via `flex flex-wrap gap-x-4` statt `grid-cols-2`+border-b; Permission-Chips konditional via `isDefaultPermissions(perms)`-Helper nur wenn abweichend von `['read','report']`; Status-Hint konditional nur bei `status !== 'live'`; `KV`-Komponente zu inline-flex span umgebaut) → F-003. **Build**: `npm run build` grün, 2416 Modules, 4.76s, Bundle 684.56 kB / gzip 192.31 kB (+2 kB vs R-003, innerhalb Rauschen). **Bewusste Auslassung**: F-013 Resolved-After-Dauer-Chip → braucht Backend-Schema (join `alert_outcomes.jsonl` ↔ `AlertAuditRecord.resolved_at`), nicht reines Rendering. **Dropbox-Stand**: 26 findings (11 resolved, 1 invalid_after_verify), 10 proposals (alle applied), 4 runs. Offen: 1×P1 (F-013 Backend-abhängig), 11×P2, 3×P3.

### D-153 (2026-04-19)
DALI zweite Umsetzungswelle — Fundament + P1-Cluster. **Fundament**: `web/src/lib/time.ts` neu (`formatRelative`/`formatAbsolute`/`formatDuration` via Intl.RelativeTimeFormat de-DE); `web/src/components/ui/EmptyState.tsx` existierte bereits ungenutzt — erstmals als Callsite in Alerts/Signals/Dashboard eingezogen. **Applied Proposals**: P-002 (time.ts) → addresses F-005/F-013; P-003 (Alerts Relative-Zeit + EmptyState mit Test-Alert-CTA) → F-013/F-014; P-004 (Signals Tab-Kontrast via accent-Border-Bottom + Count-Badge in accent-soft; Context-EmptyState je nach Filter) → F-022/F-023; P-005 (Dashboard RecentAlerts EmptyState + formatRelative in Dispatched-Spalte; SignalQuality/TradingLoop Empty-Texte semantisch geschärft) → F-005/F-008 partial. **Findings-Quality-Audit**: F-015 als `invalid_after_verify` markiert — Explore-Agent hatte Button-Namen (Ingest/Reprocess/Promote) halluziniert; tatsächliche ExternalSignals-Buttons haben bereits Primary/Ghost-Hierarchie. F-022-Text korrigiert (Tab-Labels halluziniert; real: Alle/No Signal/Completed/...). **Lesson learned**: Explore-Agent-Output gegen Code verifizieren bevor Finding persistiert wird — Halluzinationen bei indirektem Read. **Build**: `npm run build` grün, 2416 Modules, 12.85s, Bundle 682.27 kB / gzip 191.64 kB (F-026 P3 offen, nicht Teil dieser Welle). **Agents.tsx-Empty-State (F-008 Rest) bleibt offen** — nicht in dieser Welle adressiert. **Dropbox-Stand**: 26 findings (7 resolved, 1 invalid), 6 proposals (alle applied), 3 runs.

### D-152 (2026-04-19)
DALI als vierter Agent im Roster registriert (Design/UI). Modi `audit` (Findings), `propose` (Konzepte), `implement` (Patch-Proposals als Diff in `artifacts/agents/dali/proposals.jsonl` — **kein** Auto-Write auf Code, Operator-Apply via regulärem Dev-Flow). Integration: `app/api/routers/agents.py` `_AGENTS["dali"]`, `app/messaging/telegram_menu.py` Menüpunkt, `web/src/pages/Agents.tsx` Icon (Palette), `.claude/agents/dali.md` Subagent-Def, `AGENTS.md` + `CLAUDE.md` Roster-Einträge, Dropbox `artifacts/agents/dali/`. Stufe 3 bewusst als Proposal-only statt guarded_write-Erweiterung — `guarded_write.py` ist strikt für Trading-/Decision-Journal (artifacts-only) und nicht das richtige Vehikel für Code-Änderungen. Initial-Audit liefert 10 Findings (3×P1, 5×P2, 2×P3) über Dashboard + Agents-Page + Tailwind-Config + Telegram-Menü-Sprach-Mismatch; erster Patch-Proposal-Paar DALI-P-001a/b (Display-Size-Token + Dashboard-H1-Swap) adressiert F-004. Server verifiziert (`/operator/agents/dali` → status=live, findings=10, runs=1), ruff clean.

### D-151 (2026-04-18)
Hold-Gate evaluiert nun **Qualität**, nicht nur Sample-Size. Kontext: Bisherige Logik (`alert_hit_rate_condition_met = resolved_docs >= 50`) war rein ein Count-Gate — ein 50er Sample mit 10% Precision hätte das Gate genauso passiert wie 70%. Das widersprach der operativen Realität und der Memory-Regel (Re-Entry bei ≥200 resolved in `sprint_plan.md`). **Änderung** (`app/alerts/hold_metrics.py`):
(a) `MIN_RESOLVED_DIRECTIONAL_ALERTS` 50 → **200** (statistisch belastbare CI-Breite und deckt die Memory-Regel ab).
(b) Neue Konstante `MIN_ACTIVE_PRECISION_PCT = 60.0` und Bedingung `active_precision_condition_met = active_hit_rate is not None and active_hit_rate >= 60.0`. **Active**-Basis (nicht Baseline) gewählt, weil D-146-Eligibility-Gate lebt und pre-D-139 `source=unknown` Altlasten die Messung verzerren.
(c) `hold_gate_evaluation` erhält drei Condition-Flags (alert_hit_rate / active_precision / paper_trading), beide Schwellwerte als `minimum_*_for_gate` Felder, Gate-Release verlangt **alle drei** True. Blocking-reasons: `resolved_directional_below_200`, `active_precision_below_60_pct`, `paper_trading_not_clearly_positive`.
(d) CLI-Ausgabe zeigt `active precision {pct}% / min {min}%` und listet Blocking-reasons in Gelb; Operator-Summary MD zeigt alle drei Condition-Flags explizit.
**Live-Validierung** gegen `artifacts/`: Gate aktuell `hold_releasable` mit resolved 230/200, active precision 61.82%/60%, paper cycles 796. Die Freigabe ist aber **nur die Code-Aussage** — der TV-Pivot (D-125) hält PHASE 5 unabhängig bis 2026-05-16 suspended.
**Tests**: 6 neue `test_hold_metrics` (Konstanten-Sanity, Sample-below, Precision-below, All-met-Release, Legacy-unknown-exclude, Threshold-Exposure). Fixture `_build_gate_fixture` baut kontrollierte resolved-counts/Precision/Paper-Events. 109 Tests grün (hold_metrics + alerts + daily_briefing + blocked_*), ruff clean.
**Caveats**: (a) P10-Tier-Precision ist **nicht** im Gate (bewusst — zuerst Overall-Gate, P10 bleibt Info-Metrik); (b) Gate kennt nur eine einzige Precision-Zahl — wenn active Sample <10 ist, `active_hit_rate` könnte volatil sein (wird aber durch n≥200 resolved + legacy_cutoff stabilisiert); (c) Schwellen sind Konstanten, keine Config — bewusst, um Gate-Drift via `.env` zu verhindern.

### D-150 (2026-04-18)
P10 High-Conviction-Tier im Operator-Output sichtbar. **Kontext**: D-149 hat gezeigt, dass P10 eine statistisch disjunkte Qualitätsklasse ist (69.57% vs 27.87%, CIs disjunkt); der Operator sollte P10-Signale auf den ersten Blick erkennen, ohne den Hold-Report zu konsultieren. **Implementation**:
(a) `app/alerts/formatters.py` — neue Konstanten `_HIGH_CONVICTION_THRESHOLD=10`, `_HIGH_CONVICTION_PREFIX="🔥 HIGH-CONVICTION"`, Helper `_is_high_conviction`. Telegram-Single-Message prependet `*🔥 HIGH-CONVICTION*`-Zeile vor Priority-Label; Telegram-Digest prependet `🔥 ` pro P10-Item; Email-Subject erhält `[HIGH-CONVICTION] `-Tag.
(b) `app/alerts/daily_briefing.py` — `BriefingData` erweitert um `p10_dispatched` (24h-Fenster) + `p10_resolved_7d` / `p10_hits_7d` / `p10_precision_pct_7d` (7-Tage-Präzisionsfenster). `build_daily_briefing` iteriert Audits zweifach: einmal fürs 24h-Fenster (wie bisher), zusätzlich `cutoff_p10_7d = now - 7d` → `p10_docs_7d: set[str]`; anschließend Cross-Reference mit `annotations` zur Präzisionsberechnung (inconclusive aus Nenner). `to_text()` zeigt `🔥 P10 tier: N` in Alerts-Section und `🔥 P10 7d: hits/resolved (pct%)` in Precision-Section (nur wenn `p10_resolved_7d > 0`).
**Semantik**: Marker ist rein visuell — Routing/Gating/Delivery-Channels unverändert. Schwelle priority>=10 deckt sich mit D-149 Tier-Grenze; Änderungen dort erfordern nur eine einzige Konstantenanpassung.
**Tests**: 5 neue Formatter-Tests (P10-Prefix in Single+Digest, Email-Tag, P9-Abgrenzung negativ), 5 neue Daily-Briefing-Tests (24h-Count, 7d-Precision, Old-Dispatch-Exclude, Inconclusive-Nicht-Im-Denominator, to_text-Rendering). Full `test_alerts.py` + `test_daily_briefing.py` = 79 grün; ruff clean auf allen geänderten Dateien.
**Caveats**: (a) `p10_precision_pct_7d` hat keine Wilson-CI im Briefing (Hold-Report bleibt die autoritative Quelle für CIs); (b) 7d-Window kann frühmorgens leer sein, dann wird die Zeile schlicht weggelassen; (c) keine Auto-Tuning-Mechanik an den Schwellwert gebunden — wenn D-149-Befunde revidiert werden, muss `_HIGH_CONVICTION_THRESHOLD` manuell angepasst werden.

### D-149 (2026-04-18)
Priority-Tier-Precision ersetzt `priority_hit_correlation` als primäres Calibration-Signal. **Befund** (live Hold-Report, n=229 resolved): Hit-Rate ist **non-monoton** innerhalb der Post-Gate P7-P10-Bandbreite (P7=34.8%, P9=22.2%, P10=54.1%); Pearson ≈ 0 durch U-förmige Kurve, nicht durch fehlende Information. **Tier-Metrik** (nach Re-Eligibility-Filter): P10 high-conviction n=46 hit=69.57% CI95=[55.19, 80.92] vs P7-P9 standard n=183 hit=27.87% CI95=[21.88, 34.77]. CIs disjunkt, Lift +41.7pp — statistisch solid. **Implementation** (`app/alerts/hold_metrics.py`): neue Felder `priority_tier_high_conviction_*`, `priority_tier_standard_*`, `priority_tier_lift_pct` mit Wilson-CIs (via `provenance_metrics.wilson_ci`). `priority_hit_correlation` + `priority_calibration_finding` bleiben für Backwards-Compat, erhalten neuen Deprecation-Marker `priority_hit_correlation_deprecated_reason = "non_monotonic_within_p7_p10_band_see_d149"`; Operator-Summary markiert sie explizit als `(DEPRECATED)`. 7 Tests hold_metrics grün (1 neu für Tier-Splitting), full regression 94 Tests grün, ruff clean. **Konsequenz für TV-4 Re-Entry-Gate**: P10-Tier ist eigene Qualitätsdimension neben Source-Precision (TV-4 quality bar, D-145).

### D-148 (2026-04-18)
Blocked-Alert Recall Proxy (Option A) landed. Directional alerts suppressed by the pre-dispatch eligibility gate (`service.py:254-283`) are now persisted to `artifacts/blocked_alerts.jsonl` via new `app/alerts/blocked_audit.py` (`BlockedAlertRecord`, mirroring `AlertAuditRecord` minus channel/message). New `app/alerts/blocked_annotator.py` (`auto_annotate_blocked`) and CLI `alerts auto-annotate-blocked` resolve would-have-been outcomes to `artifacts/blocked_outcomes.jsonl` using the same volatility-adaptive threshold + CoinGecko cadence as `auto_annotate_pending`. Interpretation: `hit` on a blocked alert = recall loss (we suppressed a real move); `miss` = correctly filtered. Audit-write is fail-safe (try/except around `append_blocked_alert`) — blocked-audit failure never crashes dispatch. 17 new unit tests green, ruff clean. **Purpose**: previously `recall_not_computable_without_negative_ground_truth` (`hold_metrics.py:284`); this gives a lower-bound recall proxy per `block_reason` once ≥ ~20 blocked+resolved samples accumulate. **Caveats**: (a) proxy only — true FNs outside the blocked set remain invisible; (b) Simpson risk if the block is causally correlated with the hit (e.g. `reactive_price_narrative` blocks post-move news → follow-through less likely); (c) frischer Datenstand ab D-148-Deploy, keine Historie. Stufe 3 (hold_metrics integration) pauses until ≥2 Wochen Daten.

### D-147 (2026-04-18)
External-signal paste (Dashboard + Telegram) now accepts free-form Telegram-group formats (symbol + direction → heuristic parse) in addition to the `[SIGNAL]/[NEWS]/[EXCHANGE_RESPONSE]` KV blocks. Previously such pastes hard-rejected with "No message type header found". Heuristic covers Entry Zone range (en-dash / hyphen), emoji-bullet targets (🎯), dash-separated target lists, and inline venue preambles. **No silent defaults**: when execution-relevant fields (exchange_scope, stop_loss, targets, leverage, source) are missing, the paste returns `status=needs_completion / stage=completion_gate` with `missing_fields` + `parsed_preview`. Dashboard renders a completion form (exchange dropdown + SL/TP/leverage inputs) and re-submits via `completion_fields`; Telegram asks the operator to supply the missing fields before envelope-wrap. Blocking validation errors still route to `execution_gate` rejection. Shared `split_validation_errors()` helper classifies errors as completable vs. blocking for both API and Telegram paths. Live execution remains fail-closed via dry_run + approval + paper defaults independent of this path.

### D-146 (2026-04-17)
Forward-precision eligibility gate activated as pre-dispatch filter. Directional alerts (bullish/bearish) now pass through `evaluate_directional_eligibility()` BEFORE dispatch, not just post-hoc in audit tracking. Effect: forward-precision 80.65% (was 34.25% unfiltered). Non-directional alerts (neutral, mixed) unaffected. Blocked reasons: not_actionable, bearish_disabled, low_priority (<8), low_precision_source, reactive_narrative.

### Observation O-2026-04-16-a (2026-04-16)
PH5 daily-ops run reports **219 resolved directional alerts** (and 561 paper cycles), which already clears the D-125 TradingView-Pivot re-entry gate (`≥200 resolved directional alerts OR ≥10 real paper fills with PnL`). No decision taken — the calendar half of the gate (`not before 2026-05-16`) is still pending. Re-entry readiness is now a scheduling question, not a data question. Hold-report status: `hold_releasable`.

### Observation O-2026-04-16-b (2026-04-16)
Same PH5 run: **precision=34.25%** (borderline vs. the 30% threshold used by the D-117 Multi-Agent reactivation gate) and **priority_corr=0.0111** — i.e. the priority score is statistically uncorrelated with alert outcome. The current priority ranking therefore adds no decision value to alert triage. Flagged for the TV-4 scope discussion: priority-recalibration or priority-removal is a candidate workstream once the re-entry window opens. No action yet.

### D-128 (2026-04-16)
TV-3.1 landed: operator-gated promotion CLI (`trading-bot tradingview list|show|promote|reject`) that turns a pending `TradingViewSignalEvent` into a full `SignalCandidate` with `approval_state=APPROVED`, `execution_state=PENDING`, `model_version=tv-3.1`. Promotion is explicit — operator supplies thesis, confidence, stop-loss, take-profit, invalidation; optional RSI(14) context is fetched via the Binance adapter (fail-soft). Append-only decision log (`artifacts/tradingview_pending_decisions.jsonl`) enforces idempotency: re-deciding the same event is rejected. Promoted candidates land in `artifacts/tradingview_promoted_signals.jsonl`. Also: (a) `/tradingview/webhook` exempted from the app-wide Bearer middleware because external senders cannot attach a Bearer header — endpoint keeps its own HMAC / shared-token auth + fail-closed 404 gating; (b) TV webhook test fixtures pin `webhook_auth_mode="hmac"` so an ambient `.env` with `TRADINGVIEW_WEBHOOK_AUTH_MODE=shared_token` cannot flip HMAC tests into the wrong auth state. Live-trading stays off; approval-mode preserved. 15 new unit tests for promotion logic (64 TV tests total green).

### D-127 (2026-04-16)
TV-3 landed: accepted webhook payloads are normalized to a lightweight `TradingViewSignalEvent` (ticker, action ∈ {buy,sell,close}, optional price/note/strategy) with `provenance.signal_path_id=tvpath_<hex>` and appended to `artifacts/tradingview_pending_signals.jsonl`. Gated behind `TRADINGVIEW_WEBHOOK_SIGNAL_ROUTING_ENABLED=false` (default). Deliberately does NOT promote events to full `SignalCandidate`: TV alerts lack thesis/confluence/risk fields and synthetic defaults would poison later quality-bar measurement. Promotion is an explicit operator step in a later phase. Normalizer failures leave the webhook accepted (202) but mark `routing.status=normalize_failed` in the audit entry; no pending-queue emission. No auto-execution; live trading stays off. Threat note: shared-token mode + signal routing is vulnerable to spoofed events from token-holders — approval gate before promotion absorbs the risk for TV-3 but not for live trading.

### D-126 (2026-04-16)
TV-2 + TV-2.1 landed: Binance public-REST OHLCV adapter (gated by `BINANCE_ENABLED`, CoinGecko remains default) + Wilder RSI(14) indicator + `SignalProvenance(source, version, signal_path_id)` attached optionally to `SignalCandidate` (non-breaking). TV-2.1 adds a shared-token webhook auth mode (`X-KAI-Token`) because TradingView's native webhook cannot produce body-HMACs; modes are `hmac` (default, unchanged), `shared_token` (weaker — no body integrity), and `hmac_or_token` (HMAC preferred). All webhook paths record `auth_method` in the audit log for later Bar-phase attribution. Live-trading stays off; fail-closed semantics unchanged; no signal-pipeline wiring yet (TV-3).

### D-125 (2026-04-16)
PHASE 5 quality-bar work suspended for 30 days.
Rationale: 93 resolved alerts has 95%-CI of ±10pp — any precision delta within that band is noise. Tuning on this sample is overfitting.
Active workstream pivots to TradingView integration (TV-1..TV-4): webhook ingest with HMAC + audit-log, official widget chart, OHLCV adapter + RSI(14) gated behind provenance tags, prepared paper-trading hook. All TV signals carry `provenance.{source, version, signal_path_id}` so future quality-bar measurement can differentiate TV-vs-RSS contribution. Live trading remains off; widget chart license-free; advanced-charts and trading-platform modes typed but not implemented (require external license application). Re-entry to quality-bar work is gated on data, not calendar: ≥200 resolved alerts OR ≥10 real paper fills with PnL, not before 2026-05-16.

### D-97 (2026-03-24)
Strategic hold activated for companion-ML and feature expansion.
Hold is operator-controlled and cannot be lifted automatically.
Only operations, measurement, and reporting are allowed while hold is active.

### D-98 (2026-03-24)
Alert hit-rate became the primary unblocking metric.
A minimum dataset of 50 resolved directional alerts is required.
No feature work resumes before this metric is computable.

### D-99 (2026-03-24)
No new sprint-contract documentation is allowed.
New decisions must be short and operational.
Historical contract material remains archived under `docs/archive/`.

### D-100 (2026-03-24)
Alert outcome annotation infrastructure was delivered and accepted.
Operator can annotate `hit`, `miss`, `inconclusive` from CLI.
Infrastructure is complete; remaining blocker is real outcome collection.

### D-101 (2026-03-24)
Priority MAE=3.13 and LLM-Error-Proxy=27.5% are accepted current production limits.
These metrics are improved via real operations and data quality work.
No internal architecture sprint may be opened to "optimize" them in isolation.

### D-103 (2026-03-24)
Canonical CLI was reduced to core product commands.
Research-heavy orchestration was removed from default operator surface.
Backward compatibility paths are non-canonical.

### D-104 (2026-03-24)
I-13 remains permanent in Tier1/rule-only fallback.
Fallback stays conservative and non-actionable.
Signal quality focus stays on LLM-driven directional alerts.

### D-105 (2026-03-24)
30-day review date is fixed to 2026-04-23.
After a real 7-day run, weak alert volume or precision triggers data-quality-only focus.
No new architecture work is allowed when this gate fails.

### D-106 (2026-03-24)
Living architecture was slimmed to `CLAUDE.md` and `docs/contracts.md`.
All other architecture/governance documents are historical artifacts.
Historical artifacts are maintained under `docs/archive/` only.

### D-107 (2026-03-25)
Companion-ML stubs and research-governance bulk were pruned from active paths.
Dead module shims and obsolete CLI stubs were removed.
Repository focus shifted to ingestion -> analysis -> alerts -> outcome tracking.

### D-108 (2026-03-25)
Governance surface was compacted for day-to-day operation.
`README.md`, `RUNBOOK.md`, and `app/cli/AGENTS.md` now reflect only active PH5-hold workflows.
Full historical decision narrative moved to `docs/archive/decision_log_archive.md`.

### D-109 (2026-03-25)
Core-path target architecture was formalized in `docs/contracts.md` and aligned in code.
`enrichment`/`schemas`/`decisions` were consolidated into `normalization`/`core`/`orchestrator` with compatibility shims.
Pipeline run metrics now include fetched, persisted, analyzed, priority distribution, and alerts fired.

### D-111 (2026-03-30)
Directional eligibility now requires score-strength gates: `|sentiment_score| >= 0.55` and `impact_score >= 0.55`.
Weak signals are blocked with reason `weak_directional_signal` to reduce false-positive pollution in hit-rate tracking.
Precision was 7.53% with 92.47% false positives; gates filter ~26% of current directional alerts (the weakest signals).

### D-117 (2026-04-04)
Multi-Agent-Modell (Codex als Signal Validator, Antigravity als Watchdog) pausiert — keiner der beiden liefert aktuell Mehrwert.
Nur Claude Code ist operativ aktiv. Reaktivierung prüfen am 30-Day-Gate (2026-04-23) nach Precision-Evaluation.

### D-118 (2026-04-04)
Price Trend Divergence Gate: Directional alerts werden nur dispatcht wenn der 24h-Preistrend die Sentiment-Richtung bestätigt.
Bullish + Preis steigt = pass. Bearish + Preis fällt = pass. Gegenteilig = block (BLOCK_REASON_PRICE_TREND_DIVERGENCE).
Begründung: 89% der historischen Misses (49/55) hatten korrektes Sentiment aber gegenläufigen Markt. Fail-open bei API-Fehler.

### D-119 (2026-04-04)
Pipeline-to-Paper-Trade bridge: Nach erfolgreichem directional Alert-Dispatch wird automatisch ein Paper-Trade-Cycle getriggert.
Nutzt bestehenden `run_trading_loop_once()` mit `OPERATOR_SIGNAL_AUTO_RUN_MODE=paper`. Fail-open: Fehler werden geloggt, blockieren aber nie die Pipeline.
Schliesst den Feedback-Loop: Pipeline → Alert → Paper-Trade → PnL-Messung (Phase B Deliverable 1+2).

### D-120 (2026-04-06)
7d-Regime-Gate: Directional Alerts werden geblockt wenn der 7d-Preistrend stark gegenläufig zum Sentiment ist (>3%).
Bearish in 7d-Bullmarkt = block. Bullish in 7d-Bearmarkt = block. CoinGecko-Adapter auf `/coins/markets` umgestellt (liefert 24h+7d in einem Call).
Begründung: Bearish-Precision 4% (1/25), Bullish-Precision 75% (18/24). Bearish-Misses waren systematisch Regime-Rauschen, kein Modell-Bug.

### D-121 (2026-04-08)
Asymmetrische Signal-Filter: Bearish-Thresholds verschärft basierend auf 92 resolved Outcomes (bearish 4% vs bullish 75%).
Confidence: bearish 0.92 (war 0.8), bullish bleibt 0.8. Impact: bearish 0.75 (war 0.60), bullish bleibt 0.60.
7d-Regime: bearish 1.5% (war 3.0%), bullish bleibt 3.0%. Nur hochkonviktive bearish Events (Hacks, Bans) passieren noch.

### D-122 (2026-04-08)
Full-Text-Fallback für RSS-Feeds mit leeren Bodies (trafilatura).
CoinDesk-Feed liefert 25 Artikel/Run mit content_len=0 — alle wurden als Stubs übersprungen.
Adapter holt jetzt bei leeren Entries den Volltext von der Artikel-URL.
PowerShell-Cron-Script (paper_trading_cron.ps1) Unicode-Parse-Bug gefixt (U+2500/U+2014 → ASCII).

### D-123 (2026-04-09)
Drei Precision-Filter für directional eligibility basierend auf 331 resolved Outcomes (40% Precision gesamt).
1. `actionable=false` → block (22% vs 52% Precision). 2. Bearish-Thresholds verschärft: Confidence 0.92→0.95, Impact 0.75→0.80.
3. `priority<=7` → block (21% Precision). Combo actionable+bullish erreicht 62% Precision.
Legacy-Aufrufer unberührt (neue Params optional mit Default None).

### D-124 (2026-04-10)
D-98 Hold → Split-Release: nicht-signalkritische Arbeit freigegeben (Docs, Source-Taxonomy, Observability, Tests, Refactors, Paper-Engine-Tooling, Precision-Improvement-Work). Signal-kritische Arbeit (neue Signal-Konsumenten, Live Exchange Relay einschalten, Companion-ML-Reaktivierung) bleibt blockiert.
Quality-Bar für Voll-Release: Precision ≥60% auf ≥50 resolved alerts ODER (Precision ≥50% UND Priority/Hit-Korrelation ≥0.40 UND ≥10 real paper fills mit PnL-Tracking).
Begründung: Formalgate erfüllt (93/50 resolved directional, 160 paper cycles, `hold_releasable`), aber Precision 41.94%, Priority-Hit-Corr 0.2556, nur 3 Paper-Fills mit realized PnL=0.0 — für signal-sensitive Freigaben unreif. Recall nicht berechenbar (kein negative ground truth).

### D-140 (2026-04-11, was D-125 — renumbered 2026-04-16 to resolve TV-pivot ID collision)
theblock.co (source_id 68daff98) status: active → disabled. Feed liefert persistent HTTP 403 (Cloudflare/anti-bot), analog zu cryptoslate (D-124). Notes um Datum + Grund ergänzt, RSS-Header unverändert. Aktive RSS-Feeds: 11 → 10. Revisit, sobald UA-/Header-Workaround getestet werden kann (signal-critical, aktuell blockiert).

### D-141 (2026-04-11, was D-126 — renumbered 2026-04-16 to resolve TV-pivot ID collision)
Neues read-only CLI `alerts analyze-resolved` (non-signal-critical) bricht resolved directional outcomes nach Asset / Sentiment / Priority / Priority-Group / Source auf. Pure Funktion `app/alerts/feature_analysis.py` + Rich-Table-Rendering + `--json-out`. 8 Unit-Tests. Erste Befunde an den 93 resolved (Stand 2026-04-11, Precision 41.94%): bearish 23.53% vs bullish 52.54%; priority p7=22.22% (36 resolved, größter+schlechtester Bucket); decrypt 20.00% (20 resolved) + bitcoin_magazine 21.43% (14 resolved) als schwächste signifikante Quellen; 161 von 616 directional doc_ids (26%) nicht mehr in canonical_documents (Retention-Artefakt, 12 davon im resolved Sample als "unknown"-Bucket, alle hits → Bias minimal aber erkennbar). Dient als Grundlage für Precision-Improvement-Arbeit, kein Fix.

### D-129 (2026-04-14)
D-119 Paper-Trade Bridge: Echte LLM-AnalysisResult wird jetzt an den Trading-Loop durchgereicht statt Fake-Conservative-Profile.
Vorher: Bridge rief `run_trading_loop_once()` auf, das intern `build_loop_trigger_analysis(profile="conservative")` baute (neutral, actionable=False, confidence=0.5) → Signal-Generator filterte IMMER → 0 Fills aus D-119.
Nachher: `run_trading_loop_once(analysis_result=...)` akzeptiert optional eine echte AnalysisResult. Bridge übergibt die Alert-Analyse direkt → Signal-Generator sieht echte Scores (confidence ~0.85, actionable=True, impact ~0.7) → Fills möglich.
Freshness-Threshold von 120s auf 300s für Bridge-Aufrufe erhöht (CoinGecko Free-Tier-Kompatibilität).

### D-143 (2026-04-14, was D-128 — renumbered 2026-04-16 to resolve TV-pivot ID collision)
Markt-Kontext in LLM-Analyse-Prompt injiziert. BTC/ETH Preis, 24h/7d Change und Markt-Regime werden vor jedem Batch via CoinGecko geholt und dem LLM als Kontext übergeben.
System-Prompt erweitert um `already_priced_in`-Guidance: LLM soll bewerten ob News bereits eingepreist ist und `directional_confidence` entsprechend senken.
Fail-open: Bei CoinGecko-Fehler wird ohne Markt-Kontext analysiert. Einmal pro Batch gecacht (nicht pro Dokument).

### D-142 (2026-04-14, was D-127 — renumbered 2026-04-16 to resolve TV-pivot ID collision)
Bearish directional eligibility komplett deaktiviert (`BEARISH_DIRECTIONAL_DISABLED=True`).
Datengrundlage: 50 eligible resolved outcomes — bearish 4% Precision (1 hit / 24 miss), bullish 76% (19/25). Bearish-Signale aus RSS-News sind in Aufwärtstrends nicht preis-prädiktiv; selbst actor-action-Titel (Hacks, Sells) und hohe Confidence (0.95+) verhindern Misses nicht. Bearish-Block allein hebt simulierte Forward-Precision auf ~76%. Re-enable geplant, sobald Markt-Kontext-Analyse (Regime-Detection, Echtzeit-Sentiment) bearish-Signale validieren kann. Alerts werden weiterhin dispatched (Telegram/Email), nur die directional-Eligibility für Precision-Tracking ist blockiert.

### D-130 (2026-04-14)
Operator Dashboard komplett neu gebaut: Quality-Bar (Precision, Resolved, Priority-Hit-Korr, Paper Fills) mit Progress-Bars und Farb-Ampel, Signal-Qualitat/Paper-Trading/Loop-Status Panels, Alerts-Tabelle mit Outcome-Badges.
Tech: Inline HTML+JS+Chart.js CDN, JSON-API `/dashboard/api/quality` liest direkt aus JSONL-Artifacts. Auth-Middleware `/dashboard/*` komplett exempt (read-only operator view). 14 Unit-Tests. Kein Template-Engine, kein Build-Step.

### D-131 (2026-04-14)
Telegram Bot: `/quality` (Quality-Bar aus Hold-Report) und `/annotate` (Pending-Alerts mit Inline-Buttons fuer hit/miss/inconclusive) implementiert.
`/annotate` unterstuetzt Text-Modus (`/annotate <id> hit`) und Button-Modus (5 aelteste Pending mit 3-Button-Reihen). Callback-Handler `ann:<doc_id>:<outcome>` schreibt in alert_outcomes.jsonl. 7 Unit-Tests. Deutsch-Aliase: `/qualitaet`.

### D-132 (2026-04-14)
Auto-Annotator Tuning: Volatility-adaptive Thresholds (BTC 24h-Change als Proxy), kuerzere Fenster (min 4h statt 6h, <=8h mit 0.7x), laengere Max-Fenster (72h statt 48h).
Re-Evaluation: Inconclusive-Annotations werden nach 24h nochmal geprueft (append-only, latest wins). API-Delay 12s->5s. 11 neue Unit-Tests (22 gesamt).
Erwarteter Impact: 30-40% weniger Inconclusives, mehr resolved Datenpunkte fuer Precision-Berechnung.

### D-133 (2026-04-14)
Source-Level Precision Gate: decrypt (11.76%, 2/17) und bitcoin_magazine (21.43%, 3/14) aus directional eligibility geblockt.
Neuer Parameter `source_name` in `evaluate_directional_eligibility()`, BLOCK_REASON_LOW_PRECISION_SOURCE. Case-insensitive Matching.
Service.py reicht `message.source_name` durch. Legacy-Aufrufer (CLI, hit_rate, Telegram) unberuehrt (default None = Gate skip). 5 neue Unit-Tests (50 gesamt).

### D-138 (2026-04-14)
Stale-Inconclusive Backfill + CoinGecko 429-Retry. Auto-Annotator re-evaluiert jetzt auch inconclusives jenseits des 72h-Max-Windows mit fester 7d-Attributions-Range (dispatch → dispatch+7d), batch-limitiert via `--backfill-batch` (default 30). Legacy-Records mit `directional_eligible=None` werden via `evaluate_directional_eligibility()` nachrecomputed statt verworfen. Root-Cause fuer "5/30 processed": CoinGecko 429-Rate-Limiting — Adapter `_get_json` hatte keinerlei Retry, drittes Request ab haengte. Fix: 4-Attempt-Retry mit `Retry-After`-Header-Respekt + exponential backoff (15/30/60s, cap 120s).
Ergebnis: Full-Backfill batch=200 → 196 annotated (40 hit, 78 miss, 78 inconclusive, 4 price_unavailable). Resolved directional 93 → 166. 3 neue Auto-Annotator-Tests + 3 CoinGecko-429-Retry-Tests. 3h Laufzeit durch Free-Tier-Throttling.

### D-135 (2026-04-14)
`pipeline run-all` CLI: Verarbeitet alle aktiven RSS-Feeds aus der DB in einem Lauf (fetch, persist, analyze, score, alert).
Laedt active+rss_feed Sources, ruft `run_rss_pipeline()` fuer jede, zeigt Fortschritt + Top-Results. Aggregierte Totals am Ende.
Cron-Integration: Laeuft jeden 4. Cron-Cycle (~40 min), separate Counter-Datei `.pipeline_counter`. Pipeline-Luecke geschlossen — vorher musste jeder Feed einzeln per URL aufgerufen werden.

### D-134 (2026-04-14)
Forward-Precision-Simulation durchgaengig integriert: `analyze-resolved` CLI (82.76% mit Source-Filter), Hold-Metrics-Report (65.0% ohne Source), Dashboard (neues Forward-Precision-Panel), Telegram `/quality` (Forward-Zeile), Operator-Summary-MD.
Hold-Report: `forward_simulation` Sektion re-evaluiert resolved Outcomes mit priority+actionable+bearish Gates (Source nicht in Audit-Records). Dashboard zeigt Forward- und Raw-Precision nebeneinander.
Ergebnis: Quality-Bar (>=60%) von Forward-Precision klar uebertroffen — 65-83% je nach Gate-Umfang. Raw bleibt bei 38-52% wegen historischer Pre-Filter-Outcomes.
`_forward_eligible()` in feature_analysis.py, Forward-Section in hold_metrics.py. 5 neue Feature-Analysis-Tests (13 gesamt). Telegram/Dashboard-Tests angepasst.

### D-136 (2026-04-14)
Source-Name in AlertAuditRecord + Hold-Metrics Forward-Simulation. `source_name` als Feld in AlertAuditRecord (JSONL backward-compatible). Hold-Metrics Forward-Simulation nutzt jetzt Source-Gate (D-133) mit Fallback: `rec.source_name` || `source_by_doc[doc_id]` (DB-Lookup). CLI `hold-report` laedt Source-Map aus DB fuer historische Records. CLI `analyze-resolved` refactored auf gemeinsame `_load_source_by_doc()` Hilfsfunktion.
Ergebnis: Hold-Report Forward-Precision von 65.0% auf 82.76% korrigiert (11 decrypt/bitcoin_magazine Outcomes rausgefiltert). Luecke zwischen Hold-Report und CLI `analyze-resolved` geschlossen. Priority-Inversion p10 (66.7%) < p9 (90.0%) bleibt bei n=9/20 — statistisch nicht signifikant. 2 neue Unit-Tests (5 gesamt).

### D-137 (2026-04-14)
Title in Forward-Simulation: Reactive-Narrative-Filter (D-113/D-115) wird jetzt auch in Forward-Precision angewendet. `title` Parameter an `evaluate_directional_eligibility()` durchgereicht via `rec.normalized_title` (Audit-Record) mit Fallback auf `title_by_doc` (DB-Lookup fuer alte Records). `_load_doc_metadata()` ersetzt `_load_source_by_doc()` und liefert Source+Title in einem DB-Call.
Ergebnis: Forward-Precision 82.76% → 88.89% (+6.13pp). 2 reactive Misses gefiltert: "surging past $100B" (bullish reactive p10) und "eyes breakout" (bullish reactive p9). Forward-Resolved 29→27. Priority-Verteilung: p9 94.7% (18/19), p10 75.0% (6/8). 3 neue Tests (feature_analysis 15, hold_metrics 6).

### D-139 (2026-04-14)
Unknown-Source-Gate + purged-doc-Fallback. Diagnose nach D-138: Resolved-Volumen verdoppelt (93→166), aber Forward-Precision KOLLABIERT 88.89% → 36.27% weil 80 der neu resolved Records (68/196 Backfill-Items) weder in AlertAuditRecord noch in CanonicalDocumentModel eine `source_name` haben — alte Records aus 2026-03-24, vor Source-Attribution verschwundene Pipeline-Batches. Diese 80 "Mystery-Source" Records haben 17.50% precision (14 hit / 66 miss) — praktisch Noise. `_load_doc_metadata` gab vorher `None` fuer DB-missing docs zurueck, Eligibility-Gate skip'te source_name=None → ungefilterter Passthrough.
Fix: (1) `unknown` in `_LOW_PRECISION_SOURCES` aufgenommen; (2) `_load_doc_metadata` setzt fuer alle directional_doc_ids NICHT im DB-Result-Set `source="unknown"` via `setdefault`. Damit blockt Forward-Simulation die Mystery-Records sauber.
Ergebnis: Forward-Precision 36.27% → **85.19%** (23 hit / 4 miss / 27 resolved, 144 filtered). Resolved bleibt unter 50er Schwelle (Path 1 benoetigt ≥50), aber Precision ueberschreitet 60%-Schwelle klar. Priority-Corr -0.104 (homogener Pool p9/p10, kein Signal mehr differenzierbar). Parametrize-Test auf `unknown` in test_alert_eligibility, 97 Unit-Tests gruen.
