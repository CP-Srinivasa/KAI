# ASSUMPTIONS.md — KAI Platform

## Current State (2026-03-24)

| Field | Value |
|---|---|
| current_phase | `PHASE 5 (active) — Signal Reliability & Trust` |
| baseline | `1609 passed, ruff clean, mypy 0 errors` |

Active production assumptions. Sprint-governance decisions are in DECISION_LOG.md.
Historical sprint assumptions (A-096–A-116) archived — see git history.

---

## Active Assumptions

### A-001: Three-Tier Analysis Stack — Graceful Degradation
**Assumption**: When Tier 3 (LLM provider) is unavailable, the system falls back through
Tier 2 (companion model, EXPERIMENTAL) to Tier 1 (rule-only) without operator action.
**Rationale**: KAI must remain operational without any external LLM configured (CLAUDE.md §3).
**Constraint**: Tier 1 rule-only output: `actionable=False` always (I-13, permanent).

### A-002: `actionable` is LLM-exclusive — no relaxation
**Assumption**: The `actionable` flag requires LLM-level analysis. Rule-only fallback
always sets `actionable=False`. This is a permanent invariant (I-13).
**Rationale**: False positives in actionability cause operator trust erosion.
**Constraint**: Any relaxation requires a new spec decision with explicit D-reference.

### A-003: Paper trading only — no live execution path
**Assumption**: `ExecutionMode.LIVE` is blocked in all control-plane surfaces
(`run_trading_loop_once`, Operator API, MCP guarded_write). Paper/shadow only.
**Rationale**: No execution infrastructure review has been completed. Safety > speed.
**Constraint**: Live mode unblock requires explicit architectural decision + security review.

### A-004: DB writes are non-fatal — JSONL is the durable fallback
**Assumption**: `TradingLoop._write_db()` and `build_portfolio_snapshot()` treat DB errors
as non-fatal. JSONL audit trail remains the authoritative fallback when DB is unavailable.
**Rationale**: PostgreSQL may not be configured in all deployment environments (V-4 design).
**Constraint**: JSONL files must never be deleted while DB-primary is not confirmed.

### A-005: No hardcoded secrets — all keys via environment
**Assumption**: All API keys (OpenAI, Anthropic, Gemini, Telegram, CoinGecko, Operator)
are injected via environment variables or `.env` file. No fallback hardcoded values exist.
**Rationale**: CLAUDE.md §3: "No credentials committed to the repository."
**Constraint**: `validate_secrets()` fails fast in non-dev environments with missing keys.

### A-006: Market data freshness gate — stale data terminates cycle
**Assumption**: If `market_data.is_stale`, `TradingLoop.run_cycle()` sets
`CycleStatus.STALE_DATA` and exits without signal generation or DB write.
**Rationale**: Stale prices make position sizing and risk calculations unreliable.
**Constraint**: Freshness threshold configurable via settings; default 120s.

---

> Historical sprint assumptions (Phase 3–4 governance) are no longer active.
> See DECISION_LOG.md for closed decisions, docs/contracts.md for permanent contracts.
