# ASSUMPTIONS.md — KAI Platform

Documented assumptions, constraints, and design decisions.
Last updated: 2026-03-21

---

## Sprint 38 Addendum

### A-045: /positions verwendete den kanonischen Collector-Read-Proxy (superseded)
**Assumption**: Diese Übergangsannahme galt bis zur Einführung des finalen Portfolio-Read-Surface in Sprint 40.
**Rationale**: Kein zweiter Positions- oder Trading-Stack während der Übergangsphase.
**Impact**: Ab Sprint 40 ersetzt durch A-040 bis A-042 (`get_paper_positions_summary` / `get_paper_exposure_summary`).
**Hinweis**: Ursprünglich als A-032 nummeriert (hook-added Sprint 38); umbenannt in A-045 zur Konfliktauflösung mit Sprint-38-Sektion A-032.

### A-046: /approve und /reject validieren decision_ref fail-closed
**Assumption**: Telegram `/approve` und `/reject` akzeptieren nur `decision_ref` im Format `dec_<12 lowercase hex>`.
**Rationale**: Ein enges, kanonisches Referenzformat reduziert Mehrdeutigkeit und blockiert fehlerhafte oder unvollständige Operator-Eingaben auf dem Audit-Pfad.
**Impact**: Fehlende oder ungültige `decision_ref` werden sauber abgewiesen (fail-closed); der Pfad bleibt append-only audit-only ohne Execution-Seiteneffekt.
**Hinweis**: Ursprünglich als A-033 nummeriert (hook-added Sprint 38); umbenannt in A-046 zur Konfliktauflösung mit Sprint-38-Sektion A-033.

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

### A-040: PortfolioSnapshot ist der einzige erlaubte Portfolio-Lesepfad nach aussen
**Assumption**: `PaperPortfolio` (mutable) wird niemals direkt an Operator-Surfaces, MCP-Tools, CLI-Commands oder Telegram-Handler weitergegeben. Nur `PortfolioSnapshot` (frozen, aus `app/execution/portfolio_read.py`) darf diese Grenze ueberschreiten. `app/execution/portfolio_surface.py` ist ein interner TradingLoop-Helper und kein Operator-Surface.
**Rationale**: Mutable State in Operator-Surfaces erzeugt versteckte Kopplung. Der frozen `PortfolioSnapshot` mit `execution_enabled=False` ist die einzig sichere Grenze.
**Impact**: Alle MCP-Tools und CLI-Commands, die Portfolio-State zeigen, gehen durch `build_portfolio_snapshot()` aus `portfolio_read.py`. Kein direkter Zugriff auf `PaperExecutionEngine._portfolio` von aussen.

### A-041: Kanonische Source of Truth fuer Portfolio-State ist das Audit-JSONL
**Assumption**: `artifacts/paper_execution_audit.jsonl` ist die einzige kanonische Quelle fuer Portfolio-State-Rekonstruktion. `build_portfolio_snapshot()` in `app/execution/portfolio_read.py` replayed `order_filled`-Events.
**Rationale**: Die MCP-Schicht kann nicht auf laufende Engine-Instanzen zugreifen. Das JSONL ist persistent, append-only, auditierbar — identisch zum Pattern von DecisionRecord, SignalHandoff etc.
**Impact**: Portfolio-State-Rekonstruktion ist deterministisch und idempotent. Kein Singleton, kein Shared Memory, kein Inter-Process-Zugriff noetig.

### A-042: Mark-to-Market ist optional und fail-closed per Position (PositionSummary)
**Assumption**: MtM-Bereicherung schlaegt fail-closed per `PositionSummary`: `market_data_available=False` oder `market_data_is_stale=True` → `market_price=None`, `market_value_usd=None`, `unrealized_pnl_usd=None`. Der gesamte `PortfolioSnapshot` bleibt verfuegbar.
**Rationale**: Marktdaten koennen temporaer unavailable sein. Der Portfolio-Snapshot darf nie blockiert werden, nur weil ein Preis nicht abgerufen werden konnte.
**Impact**: `PortfolioSnapshot.available=False` nur wenn ALLE Positionen unbepreist sind. Partiell bepreiste Snapshots haben `available=True` mit entsprechenden `PositionSummary`-Flags.

### A-043: ExposureSummary (portfolio_read.py) hat keinen eigenstaendigen Datenpfad
**Assumption**: `ExposureSummary` in `app/execution/portfolio_read.py` ist ausschliesslich eine Projektion von `PortfolioSnapshot`. `build_exposure_summary(snapshot)` nimmt einen `PortfolioSnapshot` entgegen und erzeugt kein eigenstaendiges Market-Data-Fetch.
**Rationale**: Einheitlicher Datenpfad: JSONL → `build_portfolio_snapshot()` → `PortfolioSnapshot` → `build_exposure_summary()`. Kein paralleler Datenpfad.
**Impact**: `get_paper_exposure_summary()` (MCP) und `research paper-exposure-summary` (CLI) delegieren intern an `build_portfolio_snapshot()` + `build_exposure_summary()`.

### A-047: LoopStatusSummary ist eine read-only Projektion des TradingLoop-Audit-Logs (Sprint 41)
**Assumption**: `LoopStatusSummary` wird ausschliesslich aus `artifacts/trading_loop_audit.jsonl` projiziert und nie aus In-Memory-Engine-Instanzen. `auto_loop_enabled=False` bleibt invariant.
**Rationale**: Operator-Surfaces duerfen keine laufenden Engine-Instanzen referenzieren. Das JSONL ist der einzige persistente, auditierbare Zustandstraeger.
**Impact**: `build_loop_status_summary(audit_path, mode)` in `app/orchestrator/trading_loop.py` ist der kanonische Status-Read-Pfad. Fehlende Audit-Datei fuehrt zu einem sicheren leeren Summary ohne Exceptions.

### A-048: run_trading_loop_once nutzt MockMarketDataAdapter als sicheren Default (Sprint 41)
**Assumption**: Der guarded-write Control-Plane-Aufruf `run_trading_loop_once` verwendet standardmaessig `provider="mock"` und damit `MockMarketDataAdapter`.
**Rationale**: Deterministik, Netzwerkunabhaengigkeit und Security - kein unbeaufsichtigter externer API-Aufruf im Standardpfad.
**Impact**: Guarded run-once Tests sind ohne Netzwerkzugang reproduzierbar.

### A-049: mode-Validierung in run_trading_loop_once ist fail-closed (Sprint 41)
**Assumption**: `run_trading_loop_once` akzeptiert ausschliesslich `mode="paper"` oder `mode="shadow"`. `live`, `research`, `backtest` und jeder andere Wert werden sofort fail-closed abgewiesen.
**Rationale**: Sprint 41 eroeffnet keinen Live-Execution-Pfad. Fail-closed ist Pflicht fuer unzulaessige Modi.
**Impact**: Bei unzulaessigem Modus wird kein Zyklus ausgefuehrt; der Aufruf endet mit kontrolliertem Fehler ohne Seiteneffekte.

### A-050: Keine autonome Hintergrundschleife im Control Plane (Sprint 41)
**Assumption**: Der TradingLoop hat keinen Daemon, keinen Scheduler, keine Hintergrundschleife und kein Auto-Retry. Jeder Zyklus ist ein expliziter Operator-Trigger (CLI `research trading-loop-run-once` oder MCP `run_trading_loop_once`).
**Rationale**: Autonome Ausfuehrung waere ein Security-Risiko und Scope-Verletzung.
**Impact**: `auto_loop_enabled=False` bleibt in allen Sprint-41 Status-/Summary-Surfaces sichtbar und unveraenderlich.

### A-051: trading_loop_audit.jsonl ist append-only, run-once bleibt isoliert (Sprint 41)
**Assumption**: `run_trading_loop_once` schreibt genau einen `LoopCycle`-Record pro Aufruf in `artifacts/trading_loop_audit.jsonl`. Der run-once Pfad erzeugt keine versteckten Live-/Broker-Seiteneffekte.
**Rationale**: Audit-Log ist das einzige persistente Artefakt des Control-Plane-Zyklus.
**Impact**: Der Cycle-Pfad ist voll auditierbar, single-shot und kompatibel mit read-only Status-/Recent-Cycles-Surfaces.

### A-044: /positions und /exposure zeigen auf kanonische MCP-Read-Surfaces (Sprint 40)
**Assumption**: Nach Sprint 40 sind `/positions` und `/exposure` vollstaendig MCP-gebackt. `get_handoff_collector_summary` als Backing fuer `/positions` ist superseded. Der `/exposure`-Stub ist ersetzt.
**Rationale**: A-045 (ehemals A-032, Sprint 38 Addendum) hatte den Handoff-Proxy als provisional deklariert. Sprint 40 ersetzt ihn durch den kanonischen Portfolio-Read-Surface.
**Impact**: `TELEGRAM_CANONICAL_RESEARCH_REFS["positions"]` = `("research paper-positions-summary",)`. `TELEGRAM_CANONICAL_RESEARCH_REFS["exposure"]` = `("research paper-exposure-summary",)`. `"exposure"` in `_READ_ONLY_COMMANDS`. Beides implementiert und gruen (1439 Tests, Sprint 40).

### A-052: Sprint-41 Surface-Namen sind trading-loop-*-kanonisch (Consolidation)
**Assumption**: Die finalen Sprint-41-Namen sind `trading-loop-status`, `trading-loop-recent-cycles`, `trading-loop-run-once` (CLI) sowie `get_trading_loop_status`, `get_recent_trading_cycles`, `run_trading_loop_once` (MCP).
**Rationale**: Fruehe Sprint-41-Planung nutzte provisional Namen (`loop-status`, `run-paper-cycle`). Fuer Contract-Lock wird ein eindeutiges kanonisches Naming benoetigt.
**Impact**: `loop-cycle-summary` und `get_loop_cycle_summary` bleiben nur als Kompatibilitaetsaliase; neue Referenzen muessen die kanonischen Namen verwenden.

### A-053: run_trading_loop_once bleibt paper/shadow-only und live-fail-closed
**Assumption**: `run_trading_loop_once` akzeptiert nur `mode in {paper, shadow}`. `live`, `research` und `backtest` werden fail-closed abgewiesen.
**Rationale**: Sprint 41 eroeffnet keinen Live-Execution-Pfad und keine Trading-Produktivfunktion.
**Impact**: Bei unzulaessigem Modus entsteht kein Zyklus, kein Order-Pfad, keine versteckte Seiteneffekte.

### A-054: run-once nutzt konservatives Default-Profil ohne Order-Folgeeffekt
**Assumption**: Der Default `analysis_profile="conservative"` erzeugt absichtlich keine actionable Signal-Lage fuer den run-once-Pfad.
**Rationale**: Control-Plane-Trigger sollen standardmaessig auditierbar und sicher sein, nicht implizit papier-exekutierend.
**Impact**: Standardaufrufe erzeugen `no_signal`-Zyklen; paper execution audit bleibt dabei unveraendert.

### A-055: Kein Autoloop, kein Scheduler, kein Daemon im TradingLoop-Control-Plane
**Assumption**: Sprint 41 bleibt strikt single-cycle triggered. Es existiert kein Hintergrundprozess fuer wiederholte Zyklen.
**Rationale**: Fail-closed und operator-kontrollierte Ausfuehrung haben Vorrang vor Automatisierung.
**Impact**: `auto_loop_enabled=False` ist in allen neuen Status-/Summary-Surfaces explizit sichtbar.

## Sprint 42 — Telegram Webhook Hardening Assumptions

### A-056: Webhook-Layer ist reine Transport-Härtung, keine Business-Logik (Sprint 42D korrigiert)
**Assumption**: `app/messaging/telegram_bot.py` enthält den vollständigen Webhook-Transport-Guard (integriert in `TelegramOperatorBot`). Keine separaten Webhook-Module. Transport-Logik (`process_webhook_update()`) ist strikt getrennt von Business-Logik (`process_update()`).
**Rationale**: Sprint 42 plante fälschlicherweise ein separates Legacy-Webhook-Modul. Die Implementierung integrierte den Guard direkt — einfacher, weniger Drift-Risiko. A-063 dokumentiert dies detaillierter.
**Impact**: `TelegramOperatorBot.process_update()` bleibt unverändert und wird nur bei `accepted=True` aufgerufen.

### A-057: webhook_secret_token leer/None = Webhook fail-closed (Sprint 42D korrigiert)
**Assumption**: Wenn `TelegramOperatorBot(webhook_secret_token=None/""...)` initialisiert wird, ist `webhook_configured == False`. Jeder `process_webhook_update()`-Aufruf → `rejection_reason="webhook_secret_not_configured"`. Kein Webhook-Request ohne konfigurierten Secret.
**Rationale**: Sprint 42 plante fälschlicherweise `OperatorSettings.telegram_webhook_secret` (nicht in OperatorSettings). Der Secret wird als Konstruktor-Parameter übergeben; der Caller ist verantwortlich, ihn aus `OPERATOR_TELEGRAM_WEBHOOK_SECRET` zu lesen.
**Impact**: `webhook_signature_required: True` in der Runtime-Config bleibt ein deklaratives Flag — die operative Durchsetzung liegt beim `webhook_secret_token`-Parameter.

### A-058: Secret-Token-Vergleich muss constant-time sein (Sprint 42)
**Assumption**: `hmac.compare_digest(provided_secret, configured_secret)` ist der einzig erlaubte Vergleich. Kein direktes `==` zwischen Secret-Strings.
**Rationale**: Timing-Angriffe auf String-Vergleiche sind reell. Constant-time-Vergleich ist Standard für jede Secret-Validierung.
**Impact**: Implementierung nutzt `hmac.compare_digest` aus der Python-Stdlib (kein externer Dependency).

### A-059: edited_message ist per Default erlaubt — Replay-Schutz via update_id (Sprint 42C korrigiert)
**Assumption**: `edited_message`-Updates sind in `_WEBHOOK_ALLOWED_UPDATES_DEFAULT = ("message", "edited_message")` enthalten und werden per Default zugelassen. Das Replay-Risiko wird durch update_id-Deduplication (A-060) mitigiert, nicht durch Typ-Filterung. Operatoren können `webhook_allowed_updates=("message",)` setzen, um `edited_message` auszuschliessen.
**Rationale**: Sprint 42 definierte fälschlicherweise `edited_message` als generell verboten. Die Implementierung erlaubt es konfigurierbar. Update-ID-Deduplication ist das primäre Schutzinstrument gegen Doppel-Dispatch.
**Impact**: Editierte Operator-Commands können durchkommen, sofern sie eine neue `update_id` tragen (was Telegram sicherstellt). Doppelter Dispatch derselben `update_id` wird durch den Replay-Buffer blockiert.

### A-060: Replay-Schutz via OrderedDict-FIFO mit maxlen=2048 (Sprint 42C korrigiert)
**Assumption**: `_webhook_seen_update_ids: OrderedDict[int, None]` mit FIFO-Eviction bei `maxlen=2048` ist der Replay-Buffer. Kein `deque`. Kein persistenter Storage. Bei Neustart: leerer Buffer.
**Rationale**: Sprint 42 definierte fälschlicherweise `deque(maxlen=1000)`. Die Implementierung nutzt `OrderedDict` mit expliziter Eviction via `popitem(last=False)`. Funktional äquivalent, aber `maxlen=2048` (doppelte Kapazität). Restart-Risiko bleibt akzeptiert.
**Impact**: `duplicate_update_id` ist der Rejection-Reason für Replay-Fälle (nicht `rejected_replay` wie im Sprint-42-Contract).

### A-061: Webhook-Rejection-Audit in telegram_webhook_rejections.jsonl — nur Rejections (Sprint 42C korrigiert)
**Assumption**: Nur abgewiesene Requests werden in `artifacts/telegram_webhook_rejections.jsonl` geloggt (via `_audit_webhook_rejection()`). Accepted Requests werden ausschliesslich in `artifacts/operator_commands.jsonl` (Bot-Layer-Audit) geloggt.
**Rationale**: Sprint 42 definierte fälschlicherweise `webhook_audit.jsonl` für alle Requests. Die Implementierung trennt Transport-Rejections (security-relevant) von Command-Audits (operator-relevant) in separate Logs.
**Impact**: Zwei getrennte Audit-Streams: `telegram_webhook_rejections.jsonl` (Transport-Security) + `operator_commands.jsonl` (Command-Operator-Surface).

### A-062: TelegramWebhookProcessResult ist nicht das Execution-Gate (Sprint 42C korrigiert)
**Assumption**: `TelegramWebhookProcessResult` mit `accepted=True` bedeutet "Transport-validiert", nicht "autorisiert zur Execution". Das Admin-Gating (`chat_id in _admin_ids`) bleibt in `TelegramOperatorBot.process_update()`. Kein `WebhookValidatedUpdate`-Modell (Sprint-42-Contract war falsch).
**Rationale**: Zwei orthogonale Sicherheitsschichten: Transport-Layer prüft Herkunft (Telegram-Server via Secret-Token), Bot-Layer prüft Operator-Berechtigung (admin_chat_ids). Keine Schicht ersetzt die andere.
**Impact**: Ein accepted Webhook-Update eines nicht-admin Users wird korrekt vom Bot-Layer mit "Unauthorized. This incident is logged." abgefangen.

### A-063: Sprint-42 konsolidiert auf telegram_bot.py als einzigen Webhook-Transportpfad
**Assumption**: Der kanonische Webhook-Transport-Gate ist `TelegramOperatorBot.process_webhook_update(...)` in `app/messaging/telegram_bot.py`; ein separater Webhook-Guard-Pfad bleibt nicht aktiv.
**Rationale**: Kein Parallelpfad, klare Verantwortlichkeit, geringere Drift.
**Impact**: Webhook-Haertung und Command-Dispatch bleiben im selben Telegram-Modul, aber als strikt getrennte Schritte (Transport zuerst, Command danach).

### A-064: Webhook-Allowed-Updates Default ist message + edited_message
**Assumption**: Der Default-Filter erlaubt `("message", "edited_message")`; disallowed Typen werden fail-closed mit `disallowed_update_type` verworfen.
**Rationale**: Kompatibel mit bestehendem `process_update()`-Verhalten, ohne neue Business-Logik.
**Impact**: Keine Inline-/Callback-/Channel-Events erreichen den Command-Handler.

### A-065: Rejection-Audit ist auf abgelehnte Requests fokussiert
**Assumption**: Webhook-Rejections werden append-only nach `artifacts/telegram_webhook_rejections.jsonl` geschrieben; accepted Updates erzeugen dort keinen Eintrag.
**Rationale**: Security-first Sichtbarkeit auf echte Ablehnungsfaelle bei niedrigem Log-Noise.
**Impact**: Rejection-Datensaetze enthalten Grund + Transport-Metadaten + `execution_enabled=False` und `write_back_allowed=False`.

## Sprint 43 — FastAPI Operator API Surface Assumptions

### A-066: Operator API nutzt APP_API_KEY als einzigen Token-Guard
**Assumption**: Der FastAPI-Operator-Surface nutzt denselben `APP_API_KEY` wie der bestehende API-Bearer-Guard. Ohne gesetzten Key ist der gesamte `/operator/*`-Surface fail-closed deaktiviert.
**Rationale**: Kein zweites Secret- oder RBAC-System in Sprint 43. Eine einzige Token-Quelle vermeidet Drift und reduziert Fehlkonfigurationen.
**Impact**: Read-only und guarded Endpunkte verlangen `Authorization: Bearer <APP_API_KEY>`. Bei leerem Key liefert der Router kontrolliert `503`.

### A-067: Guarded run-once bleibt reine Delegation auf kanonischen TradingLoop-Guard
**Assumption**: `POST /operator/trading-loop/run-once` enthält keine eigene Mode- oder Trading-Logik, sondern delegiert vollständig an `mcp_server.run_trading_loop_once()` und damit an den kanonischen Guard in `app/orchestrator/trading_loop.py`.
**Rationale**: Genau ein technischer Guard-Pfad für paper/shadow-only Ausführung; kein paralleler Kontrollpfad im API-Layer.
**Impact**: `mode=live` bleibt fail-closed abgewiesen. Kein Broker-/Live-Execution-Pfad wird durch die API neu eröffnet.

### A-068: require_operator_api_token ist Router-Dependency, nicht Bearer-Middleware
**Assumption**: Der Operator-Router implementiert Auth via `require_operator_api_token` als FastAPI-Dependency auf Router-Ebene (`dependencies=[Depends(...)]`), nicht via `app/security/auth.py`-Bearer-Middleware. Dies erlaubt selektive Exemption einzelner Endpoints (z.B. Webhook) ohne globale Middleware-Änderungen.
**Rationale**: Sprint 43 definierte fälschlicherweise "Bearer-Auth via bestehendes `app/security/auth.py`". Die tatsächliche Implementierung nutzt DI, was sauberer und testbarer ist.
**Impact**: Kein Bypass-Eintrag in `app/security/auth.py` nötig. Auth-Verhalten ist vollständig im Router-Modul kapseliert.

### A-069: /operator/status und /operator/readiness sind Aliases für dieselbe MCP-Funktion
**Assumption**: Beide Endpoints rufen `mcp_server.get_operational_readiness_summary()` auf. Es gibt keinen inhaltlichen Unterschied. `/operator/readiness` ist der primäre Endpoint (test_api_operator.py nutzt ihn für Auth-Tests), `/operator/status` ist ein Alias.
**Rationale**: Operator-facing tooling kann unterschiedliche Naming-Konventionen bevorzugen. Beide Wege auf dieselbe canonical Source vermeidet Divergenz.
**Impact**: Response-Struktur ist identisch. Kein Routing-Unterschied. Tests für Auth-Verhalten nutzen `/operator/readiness`.

### A-070: Operator-Endpoints sind pure Passthrough ohne Fallback-Handler im Router
**Assumption**: Der Router enthält kein Try/Except um die MCP-Aufrufe (ausser `POST /operator/trading-loop/run-once` für ValueError). Alle read-only Endpunkte propagieren unhandled Exceptions direkt. Das fail-closed Verhalten (`execution_enabled=False`) kommt ausschliesslich aus den MCP-Backing-Funktionen selbst.
**Rationale**: Kein redundanter Catch-All-Handler. Die MCP-Funktionen sind bereits fail-closed (never-raise oder return safe default). Der Router ist so dünn wie möglich.
**Impact**: Bei unerwarteten Fehlern in MCP-Funktionen kann ein HTTP 500 entstehen. Akzeptiert für Sprint 43 — kein HTTP-500-Shield auf Router-Ebene nötig, da Backing-Surfaces bereits fail-closed sind.

### A-071: ValueError aus run_trading_loop_once → HTTP 400 mit Detail-String
**Assumption**: `POST /operator/trading-loop/run-once` fängt `ValueError` (z.B. bei `mode=live`) und gibt HTTP 400 mit `detail=str(exc)` zurück. Der Detail-String enthält den kanonischen Mode-Guard-Text ("allowed: paper, shadow").
**Rationale**: HTTP 400 (Bad Request) ist semantisch korrekt für ungültige Mode-Werte. Der Aufrufer kann die Ablehnung von einem Server-Fehler unterscheiden. Der ValueError kommt vom kanonischen Guard in `run_once_guard()`.
**Impact**: `test_operator_run_once_live_mode_is_fail_closed` verifiziert "allowed: paper, shadow" im Detail-String. Client muss für `mode=live` mit 400 rechnen.

### A-072: Webhook-Delegation ist Sprint-43+-Backlog (nicht in Sprint-43-Implementierung)
**Assumption**: `GET /operator/webhook-status` und `POST /operator/webhook` (mit `app.state.telegram_bot`-Delegation) sind im Sprint-43-Entwurf (§54) definiert aber in der tatsächlichen Implementierung **nicht vorhanden**. `test_operator_api.py` mit 8 failing Tests dokumentiert diese Lücke. Die Webhook-Endpoints werden in einem späteren Sprint implementiert.
**Rationale**: Codex implementierte zuerst den schlanken Core-Surface ohne Webhook-Delegation. Die 8 failing Tests sind der Restdrift zwischen §54-Entwurf und tatsächlichem Stand.
**Impact**: Bis zur Implementierung: `test_operator_api.py` bleibt mit 8 failing. Sprint 43+ muss Webhook-Endpoints und `test_operator_api.py`-Korrekturen umfassen.

## Sprint 44 â€” Operator API Hardening & Request Governance Assumptions

### A-073: Request- und Correlation-ID werden im Operator-Router kanonisch gebunden
**Assumption**: `X-Request-ID` und `X-Correlation-ID` werden im Router normalisiert; bei fehlenden/ungueltigen Werten werden sichere IDs mit Prefix (`req_`, `corr_`) erzeugt.
**Rationale**: Einheitliche, transportseitige Korrelation ohne zweite Middleware-Architektur.
**Impact**: Jeder `/operator/*`-Response traegt beide Header, auch bei Fehlern.

### A-074: Error-Payload ist fuer Operator-Fehler einheitlich und fail-closed
**Assumption**: Auth-, Read- und Guarded-Fehler liefern eine einheitliche `detail.error`-Struktur inkl. request_id/correlation_id sowie `execution_enabled=False` und `write_back_allowed=False`.
**Rationale**: Sicherheitsorientierte Fehlerbehandlung mit klarer Auditierbarkeit und ohne implizite Execution-Semantik.
**Impact**: Clients koennen Fehlertypen stabil parsen; kein nackter, unstrukturierter Fehlerpfad.

### A-075: Idempotency-Key ist fuer guarded run-once verpflichtend
**Assumption**: `POST /operator/trading-loop/run-once` akzeptiert nur Requests mit gueltigem `Idempotency-Key`; identische Requests werden replayed, abweichende Payloads mit gleichem Key werden mit `409` blockiert.
**Rationale**: Doppel-Submit-Schutz fuer den einzigen guarded Endpoint, ohne Queue/Scheduler.
**Impact**: Kein unbeabsichtigter Mehrfach-Trigger desselben Control-Plane-Aufrufs.

### A-076: Guarded Endpoint hat bewusst leichtes In-Memory-Rate-Limit
**Assumption**: Ein kleines tokenbasiertes In-Memory-Limit schuetzt nur den guarded run-once Endpoint; read-only Endpoints bleiben ohne neues Rate-Limit-System.
**Rationale**: Minimaler Transportschutz ohne Scope-Expansion in eine neue Infrastruktur.
**Impact**: Burst-Requests auf guarded run-once werden mit `429` fail-closed geblockt.

### A-077: Guarded Audit-Log ist append-only und getrennt von anderen Audit-Pfaden
**Assumption**: Guarded Requests werden nach `artifacts/operator_api_guarded_audit.jsonl` geschrieben; Logging ist best-effort und darf den Request-Pfad nicht destabilisieren.
**Rationale**: Security-first Nachvollziehbarkeit mit klarer Trennung zu Telegram-/Webhook-Audits.
**Impact**: Outcomes (`accepted`, `idempotency_replay`, `rejected`, `failed`) sind transportseitig sichtbar.

### A-078: Bestehende TradingLoop-Guards bleiben alleinige Business-Kontrolle
**Assumption**: Der Router fuehrt keine neue Trading-Business-Logik ein; `paper/shadow`-Erlaubnis und `live`-Fail-Closed bleiben kanonisch im bestehenden TradingLoop-Pfad verankert.
**Rationale**: Keine Parallel-Architektur, kein Business-Drift im API-Layer.
**Impact**: Sprint 44 bleibt reines Transport-/Request-Hardening ohne Trading-Execution-Erweiterung.


## Sprint 44 — Operator API Hardening & Request Governance Assumptions

### A-073: request_id ist UUID4 — server-generiert oder client-gesetzt (validiert)
**Assumption**: Der Server generiert standardmaessig ein UUID4 pro Request via `uuid.uuid4()`. Der Client kann `X-Request-Id` mit einem validen UUID4 senden — dann wird dieser Wert uebernommen. Ungueltige Werte (leer, nicht-UUID, zu lang) werden ignoriert und server-generiert.
**Rationale**: Client-seitige Correlation (z.B. fuer Logging-Aggregation) ist ein Standard-Pattern. Validierung verhindert Injection oder Enumeration via Header.
**Impact**: `get_request_id()` Dependency prueft UUID4-Regex. Jeder Response-Body enthaelt `request_id`. Response-Header `X-Request-Id` ist immer gesetzt.

### A-074: Idempotency-Buffer ist in-memory, nicht persistent — Restart = akzeptiert
**Assumption**: Der Idempotency-Buffer fuer `POST /operator/trading-loop/run-once` ist ein In-memory `OrderedDict` (maxlen=256, FIFO). Er wird nicht in die DB oder ein JSONL-Log persistiert. Ein Prozess-Neustart leert den Buffer.
**Rationale**: Analog zum Telegram-Replay-Buffer (I-316, A-060). Einfachheit vor absoluter Durability. Das Risiko eines Doppel-Submits nach Neustart ist akzeptiert — run-once ist paper/shadow only, kein Live-Impact.
**Impact**: Cluster-Deployments ohne Sticky Session koennen denselben Idempotency-Key auf unterschiedlichen Instanzen akzeptieren. Akzeptiert fuer Sprint 44.

### A-075: Operator API Audit ist von Telegram-Audits streng getrennt
**Assumption**: `artifacts/operator_api_audit.jsonl` (Sprint 44) ist ein separates Log von `artifacts/operator_commands.jsonl` (Telegram Commands) und `artifacts/telegram_webhook_rejections.jsonl` (Telegram Transport). Kein Cross-Write zwischen diesen drei Logs.
**Rationale**: Jede Audit-Surface hat ihre eigene Verantwortlichkeit. Gemischte Logs erschweren Forensik und Monitoring.
**Impact**: Drei getrennte Audit-Streams, drei getrennte JSONL-Dateien. Monitoring-Tools koennen gezielt nach Transport-Typ filtern.

### A-076: HTTP 409 ist der kanonische Response bei Idempotency-Duplikat
**Assumption**: Ein bereits gesehener `X-Idempotency-Key` → HTTP 409 mit `error="duplicate_idempotency_key"`. Kein HTTP 200 "accepted already". Kein HTTP 202. HTTP 409 ("Conflict") ist semantisch korrekt: Request ist strukturell valid, aber im aktuellen State nicht erlaubt.
**Rationale**: HTTP 200/202 wuerde einen Erfolg signalisieren, der nicht eingetreten ist. HTTP 409 zwingt den Client zur expliziten Behandlung von Duplikaten.
**Impact**: Clients muessen fuer `POST /operator/trading-loop/run-once` mit HTTP 409 rechnen wenn sie denselben Key wiederverwenden. Sprint-44-Tests verifizieren dieses Verhalten.

### A-077: error_code ist machine-readable, detail ist human-readable — beide verpflichtend
**Assumption**: Jedes Fehler-Objekt enthaelt `error` (machine-readable Code aus der kanonischen Liste §55.5) UND `detail` (human-readable Beschreibung) UND `request_id`. Kein Feld darf fehlen.
**Rationale**: Machine-readable Codes erlauben automatisierte Alert-Klassifikation. Human-readable Detail erleichtert Operator-Debugging ohne Stack-Trace-Exposure.
**Impact**: `require_operator_api_token` und alle Exception-Handler in `operator.py` muessen auf die strukturierte Form umgestellt werden (Codex-Task).

### A-078: Audit-Log-Fehler sind nicht fatal — never-raise Kontrakt
**Assumption**: Ein Fehler beim Schreiben des Audit-Logs (`artifacts/operator_api_audit.jsonl`) blockiert weder den Request noch den Response. Der Fehler wird auf WARNING-Ebene geloggt. Analog zum Telegram-Command-Audit (§55.4).
**Rationale**: Audit-Log-Infrastruktur darf den Operator-Control-Path nicht degradieren. Besser ein Request ohne Audit als ein blockierter Request.
**Impact**: `_audit_operator_request()` ist in einem try/except gekapselt. Monitoring sollte auf Audit-Write-Fehler alertieren.


## Sprint 44C — Operator API Hardening Korrekturen (A-073C–A-078C)

### A-073C: request_id Format ist req_<hex>, nicht UUID4 (Sprint 44C korrigiert)
**Assumption**: `request_id` hat das Format `req_<uuid4_hex>` (z.B. `req_a1b2c3...`), generiert via `_new_context_id("req")`. Es ist kein reines UUID4. Client kann via `X-Request-ID` Header einen validen alphanumerischen Wert vorgeben (Regex `^[A-Za-z0-9._:-]{1,128}$`).
**Rationale**: §55 definierte fälschlicherweise UUID4. Die Implementierung nutzt ein Prefix-Format für bessere Lesbarkeit in Logs.
**Impact**: Tests prüfen `response.headers["X-Request-ID"].startswith("req_")` — kein UUID4-Format-Check.

### A-074C: Idempotency ist REQUIRED und bietet Replay, nicht nur 409 (Sprint 44C korrigiert)
**Assumption**: `Idempotency-Key` Header (nicht `X-Idempotency-Key`) ist PFLICHT für `POST /operator/trading-loop/run-once`. Fehlt er → 400 `missing_idempotency_key`. Bei gleichem Key + gleichem Payload-Fingerprint → gespeicherte Response zurück (kein zweiter API-Aufruf). Bei gleichem Key + anderem Payload → 409 `idempotency_key_conflict`.
**Rationale**: §55 definierte optionale Idempotency mit einfachem 409. Die Implementierung bietet vollständiges Replay-Pattern mit SHA256-Payload-Fingerprinting.
**Impact**: Clients MÜSSEN immer einen `Idempotency-Key` senden. Deterministische Keys erlauben sichere Retries ohne Doppel-Execution.

### A-075C: Audit-Log ist operator_api_guarded_audit.jsonl, nur guarded POST (Sprint 44C korrigiert)
**Assumption**: Das Audit-Log für Sprint 44 ist `artifacts/operator_api_guarded_audit.jsonl` — ausschliesslich für den guarded POST-Endpoint, NICHT für alle Operator-Requests. Read-only Endpoints werden NICHT in dieses Log geschrieben.
**Rationale**: §55 definierte `artifacts/operator_api_audit.jsonl` für alle authentifizierten Requests. Die Implementierung auditiert gezielt den guarded Pfad — sicherheitsrelevanter und weniger Noise auf read-only.
**Impact**: Drei separate Audit-Streams: `operator_commands.jsonl` (Telegram), `telegram_webhook_rejections.jsonl` (Transport), `operator_api_guarded_audit.jsonl` (guarded API POST).

### A-076C: Error-Shape ist verschachtelt mit correlation_id (Sprint 44C korrigiert)
**Assumption**: Die kanonische Error-Shape ist `{"error": {"code": "<code>", "message": "<msg>", "request_id": "<id>", "correlation_id": "<id>"}, "execution_enabled": false, "write_back_allowed": false}`. Sie ist verschachtelt (nicht flach wie §55 definierte) und enthält `correlation_id` als eigenes Feld.
**Rationale**: §55 definierte flache Shape `{error: "<code>", detail: "<msg>", request_id: "<uuid>"}`. Die Implementierung ist strukturreicher und konsistenter mit API-Design-Standards.
**Impact**: API-Clients müssen `response.json()["detail"]["error"]["code"]` lesen (FastAPI gibt HTTPException.detail als JSON zurück). Tests verifizieren via `_assert_error_payload()`.

### A-077C: Rate-Limiter basiert auf token_fingerprint als operator_subject (Sprint 44C neu)
**Assumption**: Der Rate-Limiter nutzt `operator_subject = "token_<sha256[:16]>"` als Bucket-Key — abgeleitet vom Bearer-Token-Wert (SHA256 der ersten 16 Hex-Zeichen). Dies ermöglicht token-basiertes Rate-Limiting ohne den Token-Wert zu loggen.
**Rationale**: §55 definierte Rate-Limiting nicht. Die Implementierung fügt es als Schutzschicht für den guarded POST hinzu. Der token_fingerprint verhindert Token-Logging bei gleichzeitiger Eindeutigkeit.
**Impact**: Verschiedene Bearer-Token haben separate Rate-Limit-Buckets. Wildcard-Token-Sharing teilt einen Bucket.

### A-078C: _reset_operator_guard_state_for_tests() ist kanonische Test-Reset-Funktion (Sprint 44C neu)
**Assumption**: `_reset_operator_guard_state_for_tests()` ist eine öffentliche Funktion in `operator.py` die `_IDEMPOTENCY_CACHE` und `_GUARDED_RATE_LIMIT_BUCKETS` leert. Sie wird in Test-Fixtures (`monkeypatch` + `tmp_path`) aufgerufen um determistische Tests zu gewährleisten.
**Rationale**: In-memory State zwischen Tests isolieren ohne Prozess-Restart. Die Funktion ist "test surface" — nicht für Production-Use gedacht.
**Impact**: Tests MÜSSEN `_reset_operator_guard_state_for_tests()` in `fixture(autouse=False)` aufrufen wenn sie Idempotency oder Rate-Limiting testen. `_WORKSPACE_ROOT` wird via `monkeypatch.setattr` umgeleitet auf `tmp_path`.
