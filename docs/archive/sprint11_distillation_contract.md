# Sprint 11 Contract — Distillation Harness und Evaluation Engine

> **Canonical reference** for Sprint-11 distillation readiness harness, shadow-aware
> evaluation engine, and unified distillation manifest.
>
> Upstream contracts: `docs/contracts.md §22`, invariants I-58–I-62.
> Upstream Sprint-10: `docs/sprint10_shadow_run_contract.md`, I-51–I-57.
> Upstream Sprint-8/9: `docs/tuning_promotion_contract.md`, I-40–I-50.

---

## Purpose

Sprint 11 builds the **distillation readiness harness** — a unified layer that combines:

1. **Evaluation engine** — `compare_datasets()` on teacher vs. candidate, already
   implemented in `evaluation.py`. Sprint 11 wires it into a single distillation-aware
   entry point.

2. **Shadow-aware comparison layer** — `compute_shadow_coverage()` reads shadow JSONL
   (from either `shadow-run` CLI or live shadow persistence) and computes aggregate
   divergence stats as additional audit context.

3. **Distillation manifest** — `DistillationReadinessReport` combines the evaluation
   report, promotion validation result, and optional shadow coverage into one structured
   JSON artifact.

Sprint 11 does NOT:
- run training jobs
- modify model weights
- change provider routing
- bypass the `record-promotion` gate

---

## Core Separation — Non-Negotiable

| Concept | What it is | What it is NOT |
|---------|-----------|----------------|
| **Distillation Harness** | Readiness assessment combining teacher/candidate/shadow data | Not a training job |
| **Evaluation Engine** | `compare_datasets()` + `validate_promotion()` — pure metrics | Not a promotion decision |
| **Shadow-aware layer** | `compute_shadow_coverage()` — aggregate divergence from shadow JSONL | Not ground truth, not teacher data |
| **Distillation Manifest** | Structured JSON audit artifact of readiness state | Not a routing change |
| **Shadow Run** | Audit sidecar (Sprint 10) — divergence between primary and companion | Not a teacher signal |

---

## Dataset Role Invariants (from Sprint 6 / I-16 / I-26 / I-29 / I-53)

| Role | `analysis_source` | Eligible as |
|------|-------------------|-------------|
| Teacher | `EXTERNAL_LLM` | Teacher JSONL only |
| Candidate / Internal | `INTERNAL` | Candidate JSONL only |
| Rule baseline | `RULE` | Baseline comparison only |
| Shadow | `record_type=companion_shadow_run` | Audit context only — NEVER teacher or candidate |

---

## Sprint-11 Scope

### What Sprint 11 delivers

1. **New module `app/research/distillation.py`**:
   - `DistillationInputs` (dataclass) — paths to inputs
   - `ShadowCoverageReport` (dataclass) — aggregate shadow divergence stats
   - `DistillationReadinessReport` (dataclass) — full readiness snapshot
   - `compute_shadow_coverage(shadow_path) -> ShadowCoverageReport`
   - `build_distillation_report(inputs) -> DistillationReadinessReport`
   - `save_distillation_manifest(report, path) -> Path`

2. **CLI: `research distillation-check`**:
   - `--teacher PATH` — teacher JSONL (required unless `--eval-report` provided)
   - `--candidate PATH` — candidate JSONL (required unless `--eval-report` provided)
   - `--eval-report PATH` — pre-computed evaluation_report.json (skip `compare_datasets()`)
   - `--shadow PATH` — shadow JSONL (optional, from `shadow-run` or live shadow)
   - `--dataset-type TYPE` — default: `internal_benchmark`
   - `--save-manifest PATH` — persist `DistillationReadinessReport` as JSON

3. **Tests `tests/unit/test_distillation.py`** — unit tests for distillation module (pure, no DB, no HTTP)

4. **CLI tests in `tests/unit/test_cli.py`** — distillation-check CLI tests (mocked file I/O)

5. **This contract document** + `docs/contracts.md §22` + I-58–I-62 +
   `docs/intelligence_architecture.md` Sprint-11 update

### What Sprint 11 does NOT deliver

- No training engine, no fine-tuning API calls, no weight updates
- No new analysis provider, no factory change, no DB migration
- No routing change — `APP_LLM_PROVIDER` never modified
- No bypass of `record-promotion` gate
- No shadow data promoted to teacher or candidate role

---

## Data Model

### DistillationInputs

```python
@dataclass
class DistillationInputs:
    teacher_path: str | None = None       # JSONL with analysis_source=EXTERNAL_LLM
                                          # Required unless eval_report_path is set
    candidate_path: str | None = None     # JSONL with analysis_source=INTERNAL
                                          # Required unless eval_report_path is set
    eval_report_path: str | None = None   # Pre-computed evaluation_report.json
                                          # If set: skips compare_datasets(), loads directly
    shadow_path: str | None = None        # Shadow JSONL (batch or live format)
                                          # NEVER teacher or candidate data
    dataset_type: str = "internal_benchmark"  # Passed to compare_datasets()
```

**Validation rules:**
- If `eval_report_path` is None → both `teacher_path` and `candidate_path` MUST be set.
- `shadow_path` may be any shadow JSONL — both batch format (`shadow.py`) and live format
  (`evaluation.py`) are accepted.
- `shadow_path` MUST NOT point to a teacher JSONL or candidate JSONL.

### ShadowCoverageReport

```python
@dataclass
class ShadowCoverageReport:
    total_records: int              # total shadow JSONL lines parsed
    error_records: int              # lines where shadow/companion result is null/missing
    valid_records: int              # lines with valid deviations/divergence
    sentiment_agreement_rate: float # fraction where sentiment_match=True (valid records only)
    actionable_agreement_rate: float
    avg_priority_diff: float        # mean absolute priority deviation (valid records only)
    avg_relevance_diff: float
    avg_impact_diff: float
    avg_tag_overlap: float

    def to_json_dict(self) -> dict[str, object]: ...
```

### DistillationReadinessReport

```python
@dataclass
class DistillationReadinessReport:
    generated_at: str                           # ISO 8601 UTC
    inputs: DistillationInputs                  # paths used to build this report
    evaluation: EvaluationReport                # from compare_datasets() or loaded JSON
    promotion_validation: PromotionValidation   # from validate_promotion(evaluation.metrics)
    shadow_coverage: ShadowCoverageReport | None = None
    notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]: ...
```

**Note**: `promotion_validation.is_promotable` is informational. A `DistillationReadinessReport`
with `is_promotable=True` does NOT constitute a promotion. The operator must still invoke
`research record-promotion` explicitly (I-58).

---

## Shadow Record Format Normalization

Sprint 10 produced **two shadow JSONL schemas** (known inconsistency, see §Erkannte Inkonsistenzen):

**Batch shadow format** (from `shadow.py` / `shadow-run` CLI):
```json
{
  "document_id": "...",
  "divergence": {
    "priority_diff": 2,
    "relevance_diff": 0.15,
    "impact_diff": 0.10,
    "sentiment_match": true,
    "actionable_match": false,
    "tag_overlap": 0.6
  }
}
```

**Live shadow format** (from `evaluation.py` / `build_shadow_run_record()`):
```json
{
  "record_type": "companion_shadow_run",
  "document_id": "...",
  "deviations": {
    "priority_delta": 2,
    "relevance_delta": 0.15,
    "impact_delta": 0.10,
    "sentiment_match": true,
    "actionable_match": false,
    "tag_overlap": 0.6
  }
}
```

`compute_shadow_coverage()` MUST handle both formats:
- Detects live format via `record.get("record_type") == "companion_shadow_run"`
  → reads from `record["deviations"]` with keys `priority_delta`, `relevance_delta`, `impact_delta`
- Detects batch format via absence of `record_type` field
  → reads from `record["divergence"]` with keys `priority_diff`, `relevance_diff`, `impact_diff`
- A record is counted as `error_record` if neither `deviations` nor `divergence` is present,
  or if the companion/shadow result was null.

---

## Function Signatures

### compute_shadow_coverage

```python
def compute_shadow_coverage(shadow_path: Path | str) -> ShadowCoverageReport:
    """Read shadow JSONL and compute aggregate divergence statistics.

    Accepts both batch shadow format (shadow.py) and live shadow format
    (evaluation.py build_shadow_run_record). Normalizes field names internally.

    Args:
        shadow_path: Path to shadow JSONL file.

    Returns:
        ShadowCoverageReport with aggregate stats across all valid records.

    Rules:
        - No DB reads, no LLM calls, no network.
        - Shadow records are NEVER used as teacher or candidate data.
        - Records with missing/null deviations → counted as error_records.
        - FileNotFoundError if path does not exist.
    """
```

### build_distillation_report

```python
def build_distillation_report(inputs: DistillationInputs) -> DistillationReadinessReport:
    """Build a distillation readiness report from available inputs.

    Logic:
        1. If inputs.eval_report_path is set:
               Load EvaluationReport from JSON (skip compare_datasets).
           Else:
               Require teacher_path + candidate_path.
               Load via load_jsonl() → compare_datasets(dataset_type=inputs.dataset_type).
        2. validate_promotion(evaluation.metrics) → PromotionValidation.
        3. If inputs.shadow_path is set:
               compute_shadow_coverage(shadow_path).
        4. Assemble DistillationReadinessReport.

    Args:
        inputs: DistillationInputs with path configuration.

    Returns:
        DistillationReadinessReport — pure in-memory result.

    Raises:
        ValueError: if neither eval_report_path nor (teacher_path + candidate_path) are set.
        FileNotFoundError: if any specified path does not exist.

    Rules:
        - No DB reads, no LLM calls, no file writes.
        - Pure computation only.
    """
```

### save_distillation_manifest

```python
def save_distillation_manifest(
    report: DistillationReadinessReport,
    path: Path | str,
) -> Path:
    """Persist DistillationReadinessReport as JSON.

    Creates parent directories. Overwrites if file exists.

    Returns: resolved Path of written file.
    """
```

---

## CLI Specification

### `research distillation-check`

```
kai research distillation-check
    [--teacher PATH]
    [--candidate PATH]
    [--eval-report PATH]
    [--shadow PATH]
    [--dataset-type TYPE]
    [--save-manifest PATH]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--teacher` | None | Teacher JSONL path (EXTERNAL_LLM) — required unless `--eval-report` |
| `--candidate` | None | Candidate JSONL path (INTERNAL) — required unless `--eval-report` |
| `--eval-report` | None | Pre-computed evaluation_report.json — skips compare_datasets |
| `--shadow` | None | Shadow JSONL (batch or live format) — optional |
| `--dataset-type` | `internal_benchmark` | Evaluation type |
| `--save-manifest` | None | Save DistillationReadinessReport JSON to this path |

**Output sections:**

1. **Evaluation Metrics** (Rich table, reusing `_build_dataset_evaluation_table()` output):
   ```
   Dataset Evaluation Metrics
   ├── Teacher Rows / Candidate Rows / Paired Documents / Missing Pairs
   ├── Sentiment Agreement / Priority MAE / Relevance MAE / Impact MAE
   ├── Tag Overlap Mean / Actionable Accuracy / False Actionable Rate
   ```

2. **Shadow Coverage** (only if `--shadow` provided):
   ```
   Shadow Coverage Report
   ├── Total Records / Error Records / Valid Records
   ├── Sentiment Agreement Rate / Actionable Agreement Rate
   ├── Avg Priority Diff / Avg Relevance Diff / Avg Impact Diff / Avg Tag Overlap
   ```

3. **Promotion Gate Summary** (Rich table, G1–G6):
   ```
   Companion Promotion Readiness
   ├── G1 Sentiment ... PASS/FAIL
   ├── G2 Priority MAE ... PASS/FAIL
   ├── ...
   ├── G6 False Actionable ... PASS/FAIL
   └── PROMOTABLE / NOT PROMOTABLE
   ```

4. **Manifest saved** (if `--save-manifest`):
   ```
   [dim]Distillation manifest saved: <path>[/dim]
   ```

**Exit codes:**
- 0: always (informational output, not a gate — operator decides)
- 1: file not found or parse error only

**Behavior:**
- `--eval-report` and `--teacher/--candidate` are mutually exclusive sources.
  If both `--eval-report` AND `--teacher`/`--candidate` are provided → use `--eval-report`,
  print dim warning that teacher/candidate are ignored.

---

## Invariants (I-58 through I-62)

| ID | Rule |
|----|------|
| I-58 | `DistillationReadinessReport` is a readiness assessment only. It MUST NOT trigger training, weight updates, or provider routing changes. `promotion_validation.is_promotable=True` is informational — the operator must still use `record-promotion` explicitly (I-36, I-39). |
| I-59 | Shadow JSONL MUST NEVER be passed as `inputs.teacher_path` or `inputs.candidate_path`. Shadow records (`record_type=companion_shadow_run` or `ShadowRunRecord`) are audit artifacts only and are not teacher-eligible (I-16, I-53). |
| I-60 | `compute_shadow_coverage()` reads shadow records for aggregate divergence stats only. It MUST NOT call `compare_datasets()` or treat shadow data as candidate baseline input. |
| I-61 | `DistillationReadinessReport.shadow_coverage` is optional. Absent shadow data does not invalidate or block a distillation readiness assessment. |
| I-62 | `build_distillation_report()` is pure computation — no DB reads, no LLM calls, no network. All I/O is JSONL/JSON file reads via `load_jsonl()` and `json.loads()`. |

---

## Six Explicit Separations

| Layer | What it is | What it is NOT |
|-------|-----------|----------------|
| **Teacher dataset** | `EXTERNAL_LLM` JSONL — ground truth for evaluation | Not shadow, not candidate |
| **Candidate dataset** | `INTERNAL` JSONL — companion output to evaluate | Not teacher, not shadow |
| **Shadow JSONL** | Divergence audit (primary vs. companion) — context only | Not teacher, not candidate |
| **EvaluationReport** | `compare_datasets()` output — metric snapshot | Not a promotion decision |
| **PromotionValidation** | Gate pass/fail from `validate_promotion()` | Not a routing change |
| **DistillationReadinessReport** | Combined readiness snapshot | Not a training job |

---

## Known Inconsistency (Sprint 10 Shadow Schema)

Sprint 10 produced two incompatible shadow record schemas:

| Source | Keys | Format |
|--------|------|--------|
| `shadow.py` `write_shadow_record()` | `divergence.priority_diff`, `.relevance_diff`, `.impact_diff` | Batch offline shadow |
| `evaluation.py` `build_shadow_run_record()` | `deviations.priority_delta`, `.relevance_delta`, `.impact_delta` | Live inline shadow |

**Sprint 11 decision**: `compute_shadow_coverage()` normalizes both formats internally.
**Sprint 12 recommendation**: Standardize on one canonical shadow record schema. The `evaluation.py`
`build_shadow_run_record()` format (with `record_type`) is the more complete format
and should be the canonical target.

---

## Security Notes

- `distillation-check` reads only local file paths — no network I/O.
- No shadow records contain raw article text (per I-53) — no PII concern in manifest.
- `save_distillation_manifest()` creates files but never overwrites promotion records.
- Teacher/candidate paths are validated as existing files before use (FileNotFoundError).

---

## Sprint-11 Tasks

| # | Task | Agent | Status |
|---|---|---|---|
| 11.1 | `app/research/distillation.py`: `DistillationInputs`, `ShadowCoverageReport`, `DistillationReadinessReport`, `compute_shadow_coverage()`, `build_distillation_report()`, `save_distillation_manifest()` + `tests/unit/test_distillation.py` | Claude Code | ✅ |
| 11.2 | CLI: `research distillation-check` + `tests/unit/test_cli.py` distillation tests | Claude Code | ✅ |
| 11.3 | `docs/sprint11_distillation_contract.md` + `contracts.md §22` + I-58–I-62 | Claude Code | ✅ |
| 11.4 | `docs/intelligence_architecture.md` Sprint-11 update + `AGENTS.md` + `TASKLIST.md` | Claude Code | ✅ |

---

## Acceptance Criteria

```
Sprint 11 gilt als abgeschlossen wenn:
  - [x] 11.1: distillation.py vollständig + tests/unit/test_distillation.py grün (11 Tests)
  - [x] 11.2: distillation-check CLI + CLI-Tests grün (5 Tests)
  - [x] 11.3: sprint11_distillation_contract.md + contracts.md §22 + I-58–I-62 vollständig
  - [x] 11.4: intelligence_architecture.md + AGENTS.md + TASKLIST.md aktualisiert
  - [x] ruff check . sauber
  - [x] pytest passing (642 Tests, kein Rückschritt)
  - [x] distillation-check akzeptiert both shadow formats (batch + live)
  - [x] distillation-check output zeigt alle 3 Sektionen (metrics / shadow / gates)
  - [x] Kein Einfluss auf Routing, pipeline, apply_to_document()
```

---

## Codex-Spec für 11.1 — app/research/distillation.py + Tests

```
Modul: app/research/distillation.py (NEU)
Testmodul: tests/unit/test_distillation.py (NEU)

Datenklassen:
  @dataclass DistillationInputs:
    teacher_path: str | None = None
    candidate_path: str | None = None
    eval_report_path: str | None = None
    shadow_path: str | None = None
    dataset_type: str = "internal_benchmark"

  @dataclass ShadowCoverageReport:
    total_records, error_records, valid_records: int
    sentiment_agreement_rate, actionable_agreement_rate: float
    avg_priority_diff, avg_relevance_diff, avg_impact_diff, avg_tag_overlap: float
    + to_json_dict() -> dict

  @dataclass DistillationReadinessReport:
    generated_at: str  (ISO UTC)
    inputs: DistillationInputs
    evaluation: EvaluationReport
    promotion_validation: PromotionValidation
    shadow_coverage: ShadowCoverageReport | None = None
    notes: list[str] = field(default_factory=list)
    + to_json_dict() -> dict

Funktionen:
  compute_shadow_coverage(shadow_path: Path | str) -> ShadowCoverageReport
    → normalisiert batch-Format (divergence.priority_diff) UND live-Format (deviations.priority_delta)
    → error_records wenn shadow/deviations=None oder Feld fehlt
    → FileNotFoundError wenn Datei nicht existiert

  build_distillation_report(inputs: DistillationInputs) -> DistillationReadinessReport
    → wenn eval_report_path: load_jsonl-ähnliches json.loads(path.read_text()) → EvaluationReport
    → sonst: load_jsonl(teacher) + load_jsonl(candidate) → compare_datasets()
    → validate_promotion(evaluation.metrics)
    → wenn shadow_path: compute_shadow_coverage()
    → ValueError wenn weder eval_report_path noch (teacher_path + candidate_path)

  save_distillation_manifest(report, path) -> Path
    → path.parent.mkdir(parents=True, exist_ok=True)
    → json.dumps(report.to_json_dict(), indent=2)

Imports erlaubt:
  from app.research.evaluation import (
      compare_datasets, load_jsonl, validate_promotion,
      EvaluationReport, EvaluationMetrics, PromotionValidation,
  )

NICHT:
  - pipeline.py, apply_to_document(), update_analysis() importieren
  - DB-Verbindung aufbauen
  - LLM aufrufen

Tests (tests/unit/test_distillation.py — mindestens 10):
  test_compute_shadow_coverage_batch_format         (divergence keys)
  test_compute_shadow_coverage_live_format          (deviations keys)
  test_compute_shadow_coverage_mixed_formats        (beide in einer Datei)
  test_compute_shadow_coverage_error_records        (null shadow → error_records++)
  test_compute_shadow_coverage_file_not_found       (FileNotFoundError)
  test_build_distillation_report_with_teacher_candidate
  test_build_distillation_report_with_eval_report_path
  test_build_distillation_report_with_shadow
  test_build_distillation_report_missing_inputs_raises
  test_save_distillation_manifest_creates_valid_json
  test_save_distillation_manifest_creates_parent_dirs

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_distillation.py grün (>= 10 neue Tests)
  - [ ] pytest tests/unit/ grün (>= 600 Tests)
```

---

## Codex-Spec für 11.2 — CLI distillation-check

```
Modul: app/cli/main.py (ERWEITERN)
Testmodul: tests/unit/test_cli.py (ERWEITERN)

Neuer Command (research subgroup):
  research distillation-check
    --teacher PATH (Option, optional)
    --candidate PATH (Option, optional)
    --eval-report PATH (Option, optional)
    --shadow PATH (Option, optional)
    --dataset-type STR (Option, default="internal_benchmark")
    --save-manifest PATH (Option, optional)

Verhalten:
  1. Validierung: weder (teacher + candidate) noch eval-report → Error + Exit 1
  2. build_distillation_report(inputs) aufrufen
  3. Section 1 — Evaluation Metrics: _build_dataset_evaluation_table() WIEDERVERWENDEN
     oder gleichwertige Rich Table
  4. Section 2 — Shadow Coverage (wenn shadow_path gesetzt):
     Rich Table: total/error/valid records, sentiment/actionable agreement,
     avg diffs, avg tag overlap
  5. Section 3 — Gate Summary: _print_companion_promotion_readiness() WIEDERVERWENDEN
     oder gleichwertige Ausgabe
  6. Section 4 — Manifest saved (wenn --save-manifest):
     save_distillation_manifest(report, path)
     console.print(f"[dim]Distillation manifest saved: {path}[/dim]")
  7. Exit 0 (immer — informativer Output, kein Gate)
  8. Exit 1 nur bei FileNotFoundError oder Parsing-Fehler

CLI-Tests (mindestens 5):
  test_research_distillation_check_with_teacher_candidate
  test_research_distillation_check_with_eval_report
  test_research_distillation_check_with_shadow
  test_research_distillation_check_missing_inputs_exits_1
  test_research_distillation_check_saves_manifest

Constraints:
  - _build_dataset_evaluation_table() WIEDERVERWENDEN (nicht neu bauen)
  - _print_companion_promotion_readiness() WIEDERVERWENDEN
  - Exit 0 wenn Evaluation erfolgreich (auch wenn Promotion-Gates FAIL)
  - NICHT: apply_to_document(), update_analysis() aufrufen

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_cli.py grün
  - [ ] pytest tests/unit/ grün (>= 600 + neue Tests)
  - [ ] Output zeigt alle 3 Sektionen (metrics / shadow / gates)
  - [ ] --save-manifest schreibt valides JSON
```
