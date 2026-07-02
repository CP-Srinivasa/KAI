# CANONICAL_SURFACE_INVENTORY.md

## Scope

S50 canonical-path inventory for operator-facing surfaces.

- phase: `PHASE 3`
- umbrella sprint: `S50_CANONICAL_CONSOLIDATION_BASELINE`
- completed sprints: `S50A_CANONICAL_PATH_INVENTORY` (closed), `S50B_PROVISIONAL_CLI_GOVERNANCE` (closed)
- active sprint: `S50C_CLI_CONTRACT_FREEZE`
- baseline: `1519 passed, ruff clean`
- mode: governance/documentation consolidation only (no new product feature logic)

Classification vocabulary used in this file:

- `canonical`: primary source-of-truth path
- `alias`: compatibility name that points to a canonical path
- `superseded`: replaced path retained only for compatibility/history
- `provisional`: registered path outside locked final inventory, pending governance decision

---

## MCP Tool Inventory

Implementation source: `app/agents/mcp_server.py` (`get_mcp_tool_inventory()`).

| Class | Entries | Notes |
|---|---|---|
| canonical read tools | 41 | readiness, decision-pack, daily-summary, review/resolution, alert-audit, portfolio/exposure, trading-loop status/cycles |
| guarded write tools | 7 | includes `run_trading_loop_once`; all writes are guarded and audited |
| aliases | 3 | `get_handoff_summary`, `get_operator_decision_pack`, `get_loop_cycle_summary` |
| superseded | 1 | `get_operational_escalation_summary` -> `get_escalation_summary` |
| provisional | 0 | none reported by runtime inventory helper |

---

## Operator API Inventory

Implementation source: `app/api/routers/operator.py`.

| Path | Class | Notes |
|---|---|---|
| `GET /operator/status` | canonical | read-only delegation |
| `GET /operator/readiness` | canonical | read-only delegation |
| `GET /operator/decision-pack` | canonical | read-only delegation |
| `GET /operator/daily-summary` | canonical | read-only delegation |
| `GET /operator/review-journal` | canonical | read-only delegation |
| `GET /operator/resolution-summary` | canonical | read-only delegation |
| `GET /operator/alert-audit` | canonical | read-only delegation |
| `GET /operator/portfolio-snapshot` | canonical | read-only delegation |
| `GET /operator/exposure-summary` | canonical | read-only delegation |
| `GET /operator/trading-loop/status` | canonical | read-only delegation |
| `GET /operator/trading-loop/recent-cycles` | canonical | read-only delegation |
| `POST /operator/trading-loop/run-once` | canonical (guarded) | paper/shadow only, idempotency + rate guard |

---

## Dashboard Inventory

Implementation source: `app/api/routers/dashboard.py`.

| Path | Class | Notes |
|---|---|---|
| `GET /dashboard` | canonical | single dashboard runtime path |
| `/static/dashboard.html` | superseded | intentionally absent; expected 404 |

---

## CLI Research Command Inventory

Implementation source: `app/cli/main.py` (`get_research_command_inventory()`).

### Canonical Final Commands (53)

`signal-handoff`, `handoff-acknowledge`, `handoff-collector-summary`, `readiness-summary`, `provider-health`, `drift-summary`, `gate-summary`, `remediation-recommendations`, `artifact-inventory`, `artifact-rotate`, `artifact-retention`, `cleanup-eligibility-summary`, `protected-artifact-summary`, `review-required-summary`, `escalation-summary`, `blocking-summary`, `operator-action-summary`, `action-queue-summary`, `blocking-actions`, `prioritized-actions`, `review-required-actions`, `decision-pack-summary`, `daily-summary`, `operator-runbook`, `runbook-summary`, `runbook-next-steps`, `review-journal-append`, `review-journal-summary`, `resolution-summary`, `market-data-quote`, `market-data-snapshot`, `paper-portfolio-snapshot`, `paper-positions-summary`, `paper-exposure-summary`, `trading-loop-status`, `trading-loop-recent-cycles`, `trading-loop-run-once`, `alert-audit-summary`, `backtest-run`, `benchmark-companion`, `benchmark-companion-run`, `brief`, `check-promotion`, `dataset-export`, `decision-journal-append`, `decision-journal-summary`, `evaluate`, `evaluate-datasets`, `prepare-tuning-artifact`, `record-promotion`, `shadow-report`, `signals`, `watchlists`

### Aliases (4)

| Alias | Canonical target |
|---|---|
| `consumer-ack` | `handoff-acknowledge` |
| `handoff-summary` | `handoff-collector-summary` |
| `operator-decision-pack` | `decision-pack-summary` |
| `loop-cycle-summary` | `trading-loop-recent-cycles` |

### Superseded (1)

| Command | Replacement |
|---|---|
| `governance-summary` | none (removed from final inventory) |

### Provisional Registered Commands (0)

None. All 15 former provisional commands promoted to canonical in S50B (2026-03-22).

---

## Telegram Command Inventory

Implementation source: `app/messaging/telegram_bot.py` (`get_telegram_command_inventory()` + dispatcher map).

### Read-only commands (11)

`/status`, `/health`, `/positions`, `/exposure`, `/risk`, `/signals`, `/journal`, `/resolution`, `/decision_pack`, `/daily_summary`, `/alert_status`

### Guarded audit-only commands (3)

`/approve`, `/reject`, `/incident`

### Guarded control commands (3)

`/pause`, `/resume`, `/kill`

---

## Drift Findings

| Finding ID | Area | Status | Detail |
|---|---|---|---|
| F-S50A-001 | CLI | **closed** (S50B) | 15 provisional commands promoted to canonical. All have test coverage; 3 have MCP backing. |
| F-S50A-002 | Docs vocabulary | mitigated | S50 docs standardized on `canonical/alias/superseded/provisional`. |
| F-S50A-003 | Dashboard path | closed | `/static/dashboard.html` remains superseded/absent; canonical path is only `GET /dashboard`. |

---

## Verification

- `python -m pytest` -> `1519 passed`
- `python -m ruff check .` -> clean

---

## S50A Freeze Result

Status: **closed**

- Claude Governance-Review: PASS (2026-03-22)
- Antigravity Readability/Onboarding Review: PASS (2026-03-22)
- Inventory is frozen as S50A output.
- F-S50A-001 is explicitly carried into S50B (not a freeze blocker).

### Freeze Record

| Field | Value |
|---|---|
| Frozen artifact | `CANONICAL_SURFACE_INVENTORY.md` |
| Freeze date | 2026-03-22 |
| Baseline at freeze | 1519 passed, ruff clean |
| Open governance item | none (F-S50A-001 resolved in S50B) |
| Next sprint | `S50C_CLI_CONTRACT_FREEZE` |

---

## S50B Provisional CLI Governance Board

### Per-command decision criteria (mandatory)

Each provisional CLI command must be evaluated against these criteria:

1. operator relevance for canonical day-to-day flow
2. naming clarity and ambiguity risk
3. overlap with canonical commands (duplicate surface risk)
4. maintenance burden and test coverage confidence
5. governance/safety impact if promoted

Decision outcomes allowed:

- `promote_to_canonical`
- `keep_provisional`
- `alias_to_canonical`
- `supersede`

### Classification rationale

- 15/15 provisional commands have confirmed test coverage.
- 3/15 have MCP backing (`watchlists`, `decision-journal-summary`, `decision-journal-append`).
- 12/15 are internal pipeline/governance commands where MCP is not required.
- No provisional command duplicates an existing canonical command surface.
- All commands originate from Sprints 9–24 research/training pipeline; none are abandoned or broken.
- Decision: promote all 15 to canonical.

### Classification worklist (15)

| Command | Previous class | S50B decision | Notes |
|---|---|---|---|
| `backtest-run` | provisional | `promote_to_canonical` | internal pipeline; test coverage confirmed |
| `benchmark-companion` | provisional | `promote_to_canonical` | internal pipeline; test coverage confirmed |
| `benchmark-companion-run` | provisional | `promote_to_canonical` | internal pipeline; test coverage confirmed |
| `brief` | provisional | `promote_to_canonical` | operator-facing summary; test coverage confirmed |
| `check-promotion` | provisional | `promote_to_canonical` | governance tool; test coverage confirmed |
| `dataset-export` | provisional | `promote_to_canonical` | pipeline export; test coverage confirmed |
| `decision-journal-append` | provisional | `promote_to_canonical` | MCP-backed (`append_decision_instance`); test coverage confirmed |
| `decision-journal-summary` | provisional | `promote_to_canonical` | MCP-backed (`get_decision_journal_summary`); test coverage confirmed |
| `evaluate` | provisional | `promote_to_canonical` | evaluation pipeline; test coverage confirmed |
| `evaluate-datasets` | provisional | `promote_to_canonical` | evaluation pipeline; test coverage confirmed |
| `prepare-tuning-artifact` | provisional | `promote_to_canonical` | training pipeline; test coverage confirmed |
| `record-promotion` | provisional | `promote_to_canonical` | governance tracking; test coverage confirmed |
| `shadow-report` | provisional | `promote_to_canonical` | internal shadow/audit projection; test coverage confirmed |
| `signals` | provisional | `promote_to_canonical` | internal research projection; test coverage confirmed |
| `watchlists` | provisional | `promote_to_canonical` | MCP-backed (`get_watchlists`); test coverage confirmed |

### Classification result

- Decisions recorded: 15/15
- Outcome: all 15 promoted to canonical
- F-S50A-001 status: **resolved** (2026-03-22)
- Baseline unchanged: `1519 passed`, `ruff clean`
