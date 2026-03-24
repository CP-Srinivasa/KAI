# Sprint 18 Contract — Controlled MCP Server Integration

> Canonical reference for the KAI MCP server: read surface, guarded write surface,
> security boundaries, and hard guardrails.
>
> Runtime: `app/agents/mcp_server.py`
> Invariants: `docs/contracts.md` I-94–I-100
> Upstream: Sprint 17 (route_runner.py), Sprint 14C (active_route.py),
>            Sprint 14 (ABCInferenceEnvelope), Sprint 13 (upgrade_cycle.py)

---

## Purpose

Sprint 18 defines and documents a controlled MCP (Model Context Protocol) server that gives
AI-capable tools (e.g. Claude Desktop, agent frameworks) structured read access to KAI's
research surface and a strictly bounded, auditable write path for route profile management.

MCP is a **controlled ingress point**. It is not a replacement for the CLI, not an admin panel,
not a trading interface. Every action is bounded by the same invariants as the CLI.

**Leitmotiv:** read-first, write only when necessary, audit everything.

---

## Non-Negotiable Rules

| Rule | Statement |
|------|-----------|
| No trading execution | MCP MUST NOT expose order submission, position management, or live market interaction (I-98) |
| No auto-routing | MCP MUST NOT change APP_LLM_PROVIDER or activate a route without an explicit operator call (I-96) |
| No auto-promotion | MCP MUST NOT call record-promotion, apply_to_document(), or any DB write on analysis data (I-99) |
| No unaudited writes | Every MCP write action returns a complete audit record with `app_llm_provider_unchanged: true` (I-97) |
| Workspace confinement | All file paths are validated against `_WORKSPACE_ROOT` (I-94) |
| No scope explosion | MCP surface ≠ full admin surface — dataset export, training, promotion remain CLI-only (I-100) |

---

## Security Guards

### `_resolve_workspace_path()`

All file path parameters pass through `_resolve_workspace_path()`:
1. Resolves the path relative to `_WORKSPACE_ROOT` if not absolute
2. Verifies the resolved path is inside `_WORKSPACE_ROOT` (blocks `../../` traversal)
3. Enforces allowed file suffixes (`.json` or `.jsonl` depending on tool)
4. Optionally checks `must_exist` for read tools

### `_require_artifacts_subpath()` (I-95)

All guarded write tool output paths additionally pass through `_require_artifacts_subpath()`:
- Verifies the path is within `_WORKSPACE_ROOT/artifacts/`
- Blocks write operations to any path outside the `artifacts/` subdirectory
- This is the hard boundary between read-only workspace access and write-capable surfaces

### `_append_mcp_write_audit()` (I-94)

Every guarded write call appends a JSONL entry to `artifacts/mcp_write_audit.jsonl`:
- `timestamp` (UTC ISO 8601)
- `tool` (tool name)
- `params` (sanitized parameter dict)
- `result_summary` (one-line result description)
- Never raises — a failing audit write must not suppress the original result

Together, these three guards implement workspace-confinement, artifacts-boundary enforcement,
and audit trail for all MCP write operations.

---

## Surface Definition

### Read Surface (I-95 — no side effects)

| Tool | What it reads | Returns |
|------|--------------|---------|
| `get_watchlists` | Monitor-dir watchlist files | `dict[str, list[str]]` |
| `get_research_brief` | Analyzed documents from DB | Markdown string |
| `get_signal_candidates` | Analyzed documents from DB | JSON string of candidates |
| `get_route_profile_report` | Analyzed documents from DB | `dict` with distribution stats |
| `get_inference_route_profile` | Workspace-local profile JSON | `dict` with profile fields |
| `get_active_route_status` | `artifacts/active_route_profile.json` | `dict`: `{active, state_path, state?}` |
| `get_upgrade_cycle_status` | Caller-supplied artifact paths | `dict` of `UpgradeCycleReport` |
| `get_mcp_capabilities` | Static declaration | JSON string with tool list + guardrails |

### Guarded Write Surface (I-96, I-97)

Guarded write tools produce exactly one artifact file per call. No DB writes. No routing changes.
Every call returns a complete audit record with `app_llm_provider_unchanged: true`.

| Tool | What it writes | Does NOT change |
|------|---------------|-----------------|
| `create_inference_profile` | `InferenceRouteProfile` JSON at `output_path` | APP_LLM_PROVIDER, DB, any live state |
| `activate_route_profile` | `ActiveRouteState` JSON at `state_path` | APP_LLM_PROVIDER, DB, any live state |
| `deactivate_route_profile` | Deletes `ActiveRouteState` file at `state_path` | APP_LLM_PROVIDER, DB, any live state |

### Permanently Out of Scope (I-98–I-100)

These surfaces are **not exposed via MCP** and must not be added without a spec change:

- Trading execution (orders, positions, live market)
- Dataset export or training job submission
- Promotion recording (`record-promotion`)
- Alert configuration
- Provider key management
- DB schema changes or direct SQL access
- Any bulk mutation of analyzed documents

---

## Tool Contracts

### `get_watchlists(watchlist_type: str = "assets") -> dict[str, list[str]]`

**Surface:** Read | **No path validation needed** (uses monitor_dir from settings)
Loads watchlist registry from `settings.monitor_dir`. Returns all lists of the given type.

---

### `get_research_brief(watchlist, watchlist_type, limit) -> str`

**Surface:** Read (DB read)
Fetches analyzed documents from DB, filters by watchlist, builds a Markdown research brief.
Does not write, does not mutate documents.

---

### `get_signal_candidates(watchlist, min_priority, limit) -> str`

**Surface:** Read (DB read)
Fetches analyzed documents, extracts signal candidates. Returns JSON. No DB writes.

---

### `get_route_profile_report(limit: int = 1000) -> dict`

**Surface:** Read (DB read)
Calls `build_route_profile()` (async read from DB). Returns `RouteProfileReport.to_json_dict()`.
Does not write, does not mutate routing.

---

### `get_inference_route_profile(profile_path: str) -> dict`

**Surface:** Read (workspace file)
**Security:** `_resolve_workspace_path(must_exist=True, suffixes={".json"})`
Loads `InferenceRouteProfile` from workspace-local path. Returns profile dict + resolved path.

---

### `get_active_route_status(state_path: str = DEFAULT_ACTIVE_ROUTE_PATH) -> dict`

**Surface:** Read (workspace file)
**Security:** `_resolve_workspace_path(suffixes={".json"})`
Returns `{active: false}` if no state file. Returns `{active: true, state: {...}}` if present.
Does NOT activate, change, or delete the route state.

---

### `get_upgrade_cycle_status(teacher_dataset_path, ...) -> dict`

**Surface:** Read (workspace files, I-75)
**Security:** `_resolve_workspace_path(must_exist=True)` for each provided path
Calls `build_upgrade_cycle_report()` — pure JSON-reads only (I-75). Returns cycle status dict.

---

### `get_mcp_capabilities() -> str`

**Surface:** Meta / Read
Returns static capability declaration: `read_tools`, `write_tools`, `guardrails`, `transport`.

---

### `create_inference_profile(...) -> dict` [GUARDED WRITE]

**Surface:** Guarded write (I-96, I-89)
**Security:** `_resolve_workspace_path(suffixes={".json"})` on `output_path`
Creates `InferenceRouteProfile` JSON. Identical to `create-inference-profile` CLI.
Returns `{output_path, profile}` audit record.

---

### `activate_route_profile(profile_path, state_path, abc_envelope_output) -> dict` [GUARDED WRITE]

**Surface:** Guarded write (I-96, I-90, I-91)
**Security:** `_resolve_workspace_path(must_exist=True)` on `profile_path`; path validation on `state_path` and `abc_envelope_output`
Calls `activate_route_profile()`. Writes `ActiveRouteState` to state file only.
Returns `{state_path, state}` audit record. `app_llm_provider_unchanged` is NOT
yet in return — see open TODO below.

---

### `deactivate_route_profile(state_path) -> dict` [GUARDED WRITE]

**Surface:** Guarded write (I-96, I-90)
**Security:** `_resolve_workspace_path(suffixes={".json"})` on `state_path`
Deletes state file if it exists. Returns `{deactivated: bool, state_path}`.
Idempotent: returns `deactivated: false` if no file present.

---

## Security Boundaries

```
MCP surface              CLI surface (full admin)
────────────────────     ──────────────────────────────
get_watchlists           sources add/remove
get_research_brief       analyze-pending (full run)
get_signal_candidates    dataset-export              ← CLI only
get_route_profile_report evaluate-datasets           ← CLI only
get_inference_route_profile check-promotion          ← CLI only
get_active_route_status  record-promotion            ← CLI only
get_upgrade_cycle_status prepare-training-job        ← CLI only
create_inference_profile link-training-evaluation    ← CLI only
activate_route_profile   shadow-run                  ← CLI only
deactivate_route_profile alert send                  ← CLI only
get_mcp_capabilities     Trading execution           ← PERMANENTLY out of scope
```

The MCP server runs as a **stdio transport process** (`mcp.run(transport="stdio")`).
No network port is opened. Path traversal is blocked by `_resolve_workspace_path()`.

---

## Invariants (I-94–I-100)

Full text in `docs/contracts.md §27`. Summary:

| ID | Summary |
|----|---------|
| I-94 | MCP is a controlled interface. No MCP tool may enumerate paths or infer state beyond explicit caller input. `_resolve_workspace_path()` enforces workspace confinement. |
| I-95 | MCP read tools MUST NOT trigger analysis, inference, DB mutation, or routing changes. |
| I-96 | MCP guarded write tools produce exactly one artifact file per call. MUST NOT change APP_LLM_PROVIDER, write to DB, or trigger analysis. |
| I-97 | Every MCP write action returns a complete audit record. `app_llm_provider_unchanged: true` MUST be present in audit records of write tools. |
| I-98 | No MCP tool exposes trading execution, position management, order submission, or live market interaction. |
| I-99 | No MCP tool performs auto-promotion, auto-routing, or state advancement. Informational read results carry no implicit action weight. |
| I-100 | Dataset export, training job submission, promotion recording, alert configuration, and provider key management remain CLI-only. |

---

## Inconsistencies Found and Resolved

| Inconsistency | Resolution |
|--------------|------------|
| `get_mcp_capabilities` referenced "Sprint 15" | Updated to "Sprint 18" in contract; label in mcp_server.py already says "controlled MCP interface" (no sprint ref needed at runtime) |
| No contract doc existed for MCP surface | `docs/sprint18_mcp_contract.md` (this file) |
| No invariants defined for MCP surface | I-94–I-100 added to `docs/contracts.md` |
| `activate_route_profile` return did not include `app_llm_provider_unchanged` | Fixed: `app_llm_provider_unchanged: True` added to return dict (I-97) |
| Write paths had no subdir restriction | Fixed: `_require_artifacts_subpath()` restricts all writes to `artifacts/` (I-95) |
| No write audit trail | Fixed: `_append_mcp_write_audit()` appends JSONL to `artifacts/mcp_write_audit.jsonl` (I-94) |

---

## Sprint 18 Completion Criteria

```
Sprint 18 gilt als abgeschlossen wenn:
  - [x] 18.1: app/agents/mcp_server.py — 11 Tools (8 read + 3 write) mit
              _resolve_workspace_path() Workspace-Confinement
  - [x] 18.2: test_mcp_server.py — 27 Tests gesamt, alle gruen
  - [x] 18.3: I-94–I-100 in docs/contracts.md §27
  - [x] 18.4: docs/sprint18_mcp_contract.md vollstaendig
  - [x] 18.5: AGENTS.md P23 eingetragen
  - [x] 18.6: TASKLIST.md Sprint-18 vollstaendig
  - [x] 18.7: intelligence_architecture.md Sprint-18 Zeile
  - [x] _require_artifacts_subpath() — Write-Pfade auf artifacts/ begrenzt (I-95) ✅
  - [x] _append_mcp_write_audit() → artifacts/mcp_write_audit.jsonl (I-94) ✅
  - [x] ruff check . sauber ✅
  - [x] pytest passing (864 Tests, kein Rueckschritt) ✅
  - [x] Kein Auto-Routing eingebaut
  - [x] Kein Auto-Promotion eingebaut
  - [x] Keine Trading-Execution
  - [x] Workspace-Confinement getestet (test_blocks_outside_workspace)
  - [ ] T1: app_llm_provider_unchanged in activate_route_profile return (noch offen)
```
