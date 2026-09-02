# KAI CORE v1 — der eine, bewiesene Kern

**Stand:** 2026-09-02 · **Basis:** Mainline `claude/p7/reentry-ia-codex-cycle` @ `9293c423` + Sprint-Commits (siehe § 13) · **Modus:** Paper-First, Live-Execution disabled · **Zielsystem:** Raspberry Pi 5 (`kai-pi5`, `ubuntu@192.168.178.23`), systemd + venv, SQLite.

Dieses Dokument beschreibt ausschließlich den **tatsächlich vorhandenen und geprüften** Zustand. Alles, was nicht reproduzierbar belegt ist, steht hier als **UNVERIFIED** oder gar nicht. Zukunftsschichten stehen in `docs/KAI_IDENTITY.md` (Zielbild) und in § 12 (bewusst vertagt) — nicht als Ist-Zustand.

---

## 1. Zweck

KAI nimmt Markt- und Informationsereignisse auf (RSS, TradingView-Webhooks, Telegram-Kanal, Binance-Liquidationsstrom, CoinGecko/Binance-Marktdaten), normalisiert sie, bewertet sie regel- und LLM-basiert, entscheidet über eine Policy-Kette und führt im **Paper-Modus** aus. Jede Ausführung, jede Entscheidung und jeder LLM-Aufruf hinterlässt eine prüfbare Spur. Der Operator sieht den Zustand über `/health*`, das Dashboard und Telegram.

Kein Live-Trading. Kein Blackbox-Bot. Kein Demo.

## 2. Architektur — acht Schichten, je eine verantwortliche Komponente

| # | Schicht | Verantwortliche Komponente | Konkurrierende Wege (Ist) → Entscheidung |
|---|---|---|---|
| 1 | INPUT | `app/pipeline/service.py` (Fetch-Orchestrierung) + `app/ingestion/*` (RSS, Telegram-MTProto, Liquidations) + `app/api/routers/tradingview.py` (Webhook) | `app/integrations/*/adapter.py` doppelt die Adapter-Rolle → **MERGE** nach `ingestion/` (DEFER) |
| 2 | NORMALIZATION | `app/normalization/` (Dokumente) · `app/execution/normalized_signal.py` (Trade-Signale, 10 Pflichtregeln) | `app/enrichment/`, `app/schemas/` waren reine Shims → **DELETE** (erledigt) |
| 3 | INTELLIGENCE | `app/analysis/pipeline.py` über das **AI-Gateway `app/ai/`** + `app/signals/generator.py` | `app/intelligence/` (ADR-0015, default-off, 0 Calls) → **QUARANTINE**; `app/research/` (Offline-Edge-Discovery/Prereg) → **QUARANTINE** |
| 4 | POLICY / DECISION | `app/execution/entry_policy.py` (`EntryRoute`) → `app/risk/engine.py` (`RiskEngine`) → `app/security/governance/gates.py` → `app/risk/promotion_gate.py` / `churn_killer.py` | Die Kette existiert nur als Aufrufreihenfolge in `app/orchestrator/trading_loop.py` (2.276 LOC) — **FIX (dokumentiert, kein Refactor)**; `app/governance/third_party_gate.py` und `app/orchestrator/governed_decision.py` sind fail-closed Gates **ohne Aufrufer** → Operator-Entscheidung (verdrahten oder löschen) |
| 5 | EXECUTION | `app/execution/paper_engine.py::PaperExecutionEngine` (Singleton, Rehydrate aus Audit) | `envelope_to_paper_bridge.py` ist der Operator-Envelope-Zubringer zur selben Engine (keine zweite Wahrheit); Live-Exchange-Teilbaum (`exchanges/{factory,binance,bybit}`, `exchange_preflight`, `recovery`, `live_audit`, `live_engine`) → **QUARANTINE** |
| 6 | STATE | SQLite (`DB_URL`; Pi: `data/dev.db`, 486 MB) via SQLAlchemy/Alembic + JSONL-Artefakte; **`artifacts/paper_execution_audit.jsonl` ist Replay-SSOT** der Paper-Positionen | DB und JSONL sind zwei Substrate; Lese-SSOT `app/execution/portfolio_read.py` (DB-first, JSONL-Fallback) |
| 7 | AUDIT / TRUTH CHAIN | `app/audit/kai_audit_service.py` (validiert, append-only, Lock) + `app/truth/` Attestation-Ledger (`artifacts/truth/attestation_ledger.jsonl`, Hash-Chain, live seq 118) | `decision_journal.jsonl` + `decision_journal_chain.jsonl` (18 Code-Referenzen) sind auf dem Pi seit 127 Tagen **tot** → **QUARANTINE**; `app/truth` + `integrity` + `compliance` gehören inhaltlich zu `audit` → **MERGE** (DEFER) |
| 8 | OBSERVABILITY | `app/observability/` + `GET /health` (Runtime-Identität), `/health/timers`, `/health/ai`, `/health/config`, `kai-health-check.timer`, `kai-service-watchdog.timer` | `app/observability/` mischt Ops-Health, Edge-Forschung und Premium-Analytik (19,6k LOC) → **SPLIT** (DEFER) |

Querschnitt: `app/pipeline/service.py` (Ingest → Analyse) ruft `app/orchestrator/trading_loop.py::run_trading_loop_once` (Signal → Policy → Execution). Beide sind verschachtelt, nicht konkurrierend.

## 3. AI Control Plane — `app/ai/`

**Vorher (verifiziert):** vier Pfade. Server-Analysepfad `openai`-only ohne Fallback (`PIPELINE_PROVIDER` auf dem Pi nicht gesetzt → Code-Default); CLI/Cron-Pfad `[openai, gemini(, grok)]` + Shadow `anthropic`; vier uninstrumentierte OpenAI-Direktaufrufe (Chat, Whisper ×2, Intent) plus `signal_consensus` (in Produktion unerreichbar); ADR-0015-Layer inert. Telemetrie kannte **keinen einzigen Fehler** (0/12.940 Zeilen `ok:false`), weil `EnsembleProvider` Fehlversuche schluckte. Gemini hatte keinen wirksamen Timeout. Der DB-LLM-Audit schrieb `model="unknown"` und ließ Gemini/Grok ganz aus.

**Jetzt:**
- **Provider-Konstruktion:** `app/analysis/factory.py` — `create_primary_provider()` = `openai → gemini (→ grok, wenn XAI_FALLBACK_ENABLED; auf dem Pi verifiziert NICHT gesetzt → Kette ist openai → gemini → internal)` mit `InternalModelProvider` als regelbasiertem Letztglied; `create_shadow_provider()` = `anthropic → gemini`. Server und CLI nutzen **dieselbe** Kette (Shadow im Server-Pfad bewusst nicht aktiviert — Kostenfrage, erst mit v2-Telemetrie entscheidbar).
- **Aufruf-Schicht:** `app/ai/audit.py::llm_call_scope` um **jeden** LLM-Call (Analyse pro Versuch, Chat, STT, Intent, Consensus): Timeout, Fehlerklassifikation (`timeout | rate_limit | auth | quota | schema | refusal | transport | server | cancelled | unknown`), Retry-Filter (401/400/Schema werden nicht wiederholt), Correlation-ID, Tokens, Outcome (`success | fallthrough | exhausted | skipped`).
- **Audit-Strom:** `artifacts/llm_telemetry.jsonl` Schema v2 (additiv; v1-Leser bleiben korrekt).
- **Provider-Health:** `GET /health/ai` aus dem Telemetriestrom (kein Probe-Call): `unknown` bei n=0, `ok` <10 % Fehler, `degraded` 10–50 %, `down` >50 % oder ≥3 Fehler in Folge; zeigt die konfigurierte Kette.
- **Secrets:** ausschließlich `.env` via `ProviderSettings` (`repr=False`); Fingerprint statt Klartext in Logs und Snapshot.
- **Nicht gebaut (bewusst):** Modell-Routing/Eskalation billig → teuer und Kosten in USD — ohne zwei Wochen v2-Telemetrie wäre jedes Routing geraten (DEFER).
- **QUARANTINE:** `app/intelligence/` (eingefroren; Löschung, wenn `KAI_LLM_ENABLED` bis zum nächsten Re-Entry nie gesetzt wurde). Branch `codex/llm-router-migration-20260901` (LiteLLM-Proxy, +4.227 LOC, eigener systemd-Dienst, neue externe Dependency, God-File-Ratchet-Verstoß) wird **nicht gemergt** — er wäre der fünfte Pfad plus eine zweite Deployment-Welt. Als Archiv-Branch auf origin gesichert; verwertbare Vorlagen: `app/inference/{errors,mode}.py`.

## 4. Aktive Komponenten (KEEP) und Laufzeit

**Dauerläufer (Pi, enabled + aktiv):** `kai-server` (uvicorn `app.api.main:app`, 127.0.0.1:8000), `kai-agent-worker`, `kai-tg-listener`, `kai-entry-watch`, `kai-liquidation-stream`, `cloudflared` (Tunnel `kai-trader.org`).

**Kern-Timer:** `kai-paper-trading` (10 min), `kai-recalc-cycle`, `kai-real-analysis-paper-feed`, `kai-shadow-resolver` / `-real-feed`, `kai-auto-annotate(-blocked)`, `kai-audit-rotate`, `kai-truth-anchor` / `-lint`, `kai-integrity-anchor` / `-ots-upgrade`, `kai-canonical-edge-attest`, `kai-health-check`, `kai-pi-health`, `kai-service-watchdog`, `kai-server-health-watchdog`, `kai-backup-*`, `kai-standby-*`, `kai-operator-digest`, `kai-daily-strategy(-reminder)`, `kai-hold-report`, `kai-source-discovery`, `kai-parser-feedback`, Marktdaten-Refresher (`okx-announcements`, `coingecko-overview-refresh`, `funding-refresh`, `unlock-refresh`, `momentum-*`, `technical-screener`, `asset-rotation-shadow`), `kai-regime-classify`.

**Pakete KEEP (17 + 4 mit FIX-Auflage):** `alerts, api, messaging, market_data, ingestion, signals, analysis, core, security, storage, agents, trading, audit, release, normalization, regime, execution` sowie `cli, orchestrator, pipeline` (Test-Ratio 0,18–0,23 — die Entscheidungsschicht ist am schlechtesten getestet; FIX über die Zeit) und `observability` (SPLIT, DEFER).

## 5. Agenten

Kanonische Roster-Quelle: `app/api/routers/agents.py::_AGENTS` (elf Einträge; Aufgaben/Modi in `AGENTS.md`). Technisch sind das zwei verschiedene Dinge:

| Ebene | Was läuft wirklich | Input → Output | Berechtigung | Audit |
|---|---|---|---|---|
| **Runtime-Worker** `kai-agent-worker` (`python -m app.agents.worker --loop`) | Autonome Handler **SENTR** (Secrets/Governance-Audit-Sidecar), **Watchdog** (Health/Drift), **Architect** (Struktur) — Dropbox-Pattern | Queue-Jobs → `artifacts/agents/<name>/{findings,proposals,runs}.jsonl` | read + report; write nur über `app/agents/tools/guarded_write.py` | jede Ausführung in `runs.jsonl` |
| **Entwicklungszeit** (Claude-Code-Subagenten `.claude/agents/*.md`) | DALI, Neo, SATOSHI, KAI-Finder, Einstein, Xqu, Architecture-Red-Team, Data-Quality-Inspector (+ SENTR/Watchdog/Architect interaktiv) | Dispatch-Kapsel → Bericht / Patch-Vorschlag | Worktree, kein Pi-Zugriff | Berichte als Artefakte, Commits |

KAI selbst (Server + Trading-Loop) bleibt oberste Orchestrierungsinstanz; kein Agent löst Ausführung aus. Redundanz: keine (die drei Runtime-Handler haben disjunkte Aufgaben). Nicht im Runtime-Core: die acht Dev-Agenten — sie sind Werkzeuge, keine Systemkomponenten.

## 6. Datenfluss — die zwei bewiesenen Use-Cases

**Use Case A — AI Orchestration** (`tests/integration/test_ai_orchestration_e2e.py`): `CanonicalDocument` → `AnalysisPipeline.run()` (Normalisierung, Keyword-Hits) → `EnsembleProvider([openai, gemini, internal])` → Provider 1 antwortet 429 → `error_class=rate_limit`, `outcome=fallthrough` → Provider 2 liefert → `PipelineResult.provider_name == "gemini"`, `analysis_source = EXTERNAL_LLM` → zwei v2-Audit-Zeilen mit derselben `correlation_id`, v1-Summary zählt 1 Fehler, kein Secret im Strom, Netzfreiheit per httpx-Negativkontrolle. Variante: alle externen fallen → `internal` (regelbasiert) liefert.

**Use Case B — Signal Execution** (`tests/integration/test_signal_execution_e2e.py`, `test_premium_pipeline_e2e.py`): TradingView-Webhook (HMAC, Replay-Schutz) → `pending_signals.jsonl` → Envelope (`tradingview_paper_feeder`) → `envelope_to_paper_bridge.run_tick()` → `NormalizedTradeSignal.validate()` → `RiskEngine` → `PaperExecutionEngine` `order_filled` → Position → Close mit `trade_pnl_usd` → `paper_execution_audit.jsonl` mit durchgängiger `correlation_id`; Kill-Switch `EXECUTION_ENTRY_MODE=disabled` blockt; Telegram-Premium-Pfad (Parser → Envelope → Approval → Watcher → Bridge → Fill) analog. Bekannte, dokumentierte Wahrheit: Operator-Alerts ohne `price` werden vom Auto-Promote zu 100 % als `unsupported_event` abgelehnt (der Shadow-Feed misst sie stattdessen).

Laufzeitbeweis Pi (7 Tage bis 2026-09-02): TV-Events stündlich, 1.524 LLM-Calls (openai 907, anthropic 617), 92 `order_filled` / 36 `position_closed`, Attestation-Ledger fortgeschrieben. Telegram-Kanal: seit 33 Tagen keine Nachricht (Listener lebt, Kanal still — **STALE**, kein Defekt nachweisbar).

## 7. Deployment — SOURCE → BUILD → CONFIG → START → HEALTH → TEST → RESULT

| Schritt | Was genau |
|---|---|
| SOURCE | GitHub `CP-Srinivasa/KAI`, Mainline `claude/p7/reentry-ia-codex-cycle`; Pi-Checkout `/home/ubuntu/ai_analyst_trading_bot` (`/home/kai/…` ist Symlink darauf) |
| BUILD | Python 3.12 venv, `pip install --no-cache-dir -r requirements.lock` (kein `-e .`); Frontend auf dem Laptop `bash scripts/pi_deploy_web.sh ubuntu@192.168.178.23` (Pi hat kein Node) |
| CONFIG | `.env` im Checkout (~190 Schlüssel, 0600), von allen 59 Units per `EnvironmentFile=` (Pflicht) geladen; Boot-Validierung `validate_secrets` + `validate_lightning_boot` + `ReEntryModeProfile`-Invarianten; Sicht ohne Secrets: `GET /health/config` |
| START | `sudo bash scripts/pi_install_systemd.sh` (install/enable/start); Service-Broker `deploy/bin/kai-service-control` für Restarts ohne Root-Shell |
| HEALTH | `curl 127.0.0.1:8000/health` → `runtime_commit == checkout_commit`, `drift_commits == 0`; `/health/timers`, `/health/ai`; `kai-service-watchdog.timer` (5 min, Auto-Restart + Telegram) |
| TEST | CI (`.github/workflows/ci.yml`): Lint/Format/God-File-Ratchet/Stream-Ratchet · Tests (pytest -n auto, Coverage-Ratchet) · Benchmarks · pip-audit + bandit · Secret-Guard/Hygiene · mypy strict · Frontend-Build. Lokal identisch: `bash ~/KAI-mirror/scripts/kai_preflight.sh` |
| RESULT | `bash ~/KAI-mirror/scripts/kai_deploy.sh [--restart kai-server]` → `scripts/pi_deploy_step.sh` auf dem Pi: ff-pull, Unit-Drift **messen**, py_compile, Broker-Restart, `/health` mit Retry, Urteil **0 SUCCESS · 10 HOLD · sonst FAILED**. HOLD = Unit-Drift / Writer-Freeze, die der Operator mit `sudo bash scripts/pi_apply_systemd_units.sh` anwendet (sichert, beweist, rollt zurück) |

Eine zweite Deployment-Welt (Docker/Postgres) existierte im Repo und in der CI ohne Nutzer — gelöscht.

## 8. Konfiguration

- **Ein Baum:** `app/core/settings.py::AppSettings` (26 Sub-Settings, env-Prefixe `APP_`, `DB_`, `EXECUTION_`, `RISK_`, `ALERT_`, `OPENAI_ / ANTHROPIC_ / GEMINI_ / XAI_`, …) + sieben eigenständige Settings-Module (`lightning_settings`, `intelligence/settings` u. a.). 18 Dateien lesen zusätzlich `os.environ` direkt (Schatten-Konfig) → FIX über die Zeit, nicht in diesem Sprint.
- **Sichere Defaults:** DB-Default ist jetzt SQLite-Datei statt Postgres-URL mit eingebettetem Credential; `entry_mode` default `paper`; Live-Gates dreifach gegated.
- **Fail-closed beim Start:** in `APP_ENV=production` müssen `DB_URL` (explizit), `OPENAI_API_KEY`, `APP_API_KEY` und bei aktivierten Kanälen deren Tokens gesetzt sein, sonst `ConfigurationError` (Tests: `tests/unit/test_core_boot_validation.py`, `tests/integration/test_startup_minimal_env.py`).
- **Offener Operator-Schritt (P0):** `APP_ENV` ist auf dem Pi **nicht gesetzt** → Default `development` → `validate_secrets` warnt nur. Reihenfolge: (1) `GET /health/config` lesen und jede kritische Variable in `explicit` bestätigen, (2) `APP_ENV=production` in die `.env`, (3) `kai-server` neu starten, (4) `/health` 200. Erst dann ist der Fail-closed-Pfad scharf.
- **Kritische Variablen (Definition):** `APP_ENV`, `APP_API_KEY`, `DB_URL`, `OPENAI_API_KEY`, `EXECUTION_ENTRY_MODE`, `ALERT_PROVENANCE_SECRET`, `TRADINGVIEW_WEBHOOK_SHARED_TOKEN` / `_SECRET` (wenn Webhook an), `INGESTION_TELEGRAM_CHANNEL_{API_ID,API_HASH,SESSION_PATH}` (wenn Listener an), `ALERT_TELEGRAM_*` (wenn Alerts an).

## 9. Tests

Pflicht-Tests des Kerns (alle per Default in CI, keine Netzaufrufe):

| Pflicht | Test |
|---|---|
| Startup | `tests/integration/test_startup_minimal_env.py` (realer Lifespan mit SQLite, `/health` 200; production ohne Secrets → `ConfigurationError`) |
| Configuration Validation | `tests/unit/test_core_boot_validation.py`, `tests/unit/test_settings*.py`, `tests/unit/test_config_redaction.py` |
| AI Gateway | `tests/unit/test_ai_audit.py`, `test_ai_health.py`, `test_ensemble_provider.py`, Factory-/Provider-Tests |
| Provider Failure / Fallback | `tests/unit/test_ensemble_provider.py`, `test_analysis_pipeline.py::test_pipeline_llm_error_uses_rule_fallback`, Retry-Filter-Tests der vier Provider |
| Persistence | `tests/integration/test_pipeline_e2e.py`, `test_loop_crash_recovery.py`, `tests/unit/test_jsonl_io*.py`, `test_document_repository.py` |
| E2E A | `tests/integration/test_ai_orchestration_e2e.py` |
| E2E B | `tests/integration/test_signal_execution_e2e.py`, `test_premium_pipeline_e2e.py` |
| Audit Chain | `tests/unit/test_truth_ledger*.py`, `test_kai_audit_service.py`, `test_decision_chain.py` (Insel-Ketten); **Lücke:** `paper_execution_audit.jsonl` und `kai_audit.jsonl` sind append-only + validiert, aber **nicht hash-verkettet** (DEFER, § 12) |
| Security / Fail-Closed | `tests/unit/test_auth*.py`, `test_tradingview_webhook*.py`, `test_live_engine.py`, `test_bridge_entry_mode_guard.py`, `test_security_governance_gates.py`, `test_paper_writer_freeze_guard.py` |

Ausführung: `python -m pytest tests -q -n auto --dist loadfile --ignore=tests/benchmarks` (Zahlen des Sprint-Endstands in § 13).

## 10. Monitoring

`GET /health` (Liveness + Runtime-Identität: laufender Commit vs. Checkout, Drift, Lock-Hash) · `GET /health/timers` (systemd-Timer-Freshness) · `GET /health/ai` (Provider-Zustand aus Telemetrie) · `GET /health/config` (effektive Konfiguration, redigiert, `explicit`-Liste) · `kai-health-check.timer` (Pi-seitige Probe mit Telegram-bei-Problem) · `kai-service-watchdog.timer` (5 min, Restart + Push) · Dashboard `https://kai-trader.org` (CF-Access) · `/metrics` (Prometheus-Text: Event-Loop-Lag, Latenz).

Grenze: `journalctl -p err` ist für App-Fehler blind (strukturiertes stdout ohne Syslog-Priority) — Fehler sind in den JSONL-Strömen und `/health/ai`, nicht im Journal-Prioritätsfilter.

## 11. Komponentenentscheidung (Kurzfassung)

| Kategorie | Inhalt |
|---|---|
| **KEEP** | 8-Schichten-Kern (§ 2), 40 Core-Units, 17 Pakete + 4 mit FIX-Auflage |
| **FIX NOW (erledigt)** | AI-Gateway (4 Pfade → 1 Aufruf-Schicht), Gemini-Timeout, Retry-Filter, DB-LLM-Audit, Server-Fallback-Kette, DB-Default/Startvalidierung, `EnvironmentFile=` Pflicht, Konfig-Snapshot, `/health/ai`, `/health/config` |
| **FIX (offen, Operator)** | `APP_ENV=production` setzen; 3 enabled Units laden Code aus `$HOME` außerhalb des Repos (`kai-edge-ic-check`, `kai-edge-ic-watch`, `kai-exploration-coverage` — Provenance-Verstoß P0: Skripte ins Repo oder Units disablen); 12 `.bak`-Unit-Dateien in `/etc/systemd/system`; Telethon-Session 0644 → 0600; 3 aktive-aber-disabled Timer (`hype-refresh`, `ln-scb-monitor`, `technical-paper-first-fill`) überleben keinen Reboot; `third_party_gate` / `governed_decision` verdrahten oder löschen |
| **MERGE (DEFER)** | `integrations/*/adapter` → `ingestion`; `truth` + `integrity` + `compliance` → `audit`; `services/timer_health` → `observability`; `decisions/` → `orchestrator` |
| **DELETE (erledigt)** | Docker/Postgres-Welt + CI-Postgres; 6 Deps; 5 Shim-Module; 37 tote app-Module (fan-in 0, nur test-getragen); 25 Waisen-Skripte — **−18.714 Zeilen** |
| **QUARANTINE** | `app/intelligence/`, `app/research/` (+ Prereg-Timer `forecaster-*`, `prereg-maturity`, `edge-ic-*`, `min-turnover-calibration`, `exploration-coverage`), `app/lightning/` + `app/chain/` (+ `ln-reconcile*`, `ln-scb-monitor`, `oracle-earnings-booking`), `app/premium/` + Fastlane (+ `premium-healthcheck`, `premium-latency-audit`), Live-Exchange-Teilbaum, `decision_journal(+chain)`, `kai-tv-auto-promote.timer` (79 Tage grün bei 100 % Rejects — der Shadow-Feed ersetzt ihn), Branch `codex/llm-router-migration-20260901`, `test/regtest-ln/`, 12 nur-in-Docs-referenzierte Skripte |
| **DEFER** | Modell-Routing/Kosten-USD (nach 2 Wochen v2-Telemetrie), Hash-Chain am Execution-Stream, `observability`-Split, `core/settings.py`-Entkopplung, `cli/commands/trading.py`-Zerlegung, Schatten-Env-Reads → Settings, immutable Release-Pfad `/home/kai/releases/<SHA>/`, `app/capital/`, `kai-llm-shadow` |

Quarantäne bedeutet: Code bleibt im Repo, Units bleiben wie sie sind (Disable ist eine Operator-Entscheidung — Befehle in § 12), aber **nichts davon ist Bestandteil von KAI CORE v1** und nichts davon wird ohne eigene Freigabe erweitert.

## 12. Bewusst vertagt / Operator-Befehle

**KAI SOVEREIGN VALUE-OS (Lightning Payment Fabric)** — eigene Folgephase: Lightning ↔ Fiat/SEPA, PSP, Merchant-Settlement, Accounting, KYT/AML, Treasury, Routing-Ökonomie. Im Core bleibt nur der kapitalfreie Adapter (`app/lightning/`: Node-Connectivity, Health, Invoice/Payment-Abstraktion mit Capability-Scopes, Geldjournal v2, `validate_lightning_boot`) — inert, policy-gegated, in QUARANTINE.

Operator-Befehle (auf dem Pi, jeweils reversibel):

```bash
# grüne, aber funktionslose Promotion abschalten (Shadow-Feed misst weiter)
sudo systemctl disable --now kai-tv-auto-promote.timer
# Reboot-Sicherheit der drei aktiven-aber-disabled Timer
sudo systemctl enable kai-hype-refresh.timer kai-ln-scb-monitor.timer kai-technical-paper-first-fill.timer
# Provenance P0 — bis die Skripte im Repo liegen
sudo systemctl disable --now kai-edge-ic-check.timer kai-edge-ic-watch.timer kai-exploration-coverage.timer
# Unit-Härtung (EnvironmentFile=) anwenden, mit Sicherung + Beweis + Rückweg
sudo bash scripts/pi_apply_systemd_units.sh
# Session-Datei schützen
chmod 0600 artifacts/telegram_channel.session
```

## 13. Release-Stand und Evidenz

Wird am Sprint-Ende ausgefüllt (Commit-SHAs, PR, CI-Lauf, Deploy-Urteil, `/health`-Body, Testzahlen). Bis dahin: **UNVERIFIED**.

<<EVIDENCE>>
