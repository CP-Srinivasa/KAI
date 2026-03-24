# TASKLIST.md

## Current State

- current_phase: `PHASE 5 (active) -- Signal Reliability & Trust`
- current_sprint: `PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS (active, D-92, §84)`
- next_required_step: `PH5B_EXECUTION`
- baseline: `1619 passed, ruff clean, CI green`
- working_tree: `clean`
- branch: `claude/p6-audit/architectural-invariants`

## Active Tasks

- [ ] **PH5B**: Cluster 19 LLM-error-proxy docs (priority=1, relevance=0, scope=unknown) — root cause classification + fix recommendations

## Recently Completed (2026-03-24)

- [x] **Alert Integration** — `analyze-pending` CLI now dispatches alerts after DB write (Phase 4); `--no-alerts` flag; fail-open; 3 new tests (`tests/unit/cli/test_analyze_pending_alerts.py`)
- [x] **CI Hardening** — `hypothesis` + `pytest-mock` added to dev-deps; bandit B324 fixed (SHA1 `usedforsecurity=False`); ruff format pass; duplicate `asyncio.run` removed; `codecov@v5`; `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`; all 5 CI jobs green
- [x] **MCP Tool Extraction (N-6)** — `compat.py` extracted from `mcp_server.py`; 0 inline `@mcp.tool()` definitions; `test_canonical_read.py` + `test_guarded_write.py` upgraded to `mcp.list_tools()` registration tests

## Closed Phases

- [x] **Phase 5 / PH5A** (D-89, 2026-03-24) -- Reliability Baseline: fallback=0%, LLM-error-proxy=27.5%, tag fill=100%, keyword coverage=62.3%, watchlist overlap=52.2%
- [x] **Phase 4** (D-87, 2026-03-24) -- Signal Quality Calibration, 11 sprints PH4A-PH4K
- [x] **Phase 3** (2026-03-22) -- GO
