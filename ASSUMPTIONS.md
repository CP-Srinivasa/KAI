# ASSUMPTIONS.md — KAI Platform

## Current State (2026-03-23)

| Field | Value |
|---|---|
| current_phase | `PHASE 4 (active)` |
| current_sprint | `PH4G_FALLBACK_INPUT_ENRICHMENT_BASELINE (definition mode)` |
| next_required_step | `PH4G_CONTRACT_AND_ACCEPTANCE_FREEZE` |
| baseline | `1538 passed, ruff clean` |

Documented assumptions, constraints, and design decisions.
Last updated: 2026-03-23

---

## PHASE 4 - PH4A assumptions (2026-03-22)

### A-105: PH4A execution is complete enough for formal review
**Assumption**: PH4A execution artifacts are complete and sufficient for final baseline review without adding new source/provider/model work.
**Rationale**: Required outputs (`quality_metrics.json`, `quality_gaps.json`, `operator_baseline_summary.md`) are present for the frozen slice.
**Impact**: Work focus stays on review governance and gap confirmation, not on expanding runtime scope.

### A-106: PH4A review gate must close before PH4B opens
**Assumption**: `PH4A_BASELINE_RESULTS_REVIEW` is a hard gate and must be formally closed before any PH4B opening.
**Rationale**: Prevents converting provisional interpretation into canonical project truth.
**Impact**: `next_required_step` stays fixed to PH4A review; PH4B remains blocked.

### A-107: Tier-3 coverage is the leading bottleneck candidate, not yet final
**Assumption**: `GAP-PH4A-001` (Tier-3 coverage 6.76%) is currently the leading candidate bottleneck, but final confirmation still belongs to review closeout.
**Rationale**: Frozen artifacts indicate weak Tier-3 reach, while additional bottlenecks may still contribute.
**Impact**: Review output must confirm top-3 gaps and primary bottleneck before intervention sprint definition.

---

## PHASE 4 - PH4E freeze assumptions (2026-03-23)

### A-108: PH4E freeze is sufficient to start diagnostic execution
**Assumption**: No further governance reconciliation is required before PH4E execution as long as contract scope/non-goals/acceptance are frozen.
**Rationale**: The PH4D/PH4E state conflict is resolved and all active governance docs carry one canonical sprint/next-step line.
**Impact**: `next_required_step` can advance to `PH4E_EXECUTION_START` without opening new feature scope.

### A-109: PH4E remains diagnostic-only until post-execution review
**Assumption**: PH4E execution must not include any intervention into scoring, thresholds, rules, providers, or source set.
**Rationale**: Early intervention would invalidate PH4A–PH4D comparability and weaken auditability.
**Impact**: PH4E outputs are analysis artifacts and sprint recommendation inputs only.

---

## PHASE 4 - PH4F execution assumptions (2026-03-23)

### A-110: PH4F input completeness is measured on the frozen PH4E paired set only
**Assumption**: PH4F execution uses only the existing paired intersection between `artifacts/ph4a/candidate_rule.jsonl` and `artifacts/ph4b/ph4b_tier3_shadow.jsonl` (69 docs), with no corpus expansion.
**Rationale**: PH4F is a diagnostic continuation of PH4E and must preserve baseline comparability.
**Impact**: PH4F artifacts remain directly comparable to PH4E findings and do not introduce provider/source drift.

### A-111: Default-like rule outputs are treated as input-completeness gaps for diagnostics
**Assumption**: In PH4F diagnostics, persistent rule floor/default patterns (for example `actionable` missing, `market_scope='unknown'`, empty `tags`, frequent floor values in relevance/impact/novelty) are counted as input-completeness deficits rather than immediate tuning targets.
**Rationale**: PH4E established defaults-by-design as root-cause class; PH4F must isolate missing input pathways before any intervention sprint.
**Impact**: PH4F output prioritizes field-level pathway evidence and keeps scoring/rule changes out of scope.

### A-112: PH4F closeout must precede any PH4G activation
**Assumption**: Even with PH4G as likely candidate, PH4G remains inactive until PH4F results review is completed and documented.
**Rationale**: Starting intervention before closeout would weaken causal traceability from PH4F diagnostics.
**Impact**: `next_required_step` stays `PH4F_RESULTS_REVIEW_AND_PH4G_SELECTION`; no PH4G execution in parallel.

### A-113: PH4G should stay intervention-minimal if selected
**Assumption**: If PH4G is activated, the first pass should be narrow and limited to highest-leverage fallback input pathways.
**Rationale**: Broad multi-field intervention would reduce interpretability and make MAE movement harder to attribute.
**Impact**: PH4G candidate scope remains tight and measurement-first.

### A-114: PH4G is the first intervention sprint after the PH4A-PH4F diagnostic arc
**Assumption**: PH4G is intentionally the first intervention sprint, following a completed diagnostic sequence from PH4A through PH4F.
**Rationale**: Starting intervention only after a frozen diagnostic arc preserves causal traceability.
**Impact**: PH4G remains constrained to measured intervention rather than new diagnostics or broad feature work.

### A-115: PH4G contract freeze must limit first pass to 1-3 intervention points
**Assumption**: The first PH4G iteration must modify only `1-3` fallback input pathways.
**Rationale**: Too many simultaneous edits would make outcome attribution unreliable.
**Impact**: Contract/acceptance freeze must explicitly enforce narrow intervention count.

### A-116: PH4G should prioritize actionable and relevance-related fallback enrichment first
**Assumption**: Highest-leverage first pass is expected in actionable and relevance-related fallback inputs.
**Rationale**: PH4F showed complete actionable absence and frequent relevance floor defaults.
**Impact**: PH4G definition favors these pathways before broader fallback enrichment.

---

## PHASE 3 — S50D assumptions (2026-03-22)

### A-101: Doc hygiene scope must stay structural â€” no content changes
**Assumption**: S50D applies document-structure rules only (navigation headers, section ordering, length limits). It must not alter the semantic content of any governance decision, assumption, or contract entry.
**Rationale**: Mixing structural cleanup with content changes defeats the purpose of a hygiene sprint and risks silent governance drift.
**Impact**: S50D tasks are rejected if they touch product logic, contracts text, or classification decisions.

### A-102: Large governance docs are the primary onboarding bottleneck post-S50C
**Assumption**: With the CLI canonical set frozen and S50C closed, the largest remaining clarity gap is navigability of TASKLIST.md, AGENTS.md, docs/contracts.md, and DECISION_LOG.md â€” all of which have grown to thousands of lines.
**Rationale**: Long flat files without section navigation slow down future agents and operators who need to find the current sprint state quickly.
**Impact**: S50D defines and applies a minimal structural standard (e.g., ToC header, per-sprint H2 anchors, summary tables) to these four docs.

### A-103: Structure rules must be contract-bound, not scattered
**Assumption**: Canonical structure rules for large governance docs are anchored in `docs/contracts.md` section 66.
**Rationale**: A single contract anchor avoids parallel rule interpretations across governance docs.
**Impact**: S50D reviews validate rule compliance against section 66 first; deviations are treated as governance drift.

### A-104: Contracts and tasklist are the first high-payoff S50D targets
**Assumption**: During S50D, `TASKLIST.md` and `docs/contracts.md` are prioritized before broader multi-document cleanup.
**Rationale**: Freezing structure rules and applying them to the operator-facing sprint task surface yields the fastest clarity gain with lowest drift risk.
**Impact**: Broad edits in AGENTS, ASSUMPTIONS, and intelligence architecture follow only after the freeze and initial TASKLIST/contracts pass.

---

## PHASE 3 â€” S50A review findings (2026-03-22)

### A-099: The 15 provisional CLI commands represent the Phase-9-to-24 research pipeline, not the Phase-2 operator baseline
**Assumption**: The provisional CLI set (`backtest-run`, `benchmark-companion`, `brief`, `evaluate`, `shadow-report`, `signals`, `watchlists`, etc.) represents legitimate platform functionality from the research/training pipeline sprints. They are not abandoned or broken commands.
**Rationale**: These commands were built in Sprints 9â€“24 for the ML training, evaluation, and signal research workflow. They are not part of the Phase-2 operator flow but are registered and functional.
**Impact**: S50B must classify them explicitly (research-platform canonical / aspirational / retain as provisional). No deletion before classification.

### A-100: S50A freeze does not imply all governance docs are in their final state
**Assumption**: Freezing the S50A inventory means the classification is locked, not that all governance docs are finalized. Residual doc updates may follow in S50A_FINAL_REVIEW_AND_FREEZE.
**Rationale**: The inventory is the authority artifact; governance docs reference it, but they may still evolve during the freeze gate step.
**Impact**: S50A is complete when inventory + Claude review + Antigravity review are all done, not when every doc reference is perfect.

## PHASE 3 â€” S50A assumptions (2026-03-22)

### A-096: Canonical path inventory must precede any renaming or removal
**Assumption**: Before any surface renaming, alias cleanup, or superseded-code removal in S50, a complete canonical path inventory must exist.
**Rationale**: Acting on naming drift without an authoritative inventory risks silent breakage of aliases that are still in use by operators or test fixtures.
**Impact**: S50A produces the inventory before S50B+ acts on it.

### A-097: The inventory is documentation-only â€” no runtime contract change
**Assumption**: `CANONICAL_SURFACE_INVENTORY.md` is an audit artifact, not a runtime contract. It does not replace or extend `docs/contracts.md`.
**Rationale**: Mixing audit artifacts with runtime contracts causes governance confusion.
**Impact**: Changes derived from S50A findings go through the normal contract-definition process in a later sub-sprint.

### A-098: Alias entries must not be deleted before canonical successors are confirmed
**Assumption**: Any alias or superseded entry flagged in S50A must have its canonical successor confirmed before removal is scheduled.
**Rationale**: Premature removal of aliases can break operator workflows that have not yet been migrated.
**Impact**: S50A flags; a later S50 sub-sprint decides and acts.

### A-099: S50A requires formal review freeze before S50B
**Assumption**: Even with inventory delivered, S50A is not complete until final review and freeze are documented.
**Rationale**: A freeze gate prevents premature refactoring on an unreviewed classification map.
**Impact**: `S50A_FINAL_REVIEW_AND_FREEZE` is mandatory before S50B.

### A-100: Provisional CLI set is the primary freeze decision
**Assumption**: The 15 provisional CLI commands are the highest-priority governance decision inside S50A freeze.
**Rationale**: Unresolved provisional paths are the largest remaining source of future naming drift.
**Impact**: S50A freeze requires explicit disposition plan for provisional commands (promote/alias/supersede/retain-provisional).

## PHASE 3 kickoff assumptions (2026-03-22)

### A-082: Consolidation yields higher value than immediate feature depth
**Assumption**: After formal PH2 closure, the next highest leverage is canonical architecture and documentation consolidation.
**Rationale**: Operator flow is accepted across API, dashboard, telegram, and CLI; remaining value now comes from clearer shared truth.
**Impact**: Sprint 50 is consolidation-first and feature-light by design.

### A-083: Phase 3 should precede any Phase-4 expansion
**Assumption**: Phase 4 should not open before S50 consolidation acceptance.
**Rationale**: Starting deeper feature work on an unconsolidated baseline increases drift and governance cost.
**Impact**: Phase-4 work remains blocked until Phase-3 baseline is explicitly accepted.

### A-084: S50 consolidation must stay narrow to avoid overengineering
**Assumption**: S50 should focus on clarity, naming consistency, and canonical documentation only.
**Rationale**: Broad technical refactors in this sprint would dilute the intended close-control scope.
**Impact**: Changes that introduce new business logic or second backbones are out of scope.

### A-085: S50A inventory-first is the smallest meaningful first step
**Assumption**: The first practical step in S50 is a canonical-path inventory before any refactoring work.
**Rationale**: Consolidation without inventory leaves naming and ownership ambiguity unresolved.
**Impact**: S50A path inventory is required before broader cleanup work.

### A-086: Canonical/alias/superseded boundaries are mandatory for team operation
**Assumption**: Team onboarding and safe maintenance require explicit path classification across surfaces.
**Rationale**: Implicit path knowledge does not scale and causes governance drift.
**Impact**: Inventory outputs must classify paths and expose provisional entries.

### A-087: Refactoring before inventory is treated as risk-increasing drift
**Assumption**: Any broad cleanup before S50A classification can reintroduce parallel paths.
**Rationale**: Without a frozen baseline map, refactors may preserve or create hidden forks.
**Impact**: S50A is a hard precondition for further phase-3 consolidation steps.

---

## PHASE 1 close-out assumptions (2026-03-22)

### A-079: P2 CI/CD Mindestschutz ist materiell erfuellt
**Assumption**: Der CI/CD Mindestschutz gilt als weitgehend erfuellt (all-branch triggers, concurrency cancel-in-progress, blocking mypy, ruff via dev setup, pip-audit im Security-Pfad).
**Rationale**: Diese Basis ist schon geliefert und darf nicht als "pending" behandelt werden.
**Impact**: Prioritaet verschiebt sich in Richtung E2E-Klarheit und Operator-Nutzbarkeit.

### A-080: Der naechste groesste Produktgewinn liegt in E2E- und Onboarding-Klarheit
**Assumption**: Zusatztiefe in Technik bringt aktuell weniger Produktwirkung als ein klarer kanonischer E2E-Paper-Workflow plus Operator-Onboarding.
**Rationale**: Ohne klaren Bedienpfad bleibt technische Tiefe fuer Team und Operator nur eingeschraenkt nutzbar.
**Impact**: PH1_CLOSEOUT_E2E priorisiert Dokumentation, Workflow-Klarheit und menschliche Nutzbarkeit vor neuen Features.

### A-081: Reine Technik-Sprints sind nachrangig bis PH1 Exit-Kriterien explizit geschlossen sind
**Assumption**: Weitere tief technische Erweiterungen werden hinter dokumentierte Phase-1-Closure gestellt.
**Rationale**: Produktreife braucht expliziten Exit, nicht nur technischen Umfang.
**Impact**: Neue technische Arbeit soll erst nach synchronisiertem README/RUNBOOK/SECURITY/ARCHITECTURE/ASSUMPTIONS und kanonischem E2E-Pfad geplant werden.

## Sprint 38 Addendum

### A-045: /positions verwendete den kanonischen Collector-Read-Proxy (superseded)
**Assumption**: Diese Ãœbergangsannahme galt bis zur EinfÃ¼hrung des finalen Portfolio-Read-Surface in Sprint 40.
**Rationale**: Kein zweiter Positions- oder Trading-Stack wÃ¤hrend der Ãœbergangsphase.
**Impact**: Ab Sprint 40 ersetzt durch A-040 bis A-042 (`get_paper_positions_summary` / `get_paper_exposure_summary`).
**Hinweis**: UrsprÃ¼nglich als A-032 nummeriert (hook-added Sprint 38); umbenannt in A-045 zur KonfliktauflÃ¶sung mit Sprint-38-Sektion A-032.

### A-046: /approve und /reject validieren decision_ref fail-closed
**Assumption**: Telegram `/approve` und `/reject` akzeptieren nur `decision_ref` im Format `dec_<12 lowercase hex>`.
**Rationale**: Ein enges, kanonisches Referenzformat reduziert Mehrdeutigkeit und blockiert fehlerhafte oder unvollstÃ¤ndige Operator-Eingaben auf dem Audit-Pfad.
**Impact**: Fehlende oder ungÃ¼ltige `decision_ref` werden sauber abgewiesen (fail-closed); der Pfad bleibt append-only audit-only ohne Execution-Seiteneffekt.
**Hinweis**: UrsprÃ¼nglich als A-033 nummeriert (hook-added Sprint 38); umbenannt in A-046 zur KonfliktauflÃ¶sung mit Sprint-38-Sektion A-033.

---

## Sprint 39 Addendum

### A-037: CoinGecko read path bleibt Spot-only und read-only
**Assumption**: Der erste externe Adapter nutzt ausschlieÃŸlich CoinGecko-Spot-Read-Endpunkte (`/simple/price`, `/coins/{id}/ohlc`) und greift nicht auf Order-, Account- oder Portfolio-Endpunkte zu.
**Rationale**: Sprint 39 ist ein reiner External-Data-Sprint ohne Execution-Erweiterung.
**Impact**: Keine Trading-Semantik, keine Broker-/Account-Aktionen, keine write-back Side Effects.

### A-038: UnterstÃ¼tzte Symbol-Quotes sind auf USD/USDT begrenzt
**Assumption**: FÃ¼r den ersten sicheren Adapterpfad werden nur Symbole mit Quote `USD` oder `USDT` akzeptiert (z. B. `BTC/USDT`, `ETH/USD`, `BTC` -> `BTC/USDT`).
**Rationale**: Begrenzter Scope reduziert Mapping-Fehler und verhindert implizite WÃ¤hrungsumrechnungen.
**Impact**: Nicht unterstÃ¼tzte Quotes werden fail-closed als `available=False` mit Fehlergrund zurÃ¼ckgegeben.

### A-039: Stale Market Data bleibt sichtbar, aber klar markiert
**Assumption**: Ein vorhandener Preis mit alter Source-Timestamp wird als `is_stale=True` markiert und als read-only Snapshot zurÃ¼ckgegeben statt still verworfen.
**Rationale**: Operator-Surfaces brauchen Sichtbarkeit Ã¼ber Datenalter; die Bewertung bleibt transparent und auditierbar.
**Impact**: Snapshot enthÃ¤lt immer `freshness_seconds`, `is_stale`, `available`, `error`; Consumer kÃ¶nnen fail-closed entscheiden, ohne versteckte Datenverluste.

---

## Sprint 40 Addendum

### A-040: Paper Portfolio Read Surface bleibt rein read-only
**Assumption**: Portfolio-/Positions-/Exposure-Surfaces lesen ausschlieÃŸlich aus append-only Audit- und Marktdaten-Read-Pfaden und mutieren keinen Execution- oder Broker-State.
**Rationale**: Sprint 40 ist ein Operator-Read-Sprint ohne Trading-Erweiterung.
**Impact**: Alle Responses bleiben mit `execution_enabled=False` und `write_back_allowed=False` gekennzeichnet.

### A-041: Portfolio-State wird per Audit-Replay projiziert, nicht aus Live-Engine-Referenzen
**Assumption**: Der kanonische Zustand wird aus `artifacts/paper_execution_audit.jsonl` rekonstruiert, nicht aus in-memory Engine-Objekten.
**Rationale**: Auditierbarkeit und deterministische Reproduzierbarkeit sind wichtiger als implizite Runtime-Kopplung.
**Impact**: Leeres/missing Audit ergibt leeres Portfolio; inkonsistente Audit-Zeilen fÃ¼hren fail-closed zu `available=False`.

### A-042: Mark-to-Market ist optional und degradierbar
**Assumption**: Mark-to-market wird Ã¼ber den bestehenden Market-Data-Read-Path angereichert; stale/unavailable Preise degradieren die Exposure-Auswertung statt Execution auszulÃ¶sen.
**Rationale**: Evidence before action und fail-closed bei unvollstÃ¤ndiger Bewertungsgrundlage.
**Impact**: Stale/fehlende Preise werden explizit markiert; vollstÃ¤ndig unbepreiste offene Positionen setzen `available=False`.

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
**Assumption**: Position size = (equity Ã— max_risk_per_trade_pct) / (entry - stop_loss).
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

### A-010: Telegram Alert Channel â‰  Operator Bot
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
Validation happens at startup â€” missing required settings fail fast.

### D-002: Frozen Dataclasses for Immutable Models
Risk results, orders, fills, market data points â€” all frozen.
Prevents accidental mutation of financial records.

### D-003: JSONL Audit Trail
All audit records written as newline-delimited JSON.
Append-only. Never modified after write. Supports streaming analysis.

### D-004: asyncio Throughout
All I/O operations (HTTP calls, DB, file writes) use async/await.
Synchronous adapters are wrapped. Never block the event loop.

### D-005: Fail-Closed on Risk Violations
If any risk gate fails â†’ order rejected. Never fail-open.
Unknown errors â†’ order rejected.

---

---

## Sprint 35 â€” Backtest Engine Assumptions

### A-012: Long-Only Mode by Default
**Assumption**: `BacktestConfig.long_only=True` â€” bearish signals are skipped.
**Rationale**: Paper trading begins with long-only exposure. Short-selling requires
  additional risk modeling and is architecturally reserved for later phases.
**Impact**: direction_hint="bearish" â†’ outcome="skipped_bearish" when long_only=True.
**Override**: `BacktestConfig(long_only=False)` enables bearish (sell) orders.

### A-013: Leverage Always 1x in Backtest
**Assumption**: `max_leverage=1.0` hardcoded in BacktestEngine (I-231).
**Rationale**: Safety principle. Leverage magnifies losses and must not be implicit.
**Impact**: Position sizing is constrained to at most 1x equity value.
**Override**: Only when a live adapter with real margin accounting is connected.

### A-014: Stop-Loss and Take-Profit Derived from Config
**Assumption**: SL = entry_price Ã— (1 - stop_loss_pct/100), TP = SL_distance Ã— multiplier.
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

### A-022: Prompt-Pack ist kanonische Governance-Basis fÃ¼r alle Agenten
**Assumption**: KAI_SYSTEM_PROMPT.md, KAI_DEVELOPER_PROMPT.md, KAI_EXECUTION_PROMPT.md sowie die Adapter-Dateien sind die verbindliche Grundlage fÃ¼r alle Agenten (Claude Code, Codex, Antigravity).
**Rationale**: Ein konsistentes Prompt-Pack verhindert Architektur-Drift, widersprÃ¼chliche Implementierungen und sicherheitskritische Abweichungen zwischen Agenten-Sessions.
**Impact**: Vor jeder Arbeitssession muss das Prompt-Pack gelesen werden. Kein Adapter ersetzt System- oder Developer-Prompt.
**Einsatzreihenfolge**: System Prompt â†’ Developer Prompt â†’ Execution Prompt â†’ Agent-Adapter.

### A-023: Rebaseline-Phase geht Feature-Sprints vor
**Assumption**: Rebaseline (Harmonisierung, Prompt-Governance, Dokumenten-Konsistenz) muss vollstÃ¤ndig abgeschlossen sein, bevor neue Feature-Sprints beginnen.
**Rationale**: Inkonsistente Governance-Basis erzeugt kumulierende Architektur-Drift und Security-LÃ¼cken.
**Impact**: WÃ¤hrend einer Rebaseline-Phase werden keine neuen Produktivfeatures geÃ¶ffnet.

---

## Out of Scope fÃ¼r Phase 1

- Live exchange connections (Binance, Coinbase, etc.)
- Real-time market data streaming
- Multi-asset portfolio optimization
- ML-based signal generation
- Voice/avatar interfaces (architektonisch vorbereitet, nicht aktiv)
- Distributed deployment (single-process in Phase 1)
- Automatische Telegram /approve und /reject Execution (A-017)

---

## Sprint 37 â€” Runtime Schema & Decision Backbone Assumptions

### A-024: CONFIG_SCHEMA.json bindet als Runtime-Projektion auf AppSettings
**Assumption**: `CONFIG_SCHEMA.json` wird Ã¼ber eine kanonische Runtime-Projektion aus `AppSettings` plus konservativen Sicherheitsdefaults fÃ¼r noch nicht explizit modellierte Vertragsfelder validiert.
**Rationale**: Der bestehende Settings-Stack bleibt die einzige Runtime-Quelle. Fehlende Vertragsfelder werden dokumentiert ergÃ¤nzt, statt eine zweite Settings-Architektur aufzubauen.
**Impact**: `AppSettings` validiert jetzt seinen Runtime-Vertrag gegen `CONFIG_SCHEMA.json`; Risk- und Execution-Drift wird fail-closed erkannt.

### A-025: Decision Journal konvergiert auf DecisionRecord als einzigen Backbone
**Assumption**: `app/decisions/journal.py` bleibt als KompatibilitÃ¤tsoberflÃ¤che erhalten, projiziert intern aber ausschlieÃŸlich auf den kanonischen `DecisionRecord`.
**Rationale**: Ein zweiter Decision-Modellpfad schwÃ¤cht Auditierbarkeit, Schema-Bindung und sichere Weiterentwicklung.
**Impact**: Journal-Zeilen werden gegen `DECISION_SCHEMA.json` normalisiert und fail-closed validiert; Legacy-Zeilen sind nur noch Eingabeformat, nicht mehr eigener Backbone.

### A-026: runtime_validator ist der einzige aktive Runtime-Schema-Pfad
**Assumption**: `app/schemas/runtime_validator.py` ist der einzige aktive Runtime-Validator fÃ¼r Payload-Schema-Binding; `app/core/settings.py` nutzt nur Wrapper auf diesen Pfad.
**Rationale**: Ein einzelner Validator-Pfad reduziert Drift, vermeidet konkurrierende Fehlersemantik und hÃ¤lt die Guardrails eindeutig.
**Impact**: Runtime-Config- und Decision-Payloads werden zentral Ã¼ber denselben Validator geprÃ¼ft; `app/core/schema_binding.py` bleibt nur Audit-/Alignment-Pfad.

### A-024: DECISION_SCHEMA.json ist Runtime-Contract, nicht Dokumentation
**Assumption**: `DECISION_SCHEMA.json` wird bei jeder `DecisionRecord`-Instanziierung gegen `Draft202012Validator` validiert.
**Rationale**: Dekorative Schema-Dateien ohne Runtime-Binding schaffen eine falsche Sicherheitsillusion.
**Impact**: Kein Payload passiert `append_decision_record_jsonl()` ohne Schema-Validation. SchlÃ¤gt die Validation fehl, wird der Payload abgelehnt (fail-closed).
**Implementierung**: `DecisionRecord._validate_safe_state()` ruft `validate_json_schema_payload(self.to_json_dict(), schema_filename="DECISION_SCHEMA.json", ...)` auf.

### A-025: DecisionInstance ist TypeAlias fÃ¼r DecisionRecord
**Assumption**: `DecisionInstance` im `app.decisions.journal`-Modul ist ab Sprint 37 ein `TypeAlias` fÃ¼r `DecisionRecord`. Kein eigenstÃ¤ndiges `DecisionInstance`-Dataclass existiert mehr.
**Rationale**: Zwei konkurrierende Entscheidungsmodelle mit unterschiedlichen Enum-Werten erzeugen Architektur-Drift und falsch positive Tests.
**Impact**: Alle CLI/MCP-Wege durch `create_decision_instance()` erzeugen kanonische `DecisionRecord`-Objekte. Legacy-Rows werden beim Laden normalisiert.

---

## Sprint 38 â€” Telegram Command Hardening Assumptions

### A-027: Telegram-Kommandos sind keine Execution-Trigger
**Assumption**: Kein Telegram-Kommando Ã¶ffnet einen Live-Execution-Pfad. `/approve` und `/reject` sind ausschlieÃŸlich Audit-EintrÃ¤ge ohne Execution-Seiteneffekt.
**Rationale**: Telegram ist ein unsicherer Kanal mit limitierter Authentifizierung (nur chat_id-basiert). Execution-Trigger Ã¼ber diesen Kanal ohne stÃ¤rkere Authentifizierung wÃ¼rden das Sicherheitsmodell untergraben.
**Impact**: Alle Telegram-Handler geben nach Audit-Log-Eintrag eine BestÃ¤tigungsnachricht zurÃ¼ck â€” niemals einen Order- oder Execution-Seiteneffekt.

### A-028: Telegram-Bot liest Risk-State via MCP, nicht via RiskEngine-Private-Attribute
**Assumption**: `_cmd_risk` liest keinen RiskEngine-State direkt. Der kanonische Pfad ist `get_protective_gate_summary()` (MCP canonical read tool). Keine privaten RiskEngine-Attribute werden im Telegram-Bot referenziert.
**Rationale**: Direct private attribute access koppelt den Bot an Implementierungsdetails. MCP canonical read surfaces sind der einzige stabile, auditierbare Lesepfad fuer Operator-Surfaces.
**Impact**: `_cmd_risk` â†’ `_get_protective_gate_summary()` â†’ `get_protective_gate_summary()` (MCP). Kein `RiskSnapshot`-Modell noetig. Sprint-38-Ursprungsannahme (get_risk_snapshot()) durch Sprint 38C praezisiert.
**Sprint 38C**: `_READ_ONLY_COMMANDS` und `_GUARDED_AUDIT_COMMANDS` sind disjunkt. `incident` wurde aus `_READ_ONLY_COMMANDS` entfernt (Klassifikationskonflikt bereinigt).

### A-029: guarded_write Kommandos sind im dry_run=True Default inaktiv
**Assumption**: `/pause`, `/resume`, `/kill` dÃ¼rfen im `dry_run=True` (Default) keine State-Mutation auslÃ¶sen. Sie antworten mit "[DRY RUN]"-Prefix und nehmen keine Aktion vor.
**Rationale**: dry_run=True ist die sichere Default-Konfiguration fÃ¼r alle Telegram-Kommandos. Versehentliche Aktivierung in Nicht-Produktions-Umgebungen darf keine operativen Konsequenzen haben.
**Impact**: Alle drei guarded_write Handler haben explizite `if self._dry_run: return` Guards vor jeder Mutation. Tests MÃœSSEN das dry_run-Verhalten verifizieren.

### A-030: /kill erfordert Zwei-Schritt-BestÃ¤tigung
**Assumption**: Ein einzelnes `/kill` aktiviert den Kill-Switch NICHT. Es setzt nur `_pending_confirm[chat_id] = "kill"`. Erst ein zweites `/kill` vom selben chat_id triggert `trigger_kill_switch()`.
**Rationale**: Accidental kill-switch activation bei Tipp- oder Verbindungsfehler muss ausgeschlossen sein. Die confirmation ist per-chat_id um Cross-User-Confirmation zu verhindern.
**Impact**: `_pending_confirm` dict ist pro-chat_id. Wird nach Confirmation konsumiert (pop). Test MUSS single-/kill und double-/kill getrennt prÃ¼fen.

### A-031: Telegram-Bot-Tests sind Pflicht â€” kein Produktivcode ohne Tests
**Assumption**: `TelegramOperatorBot` MUSS durch â‰¥20 Unit-Tests in `tests/unit/test_telegram_bot.py` abgedeckt sein, bevor Sprint 38 als abgeschlossen gilt.
**Rationale**: Der Bot ist ein Operator-Sicherheitskanal. Ungetesteter Operator-Code widerspricht dem Auditierbarkeits-Prinzip (PrioritÃ¤t 5) und dem Fail-Closed-Prinzip.
**Impact**: Sprint 38 ist NICHT abgeschlossen ohne grÃ¼ne Test-Suite fÃ¼r TelegramOperatorBot. Codex implementiert die Tests als Sprint-38-Task 38.7.

### A-032: Market-Data-Adapter sind ausschlieÃŸlich read-only
**Assumption**: Kein `BaseMarketDataAdapter`-Subtyp darf Orders senden, Positionen Ã¶ffnen, oder Broker-State mutieren. Der Market-Data-Layer ist eine passive Datenquelle ohne Schreibzugriff auf Broker-Systeme.
**Rationale**: Lese- und Schreibpfade zu Brokern MÃœSSEN getrennt sein. Ein Adapter, der lesen und schreiben kann, verletzt das Principle of Least Privilege und Ã¶ffnet unbeabsichtigte Execution-Pfade.
**Impact**: Alle Adapter-Konstruktoren DÃœRFEN KEINE Broker-Credentials fÃ¼r Schreibzugriff initialisieren. Jede Methode im Adapter ist idempotent und seiteneffektfrei bezÃ¼glich Broker-State.

### A-033: MockMarketDataAdapter ist der Pflicht-Default fÃ¼r Paper-Trading und Tests
**Assumption**: Solange kein echter externer Adapter konfiguriert ist, MUSS `MockMarketDataAdapter` als Default verwendet werden. Der Mock hat kein externes Netzwerk, keinen Zufall, keine AbhÃ¤ngigkeiten.
**Rationale**: Paper-Trading und Tests DÃœRFEN NICHT von externen Provider-APIs abhÃ¤ngen. Flaky Tests durch Netzwerk-AusfÃ¤lle oder API-Rate-Limits sind nicht akzeptabel.
**Impact**: `TradingLoop` und Backtest-Setups MÃœSSEN `MockMarketDataAdapter` als default in Testumgebungen akzeptieren. A-003 bestÃ¤tigt: MockAdapter ist der Default-Adapter (vgl. contracts.md Â§50.4).

### A-034: Veraltete oder fehlende Marktdaten sind fail-closed â€” kein Auto-Routing
**Assumption**: Wenn `get_market_data_point()` `None` zurÃ¼ckgibt oder `is_stale=True`, Ã¼berspringt der TradingLoop den Zyklus fÃ¼r dieses Symbol. Kein automatischer Wechsel auf einen anderen Provider.
**Rationale**: Auto-Routing zwischen Providern bei Datenproblemen wÃ¼rde implizit unterschiedliche Datenquellen mit unterschiedlicher QualitÃ¤t mischen. Das ist eine versteckte Entscheidung mit Execution-Konsequenzen.
**Impact**: `TradingLoop` behandelt `None` und `is_stale=True` identisch: Zyklus wird als `no_market_data:symbol` aufgezeichnet. Kein Signal, kein Order, kein Alarm. Kein Retry-Loop.

### A-035: BacktestEngine hat keine interne Adapter-AbhÃ¤ngigkeit
**Assumption**: `BacktestEngine.run(signals, prices)` empfÃ¤ngt Marktpreise als `dict[str, float]` (pre-fetched). Innerhalb von `run()` wird kein Adapter aufgerufen.
**Rationale**: Deterministischer Backtest-Replay erfordert, dass keine Live-Daten in `run()` injiziert werden kÃ¶nnen. Der Caller ist verantwortlich fÃ¼r die DatenqualitÃ¤t, nicht der BacktestEngine.
**Impact**: Backtest-Tests kÃ¶nnen mit statischen `prices`-Dicts arbeiten â€” vollstÃ¤ndig offline. `MockMarketDataAdapter` kann auÃŸerhalb von `run()` fÃ¼r Testdaten verwendet werden.

### A-036: Provider-Health-Check ist ein Monitoring-Signal, kein Routing-Trigger
**Assumption**: `health_check()` returning `False` aktiviert KEINEN anderen Provider und stoppt KEIN Trading. Es ist ein Liveness-Indikator, der in der Operator-Surface `/health` erscheinen kann.
**Rationale**: Kill-Switch-AutoritÃ¤t liegt beim RiskEngine, nicht beim Market-Data-Layer. Eine automatische Trading-Unterbrechung aufgrund eines Health-Checks wÃ¤re ein versteckter Execution-Pfad.
**Impact**: `health_check()` â†’ `False` wird geloggt. Es kann in MCP `get_provider_health()` surfaced werden. Es darf KEINEN RiskEngine-State Ã¤ndern und KEINEN anderen Adapter aktivieren.
**Override**: Nicht verhandelbar â€” die Konvergenz ist die Grundlage fÃ¼r zukÃ¼nftige Execution-Erweiterungen.

---

## Sprint 40 â€” Paper Portfolio Read Surface Assumptions

### A-040: PortfolioSnapshot ist der einzige erlaubte Portfolio-Lesepfad nach aussen
**Assumption**: `PaperPortfolio` (mutable) wird niemals direkt an Operator-Surfaces, MCP-Tools, CLI-Commands oder Telegram-Handler weitergegeben. Nur `PortfolioSnapshot` (frozen, aus `app/execution/portfolio_read.py`) darf diese Grenze ueberschreiten. `app/execution/portfolio_surface.py` ist ein interner TradingLoop-Helper und kein Operator-Surface.
**Rationale**: Mutable State in Operator-Surfaces erzeugt versteckte Kopplung. Der frozen `PortfolioSnapshot` mit `execution_enabled=False` ist die einzig sichere Grenze.
**Impact**: Alle MCP-Tools und CLI-Commands, die Portfolio-State zeigen, gehen durch `build_portfolio_snapshot()` aus `portfolio_read.py`. Kein direkter Zugriff auf `PaperExecutionEngine._portfolio` von aussen.

### A-041: Kanonische Source of Truth fuer Portfolio-State ist das Audit-JSONL
**Assumption**: `artifacts/paper_execution_audit.jsonl` ist die einzige kanonische Quelle fuer Portfolio-State-Rekonstruktion. `build_portfolio_snapshot()` in `app/execution/portfolio_read.py` replayed `order_filled`-Events.
**Rationale**: Die MCP-Schicht kann nicht auf laufende Engine-Instanzen zugreifen. Das JSONL ist persistent, append-only, auditierbar â€” identisch zum Pattern von DecisionRecord, SignalHandoff etc.
**Impact**: Portfolio-State-Rekonstruktion ist deterministisch und idempotent. Kein Singleton, kein Shared Memory, kein Inter-Process-Zugriff noetig.

### A-042: Mark-to-Market ist optional und fail-closed per Position (PositionSummary)
**Assumption**: MtM-Bereicherung schlaegt fail-closed per `PositionSummary`: `market_data_available=False` oder `market_data_is_stale=True` â†’ `market_price=None`, `market_value_usd=None`, `unrealized_pnl_usd=None`. Der gesamte `PortfolioSnapshot` bleibt verfuegbar.
**Rationale**: Marktdaten koennen temporaer unavailable sein. Der Portfolio-Snapshot darf nie blockiert werden, nur weil ein Preis nicht abgerufen werden konnte.
**Impact**: `PortfolioSnapshot.available=False` nur wenn ALLE Positionen unbepreist sind. Partiell bepreiste Snapshots haben `available=True` mit entsprechenden `PositionSummary`-Flags.

### A-043: ExposureSummary (portfolio_read.py) hat keinen eigenstaendigen Datenpfad
**Assumption**: `ExposureSummary` in `app/execution/portfolio_read.py` ist ausschliesslich eine Projektion von `PortfolioSnapshot`. `build_exposure_summary(snapshot)` nimmt einen `PortfolioSnapshot` entgegen und erzeugt kein eigenstaendiges Market-Data-Fetch.
**Rationale**: Einheitlicher Datenpfad: JSONL â†’ `build_portfolio_snapshot()` â†’ `PortfolioSnapshot` â†’ `build_exposure_summary()`. Kein paralleler Datenpfad.
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

## Sprint 42 â€” Telegram Webhook Hardening Assumptions

### A-056: Webhook-Layer ist reine Transport-HÃ¤rtung, keine Business-Logik (Sprint 42D korrigiert)
**Assumption**: `app/messaging/telegram_bot.py` enthÃ¤lt den vollstÃ¤ndigen Webhook-Transport-Guard (integriert in `TelegramOperatorBot`). Keine separaten Webhook-Module. Transport-Logik (`process_webhook_update()`) ist strikt getrennt von Business-Logik (`process_update()`).
**Rationale**: Sprint 42 plante fÃ¤lschlicherweise ein separates Legacy-Webhook-Modul. Die Implementierung integrierte den Guard direkt â€” einfacher, weniger Drift-Risiko. A-063 dokumentiert dies detaillierter.
**Impact**: `TelegramOperatorBot.process_update()` bleibt unverÃ¤ndert und wird nur bei `accepted=True` aufgerufen.

### A-057: webhook_secret_token leer/None = Webhook fail-closed (Sprint 42D korrigiert)
**Assumption**: Wenn `TelegramOperatorBot(webhook_secret_token=None/""...)` initialisiert wird, ist `webhook_configured == False`. Jeder `process_webhook_update()`-Aufruf â†’ `rejection_reason="webhook_secret_not_configured"`. Kein Webhook-Request ohne konfigurierten Secret.
**Rationale**: Sprint 42 plante fÃ¤lschlicherweise `OperatorSettings.telegram_webhook_secret` (nicht in OperatorSettings). Der Secret wird als Konstruktor-Parameter Ã¼bergeben; der Caller ist verantwortlich, ihn aus `OPERATOR_TELEGRAM_WEBHOOK_SECRET` zu lesen.
**Impact**: `webhook_signature_required: True` in der Runtime-Config bleibt ein deklaratives Flag â€” die operative Durchsetzung liegt beim `webhook_secret_token`-Parameter.

### A-058: Secret-Token-Vergleich muss constant-time sein (Sprint 42)
**Assumption**: `hmac.compare_digest(provided_secret, configured_secret)` ist der einzig erlaubte Vergleich. Kein direktes `==` zwischen Secret-Strings.
**Rationale**: Timing-Angriffe auf String-Vergleiche sind reell. Constant-time-Vergleich ist Standard fÃ¼r jede Secret-Validierung.
**Impact**: Implementierung nutzt `hmac.compare_digest` aus der Python-Stdlib (kein externer Dependency).

### A-059: edited_message ist per Default erlaubt â€” Replay-Schutz via update_id (Sprint 42C korrigiert)
**Assumption**: `edited_message`-Updates sind in `_WEBHOOK_ALLOWED_UPDATES_DEFAULT = ("message", "edited_message")` enthalten und werden per Default zugelassen. Das Replay-Risiko wird durch update_id-Deduplication (A-060) mitigiert, nicht durch Typ-Filterung. Operatoren kÃ¶nnen `webhook_allowed_updates=("message",)` setzen, um `edited_message` auszuschliessen.
**Rationale**: Sprint 42 definierte fÃ¤lschlicherweise `edited_message` als generell verboten. Die Implementierung erlaubt es konfigurierbar. Update-ID-Deduplication ist das primÃ¤re Schutzinstrument gegen Doppel-Dispatch.
**Impact**: Editierte Operator-Commands kÃ¶nnen durchkommen, sofern sie eine neue `update_id` tragen (was Telegram sicherstellt). Doppelter Dispatch derselben `update_id` wird durch den Replay-Buffer blockiert.

### A-060: Replay-Schutz via OrderedDict-FIFO mit maxlen=2048 (Sprint 42C korrigiert)
**Assumption**: `_webhook_seen_update_ids: OrderedDict[int, None]` mit FIFO-Eviction bei `maxlen=2048` ist der Replay-Buffer. Kein `deque`. Kein persistenter Storage. Bei Neustart: leerer Buffer.
**Rationale**: Sprint 42 definierte fÃ¤lschlicherweise `deque(maxlen=1000)`. Die Implementierung nutzt `OrderedDict` mit expliziter Eviction via `popitem(last=False)`. Funktional Ã¤quivalent, aber `maxlen=2048` (doppelte KapazitÃ¤t). Restart-Risiko bleibt akzeptiert.
**Impact**: `duplicate_update_id` ist der Rejection-Reason fÃ¼r Replay-FÃ¤lle (nicht `rejected_replay` wie im Sprint-42-Contract).

### A-061: Webhook-Rejection-Audit in telegram_webhook_rejections.jsonl â€” nur Rejections (Sprint 42C korrigiert)
**Assumption**: Nur abgewiesene Requests werden in `artifacts/telegram_webhook_rejections.jsonl` geloggt (via `_audit_webhook_rejection()`). Accepted Requests werden ausschliesslich in `artifacts/operator_commands.jsonl` (Bot-Layer-Audit) geloggt.
**Rationale**: Sprint 42 definierte fÃ¤lschlicherweise `webhook_audit.jsonl` fÃ¼r alle Requests. Die Implementierung trennt Transport-Rejections (security-relevant) von Command-Audits (operator-relevant) in separate Logs.
**Impact**: Zwei getrennte Audit-Streams: `telegram_webhook_rejections.jsonl` (Transport-Security) + `operator_commands.jsonl` (Command-Operator-Surface).

### A-062: TelegramWebhookProcessResult ist nicht das Execution-Gate (Sprint 42C korrigiert)
**Assumption**: `TelegramWebhookProcessResult` mit `accepted=True` bedeutet "Transport-validiert", nicht "autorisiert zur Execution". Das Admin-Gating (`chat_id in _admin_ids`) bleibt in `TelegramOperatorBot.process_update()`. Kein `WebhookValidatedUpdate`-Modell (Sprint-42-Contract war falsch).
**Rationale**: Zwei orthogonale Sicherheitsschichten: Transport-Layer prÃ¼ft Herkunft (Telegram-Server via Secret-Token), Bot-Layer prÃ¼ft Operator-Berechtigung (admin_chat_ids). Keine Schicht ersetzt die andere.
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

## Sprint 43 â€” FastAPI Operator API Surface Assumptions

### A-066: Operator API nutzt APP_API_KEY als einzigen Token-Guard
**Assumption**: Der FastAPI-Operator-Surface nutzt denselben `APP_API_KEY` wie der bestehende API-Bearer-Guard. Ohne gesetzten Key ist der gesamte `/operator/*`-Surface fail-closed deaktiviert.
**Rationale**: Kein zweites Secret- oder RBAC-System in Sprint 43. Eine einzige Token-Quelle vermeidet Drift und reduziert Fehlkonfigurationen.
**Impact**: Read-only und guarded Endpunkte verlangen `Authorization: Bearer <APP_API_KEY>`. Bei leerem Key liefert der Router kontrolliert `503`.

### A-067: Guarded run-once bleibt reine Delegation auf kanonischen TradingLoop-Guard
**Assumption**: `POST /operator/trading-loop/run-once` enthÃ¤lt keine eigene Mode- oder Trading-Logik, sondern delegiert vollstÃ¤ndig an `mcp_server.run_trading_loop_once()` und damit an den kanonischen Guard in `app/orchestrator/trading_loop.py`.
**Rationale**: Genau ein technischer Guard-Pfad fÃ¼r paper/shadow-only AusfÃ¼hrung; kein paralleler Kontrollpfad im API-Layer.
**Impact**: `mode=live` bleibt fail-closed abgewiesen. Kein Broker-/Live-Execution-Pfad wird durch die API neu erÃ¶ffnet.

### A-068: require_operator_api_token ist Router-Dependency, nicht Bearer-Middleware
**Assumption**: Der Operator-Router implementiert Auth via `require_operator_api_token` als FastAPI-Dependency auf Router-Ebene (`dependencies=[Depends(...)]`), nicht via `app/security/auth.py`-Bearer-Middleware. Dies erlaubt selektive Exemption einzelner Endpoints (z.B. Webhook) ohne globale Middleware-Ã„nderungen.
**Rationale**: Sprint 43 definierte fÃ¤lschlicherweise "Bearer-Auth via bestehendes `app/security/auth.py`". Die tatsÃ¤chliche Implementierung nutzt DI, was sauberer und testbarer ist.
**Impact**: Kein Bypass-Eintrag in `app/security/auth.py` nÃ¶tig. Auth-Verhalten ist vollstÃ¤ndig im Router-Modul kapseliert.

### A-069: /operator/status und /operator/readiness sind Aliases fÃ¼r dieselbe MCP-Funktion
**Assumption**: Beide Endpoints rufen `mcp_server.get_operational_readiness_summary()` auf. Es gibt keinen inhaltlichen Unterschied. `/operator/readiness` ist der primÃ¤re Endpoint (test_api_operator.py nutzt ihn fÃ¼r Auth-Tests), `/operator/status` ist ein Alias.
**Rationale**: Operator-facing tooling kann unterschiedliche Naming-Konventionen bevorzugen. Beide Wege auf dieselbe canonical Source vermeidet Divergenz.
**Impact**: Response-Struktur ist identisch. Kein Routing-Unterschied. Tests fÃ¼r Auth-Verhalten nutzen `/operator/readiness`.

### A-070: Operator-Endpoints sind pure Passthrough ohne Fallback-Handler im Router
**Assumption**: Der Router enthÃ¤lt kein Try/Except um die MCP-Aufrufe (ausser `POST /operator/trading-loop/run-once` fÃ¼r ValueError). Alle read-only Endpunkte propagieren unhandled Exceptions direkt. Das fail-closed Verhalten (`execution_enabled=False`) kommt ausschliesslich aus den MCP-Backing-Funktionen selbst.
**Rationale**: Kein redundanter Catch-All-Handler. Die MCP-Funktionen sind bereits fail-closed (never-raise oder return safe default). Der Router ist so dÃ¼nn wie mÃ¶glich.
**Impact**: Bei unerwarteten Fehlern in MCP-Funktionen kann ein HTTP 500 entstehen. Akzeptiert fÃ¼r Sprint 43 â€” kein HTTP-500-Shield auf Router-Ebene nÃ¶tig, da Backing-Surfaces bereits fail-closed sind.

### A-071: ValueError aus run_trading_loop_once â†’ HTTP 400 mit Detail-String
**Assumption**: `POST /operator/trading-loop/run-once` fÃ¤ngt `ValueError` (z.B. bei `mode=live`) und gibt HTTP 400 mit `detail=str(exc)` zurÃ¼ck. Der Detail-String enthÃ¤lt den kanonischen Mode-Guard-Text ("allowed: paper, shadow").
**Rationale**: HTTP 400 (Bad Request) ist semantisch korrekt fÃ¼r ungÃ¼ltige Mode-Werte. Der Aufrufer kann die Ablehnung von einem Server-Fehler unterscheiden. Der ValueError kommt vom kanonischen Guard in `run_once_guard()`.
**Impact**: `test_operator_run_once_live_mode_is_fail_closed` verifiziert "allowed: paper, shadow" im Detail-String. Client muss fÃ¼r `mode=live` mit 400 rechnen.

### A-072: Webhook-Delegation ist Sprint-43+-Backlog (nicht in Sprint-43-Implementierung)
**Assumption**: `GET /operator/webhook-status` und `POST /operator/webhook` (mit `app.state.telegram_bot`-Delegation) sind im Sprint-43-Entwurf (Â§54) definiert aber in der tatsÃ¤chlichen Implementierung **nicht vorhanden**. `test_operator_api.py` mit 8 failing Tests dokumentiert diese LÃ¼cke. Die Webhook-Endpoints werden in einem spÃ¤teren Sprint implementiert.
**Rationale**: Codex implementierte zuerst den schlanken Core-Surface ohne Webhook-Delegation. Die 8 failing Tests sind der Restdrift zwischen Â§54-Entwurf und tatsÃ¤chlichem Stand.
**Impact**: Bis zur Implementierung: `test_operator_api.py` bleibt mit 8 failing. Sprint 43+ muss Webhook-Endpoints und `test_operator_api.py`-Korrekturen umfassen.

## Sprint 44 Ã¢â‚¬â€ Operator API Hardening & Request Governance Assumptions

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


## Sprint 44 â€” Operator API Hardening & Request Governance Assumptions

### A-073: request_id ist UUID4 â€” server-generiert oder client-gesetzt (validiert)
**Assumption**: Der Server generiert standardmaessig ein UUID4 pro Request via `uuid.uuid4()`. Der Client kann `X-Request-Id` mit einem validen UUID4 senden â€” dann wird dieser Wert uebernommen. Ungueltige Werte (leer, nicht-UUID, zu lang) werden ignoriert und server-generiert.
**Rationale**: Client-seitige Correlation (z.B. fuer Logging-Aggregation) ist ein Standard-Pattern. Validierung verhindert Injection oder Enumeration via Header.
**Impact**: `get_request_id()` Dependency prueft UUID4-Regex. Jeder Response-Body enthaelt `request_id`. Response-Header `X-Request-Id` ist immer gesetzt.

### A-074: Idempotency-Buffer ist in-memory, nicht persistent â€” Restart = akzeptiert
**Assumption**: Der Idempotency-Buffer fuer `POST /operator/trading-loop/run-once` ist ein In-memory `OrderedDict` (maxlen=256, FIFO). Er wird nicht in die DB oder ein JSONL-Log persistiert. Ein Prozess-Neustart leert den Buffer.
**Rationale**: Analog zum Telegram-Replay-Buffer (I-316, A-060). Einfachheit vor absoluter Durability. Das Risiko eines Doppel-Submits nach Neustart ist akzeptiert â€” run-once ist paper/shadow only, kein Live-Impact.
**Impact**: Cluster-Deployments ohne Sticky Session koennen denselben Idempotency-Key auf unterschiedlichen Instanzen akzeptieren. Akzeptiert fuer Sprint 44.

### A-075: Operator API Audit ist von Telegram-Audits streng getrennt
**Assumption**: `artifacts/operator_api_audit.jsonl` (Sprint 44) ist ein separates Log von `artifacts/operator_commands.jsonl` (Telegram Commands) und `artifacts/telegram_webhook_rejections.jsonl` (Telegram Transport). Kein Cross-Write zwischen diesen drei Logs.
**Rationale**: Jede Audit-Surface hat ihre eigene Verantwortlichkeit. Gemischte Logs erschweren Forensik und Monitoring.
**Impact**: Drei getrennte Audit-Streams, drei getrennte JSONL-Dateien. Monitoring-Tools koennen gezielt nach Transport-Typ filtern.

### A-076: HTTP 409 ist der kanonische Response bei Idempotency-Duplikat
**Assumption**: Ein bereits gesehener `X-Idempotency-Key` â†’ HTTP 409 mit `error="duplicate_idempotency_key"`. Kein HTTP 200 "accepted already". Kein HTTP 202. HTTP 409 ("Conflict") ist semantisch korrekt: Request ist strukturell valid, aber im aktuellen State nicht erlaubt.
**Rationale**: HTTP 200/202 wuerde einen Erfolg signalisieren, der nicht eingetreten ist. HTTP 409 zwingt den Client zur expliziten Behandlung von Duplikaten.
**Impact**: Clients muessen fuer `POST /operator/trading-loop/run-once` mit HTTP 409 rechnen wenn sie denselben Key wiederverwenden. Sprint-44-Tests verifizieren dieses Verhalten.

### A-077: error_code ist machine-readable, detail ist human-readable â€” beide verpflichtend
**Assumption**: Jedes Fehler-Objekt enthaelt `error` (machine-readable Code aus der kanonischen Liste Â§55.5) UND `detail` (human-readable Beschreibung) UND `request_id`. Kein Feld darf fehlen.
**Rationale**: Machine-readable Codes erlauben automatisierte Alert-Klassifikation. Human-readable Detail erleichtert Operator-Debugging ohne Stack-Trace-Exposure.
**Impact**: `require_operator_api_token` und alle Exception-Handler in `operator.py` muessen auf die strukturierte Form umgestellt werden (Codex-Task).

### A-078: Audit-Log-Fehler sind nicht fatal â€” never-raise Kontrakt
**Assumption**: Ein Fehler beim Schreiben des Audit-Logs (`artifacts/operator_api_audit.jsonl`) blockiert weder den Request noch den Response. Der Fehler wird auf WARNING-Ebene geloggt. Analog zum Telegram-Command-Audit (Â§55.4).
**Rationale**: Audit-Log-Infrastruktur darf den Operator-Control-Path nicht degradieren. Besser ein Request ohne Audit als ein blockierter Request.
**Impact**: `_audit_operator_request()` ist in einem try/except gekapselt. Monitoring sollte auf Audit-Write-Fehler alertieren.


## Sprint 44C â€” Operator API Hardening Korrekturen (A-073Câ€“A-078C)

### A-073C: request_id Format ist req_<hex>, nicht UUID4 (Sprint 44C korrigiert)
**Assumption**: `request_id` hat das Format `req_<uuid4_hex>` (z.B. `req_a1b2c3...`), generiert via `_new_context_id("req")`. Es ist kein reines UUID4. Client kann via `X-Request-ID` Header einen validen alphanumerischen Wert vorgeben (Regex `^[A-Za-z0-9._:-]{1,128}$`).
**Rationale**: Â§55 definierte fÃ¤lschlicherweise UUID4. Die Implementierung nutzt ein Prefix-Format fÃ¼r bessere Lesbarkeit in Logs.
**Impact**: Tests prÃ¼fen `response.headers["X-Request-ID"].startswith("req_")` â€” kein UUID4-Format-Check.

### A-074C: Idempotency ist REQUIRED und bietet Replay, nicht nur 409 (Sprint 44C korrigiert)
**Assumption**: `Idempotency-Key` Header (nicht `X-Idempotency-Key`) ist PFLICHT fÃ¼r `POST /operator/trading-loop/run-once`. Fehlt er â†’ 400 `missing_idempotency_key`. Bei gleichem Key + gleichem Payload-Fingerprint â†’ gespeicherte Response zurÃ¼ck (kein zweiter API-Aufruf). Bei gleichem Key + anderem Payload â†’ 409 `idempotency_key_conflict`.
**Rationale**: Â§55 definierte optionale Idempotency mit einfachem 409. Die Implementierung bietet vollstÃ¤ndiges Replay-Pattern mit SHA256-Payload-Fingerprinting.
**Impact**: Clients MÃœSSEN immer einen `Idempotency-Key` senden. Deterministische Keys erlauben sichere Retries ohne Doppel-Execution.

### A-075C: Audit-Log ist operator_api_guarded_audit.jsonl, nur guarded POST (Sprint 44C korrigiert)
**Assumption**: Das Audit-Log fÃ¼r Sprint 44 ist `artifacts/operator_api_guarded_audit.jsonl` â€” ausschliesslich fÃ¼r den guarded POST-Endpoint, NICHT fÃ¼r alle Operator-Requests. Read-only Endpoints werden NICHT in dieses Log geschrieben.
**Rationale**: Â§55 definierte `artifacts/operator_api_audit.jsonl` fÃ¼r alle authentifizierten Requests. Die Implementierung auditiert gezielt den guarded Pfad â€” sicherheitsrelevanter und weniger Noise auf read-only.
**Impact**: Drei separate Audit-Streams: `operator_commands.jsonl` (Telegram), `telegram_webhook_rejections.jsonl` (Transport), `operator_api_guarded_audit.jsonl` (guarded API POST).

### A-076C: Error-Shape ist verschachtelt mit correlation_id (Sprint 44C korrigiert)
**Assumption**: Die kanonische Error-Shape ist `{"error": {"code": "<code>", "message": "<msg>", "request_id": "<id>", "correlation_id": "<id>"}, "execution_enabled": false, "write_back_allowed": false}`. Sie ist verschachtelt (nicht flach wie Â§55 definierte) und enthÃ¤lt `correlation_id` als eigenes Feld.
**Rationale**: Â§55 definierte flache Shape `{error: "<code>", detail: "<msg>", request_id: "<uuid>"}`. Die Implementierung ist strukturreicher und konsistenter mit API-Design-Standards.
**Impact**: API-Clients mÃ¼ssen `response.json()["detail"]["error"]["code"]` lesen (FastAPI gibt HTTPException.detail als JSON zurÃ¼ck). Tests verifizieren via `_assert_error_payload()`.

### A-077C: Rate-Limiter basiert auf token_fingerprint als operator_subject (Sprint 44C neu)
**Assumption**: Der Rate-Limiter nutzt `operator_subject = "token_<sha256[:16]>"` als Bucket-Key â€” abgeleitet vom Bearer-Token-Wert (SHA256 der ersten 16 Hex-Zeichen). Dies ermÃ¶glicht token-basiertes Rate-Limiting ohne den Token-Wert zu loggen.
**Rationale**: Â§55 definierte Rate-Limiting nicht. Die Implementierung fÃ¼gt es als Schutzschicht fÃ¼r den guarded POST hinzu. Der token_fingerprint verhindert Token-Logging bei gleichzeitiger Eindeutigkeit.
**Impact**: Verschiedene Bearer-Token haben separate Rate-Limit-Buckets. Wildcard-Token-Sharing teilt einen Bucket.

### A-078C: _reset_operator_guard_state_for_tests() ist kanonische Test-Reset-Funktion (Sprint 44C neu)
**Assumption**: `_reset_operator_guard_state_for_tests()` ist eine Ã¶ffentliche Funktion in `operator.py` die `_IDEMPOTENCY_CACHE` und `_GUARDED_RATE_LIMIT_BUCKETS` leert. Sie wird in Test-Fixtures (`monkeypatch` + `tmp_path`) aufgerufen um determistische Tests zu gewÃ¤hrleisten.
**Rationale**: In-memory State zwischen Tests isolieren ohne Prozess-Restart. Die Funktion ist "test surface" â€” nicht fÃ¼r Production-Use gedacht.
**Impact**: Tests MÃœSSEN `_reset_operator_guard_state_for_tests()` in `fixture(autouse=False)` aufrufen wenn sie Idempotency oder Rate-Limiting testen. `_WORKSPACE_ROOT` wird via `monkeypatch.setattr` umgeleitet auf `tmp_path`.
## PH1_FINAL_SECURITY_CLOSURE_002 Assumptions

### A-082: Externe Key-Rotation ist ein realer Security-Blocker
**Assumption**: Befund E-1 (externe Key-Rotation) ist ein echter Sicherheitsrest und keine reine Dokumentationskosmetik.
**Rationale**: Ohne nachweisbare Rotation/Revoke-Information bleibt ein Risiko auf reale Secret-Kompromittierung bestehen, trotz technischem Hardening.
**Impact**: Phase 1 bleibt offen, bis Evidence vorliegt oder formale Risk Acceptance dokumentiert wurde.

### A-083: Kein neuer Feature-Sprint vor finaler Security-Closure
**Assumption**: Sprint 45 und weitere Feature-Erweiterungen bleiben deferred, bis PH1_FINAL_SECURITY_CLOSURE_002 abgeschlossen ist.
**Rationale**: Priorisierung bleibt auf Security first und Governance-Stabilitaet, um Drift zwischen Reifegrad und Sicherheitswahrheit zu vermeiden.
**Impact**: Nur Synchronisierung, Risk-Tracking und Closure-Nachweis sind im aktuellen Sprint im Scope.

### A-084: Phase-2-Start ist an Closure oder Risk Acceptance gebunden
**Assumption**: Phase 2 darf technisch geplant, aber nicht operativ gestartet werden, solange E-1 nicht geschlossen oder formal akzeptiert ist.
**Rationale**: Fail-closed Governance verhindert, dass offene Sicherheitsrestpunkte durch neue Lieferzyklen verdeckt werden.
**Impact**: `PHASE_PLAN.md`, `RISK_REGISTER.md` und `DECISION_LOG.md` bleiben als bindende Gate-Quellen synchron.

### A-085: D-12 ist ein legitimer Abschlussweg bei nicht praktikabler Sofort-Rotation
**Assumption**: Wenn unmittelbare externe Key-Rotation operativ nicht praktikabel ist, darf PH1_FINAL_SECURITY_CLOSURE_002 ueber den formalen D-12 Risk-Acceptance-Pfad geschlossen werden.
**Rationale**: Governance bleibt fail-closed, ohne in eine unendliche Zwischenlage zu geraten.
**Impact**: D-12 muss in `DECISION_LOG.md` explizit dokumentiert und in `PHASE_PLAN.md`/`RISK_REGISTER.md` synchronisiert werden.


## Sprint 45 â€” Daily Operator View Assumptions

### A-086: Open incidents werden aus dem Review-Journal abgeleitet
**Assumption**: `open_incidents` im `daily_operator_summary` wird aus `get_review_journal_summary().open_count` projiziert.
**Rationale**: Bestehende append-only Journal-Surface ist bereits kanonisch und auditierbar.
**Impact**: Daily Summary bleibt read-only, ohne neue Incident- oder Escalation-Architektur.

### A-087: Exposure-Prozent wird aus bestehender Portfolio+Exposure-Surface berechnet
**Assumption**: `total_exposure_pct = gross_exposure_usd / total_equity_usd * 100`, mit fail-closed `0.0`, wenn `total_equity_usd <= 0`.
**Rationale**: Keine neue Berechnungslogik im Datenpfad; nur Projektion vorhandener Read-Surfaces.
**Impact**: Konsistente, robuste Exposure-Anzeige auch bei leerem oder degradiertem Portfolio.

### A-088: TageszykluszÃ¤hlung basiert auf UTC-Datum vorhandener Audit-Felder
**Assumption**: `cycle_count_today` wird aus `recent_cycles` Ã¼ber `completed_at` (Fallback `started_at`) anhand des UTC-Datums berechnet.
**Rationale**: TradingLoop-Audit ist der kanonische Nachweis fÃ¼r Zyklen; keine neue Loop-ZÃ¤hlarchitektur.
**Impact**: Daily-View bleibt auditgetrieben und deterministisch.

### A-089: Daily Summary ist best-effort und fail-closed pro Subsurface
**Assumption**: Fehler einzelner delegierter MCP-Read-Tools fÃ¼hren zu degradierten Feldern statt zu einer propagierten Exception.
**Rationale**: Operator-Nutzbarkeit und StabilitÃ¤t sind wichtiger als harte AbbrÃ¼che bei Teilfehlern.
**Impact**: `sources` zeigt transparent, welche Subsurfaces erfolgreich beigetragen haben.

## Sprint 47 â€” Drilldown & History Assumptions

### A-090: Journal-API-Drilldown bleibt reine Delegation ohne neue Aggregation
**Assumption**: `GET /operator/review-journal` und `GET /operator/resolution-summary` sind reine Delegations-Endpunkte auf bestehende MCP-Tools.
**Rationale**: Verhindert eine zweite Journal-/History-Architektur im API-Layer.
**Impact**: Alle Drilldown-Daten bleiben an denselben kanonischen MCP-Backbone gebunden.

### A-091: Operator-Journal-Pfad bleibt kanonisch unter artifacts/operator_review_journal.jsonl
**Assumption**: Der Default-Pfad fÃ¼r die neuen Operator-Endpunkte ist `artifacts/operator_review_journal.jsonl`.
**Rationale**: Konsistenz mit bestehenden CLI-/MCP-Surfaces und vorhandenen Contracts.
**Impact**: Keine Path-Drift zwischen Operator API, CLI und MCP.

### A-092: Fehlerbehandlung folgt identischer fail-closed Operator-Error-Shape
**Assumption**: Neue Drilldown-Endpunkte nutzen unverÃ¤ndert `_resolve_read_payload()` und damit dieselbe strukturierte Fehlerform.
**Rationale**: Security-first und minimales Risiko durch Wiederverwendung der bestehenden Request-Governance.
**Impact**: Keine neue Error-Policy und keine unerwarteten Seiteneffekte fÃ¼r Operator-Clients.

## Sprint 48 â€” Surface Completion Assumptions

### A-093: resolution_status wird in Telegram aus journal_status abgeleitet, falls Feld fehlt
**Assumption**: Der Telegram-Handler `/resolution` liest primÃ¤r `resolution_status`; wenn dieses Feld im MCP-Payload fehlt, wird auf `journal_status` zurÃ¼ckgefallen.
**Rationale**: Bestehende kanonische `review_resolution_summary` Payloads enthalten `journal_status` sicher; damit bleibt die Anzeige robust ohne neue Backend-Logik.
**Impact**: `/resolution` bleibt read-only, fail-closed und kompatibel mit bestehender MCP/CLI-Datenstruktur.

### A-094: operator_action_count wird aus action_queue_summary gelesen, mit sicherem Fallback
**Assumption**: Der Telegram-Handler `/decision_pack` nutzt `operator_action_count` direkt, sonst `action_queue_summary.operator_action_count`, sonst `action_queue_count`.
**Rationale**: Decision-Pack-Payloads sind historisch leicht unterschiedlich strukturiert; der Fallback verhindert Anzeige-Drift ohne zweite Aggregationslogik.
**Impact**: Konsistente Operator-Ausgabe bei unverÃ¤nderter kanonischer Decision-Pack-Quelle.

### A-095: Dashboard-Drilldown bleibt bewusst referenziert statt navigierend
**Assumption**: Die Dashboard-Erweiterung ist eine statische Referenz-Sektion (Pfadliste), keine klickbare, tokenlose Navigation.
**Rationale**: Browser-Links auf Bearer-geschÃ¼tzte Operator-Endpoints wÃ¼rden ohne JS/Auth-Flow zu Fehlverhalten oder Scope-Drift fÃ¼hren.
**Impact**: Einhaltung von Â§61 (kein JS, kein zweiter Backend-Call, keine Subpage-Architektur).
