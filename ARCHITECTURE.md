# ARCHITECTURE.md — KAI / AI-Analyst-Trading-Bot

**Stand:** 2026-05-21 · **Phase:** Re-Entry + Stabilisierung · **Modus:** Paper-First, Live-Disabled

Dieses Dokument ist der **Architektur-Einstieg für menschliche und KI-Bearbeiter**. Es beschreibt die tragenden Strukturen + bekannte Grenzen. Es ist KEIN vollständiges Spec-Dokument — Details liegen in `docs/adr/`, `docs/architecture/`, `DECISION_LOG.md` und den `artifacts/operator_memos/`.

---

## Projektidentität

KAI ist eine **produktive Crypto-Analyse- und Signal-Pipeline** mit:
- RSS + TradingView + Telegram-Ingestion
- LLM- und regelbasierter Analyse
- Priority-Scoring + Sentiment-Klassifikation
- Premium-Telegram-Signal → Paper-Trade-Bridge
- AuditStream als zentrale Vertrauensbasis
- Dashboard + Cloudflare Tunnel für Operator-Remote-Zugang

**Nicht-Ziele:**
- Kein Rewrite — Architektur ist tragfähig.
- Keine Live-Aktivierung vor Phase-0-Gates (HOTP, server-side-SL, exchange-perms).
- Kein automatisierter SHADOW_ONLY-Flip vor [[kai-bayes-shadow-only-flip-heuristik]]-Bedingungen.
- Keine zweite parallele State-Machine zu `LIFECYCLE_TRANSITIONS`.

---

## Signal-zu-Order-Pipeline (Premium-Telegram-Pfad)

```
Premium-Telegram-Channel (MTProto via Telethon)
  │
  ▼
telegram_channel_worker → parse_premium_channel_message
                          (app/messaging/signal_parser.py:128)
  │
  ▼
ParsedSignal (dataclass, immutable)
  │
  ▼
emit_parsed_signal → artifacts/telegram_message_envelope.jsonl
  │
  ▼
telegram_channel_approval (Operator-Klick ODER ADR-0004 auto-fill)
  │
  ▼
NormalizedTradeSignal mit correlation_id + status_history
  (app/execution/normalized_signal.py)
  │
  ▼
validate() — 10 Pflicht-Regeln (Plausibility, SL/Targets/Leverage/Margin)
  │
  ▼
EntryRangeWatcher.step() — deterministisch market-data-getriggert
  (app/execution/entry_watcher.py, 404 Z, 32 Tests)
  │ Status: WAITING_FOR_ENTRY → ENTRY_TRIGGERED
  ▼
envelope_to_paper_bridge.run_tick()
  (app/execution/envelope_to_paper_bridge.py, 1169 Z)
  │ Gates: allowlist · TTL · completeness · SHORT-native (V25)
  │        · existing_position · market-data + scale_resolver
  │        · Bridge Gate 4.5 (scale_resolver.validate_scaled_signal, 7 Reasons)
  │        · risk_gate · tolerance ±0.5%
  ▼
ExecutableOrderIntent (einheitlicher Paper/Live-Vertrag)
  (app/execution/order_intent.py)
  │
  ▼
PaperExecutionEngine.create_order
  (app/execution/paper_engine.py, 1108 Z, 56 Tests)
  │ Slippage 0.05% + venue-spezifische Fees (app/execution/fees.py)
  │ correlation_id auf PaperOrder/PaperFill/PaperPosition
  │
  ▼
artifacts/paper_execution_audit.jsonl (26 Audit-Streams total)
```

**RSS-Pfad** läuft parallel mit eigenem `analysis/pipeline.py` + `alerts/auto_annotator.py`.
**TradingView-Pfad** über `tv_bridge.py` mit Webhook-Signatur-Verifikation.

---

## 16-State-Lifecycle (Single Source of Truth)

Single Source of Truth: `app/execution/normalized_signal.py:127 LIFECYCLE_TRANSITIONS`.
Alias-Mapping: `app/execution/models.py:218 OrderLifecycleState = SignalStatus`.

```
RECEIVED → PARSED → VALIDATED → WAITING_FOR_ENTRY → ENTRY_TRIGGERED
   │         │         │              │                    │
   │         │         │              │                    ▼
   │         │         │              │              ORDER_BUILDING
   │         │         │              │                    │
   │         │         │              │                    ▼
   │         │         │              │              ORDER_SUBMITTED
   │         │         │              │                    │
   │         │         │              │                    ▼
   │         │         │              │              ORDER_ACCEPTED
   │         │         │              │                    │
   │         │         │              │                    ▼
   │         │         │              │              POSITION_OPEN
   │         │         │              │              │   │       │
   │         │         │              │              ▼   ▼       ▼
   │         │         │              │     PARTIAL_TP_HIT  TP_HIT  SL_HIT
   │         │         │              │                    │
   ▼         ▼         ▼              ▼                    ▼
REJECTED_INVALID_SIGNAL | EXPIRED | CANCELLED | FAILED
```

**Transition-Validierung** via `transition_to()` wirft `IllegalLifecycleTransition` bei jedem Übergang außerhalb der Matrix. 19 Unit-Tests sichern die Matrix gegen unbeabsichtigte Erweiterung.

**KAI-Regel:** Keine zweite parallele State-Machine. Bei Skalierung neuer Engines (Live, weitere Provider) gilt diese Matrix verbindlich.

---

## Tragende Module

| Modul | Datei | Z | Zweck |
|---|---|---|---|
| Signal-Parser | `app/messaging/signal_parser.py` | 765 | Telegram-Text → ParsedSignal |
| Normalized-Signal | `app/execution/normalized_signal.py` | 592 | 16-State-Lifecycle + validate() + status_history |
| Models | `app/execution/models.py` | 400+ | PaperOrder/Fill/Position + LifecycleTransition + Approval/DecisionStates |
| Order-Intent | `app/execution/order_intent.py` | 56 | ExecutableOrderIntent (Paper/Live-Vertrag) |
| Bridge | `app/execution/envelope_to_paper_bridge.py` | 1169 | Gates + ExecutableOrderIntent-Build + Audit |
| Scale-Resolver | `app/execution/scale_resolver.py` | 214 | Symbol-Scale-Detection + Bridge Gate 4.5 (validate_scaled_signal, 7 Reasons) |
| Entry-Watcher | `app/execution/entry_watcher.py` | 404 | Deterministisches Entry-Range-Polling |
| Paper-Engine | `app/execution/paper_engine.py` | 1108 | Order-Execution + Slippage + Fees + tp_tier-Partial-Exit |
| Recovery | `app/execution/recovery.py` | ~350 | Crash-Recovery (recover_pending_signals + run_recovery_sweep) |
| Auto-Annotator | `app/alerts/auto_annotator.py` | 459 | Outcome-Annotation mit Vol-/Window-Scaling |
| Trail-API | `app/observability/premium_signal_trail.py` | 990+ | End-to-End-Pipeline-Visibility |
| Pipeline-Service | `app/pipeline/service.py` | 1027 | RSS-Pipeline + _maybe_trigger_paper_trade |

---

## ExecutableOrderIntent (Paper/Live-Vertrag)

**Zweck:** ein einheitliches Datenmodell für Paper- und Live-Engine, sodass beide denselben Audit-Anker, dieselbe Validierung und dieselbe Lifecycle-Spur erzeugen.

**Felder (Auszug):** `symbol`, `side`, `position_side`, `entry_value`, `stop_loss`, `targets`, `leverage`, `margin_mode`, `risk_allocation_pct`, `correlation_id`, `idempotency_key`.

**Build-Site:** `envelope_to_paper_bridge.py:414 _build_executable_intent` aus `NormalizedTradeSignal`.

**Konsumenten:** `PaperExecutionEngine.create_order` (heute) + `LiveExecutionEngine` (vorbereitet, deaktiviert).

---

## AuditStream (zentrale Vertrauensbasis)

26 JSONL-Files unter `artifacts/`. Zentrale Hooks:
- `_publish_paper_event` (`paper_engine.py:66`) für Paper-Events.
- `_append_bridge_audit` (`envelope_to_paper_bridge.py:153`) für Bridge-Events.
- `append_outcome_annotation` (`app/alerts/audit.py`) für Outcome-Klassifikation.

**Disziplin:** Jeder neue Event-Type kommt über diese Hooks, keine inline-prints. `correlation_id` ist Pflichtfeld auf allen pipeline-relevanten Events.

**Streams (Auswahl, sortiert nach Wichtigkeit für Pipeline-Forensik):**
- `telegram_message_envelope.jsonl` — Ingestion-Anker
- `bridge_pending_orders.jsonl` — Bridge-Stages
- `paper_execution_audit.jsonl` — Paper-Engine-Events
- `alert_audit.jsonl` + `alert_outcomes.jsonl` — Alert-Pipeline + Lernschicht
- `bayes_confidence_audit.jsonl` + `bayes_posterior_audit.jsonl` — Lern-Stack
- `entry_watcher_audit.jsonl` — Entry-Range-Polling

---

## correlation_id-Kette

Pflichtweg: `envelope.correlation_id` → `NormalizedTradeSignal.correlation_id` → `ExecutableOrderIntent.correlation_id` → `PaperOrder.correlation_id` → `PaperFill.correlation_id` → `PaperPosition.correlation_id` → alle Audit-Events.

**Cross-Check via Trail-API:** `GET /api/premium-signals/trail?limit=N` (siehe `app/api/routers/premium_signals.py:434`) joint 4 Streams pro envelope_id zu einem 6-Stage-Trail.

---

## Bekannte Grenzen (ehrlich, Stand 2026-05-21)

| # | Lücke | Status | Anker |
|---|---|---|---|
| 1 | Partial-ENTRY-Fill ohne Resting-Order-Simulation (V2 2026-05-21 done) | teilweise gelöst | `paper_engine.py:295+` `partial_fill_ratio`-Parameter, Restmenge nur im Audit-Feld `remaining_quantity`, keine pending-Order im Engine-State; Folge-Fill aktuell nur via neue Order auslösbar |
| 2 | Echter Premium-Pipeline-E2E-Test (V1 2026-05-21 done) | gelöst | `tests/integration/test_premium_pipeline_e2e.py` (2 Tests: happy + reject_long_sl_at_or_above_spot) |
| 3 | Lern-Datenbasis dünn (Bayes 4 organisch / Source-Reliability 8/8 insufficient) | beobachtet bis 2026-05-30 | [[kai-bayes-shadow-only-flip-heuristik]] |
| 4 | Singleton-paper_engine | Phase-1-tauglich, P3 für Multi-Asset | `paper_engine_singleton.py` |
| 5 | Audit-Stream-Rotation offen | P2 | V6 |
| 6 | .env-Off-Pi-Backup fehlt | P1 | V4 |
| 7 | Auto-Annotate-Pipeline-Disziplin | Pipeline A reaktiviert 2026-05-21 | [[kai-auto-annotate-reactivation-20260521]] |
| 8 | Telegram-Lesbarkeit (Trail-Summary fehlt) | P2 | V8 |
| 9 | Priority-Scoring vs Sentiment Negativkorrelation | Decision-Pflicht 30.05. | [[kai-priority-sentiment-correlation-paradox]] |
| 10 | Auto-Annotate-Reporting vermischt Fresh/Backfill/Reeval | spezifiziert, kein Tuning | `docs/architecture/auto_annotate_reporting_split_spec.md` |

---

## Verweise

- **ADRs:** `docs/adr/0001..0004` (TradingView, Signal-Consensus-Experimental, DuckDB-Pivot, Premium-Signal-Auto-Fill)
- **Architektur-Reports:** `docs/architecture/signal_to_execution_gap_analysis_20260510.md`, `signal_to_execution_implementation_report_20260510.md`
- **Auto-Annotate-Reporting:** `docs/architecture/auto_annotate_reporting_split_spec.md` (V5-Folgepaket: Reporting-Split ohne Threshold-Tuning)
- **Decision-Log:** `DECISION_LOG.md` (kompakter Verlauf, 29KB)
- **Operator-Memos:** `artifacts/operator_memos/` (laufende Entscheidungen + Forensik)
- **Daily-Strategy:** `artifacts/daily_strategy/YYYY-MM-DD.md` (Bootstrap morgens 08:00 CEST)
- **Memory-Pins:** im Workstation-Memory-Store (`session_pin_*`, `feedback_*`, `kai_*`)
- **README:** `README.md` (Quick-Start + Operator-Commands)
- **Onboarding:** `ONBOARDING.md` (Einstieg für neue Bearbeiter)

---

## Disziplin für KI-Bearbeiter

- Vor Code-Eingriff: ARBEITSPAKET (Format in [kai-master-coding-regeln Skill]) erforderlich.
- LIFECYCLE_TRANSITIONS, ExecutableOrderIntent, correlation_id-Kette = Verbots-Berührung ohne ADR.
- Worktree-Isolation Pflicht bei >=2 parallelen Sessions ([[feedback-multi-agent-main-worktree-ban]]).
- Operator-Sign-off vor Pi-Deploy bei Code-Pfad-Berührung.
- Memo-Pflicht für jede architektur-relevante Decision.
