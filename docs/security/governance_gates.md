# Governance Gates (SENTR)

**Status:** gate primitives + tests live; **nicht verdrahtet** — der produktive
Aufrufer wurde am 2026-09-04 entfernt (siehe "Integration" unten).
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

## Integration — entfernt am 2026-09-04

Die produktive Verdrahtung (`app/orchestrator/governed_decision.py`, der
Governance-Audit-Sidecar `decision_governance_audit.jsonl` und der SENTR-Modus
`governance-audit`) wurde am 2026-09-04 entfernt: sie hatte fan-in 0 in
Produktion, und der Sidecar-Leser war über keinen Enqueue-Pfad erreichbar
(`_AGENTS["sentr"].modes` kennt den Modus nicht).

Damit sind die Gates in `gates.py`/`models.py` heute **geprüfte, ungenutzte
Primitive**: `authorize_productive_decision` hat keinen Produktionsaufrufer, und
`app/orchestrator/trading_loop.py` importiert `app.security.governance` nicht.
`registry_store.py` (Model-/Prompt-Registry) bleibt als Operator-/CLI-Substrat
stehen. Wer die Gates verdrahtet, baut den Aufrufer neu — vorheriger Stand:
Commit e6c8936.
