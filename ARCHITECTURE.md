# ARCHITECTURE.md

## Intent

KAI is a modular, security-first analysis and operator system.
Architecture prioritizes deterministic behavior, auditability, and fail-closed controls.

## Canonical Runtime Backbone

| Layer | Canonical Path | Role |
|---|---|---|
| Settings and runtime policy | `app/core/settings.py` | Typed config and safety defaults |
| Decision backbone | `app/execution/models.py`, `app/decisions/journal.py` | Canonical decision record and journal projection |
| Market data read-only | `app/market_data/` | External quote/snapshot data, no trading side effects |
| Paper portfolio read | `app/execution/portfolio_read.py` | Canonical positions/exposure projections |
| Trading loop control/audit | `app/orchestrator/trading_loop.py` | Paper/shadow run-once + cycle audit visibility |
| Operator API | `app/api/routers/operator.py` | Read-only and guarded operator HTTP surface |
| Operator messaging | `app/messaging/telegram_bot.py` | Read/audit command interface with webhook hardening |
| MCP surface | `app/agents/mcp_server.py` | Canonical read and guarded tools |
| CLI surface | `app/cli/main.py` | Canonical operator commands |

## Operator Surface Model

- Read-only:
  - readiness, decision pack, portfolio, exposure, loop status, recent cycles
- Guarded:
  - run trading loop once (`paper`/`shadow` only)
- Audit-only:
  - review and decision intent journaling paths

## Request Governance (Operator API)

- request and correlation ID propagation
- unified fail-closed error payload
- idempotency key enforcement on guarded run-once
- append-only guarded audit log
- light rate limiting on guarded endpoint

## Phase-2 Usability and Dashboard Baseline (S45 -> S46)

S45 introduced no new business architecture and froze one Daily backbone.
S46 continues on that foundation with a minimal visual operator baseline:

- daily operator view as canonical read-flow anchor
- readability-first alignment across Telegram, CLI, and API
- dashboard projection from existing summaries only (no second aggregate path)

## S47 Drilldown and History Baseline

S47 extends operator usability depth without extending architecture:

- `GET /operator/review-journal` delegates to `mcp_server.get_review_journal_summary()`
- `GET /operator/resolution-summary` delegates to `mcp_server.get_resolution_summary()`
- no new aggregation model, no new storage path, no new control semantics

## S48 Telegram Surface Completion

S48 completes operator surface parity across channels without new architecture:

- Telegram `/resolution` and `/decision_pack` added as read-only delegation surfaces
- dashboard drilldown reference section added (static path list, no nav or auth bypass)
- all surfaces remain read-only delegation to canonical MCP tools

## S49 Alert Audit Surface Baseline

S49 adds the alert audit read surface without new aggregation:

- `get_alert_audit_summary()` MCP tool — reads `artifacts/alert_audit.jsonl` via `load_alert_audits()` + `_build_alert_dispatch_summary()`
- `GET /operator/alert-audit` — pure delegation, bearer auth, same fail-closed governance as other operator endpoints
- Telegram `/alert_status` — read-only, same pattern as `/resolution`
- CLI `research alert-audit-summary` — canonical read command, registered in `RESEARCH_COMMAND_NAMES`
- no new aggregation backbone, no write-back, `execution_enabled=False` on all surfaces

## Phase-3 Canonical Consolidation Baseline (S50)

Phase 2 is formally closed. Phase 3 starts with consolidation only:

- one canonical runtime truth across API, dashboard, telegram, CLI, and MCP
- no new business features in S50
- focus on architecture clarity, naming consistency, and team-usable documentation
- no new execution semantics, no live-mode broadening

## S50A Canonical Path Inventory Principle

Path ownership in S50A is classified explicitly:

- `canonical`: actively used source-of-truth path
- `alias`: compatibility entry that resolves to canonical path
- `superseded`: intentionally replaced path kept only for history/tests
- `provisional`: registered but outside locked final inventory, requires review

S50A forbids refactoring before this classification is documented and synchronized.
The current inventory artifact is `CANONICAL_SURFACE_INVENTORY.md`.

## Safety Invariants

- `live` remains default-off and fail-closed
- no direct broker/live execution path from operator surfaces
- no critical action without logging and traceability
- no parallel architecture for equivalent runtime concerns
