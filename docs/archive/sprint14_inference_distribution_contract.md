# Sprint 14 Contract - Controlled A/B/C Inference Profiles and Signal Distribution

> Canonical reference for Sprint 14 A/B/C inference profiles, distribution artifacts,
> and audit-safe routing envelopes.
>
> Upstream contracts: `docs/contracts.md §26`, invariants I-80 through I-93 (Sprint 14C).
> Upstream Sprint 10: `docs/sprint10_shadow_run_contract.md`
> Upstream Sprint 13: `docs/sprint13_comparison_contract.md`

---

## Purpose

Sprint 14 defines the next control layer above shadow, comparison, promotion, and upgrade-cycle
artifacts:

- how three inference paths are described without changing live routing
- how their outputs are packaged for audit and operator review
- how signal and research outputs are distributed without turning distribution into decision logic

Sprint 14 is intentionally contract-first. It does **not** introduce auto-routing, auto-promotion,
new provider families, or live execution changes.

---

## A/B/C Path Semantics

| Path | Meaning | Typical tier | Ownership |
|------|---------|--------------|-----------|
| `A` | Primary production path | `EXTERNAL_LLM`, `INTERNAL`, or `RULE` | Only path allowed to update persisted document analysis |
| `B` | Shadow or trained companion path | `INTERNAL` | Audit and comparison only |
| `C` | Control path | `RULE` | Baseline and drift-reference only |

Explicit interpretation:

- `A = primary path`
- `B = shadow/trained companion path`
- `C = control/rule/fallback path`

The three paths stay logically separated even when evaluated on the same document.

---

## Core Separation - Non-Negotiable

| Concept | What it is | What it is NOT |
|---------|------------|----------------|
| Primary path (`A`) | Current operator-selected production path | Not an automatic winner over B/C by comparison alone |
| Shadow path (`B`) | Sidecar analysis for trained companion validation | Not a DB overwrite path |
| Control path (`C`) | Deterministic rule/fallback reference | Not a production override |
| Distribution | Controlled emission of artifacts to output channels | Not a routing or promotion decision |
| Route profile | Declarative intent for allowed path layout | Not a live switch by itself |

Two explicit statements remain binding:

- Distribution does not equal decision.
- Routing configuration does not equal activation by itself.

---

## Sprint 14 Scope

### What Sprint 14 defines

1. **Inference profiles / route modes**
2. **Signal distribution schema**
3. **A/B/C result envelope**
4. **Audit invariants for primary, shadow, and control outputs**

### What Sprint 14 does not define

- no automatic route switching
- no automatic promotion
- no provider rewrite
- no new persistence architecture
- no trading execution

---

## Inference Profiles / Route Modes

Sprint 14 keeps route modes small and explicit.

| `route_profile` | Active paths | Intended use |
|-----------------|--------------|--------------|
| `primary_only` | `A` | Current production behavior without sidecars |
| `primary_with_shadow` | `A + B` | Real-world comparison against trained companion |
| `primary_with_control` | `A + C` | Real-world comparison against deterministic control |
| `primary_with_shadow_and_control` | `A + B + C` | Full audit envelope for operator review |

### Path identifiers

The contract distinguishes logical path labels from provider/tier identity:

| Path ID | Allowed `analysis_source` | Typical provider |
|---------|---------------------------|------------------|
| `A.external_llm` | `EXTERNAL_LLM` | `openai`, `anthropic`, `gemini` |
| `A.internal` | `INTERNAL` | `companion`, `internal` |
| `A.rule` | `RULE` | `fallback`, `rule` |
| `B.companion` | `INTERNAL` | `companion` |
| `B.internal` | `INTERNAL` | `internal` |
| `C.rule` | `RULE` | `fallback`, `rule` |

`C` is intentionally rule-bound in Sprint 14. No external or promoted model may occupy the control path.

---

## Routing / Distribution Artifact Contract

Sprint 14 introduces a declarative profile artifact. The contract is defined now; runtime loading
and CLI wiring remain follow-up implementation work.

### `InferenceRouteProfile`

```python
@dataclass
class InferenceRouteProfile:
    profile_name: str
    route_profile: str                    # one of the four route modes above
    active_primary_path: str              # e.g. "A.external_llm"
    enabled_shadow_paths: list[str]       # e.g. ["B.companion"]
    control_path: str | None = None       # "C.rule" or None
    distribution_targets: list[DistributionTarget] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
```

### Validation rules

- `active_primary_path` must always be an `A.*` path.
- `enabled_shadow_paths` may only contain `B.*` paths.
- `control_path` may only be `C.rule` or `None`.
- `route_profile` and enabled paths must agree.
- A route profile is declarative only. It does not change `APP_LLM_PROVIDER`, DB state, or CLI defaults.

---

## Signal Distribution Schema

Distribution is modeled as explicit targets instead of implicit side effects.

### `DistributionTarget`

```python
@dataclass
class DistributionTarget:
    channel: str                          # see table below
    include_paths: list[str]              # subset of ["A", "B", "C", "comparison"]
    mode: str                             # "primary_only", "audit_only", "comparison_only", "audit_appendix"
    artifact_path: str | None = None      # optional output file / report path
```

### Allowed channels

| `channel` | Intended payload | Default inclusion |
|-----------|------------------|-------------------|
| `research_brief` | Operator-facing brief | `A` only |
| `signal_candidates` | Operator-facing signal list | `A` only |
| `shadow_audit_jsonl` | Sidecar shadow traces | `B` only |
| `comparison_report_json` | A-vs-B and/or A-vs-C deltas | `comparison` |
| `upgrade_cycle_report_json` | Upgrade-cycle status summary | `comparison`, optional `B` |
| `promotion_audit_json` | Promotion audit linkage only | `comparison` |

### Distribution rules

- `research_brief` and `signal_candidates` remain primary-owned in Sprint 14.
- `B` and `C` may be distributed only through audit-oriented channels unless a later spec says otherwise.
- Distribution targets must preserve `document_id`, provider, `analysis_source`, and route path for traceability.
- Writing an output channel does not authorize promotion, routing change, or execution.

---

## A/B/C Result Envelope Contract

Sprint 14 does not replace `AnalysisResult`, `ShadowRunRecord`, or `EvaluationComparisonReport`.
Instead it wraps references and compatible snapshots in one audit surface.

### `ABCInferenceEnvelope`

```python
@dataclass
class PathResultEnvelope:
    path_id: str                          # e.g. "A.external_llm", "B.companion", "C.rule"
    provider: str                         # actual provider name / winner name
    analysis_source: str                  # "external_llm" | "internal" | "rule"
    result_ref: str | None = None         # path to persisted artifact when present
    summary: str | None = None            # short operator-facing summary
    scores: dict[str, object] = field(default_factory=dict)


@dataclass
class PathComparisonSummary:
    compared_path: str                    # B.* or C.rule
    sentiment_match: bool | None = None
    actionable_match: bool | None = None
    tag_overlap: float | None = None
    deviations: dict[str, float] = field(default_factory=dict)   # priority_delta, relevance_delta, impact_delta
    comparison_report_path: str | None = None                    # optional EvaluationComparisonReport link


@dataclass
class DistributionMetadata:
    route_profile: str
    active_primary_path: str
    distribution_targets: list[DistributionTarget]
    decision_owner: str = "operator"
    activation_state: str = "audit_only"


@dataclass
class ABCInferenceEnvelope:
    document_id: str
    route_profile: str
    primary_result: PathResultEnvelope
    shadow_results: list[PathResultEnvelope] = field(default_factory=list)
    control_result: PathResultEnvelope | None = None
    comparison_summary: list[PathComparisonSummary] = field(default_factory=list)
    distribution_metadata: DistributionMetadata | None = None
```

### Envelope rules

- `primary_result` is mandatory and references the persisted production outcome.
- `shadow_results` are optional and may include multiple `B.*` entries in later phases, but Sprint 14 assumes one companion-like sidecar at most.
- `control_result` is optional and rule-based only.
- `comparison_summary` is additive audit context. It never changes primary persistence.
- `distribution_metadata.activation_state` is informational. It must not auto-activate routing.

---

## Compatibility with Existing Artifacts

Sprint 14 reuses existing artifact families rather than introducing a second evaluation stack.

| Existing artifact | Sprint 14 role |
|-------------------|----------------|
| `CanonicalDocument` + `AnalysisResult` | authoritative `A` result source |
| `ShadowRunRecord` | `B` trace source |
| `EvaluationComparisonReport` | structured A-vs-B or baseline-vs-candidate comparison |
| `PromotionRecord` | downstream audit link only |
| `UpgradeCycleReport` | upgrade lifecycle summary, not route activation |

This keeps the implementation path additive and avoids parallel architecture.

---

## Invariants

See `docs/contracts.md §26` for canonical invariant text. Sprint 14 depends on these principles:

- no auto-routing
- no auto-promotion
- no productive overwrite by shadow or control
- output distribution remains audit-traceable

---

## Implementation Readiness

Sprint 14 is ready for controlled implementation in small steps:

1. route profile artifact loader / validator
2. A/B/C envelope builder using existing primary/shadow/comparison artifacts
3. distribution-channel writer limited to audit-safe targets
4. CLI surface that exposes profiles and envelopes without changing routing

No step in Sprint 14 authorizes auto-switching or decision automation.

---

## Additional Invariants (I-88–I-89)

These extend `docs/contracts.md §Immutable Invariants`:

| ID | Rule |
|----|------|
| I-88 | `ABCInferenceEnvelope` is a pure composition artifact. Creating or saving an envelope MUST NOT call `analyze()`, `apply_to_document()`, `update_analysis()`, or any DB mutation. All inputs come from already-persisted artifacts. |
| I-89 | `create-inference-profile` CLI produces a declarative `InferenceRouteProfile` JSON file only. It MUST NOT trigger analysis, routing changes, provider instantiation, DB calls, or any modification to `APP_LLM_PROVIDER`. |

---

## Part A — Module Contract Signatures

### A1. `app/research/inference_profile.py`

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import json


@dataclass
class DistributionTarget:
    channel: str           # research_brief | signal_candidates | shadow_audit_jsonl |
                           # comparison_report_json | upgrade_cycle_report_json |
                           # promotion_audit_json
    include_paths: list[str]  # subset of ["A", "B", "C", "comparison"]
    mode: str              # primary_only | audit_only | comparison_only | audit_appendix
    artifact_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "include_paths": self.include_paths,
            "mode": self.mode,
            "artifact_path": self.artifact_path,
        }


@dataclass
class InferenceRouteProfile:
    profile_name: str
    route_profile: str                         # primary_only | primary_with_shadow |
                                               # primary_with_control |
                                               # primary_with_shadow_and_control
    active_primary_path: str                   # e.g. "A.external_llm"
    enabled_shadow_paths: list[str]            # e.g. ["B.companion"] or []
    control_path: str | None = None            # "C.rule" or None
    distribution_targets: list[DistributionTarget] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict:
        return {
            "report_type": "inference_route_profile",
            "profile_name": self.profile_name,
            "route_profile": self.route_profile,
            "active_primary_path": self.active_primary_path,
            "enabled_shadow_paths": self.enabled_shadow_paths,
            "control_path": self.control_path,
            "distribution_targets": [t.to_dict() for t in self.distribution_targets],
            "notes": self.notes,
        }


VALID_ROUTE_PROFILES = frozenset({
    "primary_only",
    "primary_with_shadow",
    "primary_with_control",
    "primary_with_shadow_and_control",
})


def save_inference_route_profile(
    profile: InferenceRouteProfile,
    output_path: Path | str,
) -> Path:
    """
    Save a route profile to JSON. Does NOT change any routing state.
    Raises ValueError if route_profile is not a valid value.
    """
    if profile.route_profile not in VALID_ROUTE_PROFILES:
        raise ValueError(
            f"Invalid route_profile: {profile.route_profile!r}. "
            f"Must be one of: {sorted(VALID_ROUTE_PROFILES)}"
        )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile.to_json_dict(), indent=2, sort_keys=True))
    return out


def load_inference_route_profile(path: Path | str) -> InferenceRouteProfile:
    """Load and reconstruct a saved route profile. Read-only, no side effects."""
    data = json.loads(Path(path).read_text())
    return InferenceRouteProfile(
        profile_name=data["profile_name"],
        route_profile=data["route_profile"],
        active_primary_path=data["active_primary_path"],
        enabled_shadow_paths=data.get("enabled_shadow_paths", []),
        control_path=data.get("control_path"),
        distribution_targets=[
            DistributionTarget(
                channel=t["channel"],
                include_paths=t["include_paths"],
                mode=t["mode"],
                artifact_path=t.get("artifact_path"),
            )
            for t in data.get("distribution_targets", [])
        ],
        notes=data.get("notes", []),
    )
```

### A2. `app/research/abc_result.py`

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import json


@dataclass
class PathResultEnvelope:
    path_id: str              # "A.external_llm" | "B.companion" | "C.rule"
    provider: str             # actual provider_name
    analysis_source: str      # "external_llm" | "internal" | "rule"
    result_ref: str | None = None    # path to persisted artifact (e.g. shadow JSONL)
    summary: str | None = None
    scores: dict[str, object] = field(default_factory=dict)
    # scores may include: priority_score, sentiment_label, relevance_score,
    #                     impact_score, actionable, tags


@dataclass
class PathComparisonSummary:
    compared_path: str             # e.g. "A_vs_B" | "A_vs_C"
    sentiment_match: bool | None = None
    actionable_match: bool | None = None
    tag_overlap: float | None = None
    deviations: dict[str, float] = field(default_factory=dict)
    # deviations: priority_delta, relevance_delta, impact_delta
    comparison_report_path: str | None = None  # link to EvaluationComparisonReport


@dataclass
class DistributionMetadata:
    route_profile: str
    active_primary_path: str
    distribution_targets: list[str] = field(default_factory=list)
    # distribution_targets: list of channel names that received this envelope
    decision_owner: str = "operator"       # always "operator" — no auto-decision
    activation_state: str = "audit_only"   # fixed in Sprint 14


@dataclass
class ABCInferenceEnvelope:
    document_id: str
    route_profile: str
    primary_result: PathResultEnvelope
    shadow_results: list[PathResultEnvelope] = field(default_factory=list)
    control_result: PathResultEnvelope | None = None
    comparison_summary: list[PathComparisonSummary] = field(default_factory=list)
    distribution_metadata: DistributionMetadata | None = None

    def to_json_dict(self) -> dict:
        def _envelope(e: PathResultEnvelope) -> dict:
            return {
                "path_id": e.path_id,
                "provider": e.provider,
                "analysis_source": e.analysis_source,
                "result_ref": e.result_ref,
                "summary": e.summary,
                "scores": e.scores,
            }

        def _comparison(c: PathComparisonSummary) -> dict:
            return {
                "compared_path": c.compared_path,
                "sentiment_match": c.sentiment_match,
                "actionable_match": c.actionable_match,
                "tag_overlap": c.tag_overlap,
                "deviations": c.deviations,
                "comparison_report_path": c.comparison_report_path,
            }

        return {
            "report_type": "abc_inference_envelope",
            "document_id": self.document_id,
            "route_profile": self.route_profile,
            "primary_result": _envelope(self.primary_result),
            "shadow_results": [_envelope(s) for s in self.shadow_results],
            "control_result": (
                _envelope(self.control_result) if self.control_result else None
            ),
            "comparison_summary": [_comparison(c) for c in self.comparison_summary],
            "distribution_metadata": {
                "route_profile": self.distribution_metadata.route_profile,
                "active_primary_path": self.distribution_metadata.active_primary_path,
                "distribution_targets": self.distribution_metadata.distribution_targets,
                "decision_owner": self.distribution_metadata.decision_owner,
                "activation_state": self.distribution_metadata.activation_state,
            } if self.distribution_metadata else None,
        }


def save_abc_inference_envelope(
    envelope: ABCInferenceEnvelope,
    output_path: Path | str,
) -> Path:
    """Write a single ABCInferenceEnvelope to JSON. Does NOT write to the DB (I-88)."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(envelope.to_json_dict(), indent=2, sort_keys=True))
    return out


def save_abc_inference_envelope_jsonl(
    envelopes: list[ABCInferenceEnvelope],
    output_path: Path | str,
) -> Path:
    """Append multiple ABCInferenceEnvelopes to a JSONL file (I-38: no in-place overwrite)."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for envelope in envelopes:
            f.write(json.dumps(envelope.to_json_dict()) + "\n")
    return out
```

---

## Part B — CLI Contract

### B1. `research create-inference-profile`

```
research create-inference-profile PROFILE_NAME
    --route-profile TEXT    (required) primary_only | primary_with_shadow |
                            primary_with_control | primary_with_shadow_and_control
    --primary-path TEXT     (default: "A.external_llm")
    --shadow-path TEXT      (repeatable, e.g. "B.companion")
    --control-path TEXT     (default: None)
    --out PATH              (default: inference_route_profile.json)
    --note TEXT             (repeatable)
```

Behavior:
- Validates `--route-profile` value; Exit 1 if unknown
- Creates `InferenceRouteProfile` → calls `save_inference_route_profile()`
- Prints profile summary table + disclaimer:
  `"Profile is inert — no routing change applied (I-80, I-84, I-89)"`
- Exit 0 on success

Constraints:
- NO DB call, NO LLM call, NO provider instantiation (I-89)
- `APP_LLM_PROVIDER` remains unchanged (I-80)

### B2. `research abc-run`

```
research abc-run DOCUMENT_ID
    --profile PATH          (required) path to inference_route_profile.json
    --shadow-jsonl PATH     (optional) shadow JSONL for B-path result lookup
    --comparison-report PATH (optional) EvaluationComparisonReport JSON for comparison_summary
    --out PATH              (default: abc_envelope.json)
```

Behavior:
1. Loads `InferenceRouteProfile` from `--profile` → Exit 1 if not found
2. Reads B-path result from `--shadow-jsonl` when `route_profile` includes shadow
   → Exit 1 if `--shadow-jsonl` not found when needed
   → Exit 1 if `document_id` not found in JSONL
3. Loads comparison report from `--comparison-report` when provided
4. Builds `ABCInferenceEnvelope` → writes to `--out`
5. Prints envelope summary table
6. Exit 0 on success

Constraints:
- NO `apply_to_document()`, NO `update_analysis()`, NO DB writes (I-88)
- Zero DB mutations — pure file I/O + composition

---

## Part C — Codex Specs

### Codex-Spec 14.3 — inference_profile.py

```
## Task: Sprint 14.3 — app/research/inference_profile.py

Agent: Codex
Phase: Sprint 14
Modul: app/research/inference_profile.py (NEU)
       tests/unit/test_inference_profile.py (NEU)
Typ: feature

Spec-Referenz: docs/sprint14_inference_distribution_contract.md Part A1
               docs/contracts.md §26a, I-80, I-84, I-89

Zu implementieren (exakt nach Part A1 oben):
  - DistributionTarget dataclass + to_dict()
  - InferenceRouteProfile dataclass + to_json_dict()
  - VALID_ROUTE_PROFILES frozenset
  - save_inference_route_profile(profile, output_path) -> Path
    (raises ValueError wenn route_profile ungueltig)
  - load_inference_route_profile(path) -> InferenceRouteProfile

Constraints:
  - KEIN DB-Aufruf, KEIN LLM-Call, KEIN Provider-Import
  - save_inference_route_profile() darf routing NICHT aendern (I-80)
  - report_type="inference_route_profile" im JSON (Pflichtfeld)
  - Alle Felder JSON-serialisierbar

Tests (>= 8):
  test_inference_route_profile_to_json_dict_structure
  test_inference_route_profile_report_type_always_present
  test_inference_route_profile_primary_only
  test_inference_route_profile_with_shadow_and_control
  test_save_inference_route_profile_creates_file
  test_save_inference_route_profile_invalid_route_raises_value_error
  test_load_inference_route_profile_roundtrip
  test_distribution_target_to_dict_structure

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_inference_profile.py gruen (>= 8 Tests)
  - [ ] pytest tests/unit/ gruen (>= 701 Tests, kein Rueckschritt)
  - [ ] Kein Import von app/analysis/ oder app/storage/ in inference_profile.py
  - [ ] save + load Roundtrip verlustfrei fuer alle Felder
```

### Codex-Spec 14.4 — abc_result.py

```
## Task: Sprint 14.4 — app/research/abc_result.py

Agent: Codex
Phase: Sprint 14
Modul: app/research/abc_result.py (NEU)
       tests/unit/test_abc_result.py (NEU)
Typ: feature

Spec-Referenz: docs/sprint14_inference_distribution_contract.md Part A2
               docs/contracts.md §26c, I-81, I-82, I-83, I-85, I-86, I-88

Zu implementieren (exakt nach Part A2 oben):
  - PathResultEnvelope dataclass
  - PathComparisonSummary dataclass
  - DistributionMetadata dataclass
  - ABCInferenceEnvelope dataclass + to_json_dict()
  - save_abc_inference_envelope(envelope, output_path) -> Path
  - save_abc_inference_envelope_jsonl(envelopes, output_path) -> Path
    (APPEND, nicht overwrite — I-38)

Constraints:
  - KEIN apply_to_document() Aufruf (I-88)
  - KEIN update_analysis() Aufruf (I-88)
  - KEIN DB-Import in diesem Modul
  - decision_owner="operator" ist unveraenderlicher Default
  - activation_state="audit_only" ist unveraenderlicher Default in Sprint 14
  - report_type="abc_inference_envelope" im JSON (Pflichtfeld)
  - primary_result ist Pflichtfeld (kein None-Default)

Tests (>= 10):
  test_abc_inference_envelope_to_json_dict_structure
  test_abc_inference_envelope_report_type_always_present
  test_abc_inference_envelope_primary_only_route
  test_abc_inference_envelope_with_shadow_result
  test_abc_inference_envelope_with_control_result
  test_abc_inference_envelope_with_comparison_summary
  test_abc_inference_envelope_with_distribution_metadata
  test_save_abc_inference_envelope_creates_file
  test_save_abc_inference_envelope_jsonl_appends
  test_distribution_metadata_decision_owner_default

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_abc_result.py gruen (>= 10 Tests)
  - [ ] pytest tests/unit/ gruen (kein Rueckschritt)
  - [ ] shadow_results und control_result optional (default: [] / None)
  - [ ] JSONL-Append (nicht overwrite) in save_abc_inference_envelope_jsonl
  - [ ] KEIN DB-Import in abc_result.py
```

### Codex-Spec 14.5 — CLI: create-inference-profile + abc-run

```
## Task: Sprint 14.5 — CLI research create-inference-profile + abc-run

Agent: Codex
Phase: Sprint 14
Modul: app/cli/main.py (ERWEITERN)
       tests/unit/test_cli.py (ERWEITERN)
Typ: feature

Spec-Referenz: docs/sprint14_inference_distribution_contract.md Part B
               docs/contracts.md §26, I-80, I-84, I-88, I-89

VORAUSSETZUNG: Tasks 14.3 + 14.4 muessen gruen sein.

1. research create-inference-profile (Signatur exakt nach Part B1):
   - Validiert --route-profile Wert (VALID_ROUTE_PROFILES) -> Exit 1 wenn ungueltig
   - Erstellt InferenceRouteProfile, ruft save_inference_route_profile()
   - Druckt Tabelle + Disclaimer:
     "Profile is inert — no routing change applied (I-80, I-84, I-89)"
   - Exit 0 bei Erfolg

2. research abc-run (Signatur exakt nach Part B2):
   - Laedt InferenceRouteProfile aus --profile → Exit 1 wenn nicht gefunden
   - Liest shadow JSONL bei --shadow-jsonl (document_id lookup)
   - Laedt comparison_report wenn --comparison-report angegeben
   - Baut ABCInferenceEnvelope, schreibt nach --out
   - Druckt Envelope-Summary-Tabelle
   - Exit 1: --profile nicht gefunden
   - Exit 1: --shadow-jsonl benoetigt aber nicht gefunden
   - Exit 1: document_id nicht in shadow JSONL

Constraints:
  - KEIN apply_to_document() Aufruf (I-88)
  - KEIN update_analysis() Aufruf (I-88)
  - KEIN DB-Aufruf in create-inference-profile (I-89)
  - APP_LLM_PROVIDER unveraendert nach beiden Commands (I-80)
  - Bestehende Commands unveraendert gruen

CLI-Tests (>= 6):
  test_research_create_inference_profile_primary_only
  test_research_create_inference_profile_invalid_route_exits_1
  test_research_create_inference_profile_creates_json_file
  test_research_abc_run_builds_envelope
  test_research_abc_run_missing_profile_exits_1
  test_research_abc_run_with_comparison_report

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_cli.py gruen (bestehende + neue Tests)
  - [ ] pytest tests/unit/ gruen (kein Rueckschritt)
  - [ ] research --help zeigt create-inference-profile und abc-run
  - [ ] Kein Auto-Routing, kein DB-Write in beiden Commands
  - [ ] Disclaimer in create-inference-profile Output sichtbar
```

---

## Part D — Sprint 14 Completion Criteria

```
Sprint 14 gilt als abgeschlossen wenn:
  - [x] 14.1: sprint14_inference_distribution_contract.md erstellt (vollstaendig)
              contracts.md §26 + I-80–I-89 vollstaendig
  - [x] 14.2: intelligence_architecture.md Sprint-14 Abschnitt + Implementierungstabelle
              TASKLIST.md Sprint-14 Ausrichtung
  - [x] 14.3: inference_profile.py + test_inference_profile.py (>= 8 Tests, gruen)
  - [x] 14.4: abc_result.py + test_abc_result.py (>= 10 Tests, gruen)
  - [x] 14.5: CLI create-inference-profile + abc-run + >= 6 Tests, gruen
  - [x] ruff check . sauber
  - [x] pytest passing (>= 808 Tests, kein Rueckschritt)
  - [x] Kein Auto-Routing eingebaut
  - [x] Kein Auto-Promote eingebaut
  - [x] Kein produktives Ueberschreiben durch B/C-Pfad
  - [x] Jeder A/B/C-Output auditierbar: document_id + path_label + provider + analysis_source
  - [x] AGENTS.md: P19 Sprint-14 eingetragen
  - [x] TASKLIST.md: Sprint-14 vollstaendig
```

---

## Sprint 14C — Runtime Route Activation (2026-03-20)

Sprint 14C fulfills invariant I-84 ("a future runtime command will apply an active
InferenceRouteProfile to analyze-pending runs"). It adds operator-driven activation
state without auto-routing or provider changes.

### Canonical Runtime Files Added

| File | Responsibility |
|------|---------------|
| `app/research/active_route.py` | `ActiveRouteState` dataclass, `activate_route_profile()`, `load_active_route_state()`, `deactivate_route_profile()` |

### New Invariants (I-90–I-93)

| ID | Rule |
|----|------|
| I-90 | `route-activate` writes `ActiveRouteState` to a dedicated state file only. MUST NOT write to `.env`, `settings.py`, or `APP_LLM_PROVIDER`. |
| I-91 | `route-activate` and `route-deactivate` do NOT change `APP_LLM_PROVIDER`. Primary provider selection remains the operator's sole responsibility. |
| I-92 | When `analyze-pending` runs with an active shadow route, primary results are written to DB only. Shadow and control outputs go to audit JSONL only. |
| I-93 | `ABCInferenceEnvelope` produced during shadow-enabled `analyze-pending` is written per-document to audit JSONL only — no DB writes, no routing changes. |

### New CLI Commands

| Command | Effect |
|---------|--------|
| `research route-activate <profile.json>` | Loads `InferenceRouteProfile`, writes `ActiveRouteState` to state file. APP_LLM_PROVIDER unchanged. |
| `research route-deactivate` | Deletes the state file. `analyze-pending` returns to `primary_only`. |

### Sprint 14C Completion Criteria

```
Sprint 14C is complete when:
  - [x] active_route.py: ActiveRouteState, activate/load/deactivate — unit-tested (20 tests)
  - [x] CLI route-activate + route-deactivate — CLI-tested (7 tests)
  - [x] I-90–I-93 in docs/contracts.md
  - [x] Sprint 14C section in this contract doc
  - [x] ruff check . passes
  - [x] pytest passing (>= 830 tests, no regression)
  - [x] A/B/C path separation remains intact and auditable
  - [x] APP_LLM_PROVIDER invariant (I-91) explicitly tested
```

---

## Sprint 17 — analyze-pending Route Integration (2026-03-20)

Sprint 17 closes the loop opened by I-92 and I-93: `analyze-pending` now reads the
active route state (written by `route-activate`) and executes shadow/control paths
per-document, writing one `ABCInferenceEnvelope` to the audit JSONL.

### Canonical Runtime Files Added

| File | Responsibility |
|------|---------------|
| `app/research/route_runner.py` | `map_path_to_provider_name()`, `build_path_result_from_llm_output()`, `build_path_result_from_analysis_result()`, `build_comparison_summaries()`, `build_abc_envelope()`, `run_route_provider()` |

### analyze-pending Phase Changes

`analyze-pending` now runs in up to 4 phases:

| Phase | What happens | Session? |
|-------|-------------|---------|
| Phase 1 | Fetch pending docs | DB read |
| Phase 2 | Primary LLM inference (pipeline.run_batch) | None |
| Phase 2.5 | Route shadow/control inference via `run_route_provider()` | None |
| Phase 3 | Write primary results to DB (I-92) | DB write |
| Phase ABC | Build `ABCInferenceEnvelope` per doc, append to JSONL (I-93) | None |

Phase 2.5 and Phase ABC only execute when an active route profile is present
(`artifacts/active_route_profile.json` exists) and the route is not `primary_only`.

### Invariant Closure

| ID | Sprint 14C status | Sprint 17 status |
|----|------------------|-----------------|
| I-92 | Defined | **Implemented**: Phase 3 writes only primary; shadow/control output is in Phase 2.5 dicts |
| I-93 | Defined | **Implemented**: Phase ABC appends `ABCInferenceEnvelope` to JSONL only — no DB calls |

### DistributionMetadata.activation_state

`ABCInferenceEnvelope` built by `route_runner.build_abc_envelope()` sets
`activation_state="active"` — distinct from Sprint 14 `abc-run` CLI which sets
`"audit_only"` (post-hoc construction from artifacts, not a live run).

### Sprint 17 Completion Criteria

```
Sprint 17 is complete when:
  - [x] route_runner.py — unit-tested (25 tests)
  - [x] analyze-pending Phase 2.5 + Phase ABC — CLI-tested (6 tests)
  - [x] I-92: primary → DB only, shadow/control → JSONL only
  - [x] I-93: ABCInferenceEnvelope → JSONL only, no DB writes
  - [x] --shadow-companion suppressed by active route (I-84) — tested
  - [x] ruff check . passes
  - [x] pytest passing (836 tests, no regression)
```

---

## Consistency Notes

**Sprint 13C architecture decision preserved:**
- `evaluation.py` is canonical for `EvaluationComparisonReport`
- `PathComparisonSummary.comparison_report_path` links to saved `EvaluationComparisonReport`
  artifacts produced by Sprint 13

**Sprint 10 shadow run reused:**
- `ShadowRunRecord` (with `deviations` canonical per I-69) is the source for B-path
  results in `abc-run`
- `PathResultEnvelope` for path B mirrors the shadow run record structure

**Routing invariants preserved:**
- `APP_LLM_PROVIDER` remains sole routing mechanism (I-42, I-80)
- No Sprint-14 code writes `APP_LLM_PROVIDER` or `companion_model_endpoint`
- Promotion remains operator-manual (I-36, I-39, I-68)
