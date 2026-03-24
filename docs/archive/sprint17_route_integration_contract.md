# Sprint 17 Contract — Route Integration in analyze-pending

> Canonical reference for the analyze-pending route runner, primary/shadow/control
> execution semantics, and the ABC envelope persistence boundary.
>
> Runtime: `app/research/route_runner.py`, `app/research/active_route.py`,
>          `app/cli/main.py` (analyze-pending)
> Invariants: `docs/contracts.md` I-90–I-93
> Upstream: Sprint 14C (active_route.py), Sprint 14 (ABCInferenceEnvelope, distribution.py)
> Downstream: Sprint 18 (MCP reads route status), Sprint 16 (SignalHandoff)

---

## Purpose

Sprint 17 closes the invariant loop opened in Sprint 14C: the `ActiveRouteState` written by
`route-activate` is now consumed by `analyze-pending`, which executes shadow and control
inference paths per document and writes one `ABCInferenceEnvelope` per successful document
to the audit JSONL.

**Core principle:**
> Primary path is the only productive persistence path.
> Shadow and control are audit-only — they never write to DB, never change routing.
> Route integration ≠ route mutation.

---

## Non-Negotiable Rules

| Rule | Statement | Invariant |
|------|-----------|-----------|
| Primary → DB only | Primary analysis result is the sole DB write; shadow/control results are never persisted to DB | I-92 |
| Shadow/Control → JSONL only | `ABCInferenceEnvelope` is written to audit JSONL only — never to DB | I-93 |
| No APP_LLM_PROVIDER change | analyze-pending MUST NOT modify `APP_LLM_PROVIDER` or any env variable | I-90, I-91 |
| No auto-routing | analyze-pending reads route state; it does not create, modify, or promote route profiles | I-80, I-84 |
| No promotion trigger | ABCInferenceEnvelope data MUST NOT auto-trigger promotion or provider switch | I-99 |
| Route takes precedence over `--shadow-companion` | Active route profile suppresses legacy `--shadow-companion` flag (I-84) | I-84 |
| Failure isolation | Shadow/control provider failure MUST NOT affect primary analysis or DB write | I-92 |

---

## analyze-pending Phase Architecture

```
Phase 1  ─ DB read: get_pending_documents()          [DB session opened + closed]
Phase 2  ─ Primary LLM inference: pipeline.run_batch()  [no DB, HTTP calls]
Phase 2.5─ Route shadow/control: run_route_provider()   [no DB, no writes]
Phase 3  ─ DB write: primary results only (I-92)      [DB session opened + closed]
Phase ABC─ Build + write ABCInferenceEnvelope JSONL (I-93)  [no DB]
```

### Phase 2.5 — Route Shadow/Control Inference

Executes only when an `ActiveRouteState` is present **and** `route_profile ≠ "primary_only"`.

For each successfully analyzed document:
- Each `enabled_shadow_path` → `run_route_provider()` → `(LLMAnalysisOutput | None, error | None)`
- `control_path` (if set) → `run_route_provider()` → same tuple
- Results keyed by `document_id` in `route_shadow_outcome_map` / `route_control_outcome_map`
- `run_route_provider()` never raises — all exceptions captured as error strings

### Phase 3 — Primary DB Write

Uses a new DB session (independent from Phase 1).
Only `res.analysis_result` (primary path result) is persisted via `repo.update_analysis()`.
Shadow/control outcomes in `route_*_outcome_map` are never touched here.

### Phase ABC — ABCInferenceEnvelope Persistence

Executes only when Phase 2.5 produced any outcomes.
For each successful document: calls `build_abc_envelope()` → appends to `abc_envelope_output` JSONL.
`save_abc_inference_envelope_jsonl()` — no DB session involved.
A failed Phase ABC write is logged as a warning and does **not** affect the primary success count.

---

## route_runner.py — Function Contracts

### `map_path_to_provider_name(path_id: str) -> str`

Extracts provider name from a dotted path ID.

| Input | Output |
|-------|--------|
| `"A.external_llm"` | `"external_llm"` |
| `"B.companion"` | `"companion"` |
| `"C.rule"` | `"rule"` |
| `"companion"` (no dot) | `"companion"` |

### `build_path_result_from_llm_output(path_id, provider_name, llm_output, error) -> PathResultEnvelope`

Builds a `PathResultEnvelope` from a raw `LLMAnalysisOutput` (shadow/control paths).
If `llm_output` is `None` and `error` is set → `summary="error: {error}"`, `scores={}`.

### `build_path_result_from_analysis_result(path_id, provider_name, analysis_result) -> PathResultEnvelope`

Builds a `PathResultEnvelope` from a normalized `AnalysisResult` (primary path).
If `analysis_result` is `None` → `summary=None`, `scores={}`.

### `build_comparison_summaries(primary, shadow_results, control_result) -> list[PathComparisonSummary]`

Builds compact A-vs-B and A-vs-C deviation summaries.
Label format: `"A_vs_B"`, `"A_vs_C"`.
Computes `sentiment_match`, `actionable_match`, and `*_delta` deviations for numeric scores.
Returns `[]` when no shadow/control paths exist.

### `build_abc_envelope(document_id, route_state, primary_provider_name, primary_analysis_result, shadow_outcomes, control_outcome) -> ABCInferenceEnvelope`

Builds the complete `ABCInferenceEnvelope` for one document.

- `distribution_metadata.activation_state = "active"` — indicates live route run (vs. `"audit_only"` from Sprint 14 `abc-run` CLI)
- `distribution_metadata.decision_owner = "operator"` — routing is operator-controlled
- Does NOT write to DB (I-93) — caller appends to audit JSONL

### `run_route_provider(provider, document) -> tuple[LLMAnalysisOutput | None, str | None]`

Async. Runs a shadow/control provider against a document.
Never raises — all exceptions are captured as `(None, str(exc))`.
Primary path is unaffected by any exception here (I-92).

---

## Path Semantics

| Path | Role | Output goes to | DB write? |
|------|------|---------------|-----------|
| A (primary) | Production truth | DB via `update_analysis()` | YES (I-92) |
| B (shadow) | Audit comparison | `ABCInferenceEnvelope` → JSONL | NO (I-93) |
| C (control) | Rule-bound reference | `ABCInferenceEnvelope` → JSONL | NO (I-93) |

**Key distinctions:**
- `envelope output ≠ production persistence` — JSONL is audit context only
- `shadow/control ≠ secondary truth` — these paths carry no routing weight
- `route integration ≠ route mutation` — analyze-pending reads state, never writes it

---

## ABCInferenceEnvelope Output Path

From `ActiveRouteState.abc_envelope_output` (default: `artifacts/abc_envelopes/envelopes.jsonl`).

```python
# Phase ABC: append envelopes to operator-configured path
abc_out = Path(active_route.abc_envelope_output)
saved_abc_path = save_abc_inference_envelope_jsonl(abc_envelopes, abc_out)
```

`save_abc_inference_envelope_jsonl()` appends (does not overwrite) each run's envelopes.
`DistributionMetadata.activation_state = "active"` marks these as live-run artifacts.

---

## `--shadow-companion` Suppression (I-84)

When an active route profile provides shadow paths, `--shadow-companion` is silently ignored:

```python
if shadow_companion and not route_shadow_providers:
    # only runs if route produced no shadow providers
    ...
```

This ensures that the route profile is the single source of truth for shadow configuration.
The legacy flag remains as a fallback for environments without a route profile.

---

## DistributionMetadata.activation_state Values

| Value | Set by | Meaning |
|-------|--------|---------|
| `"active"` | `route_runner.build_abc_envelope()` | Live route run via analyze-pending |
| `"audit_only"` | Sprint 14 `abc-run` CLI | Post-hoc construction from existing artifacts |

---

## Relation to Other Layers

```
Sprint 14C (route-activate)          Sprint 17 (analyze-pending route integration)
──────────────────────────           ─────────────────────────────────────────────
Writes ActiveRouteState JSON         Reads ActiveRouteState JSON
route-activate / route-deactivate    analyze-pending --provider <primary>
APP_LLM_PROVIDER unchanged           APP_LLM_PROVIDER unchanged
```

```
Sprint 17 (ABCInferenceEnvelope)     Sprint 18 (MCP: get_active_route_status)
────────────────────────────────     ─────────────────────────────────────────
Writes audit JSONL (I-93)            Reads active_route_profile.json (read-only)
No DB write for shadow/control       No DB write, no routing change
```

---

## Invariants Implemented (I-90–I-93)

| ID | Statement | Where enforced |
|----|-----------|---------------|
| I-90 | ActiveRouteState writes to state file only. NEVER `.env`, `settings.py`, `APP_LLM_PROVIDER`. | `active_route.activate_route_profile()` |
| I-91 | `route-activate` does NOT change `APP_LLM_PROVIDER`. Primary provider selection is operator-only. | `active_route.py` + CLI test |
| I-92 | Primary results → DB only. Shadow/control → audit JSONL only. | Phase 3 (DB write) vs. Phase 2.5 (JSONL only) |
| I-93 | `ABCInferenceEnvelope` → JSONL only. No DB writes. No routing changes. | Phase ABC: `save_abc_inference_envelope_jsonl()` only |

---

## Security Boundaries

```
What analyze-pending does:
  ✅ Reads ActiveRouteState from artifacts/active_route_profile.json
  ✅ Creates shadow/control providers from route config
  ✅ Runs shadow/control inference per document
  ✅ Writes primary results to DB
  ✅ Writes ABCInferenceEnvelope per document to audit JSONL

What analyze-pending does NOT do:
  ✗ Write APP_LLM_PROVIDER or any env variable (I-91)
  ✗ Write shadow/control results to DB (I-92, I-93)
  ✗ Create, modify, or delete route profiles (I-80, I-84)
  ✗ Trigger promotion or routing changes (I-99)
  ✗ Execute trades (I-98, I-101)
```

---

## Inconsistencies Found and Resolved

| Inconsistency | Resolution |
|--------------|------------|
| Sprint 17 content embedded in sprint14_inference_distribution_contract.md | Standalone `docs/sprint17_route_integration_contract.md` (this file) created |
| No §29 for Sprint 17 in contracts.md | §29 added to `docs/contracts.md` |
| TASKLIST.md Sprint-17 block didn't reference contract doc | Updated to reference this file |

---

## Sprint 17 Completion Criteria

```
Sprint 17 gilt als abgeschlossen wenn:
  - [x] 17.1: route_runner.py — 6 Funktionen implementiert ✅
  - [x] 17.2: test_route_runner.py — 25 Tests gruen ✅
  - [x] 17.3: analyze-pending Phase 2.5 + Phase ABC — 6 CLI-Tests gruen ✅
  - [x] 17.4: I-92: Primary → DB only, Shadow/Control → JSONL only ✅
  - [x] 17.5: I-93: ABCInferenceEnvelope → JSONL only, kein DB-Write ✅
  - [x] 17.6: --shadow-companion suppressed by active route (I-84) ✅
  - [x] 17.7: run_route_provider() never raises — exception captured ✅
  - [x] 17.8: activation_state="active" in DistributionMetadata ✅
  - [x] 17.9: docs/sprint17_route_integration_contract.md ✅
  - [x] 17.10: I-90–I-93 in docs/contracts.md §29 ✅
  - [x] ruff check . sauber ✅
  - [x] pytest passing (kein Rueckschritt) ✅
  - [x] Kein Auto-Routing ✅
  - [x] Kein DB-Write für Shadow/Control ✅
  - [x] Primary-Ergebnis nicht überschreibbar durch Shadow/Control ✅
```
