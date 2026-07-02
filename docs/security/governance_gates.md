# Governance Gates (SENTR)

**Status:** live (gate primitives + tests) **and wired** into the productive
decision path (Issue #165) — see "Integration" below.
**Owner:** SENTR (Security & Inspection)
**Module:** `app/security/governance/`
**Scope guard:** This is deliberately **not** a full RBAC/ABAC enterprise
system. While live is disabled and KAI is single-operator / paper-first, the
goal is to harden only the few security-critical governance gates — not to build
a role/policy engine.

## Why this exists

A model output or an agent prompt must never silently influence a productive
decision. KAI already guards the *execution mechanism* (`ExecutionSettings`
validator, `EXECUTION_ENTRY_MODE` kill-switch) and the *release posture*
(`app/release/readiness.py`). This module adds the *governance* answer: **may
this model / this prompt / this agent capability influence a productive decision
at all, and is the decision's audit trail complete enough to prove under which
governance posture it ran.**

Design invariants (identical to `readiness.py`):

- **Fail-closed.** Missing evidence == unmet gate == blocked. `None` never
  passes.
- **Pure / read-only.** No IO, no secret access, no settings mutation, no
  execution. A gate can never itself read a live key — it only inspects the
  registry entry handed to it.
- **Machine- and operator-readable.** Every block carries a stable `code`, the
  offending `field`, and a human `message`.

## The four gates

### 1. Model Registry Gate — `evaluate_model_gate(entry)`
A model may influence productive decisions only when its registry entry has all
of: `model_id`, `version`, `eval_suite_id`, `approval_status`, `risk_rating`,
`owner`, `last_validation_at` — and `approval_status ∈ {shadow_approved,
production_approved}`.

### 2. Prompt Registry Gate — `evaluate_prompt_gate(entry)`
A prompt may influence productive agent decisions only when its registry entry
has all of: `prompt_id`, `prompt_version`, `owner_agent`, `allowed_tools`
(defined), `forbidden_tools` (defined), `output_contract`,
`prompt_injection_eval_status = passed`, and `approval_status` freigegeben
(`∈ {approved, production_approved}`). The allow/forbid tool lists must be
disjoint. `allowed_tools=None` (undefined) blocks; `allowed_tools=()`
(defined-empty) is allowed.

> The prompt approval bar is intentionally stricter than the model bar: a
> shadow-approved prompt is **not** freigegeben, because a prompt drives
> tool-bearing agent behaviour.

### 3. Agent Permission Boundary — `check_agent_capability(cap)` / `check_tool_request(prompt, tool)`
Agents **may**: `analyze`, `warn`, `request_cancel`, `raise_risk_escalation`.
Agents **may not**: `read_live_keys`, `place_live_order`, `grant_own_tools`,
`mutate_registry`, `disable_audit`. Any unknown capability is denied
(fail-closed). `check_tool_request` enforces the prompt's allowlist/denylist —
this is the gate that catches a prompt-injection attempt to escalate into a tool
the prompt was never granted.

### 4. Audit Registry Reference — `validate_decision_audit(ref)`
Every productive decision's audit event must carry a complete
`DecisionRegistryReference`: `model_id`, `model_version`, `prompt_id`,
`prompt_version`, `approval_status`, `registry_hash`. A missing or incomplete
reference is rejected. `registry_hash` is a SHA-256 over the canonical
(model, prompt) registry pair (`compute_registry_hash`) — tamper-evident, same
algorithm as `app/audit/decision_chain.py`.

### Combined — `authorize_productive_decision(model, prompt, requested_tools=...)`
Runs model gate + prompt gate + per-tool checks and, **only** when all pass,
emits a complete `DecisionRegistryReference` (with computed `registry_hash`)
ready for the audit event. Any failing gate yields `authorized=False` and
`registry_reference=None` with aggregated blockers.

## Tests

`tests/unit/test_security_governance_gates.py` (38 cases) covers the five
required adversarial scenarios plus happy paths and fail-closed edges:

1. Agent uses a not-freigegeben prompt → blocked
2. Model without `approval_status` → blocked
3. Prompt-injection tool escalation → denied
4. Agent attempts live-key access → denied
5. Audit event without registry reference → rejected

## Integration (wired — Issue #165)

The gate primitives are now wired into the productive decision path (additive,
fail-closed, no `entry_mode` change):

- **Registry persistence** — `app/security/governance/registry_store.py`:
  append-only JSONL under `artifacts/governance/` for the model + prompt
  registries, with loaders keyed by `(id, version)`. A missing/unknown entry
  resolves to `None` → the gate refuses (fail-closed). The `save_*` writers are
  **operator/CLI-only**; agents have no import path to them and the
  `mutate_registry` capability stays forbidden.
- **Governed append** — `app/orchestrator/governed_decision.py`
  `authorize_and_append_decision(...)`: runs `authorize_productive_decision`
  then `validate_decision_audit` as a **hard gate before the append**. On pass it
  writes the journal record and persists the `DecisionRegistryReference` (incl.
  `registry_hash`) to the governance audit sidecar
  (`decision_governance_audit.jsonl`, keyed by `decision_id`). On fail it writes
  a refusal audit record, **no** journal record, and raises
  `GovernanceRejectedError`. `resolve_and_append_decision(...)` resolves the
  entries from the persisted registries first.
- **Why a sidecar, not the record?** The canonical `DecisionRecord` is
  `extra="forbid", frozen`. Persisting the reference in a parallel audit stream
  keeps the wiring additive and tolerates legacy records without a reference
  (analog to the `decision_chain` legacy gap) — they surface as
  `ungoverned (legacy)` in the report rather than crashing the loader.
- **SENTR `governance-audit` worker mode** — `app/agents/worker.py`: read-only
  report over the journal + sidecar; counts governed / refused / ungoverned
  decisions and raises a finding whose severity tracks refusals
  (`sentr governance-audit`, analog to `sentr kyt-review`).

Existing `append_decision_jsonl` is unchanged (back-compat); callers opt in to
governance explicitly. `tests/unit/test_governance_registry_store.py`,
`test_governed_decision.py`, `test_worker_governance_audit.py` pin the contract.
