# TASKLIST.md

## Current State

- current_phase: `PHASE 5 (active) -- Signal Reliability & Trust`
- current_sprint: `PH5C_FILTER_BEFORE_LLM_BASELINE (frozen §85)`
- next_required_step: `PH5C_EXECUTION`
- baseline: `1619 passed, ruff clean, CI green`
- working_tree: `clean`
- branch: `claude/p6-audit/architectural-invariants`

## Active Tasks

- [ ] **PH5C**: Implement pre-LLM stub/empty filter — skip LLM for docs with content_len < threshold, tag as `stub_document`

## Recently Completed (2026-03-24)

- [x] **PH5B** (D-92) — Low signal cluster: 19/19 EMPTY_MANUAL; root cause: placeholder content, not model failure
- [x] **Alert Integration** — `analyze-pending` CLI now dispatches alerts after DB write
- [x] **CI Hardening** — hypothesis + pytest-mock in dev-deps; bandit B324 fixed; ruff format; codecov@v5; all 5 CI jobs green
- [x] **MCP Tool Extraction (N-6)** — `compat.py` extracted from `mcp_server.py`

## Closed Phases / Sprints

- [x] **Phase 5 / PH5A** (D-89) -- Reliability Baseline: LLM-error-proxy=27.5%, zero-hit=37.7%, actionable=0%
- [x] **Phase 5 / PH5B** (D-92) -- Low Signal Cluster: 19/19 EMPTY_MANUAL
- [x] **Phase 4** (D-87, 2026-03-24) -- Signal Quality Calibration, 11 sprints PH4A-PH4K
- [x] **Phase 3** (2026-03-22) -- GO
