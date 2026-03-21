# ASSUMPTIONS.md — KAI Platform

Documented assumptions, constraints, and design decisions.
Last updated: 2026-03-21

---

## Sprint 38 Addendum

### A-032: /positions verwendet den kanonischen Collector-Read-Proxy
**Assumption**: Solange kein finaler Portfolio-Read-Surface kanonisiert ist, projiziert Telegram `/positions` auf `get_handoff_collector_summary()` als read-only Operator-Proxy.
**Rationale**: Kein zweiter Positions- oder Trading-Stack, keine Semantik-Ausweitung, kein Architektur-Drift gegen MCP/CLI.
**Impact**: `/positions` zeigt handoff/ack/backlog-Lage statt Broker-/Order-Positionen und bleibt strikt non-executing.

### A-033: /approve und /reject validieren decision_ref fail-closed
**Assumption**: Telegram `/approve` und `/reject` akzeptieren nur `decision_ref` im Format `dec_<12 lowercase hex>`.
**Rationale**: Ein enges, kanonisches Referenzformat reduziert Mehrdeutigkeit und blockiert fehlerhafte oder unvollständige Operator-Eingaben auf dem Audit-Pfad.
**Impact**: Fehlende oder ungÃ¼ltige `decision_ref` werden sauber abgewiesen (fail-closed); der Pfad bleibt append-only audit-only ohne Execution-Seiteneffekt.

---

## Sprint 39 Addendum

### A-037: CoinGecko read path bleibt Spot-only und read-only
**Assumption**: Der erste externe Adapter nutzt ausschließlich CoinGecko-Spot-Read-Endpunkte (`/simple/price`, `/coins/{id}/ohlc`) und greift nicht auf Order-, Account- oder Portfolio-Endpunkte zu.
**Rationale**: Sprint 39 ist ein reiner External-Data-Sprint ohne Execution-Erweiterung.
**Impact**: Keine Trading-Semantik, keine Broker-/Account-Aktionen, keine write-back Side Effects.

### A-038: Unterstützte Symbol-Quotes sind auf USD/USDT begrenzt
**Assumption**: Für den ersten sicheren Adapterpfad werden nur Symbole mit Quote `USD` oder `USDT` akzeptiert (z. B. `BTC/USDT`, `ETH/USD`, `BTC` -> `BTC/USDT`).
**Rationale**: Begrenzter Scope reduziert Mapping-Fehler und verhindert implizite Währungsumrechnungen.
**Impact**: Nicht unterstützte Quotes werden fail-closed als `available=False` mit Fehlergrund zurückgegeben.

### A-039: Stale Market Data bleibt sichtbar, aber klar markiert
**Assumption**: Ein vorhandener Preis mit alter Source-Timestamp wird als `is_stale=True` markiert und als read-only Snapshot zurückgegeben statt still verworfen.
**Rationale**: Operator-Surfaces brauchen Sichtbarkeit über Datenalter; die Bewertung bleibt transparent und auditierbar.
**Impact**: Snapshot enthält immer `freshness_seconds`, `is_stale`, `available`, `error`; Consumer können fail-closed entscheiden, ohne versteckte Datenverluste.

---

## Sprint 40 Addendum

### A-040: Paper Portfolio Read Surface bleibt rein read-only
**Assumption**: Portfolio-/Positions-/Exposure-Surfaces lesen ausschließlich aus append-only Audit- und Marktdaten-Read-Pfaden und mutieren keinen Execution- oder Broker-State.
**Rationale**: Sprint 40 ist ein Operator-Read-Sprint ohne Trading-Erweiterung.
**Impact**: Alle Responses bleiben mit `execution_enabled=False` und `write_back_allowed=False` gekennzeichnet.

### A-041: Portfolio-State wird per Audit-Replay projiziert, nicht aus Live-Engine-Referenzen
**Assumption**: Der kanonische Zustand wird aus `artifacts/paper_execution_audit.jsonl` rekonstruiert, nicht aus in-memory Engine-Objekten.
**Rationale**: Auditierbarkeit und deterministische Reproduzierbarkeit sind wichtiger als implizite Runtime-Kopplung.
**Impact**: Leeres/missing Audit ergibt leeres Portfolio; inkonsistente Audit-Zeilen führen fail-closed zu `available=False`.

### A-042: Mark-to-Market ist optional und degradierbar
**Assumption**: Mark-to-market wird über den bestehenden Market-Data-Read-Path angereichert; stale/unavailable Preise degradieren die Exposure-Auswertung statt Execution auszulösen.
**Rationale**: Evidence before action und fail-closed bei unvollständiger Bewertungsgrundlage.
**Impact**: Stale/fehlende Preise werden explizit markiert; vollständig unbepreiste offene Positionen setzen `available=False`.

---

## Phase 1 Assumptions

### A-001: No Live Trading in Phase 1
**Assumption**: Live trading is disabled by default (live_enabled=False).
**Rationale**: Safety-first principle. Paper trading must be validated before live exposure.
**Impact**: PaperExecutionEngine raises ValueError if live_enabled=True is passed.
**Override**: Requires explicit opt-in (ENV: EXECUTION_LIVE_ENABLED=true) AND approval_required=True gate.

### A-002: Risk Engine is Non-Bypassable
**Assumption**: Every potential order MUST pass through RiskEngine.check_order() before execution.
**Rationale**: Hard risk gates prevent silent financial loss.
**Impact**: Any execution path that bypasses RiskEngine is a security defect.
**Validation**: Tests verify kill switch, daily loss, and position limit gates.

### A-003: Mock Market Data as Default
**Assumption**: MockMarketDataAdapter is the default data source for paper trading.
**Rationale**: Zero external dependencies; deterministic; always available.
**Impact**: Price data is sinusoidal simulation, not real market prices.
**Override**: Replace with exchange adapter (e.g., BinanceAdapter) when live data is needed.

### A-004: Telegram Bot Commands are Admin-Gated
**Assumption**: Only chat IDs listed in OPERATOR_ADMIN_CHAT_IDS can issue commands.
**Rationale**: Prevent unauthorized system control.
**Impact**: Unknown chat IDs receive "Unauthorized" response and are logged.
**Config**: Set OPERATOR_ADMIN_CHAT_IDS=123456,789012 (comma-separated).

### A-005: All Sensitive Operations are Audit-Logged
**Assumption**: Orders, fills, and operator commands are written to JSONL audit logs.
**Rationale**: Full audit trail for forensics and compliance.
**Location**: artifacts/paper_execution_audit.jsonl, artifacts/operator_commands.jsonl
**Rotation**: Not implemented in Phase 1. Manual rotation expected.

### A-006: Kill Switch Requires Manual Reset
**Assumption**: Once triggered, kill switch requires explicit reset_kill_switch() call.
**Rationale**: Prevents automatic recovery from safety events.
**Impact**: System stays halted until operator explicitly resets.

### A-007: Position Sizing Based on Risk Percentage
**Assumption**: Position size = (equity × max_risk_per_trade_pct) / (entry - stop_loss).
**Rationale**: Fixed-risk position sizing is the safest baseline.
**Default**: 0.25% risk per trade.
**Impact**: Very small positions relative to equity at default settings.

### A-008: Stop Loss Required by Default
**Assumption**: require_stop_loss=True in RiskSettings.
**Rationale**: Prevents unlimited loss exposure.
**Override**: RISK_REQUIRE_STOP_LOSS=false (not recommended for production).

### A-009: Paper Execution Uses Slippage and Fees
**Assumption**: Buy orders: +0.05% slippage; Sell orders: -0.05% slippage. All: 0.1% fee.
**Rationale**: Realistic simulation prevents false performance metrics.
**Config**: EXECUTION_PAPER_SLIPPAGE_PCT, EXECUTION_PAPER_FEE_PCT.

### A-010: Telegram Alert Channel ≠ Operator Bot
**Assumption**: TelegramAlertChannel (one-way alerts) and TelegramOperatorBot (commands) are separate.
**Rationale**: Different tokens, different purposes, different security levels.
**Config**: ALERT_TELEGRAM_TOKEN for alerts; OPERATOR_TELEGRAM_BOT_TOKEN for operator commands.

### A-011: Settings are Loaded from Environment
**Assumption**: All secrets from ENV or .env file. Never hardcoded.
**Validation**: companion_model_endpoint must be localhost (enforced by field_validator).
**Secret handling**: No secrets in logs, no secrets in code, no secrets in audit records.

### A-012: KAI Defaults to Non-Live Operating Modes
**Assumption**: KAI defaults to `paper` mode and supports `research`, `backtest`, `paper`, `shadow`, `live` as explicit execution modes.
**Rationale**: The platform is an agentic analysis system first; live action is the highest-risk mode and must never be implicit.
**Impact**: `ExecutionSettings.mode` is typed and defaults to `paper`; `live_enabled=False`, `dry_run=True`, and `approval_required=True` remain the safe baseline.
**Override**: `live` requires an explicit aligned configuration and must fail closed if guardrails are missing.

### A-013: Live Mode is Double-Gated
**Assumption**: `EXECUTION_MODE=live` is invalid unless `EXECUTION_LIVE_ENABLED=true`, `EXECUTION_DRY_RUN=false`, and `EXECUTION_APPROVAL_REQUIRED=true`.
**Rationale**: No critical mode transition may happen through a half-configured or ambiguous state.
**Impact**: Invalid live configurations fail fast during settings validation instead of partially enabling execution paths.

### A-014: Evidence Before Action
**Assumption**: Model output, signals, operator surfaces, and journals are advisory unless they pass explicit gates and human/operator controls.
**Rationale**: KAI must remain auditierbar, kontrolliert, and fail-closed instead of trusting unverifiable autonomy.
**Impact**: Research, shadow, escalation, decision-pack, runbook, and review-journal surfaces remain non-executing by default.

### A-015: Controlled Learning Only
**Assumption**: Learning, mutation, promotion, and behavior changes happen only through validated artifacts, comparison reports, gates, and rollback-capable workflows.
**Rationale**: Self-modification without validation is incompatible with capital preservation and stable architecture.
**Impact**: No production self-change path may bypass evaluation, audit artifacts, and operator review.

### A-016: Persona and Multichannel Stay Outside the Critical Core
**Assumption**: Telegram, future voice, avatar, and multichannel surfaces are extensions around the core, not replacements for typed core contracts.
**Rationale**: KAI's identity must not destabilize execution safety, risk controls, or canonical data flow.
**Impact**: Communication layers remain modular and subordinate to core guardrails, logging, and audit trails.

### A-017: Telegram Approvals are Audit-Only Until a Real Approval Queue Exists
**Assumption**: `/approve` and `/reject` over Telegram currently document operator intent only.
**Rationale**: No live or critical control path may be opened through chat commands without an explicit approval queue and reconciliation layer.
**Impact**: Telegram approvals have no execution side effect and remain safe stubs.

### A-018: Persona, Voice, STT, and Avatar Interfaces Stay Disabled by Default
**Assumption**: Persona, text-to-speech, speech-to-text, and avatar channels are prepared as no-op interfaces only.
**Rationale**: Future multichannel expansion must not destabilize the current operator-safe core.
**Impact**: The interfaces are testable and importable now, but they perform no external action until an approved backend is added.

### A-019: Decision Records Are Immutable, Append-Only, and Live-Incompatible by Default
**Assumption**: Runtime decision instances are stored as immutable `DecisionRecord` objects and appended to audit streams only.
**Rationale**: KAI decisions must remain replayable, schema-bound, and fail-closed rather than being ad-hoc mutable dictionaries.
**Impact**: Research decisions cannot be marked executable, rejected decisions cannot be marked executed, and live-mode decisions require explicit approved state.

### A-020: Unspecified Next Phase Defaults to the Strictest Runtime Decision Contract
**Assumption**: If the operator requests the next sensible expansion without naming a concrete phase, KAI extends the typed decision contract before opening new automation or execution capabilities.
**Rationale**: Tightening the decision boundary is safer than broadening control surfaces when scope is intentionally open.
**Impact**: This phase prioritizes immutable decision validation and append-only persistence over new live, routing, or autonomy features.

### A-021: Rebaseline Documents Override Historical Sprint Counts When They Diverge
**Assumption**: During rebaseline, the verified inventory and count files are the source of truth over older historical sprint summaries.
**Rationale**: Sprint reports preserve history, but hard counts drift as the repo evolves; rebaseline must freeze the current technical reality.
**Impact**: `KAI_BASELINE_MATRIX.md`, `AGENTS.md`, `docs/kai_identity.md`, and live inventory helpers take precedence over stale historical counts in legacy summaries.

---

## Architecture Decisions

### D-001: Pydantic Settings for Configuration
All settings use Pydantic BaseSettings with env_prefix and .env file support.
Validation happens at startup — missing required settings fail fast.

### D-002: Frozen Dataclasses for Immutable Models
Risk results, orders, fills, market data points — all frozen.
Prevents accidental mutation of financial records.

### D-003: JSONL Audit Trail
All audit records written as newline-delimited JSON.
Append-only. Never modified after write. Supports streaming analysis.

### D-004: asyncio Throughout
All I/O operations (HTTP calls, DB, file writes) use async/await.
Synchronous adapters are wrapped. Never block the event loop.

### D-005: Fail-Closed on Risk Violations
If any risk gate fails → order rejected. Never fail-open.
Unknown errors → order rejected.

---

---

## Sprint 35 — Backtest Engine Assumptions

### A-012: Long-Only Mode by Default
**Assumption**: `BacktestConfig.long_only=True` — bearish signals are skipped.
**Rationale**: Paper trading begins with long-only exposure. Short-selling requires
  additional risk modeling and is architecturally reserved for later phases.
**Impact**: direction_hint="bearish" → outcome="skipped_bearish" when long_only=True.
**Override**: `BacktestConfig(long_only=False)` enables bearish (sell) orders.

### A-013: Leverage Always 1x in Backtest
**Assumption**: `max_leverage=1.0` hardcoded in BacktestEngine (I-231).
**Rationale**: Safety principle. Leverage magnifies losses and must not be implicit.
**Impact**: Position sizing is constrained to at most 1x equity value.
**Override**: Only when a live adapter with real margin accounting is connected.

### A-014: Stop-Loss and Take-Profit Derived from Config
**Assumption**: SL = entry_price × (1 - stop_loss_pct/100), TP = SL_distance × multiplier.
**Rationale**: Consistent mechanical risk management without per-signal negotiation.
**Impact**: All positions get the same SL/TP regime. Real signal risk notes are not parsed.
**Override**: A future signal-level risk parser can supply per-signal SL/TP values.

### A-015: Signal Confluence Count = 1 in Backtest
**Assumption**: Each SignalCandidate counts as `signal_confluence_count=1`.
**Rationale**: BacktestEngine processes individual signals sequentially. Aggregated
  confluence across multiple signals in the same batch is a future capability.
**Impact**: `min_signal_confluence_count` in RiskLimits should be set to 1 for backtests.
**Override**: A future multi-signal confluence scoring layer can supply higher counts.

---

### A-019: Decision Journal is Append-Only and Non-Executive
**Assumption**: Decision instances are recorded as append-only JSONL. They capture thesis, risk, and context but do not trigger any execution.
**Rationale**: Evidence-before-action principle. Every decision must be recorded with full context for audit, but recording a decision must never cause a trade.
**Impact**: `execution_enabled=False`, `write_back_allowed=False` on all summary models. File mode 'a' for persistence.
**Validation**: Tests verify 26 mandatory fields, frozen models, deterministic IDs, and malformed-line resilience.

---

### A-022: Prompt-Pack ist kanonische Governance-Basis für alle Agenten
**Assumption**: KAI_SYSTEM_PROMPT.md, KAI_DEVELOPER_PROMPT.md, KAI_EXECUTION_PROMPT.md sowie die Adapter-Dateien sind die verbindliche Grundlage für alle Agenten (Claude Code, Codex, Antigravity).
**Rationale**: Ein konsistentes Prompt-Pack verhindert Architektur-Drift, widersprüchliche Implementierungen und sicherheitskritische Abweichungen zwischen Agenten-Sessions.
**Impact**: Vor jeder Arbeitssession muss das Prompt-Pack gelesen werden. Kein Adapter ersetzt System- oder Developer-Prompt.
**Einsatzreihenfolge**: System Prompt → Developer Prompt → Execution Prompt → Agent-Adapter.

### A-023: Rebaseline-Phase geht Feature-Sprints vor
**Assumption**: Rebaseline (Harmonisierung, Prompt-Governance, Dokumenten-Konsistenz) muss vollständig abgeschlossen sein, bevor neue Feature-Sprints beginnen.
**Rationale**: Inkonsistente Governance-Basis erzeugt kumulierende Architektur-Drift und Security-Lücken.
**Impact**: Während einer Rebaseline-Phase werden keine neuen Produktivfeatures geöffnet.

---

## Out of Scope für Phase 1

- Live exchange connections (Binance, Coinbase, etc.)
- Real-time market data streaming
- Multi-asset portfolio optimization
- ML-based signal generation
- Voice/avatar interfaces (architektonisch vorbereitet, nicht aktiv)
- Distributed deployment (single-process in Phase 1)
- Automatische Telegram /approve und /reject Execution (A-017)

---

## Sprint 37 — Runtime Schema & Decision Backbone Assumptions

### A-024: CONFIG_SCHEMA.json bindet als Runtime-Projektion auf AppSettings
**Assumption**: `CONFIG_SCHEMA.json` wird über eine kanonische Runtime-Projektion aus `AppSettings` plus konservativen Sicherheitsdefaults für noch nicht explizit modellierte Vertragsfelder validiert.
**Rationale**: Der bestehende Settings-Stack bleibt die einzige Runtime-Quelle. Fehlende Vertragsfelder werden dokumentiert ergänzt, statt eine zweite Settings-Architektur aufzubauen.
**Impact**: `AppSettings` validiert jetzt seinen Runtime-Vertrag gegen `CONFIG_SCHEMA.json`; Risk- und Execution-Drift wird fail-closed erkannt.

### A-025: Decision Journal konvergiert auf DecisionRecord als einzigen Backbone
**Assumption**: `app/decisions/journal.py` bleibt als Kompatibilitätsoberfläche erhalten, projiziert intern aber ausschließlich auf den kanonischen `DecisionRecord`.
**Rationale**: Ein zweiter Decision-Modellpfad schwächt Auditierbarkeit, Schema-Bindung und sichere Weiterentwicklung.
**Impact**: Journal-Zeilen werden gegen `DECISION_SCHEMA.json` normalisiert und fail-closed validiert; Legacy-Zeilen sind nur noch Eingabeformat, nicht mehr eigener Backbone.

### A-026: runtime_validator ist der einzige aktive Runtime-Schema-Pfad
**Assumption**: `app/schemas/runtime_validator.py` ist der einzige aktive Runtime-Validator für Payload-Schema-Binding; `app/core/settings.py` nutzt nur Wrapper auf diesen Pfad.
**Rationale**: Ein einzelner Validator-Pfad reduziert Drift, vermeidet konkurrierende Fehlersemantik und hält die Guardrails eindeutig.
**Impact**: Runtime-Config- und Decision-Payloads werden zentral über denselben Validator geprüft; `app/core/schema_binding.py` bleibt nur Audit-/Alignment-Pfad.

### A-024: DECISION_SCHEMA.json ist Runtime-Contract, nicht Dokumentation
**Assumption**: `DECISION_SCHEMA.json` wird bei jeder `DecisionRecord`-Instanziierung gegen `Draft202012Validator` validiert.
**Rationale**: Dekorative Schema-Dateien ohne Runtime-Binding schaffen eine falsche Sicherheitsillusion.
**Impact**: Kein Payload passiert `append_decision_record_jsonl()` ohne Schema-Validation. Schlägt die Validation fehl, wird der Payload abgelehnt (fail-closed).
**Implementierung**: `DecisionRecord._validate_safe_state()` ruft `validate_json_schema_payload(self.to_json_dict(), schema_filename="DECISION_SCHEMA.json", ...)` auf.

### A-025: DecisionInstance ist TypeAlias für DecisionRecord
**Assumption**: `DecisionInstance` im `app.decisions.journal`-Modul ist ab Sprint 37 ein `TypeAlias` für `DecisionRecord`. Kein eigenständiges `DecisionInstance`-Dataclass existiert mehr.
**Rationale**: Zwei konkurrierende Entscheidungsmodelle mit unterschiedlichen Enum-Werten erzeugen Architektur-Drift und falsch positive Tests.
**Impact**: Alle CLI/MCP-Wege durch `create_decision_instance()` erzeugen kanonische `DecisionRecord`-Objekte. Legacy-Rows werden beim Laden normalisiert.

---

## Sprint 38 — Telegram Command Hardening Assumptions

### A-027: Telegram-Kommandos sind keine Execution-Trigger
**Assumption**: Kein Telegram-Kommando öffnet einen Live-Execution-Pfad. `/approve` und `/reject` sind ausschließlich Audit-Einträge ohne Execution-Seiteneffekt.
**Rationale**: Telegram ist ein unsicherer Kanal mit limitierter Authentifizierung (nur chat_id-basiert). Execution-Trigger über diesen Kanal ohne stärkere Authentifizierung würden das Sicherheitsmodell untergraben.
**Impact**: Alle Telegram-Handler geben nach Audit-Log-Eintrag eine Bestätigungsnachricht zurück — niemals einen Order- oder Execution-Seiteneffekt.

### A-028: Telegram-Bot liest Risk-State via MCP, nicht via RiskEngine-Private-Attribute
**Assumption**: `_cmd_risk` liest keinen RiskEngine-State direkt. Der kanonische Pfad ist `get_protective_gate_summary()` (MCP canonical read tool). Keine privaten RiskEngine-Attribute werden im Telegram-Bot referenziert.
**Rationale**: Direct private attribute access koppelt den Bot an Implementierungsdetails. MCP canonical read surfaces sind der einzige stabile, auditierbare Lesepfad fuer Operator-Surfaces.
**Impact**: `_cmd_risk` → `_get_protective_gate_summary()` → `get_protective_gate_summary()` (MCP). Kein `RiskSnapshot`-Modell noetig. Sprint-38-Ursprungsannahme (get_risk_snapshot()) durch Sprint 38C praezisiert.
**Sprint 38C**: `_READ_ONLY_COMMANDS` und `_GUARDED_AUDIT_COMMANDS` sind disjunkt. `incident` wurde aus `_READ_ONLY_COMMANDS` entfernt (Klassifikationskonflikt bereinigt).

### A-029: guarded_write Kommandos sind im dry_run=True Default inaktiv
**Assumption**: `/pause`, `/resume`, `/kill` dürfen im `dry_run=True` (Default) keine State-Mutation auslösen. Sie antworten mit "[DRY RUN]"-Prefix und nehmen keine Aktion vor.
**Rationale**: dry_run=True ist die sichere Default-Konfiguration für alle Telegram-Kommandos. Versehentliche Aktivierung in Nicht-Produktions-Umgebungen darf keine operativen Konsequenzen haben.
**Impact**: Alle drei guarded_write Handler haben explizite `if self._dry_run: return` Guards vor jeder Mutation. Tests MÜSSEN das dry_run-Verhalten verifizieren.

### A-030: /kill erfordert Zwei-Schritt-Bestätigung
**Assumption**: Ein einzelnes `/kill` aktiviert den Kill-Switch NICHT. Es setzt nur `_pending_confirm[chat_id] = "kill"`. Erst ein zweites `/kill` vom selben chat_id triggert `trigger_kill_switch()`.
**Rationale**: Accidental kill-switch activation bei Tipp- oder Verbindungsfehler muss ausgeschlossen sein. Die confirmation ist per-chat_id um Cross-User-Confirmation zu verhindern.
**Impact**: `_pending_confirm` dict ist pro-chat_id. Wird nach Confirmation konsumiert (pop). Test MUSS single-/kill und double-/kill getrennt prüfen.

### A-031: Telegram-Bot-Tests sind Pflicht — kein Produktivcode ohne Tests
**Assumption**: `TelegramOperatorBot` MUSS durch ≥20 Unit-Tests in `tests/unit/test_telegram_bot.py` abgedeckt sein, bevor Sprint 38 als abgeschlossen gilt.
**Rationale**: Der Bot ist ein Operator-Sicherheitskanal. Ungetesteter Operator-Code widerspricht dem Auditierbarkeits-Prinzip (Priorität 5) und dem Fail-Closed-Prinzip.
**Impact**: Sprint 38 ist NICHT abgeschlossen ohne grüne Test-Suite für TelegramOperatorBot. Codex implementiert die Tests als Sprint-38-Task 38.7.

### A-032: Market-Data-Adapter sind ausschließlich read-only
**Assumption**: Kein `BaseMarketDataAdapter`-Subtyp darf Orders senden, Positionen öffnen, oder Broker-State mutieren. Der Market-Data-Layer ist eine passive Datenquelle ohne Schreibzugriff auf Broker-Systeme.
**Rationale**: Lese- und Schreibpfade zu Brokern MÜSSEN getrennt sein. Ein Adapter, der lesen und schreiben kann, verletzt das Principle of Least Privilege und öffnet unbeabsichtigte Execution-Pfade.
**Impact**: Alle Adapter-Konstruktoren DÜRFEN KEINE Broker-Credentials für Schreibzugriff initialisieren. Jede Methode im Adapter ist idempotent und seiteneffektfrei bezüglich Broker-State.

### A-033: MockMarketDataAdapter ist der Pflicht-Default für Paper-Trading und Tests
**Assumption**: Solange kein echter externer Adapter konfiguriert ist, MUSS `MockMarketDataAdapter` als Default verwendet werden. Der Mock hat kein externes Netzwerk, keinen Zufall, keine Abhängigkeiten.
**Rationale**: Paper-Trading und Tests DÜRFEN NICHT von externen Provider-APIs abhängen. Flaky Tests durch Netzwerk-Ausfälle oder API-Rate-Limits sind nicht akzeptabel.
**Impact**: `TradingLoop` und Backtest-Setups MÜSSEN `MockMarketDataAdapter` als default in Testumgebungen akzeptieren. A-003 bestätigt: MockAdapter ist der Default-Adapter (vgl. contracts.md §50.4).

### A-034: Veraltete oder fehlende Marktdaten sind fail-closed — kein Auto-Routing
**Assumption**: Wenn `get_market_data_point()` `None` zurückgibt oder `is_stale=True`, überspringt der TradingLoop den Zyklus für dieses Symbol. Kein automatischer Wechsel auf einen anderen Provider.
**Rationale**: Auto-Routing zwischen Providern bei Datenproblemen würde implizit unterschiedliche Datenquellen mit unterschiedlicher Qualität mischen. Das ist eine versteckte Entscheidung mit Execution-Konsequenzen.
**Impact**: `TradingLoop` behandelt `None` und `is_stale=True` identisch: Zyklus wird als `no_market_data:symbol` aufgezeichnet. Kein Signal, kein Order, kein Alarm. Kein Retry-Loop.

### A-035: BacktestEngine hat keine interne Adapter-Abhängigkeit
**Assumption**: `BacktestEngine.run(signals, prices)` empfängt Marktpreise als `dict[str, float]` (pre-fetched). Innerhalb von `run()` wird kein Adapter aufgerufen.
**Rationale**: Deterministischer Backtest-Replay erfordert, dass keine Live-Daten in `run()` injiziert werden können. Der Caller ist verantwortlich für die Datenqualität, nicht der BacktestEngine.
**Impact**: Backtest-Tests können mit statischen `prices`-Dicts arbeiten — vollständig offline. `MockMarketDataAdapter` kann außerhalb von `run()` für Testdaten verwendet werden.

### A-036: Provider-Health-Check ist ein Monitoring-Signal, kein Routing-Trigger
**Assumption**: `health_check()` returning `False` aktiviert KEINEN anderen Provider und stoppt KEIN Trading. Es ist ein Liveness-Indikator, der in der Operator-Surface `/health` erscheinen kann.
**Rationale**: Kill-Switch-Autorität liegt beim RiskEngine, nicht beim Market-Data-Layer. Eine automatische Trading-Unterbrechung aufgrund eines Health-Checks wäre ein versteckter Execution-Pfad.
**Impact**: `health_check()` → `False` wird geloggt. Es kann in MCP `get_provider_health()` surfaced werden. Es darf KEINEN RiskEngine-State ändern und KEINEN anderen Adapter aktivieren.
**Override**: Nicht verhandelbar — die Konvergenz ist die Grundlage für zukünftige Execution-Erweiterungen.

---

## Sprint 40 — Paper Portfolio Read Surface Assumptions

### A-040: PaperPortfolioSnapshot ist der einzige erlaubte Portfolio-Lesepfad nach aussen
**Assumption**: `PaperPortfolio` (mutable) wird niemals direkt an Operator-Surfaces, MCP-Tools, CLI-Commands oder Telegram-Handler weitergegeben. Nur `PaperPortfolioSnapshot` (frozen) darf diese Grenze ueberschreiten.
**Rationale**: Mutable State direkt in Operator-Surfaces zu exponieren erzeugt versteckte Kopplungen und potenzielle Mutation via Referenz. frozen + read-only ist die einzig sichere Grenze.
**Impact**: Alle MCP-Tools und CLI-Commands, die Portfolio-State zeigen, gehen durch `build_paper_portfolio_snapshot_from_audit()`. Kein direkter Zugriff auf `PaperExecutionEngine._portfolio`.

### A-041: Kanonische Source of Truth fuer Portfolio-State ist das Audit-JSONL
**Assumption**: `artifacts/paper_execution_audit.jsonl` ist die einzige kanonische Quelle fuer Portfolio-State-Rekonstruktion. `build_paper_portfolio_snapshot_from_audit()` replayed `order_filled`-Events.
**Rationale**: Die MCP-Schicht kann nicht auf laufende Engine-Instanzen zugreifen. Das JSONL ist persistent, append-only, auditierbar — identisch zum Pattern von DecisionRecord, SignalHandoff etc.
**Impact**: Portfolio-State-Rekonstruktion ist deterministisch und idempotent. Kein Singleton, kein Shared Memory, kein Inter-Process-Zugriff noetig.

### A-042: Mark-to-Market ist optional und fail-closed per Position
**Assumption**: MtM-Bereicherung schlaegt fuer einzelne Positionen fail-closed: `is_stale=True` oder `available=False` → `is_mark_to_market=False`, Fallback auf `entry_price`. Der gesamte Snapshot bleibt verfuegbar.
**Rationale**: Marktdaten koennen temporaer unavailable sein. Der Portfolio-Snapshot darf nie blockiert werden, nur weil ein Preis nicht abgerufen werden konnte. Observation muss immer moeglich sein.
**Impact**: `PaperPortfolioSnapshot` kann mit und ohne MtM gebaut werden. `is_mark_to_market=False` signalisiert dem Operator, dass Preise veraltet oder unavailable sind.

### A-043: ExposureSummary hat keinen eigenstaendigen Datenpfad
**Assumption**: `ExposureSummary` ist ausschliesslich eine Projektion von `PaperPortfolioSnapshot`. Sie hat keine eigene JSONL, keinen eigenen Market-Data-Abruf, keinen eigenen Backend-Pfad.
**Rationale**: Separate Datenpfade fuer denselben Zustand erzeugen Inkonsistenz und Architektur-Drift. Einheitlicher Datenpfad: JSONL → PaperPortfolioSnapshot → ExposureSummary.
**Impact**: `get_portfolio_exposure_summary()` (MCP) und `research portfolio-exposure` (CLI) delegieren intern immer an den Portfolio-Snapshot-Builder.

### A-044: /positions und /exposure sind kanonische MCP-Read-Surfaces nach Sprint 40
**Assumption**: Nach Sprint 40 ist `get_handoff_collector_summary` nicht mehr das Backing fuer Telegram `/positions`. `/exposure` ist kein Stub mehr. Beide Commands sind vollstaendig MCP-gebackt.
**Rationale**: A-032 (Sprint 38 Addendum) hatte den Handoff-Proxy als provisional deklariert. Sprint 40 ersetzt ihn durch den kanonischen Portfolio-Read-Surface.
**Impact**: `TELEGRAM_CANONICAL_RESEARCH_REFS["positions"]` = `("research paper-portfolio-snapshot",)`. `TELEGRAM_CANONICAL_RESEARCH_REFS["exposure"]` = `("research portfolio-exposure",)`. `"exposure"` in `_READ_ONLY_COMMANDS`.
