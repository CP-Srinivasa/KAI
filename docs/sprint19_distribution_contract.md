# Sprint 19 — Route-Aware Signal Distribution Contract

**Status: ✅ Implemented**
**Date: 2026-03-20**
**Invariants: I-109–I-115**
**Contracts.md: §31**

---

## Scope

Sprint 19 formalises delivery-class separation across the A/B/C route architecture.
It adds route-aware classification types and aggregation functions to `distribution.py`
and extends `execution_handoff.py` with `DeliveryClassification` and `classify_delivery_for_route()`.

**Hard boundaries:**
- No new trade engine
- No auto-routing
- No trading execution
- No second signal-qualification stack

---

## Architecture

### Delivery Classification (execution_handoff.py)

```python
@dataclass(frozen=True)
class DeliveryClassification:
    path_type: str           # "primary" | "shadow" | "control" | "unknown"
    delivery_class: str      # "productive_handoff" | "audit_only" | "comparison_only"
    consumer_visibility: str # "visible" | "hidden"
    audit_visibility: str    # always "visible"

def classify_delivery_for_route(route_path: str) -> DeliveryClassification:
    """A.* → primary/productive_handoff/visible
       B.* → shadow/audit_only/hidden
       C.* → control/comparison_only/hidden
       unknown → unknown/audit_only/hidden (fail closed)
    """
```

`DeliveryClassification` fields are populated into `SignalHandoff` at construction time
via `create_signal_handoff()`. They are derived-only metadata — never operator inputs (I-110).

### SignalHandoff Extension

`SignalHandoff` (frozen=True) carries four new fields (I-110):

| Field | Values |
|-------|--------|
| `path_type` | `"primary"` / `"shadow"` / `"control"` / `"unknown"` |
| `delivery_class` | `"productive_handoff"` / `"audit_only"` / `"comparison_only"` |
| `consumer_visibility` | `"visible"` (primary) / `"hidden"` (shadow/control) |
| `audit_visibility` | always `"visible"` |

### Delivery Class Constants (distribution.py)

```python
_DELIVERY_CLASS_PRODUCTION = "production_delivery"   # A.* → classify_delivery_class()
_DELIVERY_CLASS_SHADOW_AUDIT = "shadow_audit"         # B.*
_DELIVERY_CLASS_CONTROL_AUDIT = "control_audit"       # C.*
```

These string constants are used by `classify_delivery_class()` and
`build_route_aware_distribution_summary()` for counting and reporting.

Note: `classify_delivery_for_route()` (execution_handoff) uses different strings
(`"productive_handoff"`, `"audit_only"`, `"comparison_only"`) for per-signal metadata.
`classify_delivery_class()` (distribution) maps to the simpler production/audit/control strings
for summary counting.

### RouteAwareDistributionSummary

```python
@dataclass
class RouteAwareDistributionSummary:
    production_count: int    # A.* signals
    shadow_audit_count: int  # B.* signals
    control_audit_count: int # C.* signals
    total_count: int
    generated_at: str

def build_route_aware_distribution_summary(
    handoffs: list[SignalHandoff],
) -> RouteAwareDistributionSummary:
    """Counts by delivery class. Shadow/control never mixed into production (I-113)."""
```

### DistributionClassificationReport

```python
@dataclass
class DistributionClassificationReport:
    primary_handoff: ExecutionHandoffReport       # productive surface only
    audit_outputs: list[DistributionAuditRecord]  # shadow + control, read-only audit
    route_profiles: list[str]
    active_primary_paths: list[str]
    execution_enabled: bool = False               # always False (I-115)
    write_back_allowed: bool = False

def build_distribution_classification_report(
    signals: list[SignalCandidate],
    documents: list[CanonicalDocument],
    envelopes: list[ABCInferenceEnvelope],
) -> DistributionClassificationReport:
    """Composes primary handoff + audit-only shadow/control from ABCInferenceEnvelope artifacts.
    Shadow/control MUST NOT be promoted to consumer-visible (I-113).
    """
```

### DistributionAuditRecord

Per-path audit record derived from `ABCInferenceEnvelope.shadow_results` and `control_result`:

```python
@dataclass
class DistributionAuditRecord:
    document_id: str; route_profile: str; active_primary_path: str
    path_id: str; provider: str; analysis_source: str
    path_type: str; delivery_class: str
    consumer_visibility: str; audit_visibility: str
    comparison_labels: list[str]   # e.g. ["A_vs_B"] — from PathComparisonSummary
    summary: str | None
    result_ref: str | None
```

---

## Invariants I-109–I-115

| ID | Rule | Where enforced |
|----|------|----------------|
| I-109 | Route-aware delivery classification MUST be derived from `route_path` only. No new signal qualification or rescoring. | `classify_delivery_for_route()`, `classify_delivery_class()` |
| I-110 | `SignalHandoff.path_type/delivery_class/consumer_visibility/audit_visibility` are derived-only metadata, never operator inputs. | `create_signal_handoff()` — computed from route_path |
| I-111 | `DistributionClassificationReport` MUST reuse `ExecutionHandoffReport` + persisted `ABCInferenceEnvelope`. Shadow/control MUST NOT be promoted into consumer-visible handoffs. | `build_distribution_classification_report()` — primary_handoff separate from audit_outputs |
| I-112 | Route-aware delivery reports are read-only. No write-back, no trade submission, no auto-routing, no auto-promotion. | `DistributionClassificationReport.execution_enabled = False` |
| I-113 | Shadow and control counts are tracked independently. Never mixed into production delivery. | `build_route_aware_distribution_summary()` — separate counters |
| I-114 | `build_route_aware_distribution_summary()` counts all three tiers. total_count = production + shadow_audit + control_audit. | `build_route_aware_distribution_summary()` |
| I-115 | `DistributionClassificationReport.execution_enabled` is always `False`. No platform code changes this. | dataclass default |

---

## Path Semantics

| Path prefix | path_type | delivery_class | consumer_visibility | Production DB? |
|-------------|-----------|----------------|---------------------|----------------|
| A.* | primary | productive_handoff | visible | ✅ |
| B.* | shadow | audit_only | hidden | ❌ |
| C.* | control | comparison_only | hidden | ❌ |
| unknown | unknown | audit_only | hidden | ❌ |

---

## Files Changed

| File | Change |
|------|--------|
| `app/research/execution_handoff.py` | Added `DeliveryClassification`, `classify_delivery_for_route()`, 4 new `SignalHandoff` fields |
| `app/research/distribution.py` | Added `_DELIVERY_CLASS_*` constants, `classify_delivery_class()`, `RouteAwareDistributionSummary`, `build_route_aware_distribution_summary()`, `DistributionAuditRecord`, `DistributionClassificationReport`, `build_distribution_classification_report()`, `save_distribution_classification_report()` |
| `tests/unit/test_distribution.py` | 21 new tests for Sprint 19 additions |
| `docs/contracts.md` | §31 added (I-109–I-115) |
| `docs/intelligence_architecture.md` | Sprint 19 row added |
| `AGENTS.md` | P25 added |
| `TASKLIST.md` | Sprint 19 block added |

---

## Sprint 19 Completion Criteria

- [x] 19.1: `DeliveryClassification` + `classify_delivery_for_route()` in `execution_handoff.py` ✅
- [x] 19.2: `SignalHandoff` extended with `path_type`, `delivery_class`, `consumer_visibility`, `audit_visibility` ✅
- [x] 19.3: `classify_delivery_class()` + `_DELIVERY_CLASS_*` constants in `distribution.py` ✅
- [x] 19.4: `RouteAwareDistributionSummary` + `build_route_aware_distribution_summary()` ✅
- [x] 19.5: `DistributionAuditRecord` + `DistributionClassificationReport` + `build_distribution_classification_report()` ✅
- [x] 19.6: `save_distribution_classification_report()` ✅
- [x] 19.7: Tests grün (21 neue Tests für Sprint-19 Additions) ✅
- [x] 19.8: I-109–I-115 in `docs/contracts.md` §31 ✅
- [x] 19.9: `docs/sprint19_distribution_contract.md` vollständig ✅
- [x] 19.10: AGENTS.md P25 eingetragen ✅
- [x] 19.11: TASKLIST.md Sprint-19 vollständig ✅
- [x] 19.12: `intelligence_architecture.md` Sprint-19 Zeile ✅
- [x] ruff check . sauber ✅
- [x] pytest passing (kein Rückschritt) ✅
- [x] Kein Auto-Routing eingebaut ✅
- [x] Kein Auto-Promotion eingebaut ✅
- [x] Keine Trading-Execution ✅
- [x] shadow/control niemals in production-visible gemischt ✅
