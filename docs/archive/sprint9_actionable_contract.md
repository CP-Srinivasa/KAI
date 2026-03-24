# Sprint 9 — Actionable Metric Automation, Distillation Flow Finalization

> **SUPERSEDED**: The canonical Sprint-9 reference is `docs/sprint9_promotion_audit_contract.md`.
> This document captures the original G6 gate design (I-34 automation), but the actual
> Sprint-9 scope was extended by Codex during Sprint-8 and formalized differently.
>
> G6 (`false_actionable_rate <= 0.05`) was already implemented by Codex in Sprint 8.
> Sprint 9's actual deliverable: `PromotionRecord.gates_summary` + artifact linkage (I-47–I-49).
>
> This document remains as design-rationale reference only. Do NOT use field names from
> this document in code — use `false_actionable_rate` and `false_actionable_pass` (the
> actual implementation), not `actionable_false_positive_rate` or `actionable_fp_pass`.
>
> Upstream Sprint-8: `docs/tuning_promotion_contract.md`.
> Canonical Sprint-9: `docs/sprint9_promotion_audit_contract.md`.
> Invariants: `docs/contracts.md §20`, I-46–I-50.

---

## Purpose

Sprint 9 closes two remaining gaps from Sprint 7/8:

1. **I-34 automation** — false-actionable rate moves from manual check to computed metric
   in `EvaluationMetrics`, with a new Gate G6 in `validate_promotion()`.

2. **Distillation flow formalization** — the complete operator path from teacher export to
   promotion record is documented canonically and the artifact schema is finalized.

**What does NOT change:**
- Promotion remains manual. G6 automation does not make promotion automatic.
- `check-promotion` exits 0/1 based on gates — operator still acts on the result.
- No training engine. No new provider. No routing change.
- All five existing gates (G1–G5) remain unchanged.

---

## Sprint-9 Scope

### What Sprint 9 delivers

1. **`actionable_false_positive_rate` in `EvaluationMetrics`** — new field computed by
   `compare_datasets()`, backward-compatible (default `0.0`).

2. **Gate G6 in `PromotionValidation`** — `actionable_fp_pass: bool` + threshold `<= 0.15`.

3. **`validate_promotion()` updated** — checks all six gates. `is_promotable` requires all six.

4. **`check-promotion` CLI updated** — shows G6 row in gate table.

5. **`record-promotion` CLI** already validates via `validate_promotion()` — gains G6 for free.

6. **Tests** for all changes.

7. **This contract document** + `contracts.md §20` + I-46–I-50.

### What Sprint 9 does NOT deliver

- No training pipeline, no fine-tuning calls, no weight updates
- No new provider or analysis tier
- No automatic promotion trigger
- No external API calls
- No trading execution

---

## I-34 Automation: `actionable_false_positive_rate`

### Definition

**False-actionable pair**: a paired row where the candidate fires `actionable=True`
but the teacher does not.

Actionable is defined as `priority_score >= 7` — identical to the alerts app threshold.

```
actionable_false_positive_rate = fp_count / paired_count

where:
  fp_count = count of rows where:
    candidate priority_score >= 7  (companion fires)
    AND teacher priority_score < 7  (teacher would NOT fire)

  paired_count = total rows with a matching document_id in both datasets

Boundary conditions:
  - paired_count == 0  →  rate = 0.0
  - all teacher pairs non-actionable AND candidate never fires  →  rate = 0.0
  - all teacher pairs non-actionable AND candidate always fires  →  rate = 1.0
```

### Metric Limitations (non-negotiable — must be visible in output)

This metric has explicit limitations that MUST be stated in the contract and referenced
in the CLI output:

1. **False negatives not captured**: the metric does NOT track companion MISSING actionable
   signals that teacher would fire. Miss rate is tracked by `actionable_accuracy` in
   `EvaluationResult` (live comparison path, `research evaluate`).

2. **Threshold is a proxy**: `priority_score >= 7` is the alert threshold, not a ground truth
   actionability label. A teacher `priority=6` is not necessarily non-actionable in all contexts.

3. **Only valid at paired-row granularity**: unpaired rows (missing_pairs) are excluded.
   A high missing_pairs count reduces the statistical validity of the metric.

4. **Requires internal_benchmark dataset type**: rule baseline rows use rule-based scoring
   which systematically under-scores priority. Interpreting `actionable_false_positive_rate`
   on a `rule_baseline` dataset is misleading. G6 is only enforced for `internal_benchmark`.

### Gate G6 Definition

```
G6: actionable_false_positive_rate <= 0.15

Meaning: companion fires actionable on at most 15% of documents where
teacher would not fire.

Applicability: only enforced for dataset_type == "internal_benchmark".
For "rule_baseline" and "custom": G6 is informational only (always passes).

Threshold rationale: a 15% false-actionable rate means at most 1 in 7 companion
signals would be spurious relative to teacher. Above 15%, alert quality degrades
enough to block promotion.
```

---

## EvaluationMetrics — Extended Schema

### Updated `EvaluationMetrics` Dataclass

Add one field with a backward-compatible default:

```python
@dataclass
class EvaluationMetrics:
    sentiment_agreement: float        # G1: fraction matching sentiment_label (0.0–1.0)
    priority_mae: float               # G2: MAE on priority_score (1–10 scale)
    relevance_mae: float              # G3: MAE on relevance_score (0.0–1.0)
    impact_mae: float                 # G4: MAE on impact_score (0.0–1.0)
    tag_overlap_mean: float           # G5: avg Jaccard similarity of tags (0.0–1.0)
    sample_count: int                 # number of paired rows evaluated
    missing_pairs: int                # baseline rows without matching teacher row
    actionable_false_positive_rate: float = 0.0  # G6: fp / paired (NEW, Sprint 9)
```

**Backward compatibility**: existing JSON reports without `actionable_false_positive_rate`
are read with `.get("actionable_false_positive_rate", 0.0)` — they predate Sprint-9
measurement and pass G6 by default (I-47).

### Updated `to_json_dict()`

Must include `"actionable_false_positive_rate"` in the returned dict.

### Computation in `compare_datasets()`

```python
# Actionable threshold — identical to alerts app (I-46)
ACTIONABLE_THRESHOLD = 7

fp_actionable = 0  # teacher not-actionable, candidate fires

for row in baseline_rows:
    # ... existing pairing logic ...
    t_actionable = int(teacher.get("priority_score", 1)) >= ACTIONABLE_THRESHOLD
    c_actionable = int(baseline.get("priority_score", 1)) >= ACTIONABLE_THRESHOLD
    if c_actionable and not t_actionable:
        fp_actionable += 1

# After loop:
actionable_false_positive_rate = fp_actionable / paired if paired else 0.0
```

---

## PromotionValidation — Extended Gate

### Updated `PromotionValidation` Dataclass

Add one field:

```python
@dataclass
class PromotionValidation:
    sentiment_pass: bool      # G1
    priority_pass: bool       # G2
    relevance_pass: bool      # G3
    impact_pass: bool         # G4
    tag_overlap_pass: bool    # G5
    actionable_fp_pass: bool  # G6 (NEW, Sprint 9)

    @property
    def is_promotable(self) -> bool:
        return all([
            self.sentiment_pass,
            self.priority_pass,
            self.relevance_pass,
            self.impact_pass,
            self.tag_overlap_pass,
            self.actionable_fp_pass,
        ])
```

### Updated `validate_promotion()`

```python
def validate_promotion(
    metrics: EvaluationMetrics,
    *,
    dataset_type: str = "internal_benchmark",
) -> PromotionValidation:
    """Check metrics against promotion thresholds.

    G6 is only enforced for dataset_type == 'internal_benchmark'.
    For other types, G6 always passes (I-48).
    """
    if dataset_type == "internal_benchmark":
        actionable_fp_pass = metrics.actionable_false_positive_rate <= 0.15
    else:
        actionable_fp_pass = True  # informational only for rule_baseline / custom

    return PromotionValidation(
        sentiment_pass=metrics.sentiment_agreement >= 0.85,
        priority_pass=metrics.priority_mae <= 1.5,
        relevance_pass=metrics.relevance_mae <= 0.15,
        impact_pass=metrics.impact_mae <= 0.20,
        tag_overlap_pass=metrics.tag_overlap_mean >= 0.30,
        actionable_fp_pass=actionable_fp_pass,
    )
```

**Important**: `validate_promotion()` gains a `dataset_type` parameter.
Default `"internal_benchmark"` preserves current call sites without change.

---

## CLI Contract — Sprint 9

### `check-promotion` update (task 9.2)

Add G6 row to the gate table. The `dataset_type` is read from the report JSON.

```python
# After loading metrics from report JSON:
dataset_type = data.get("dataset_type", "internal_benchmark")
validation = validate_promotion(metrics, dataset_type=dataset_type)

# Add to gate table:
gate_table.add_row(
    "Actionable FP Rate", "<= 0.150",
    f"{metrics.actionable_false_positive_rate:.3f}",
    _gate_status(validation.actionable_fp_pass),
)

# Update footer note — replace I-34 text:
console.print(
    "[yellow]Note: G6 measures false-actionable rate on paired rows only. "
    "See docs/sprint9_actionable_contract.md for limitations.[/yellow]"
)
```

### `record-promotion` — no change required

`record-promotion` already calls `validate_promotion(metrics)`. Once `validate_promotion()`
is updated, G6 blocking happens automatically. The `dataset_type` must be passed:

```python
dataset_type = data.get("dataset_type", "internal_benchmark")
validation = validate_promotion(metrics, dataset_type=dataset_type)
```

### `evaluate-datasets` — no change required

The new field is computed in `compare_datasets()` and is printed as part of the metrics
table automatically via the existing table-building code. Add one new row:

```python
table.add_row(
    "Actionable FP Rate",
    f"{metrics.actionable_false_positive_rate:.4f}",
    "0.0-1.0, <= 0.15 good (internal_benchmark only)"
)
```

---

## Distillation Flow — Canonical Definition

The complete operator flow after Sprint 9. No further steps are expected before Sprint 10.

```
Step 1: Teacher Export
  research dataset-export teacher.jsonl \
    --source-type external_llm --teacher-only

Step 2: Companion Inference (requires running local endpoint)
  research benchmark-companion-run teacher.jsonl companion.jsonl \
    --endpoint http://localhost:11434 \
    --report-out artifacts/report.json \
    --artifact-out artifacts/benchmark.json

  (Or separately: run evaluate-datasets with pre-generated companion.jsonl)

Step 3: Offline Evaluation
  research evaluate-datasets teacher.jsonl companion.jsonl \
    --dataset-type internal_benchmark \
    --save-report artifacts/report.json \
    --save-artifact artifacts/benchmark.json

Step 4: Gate Check (Exit 0 = all 6 gates pass)
  research check-promotion artifacts/report.json

Step 5: Tuning Manifest (record only, no training trigger)
  research prepare-tuning-artifact teacher.jsonl llama3.2:3b \
    --eval-report artifacts/report.json \
    --out artifacts/tuning_manifest.json

Step 6: External Fine-Tuning (operator-run, not in platform)
  [ollama fine-tune / huggingface / axolotl / etc.]

Step 7: Promotion Record (requires all 6 gates passing)
  research record-promotion artifacts/report.json <model_id> \
    --endpoint http://localhost:11434 \
    --operator-note "Review complete, I-34 automated + manual spot-check done" \
    --tuning-artifact artifacts/tuning_manifest.json \
    --out artifacts/promotion_record.json

Step 8: Activate (env var only, no code change)
  export APP_LLM_PROVIDER=companion
  export COMPANION_MODEL_ENDPOINT=http://localhost:11434

Reversal:
  export APP_LLM_PROVIDER=openai  (or previous value)
```

**Invariant**: Steps 1–7 produce audit artifacts only. No step changes system state.
Step 8 is the only step that changes behavior — and it is exclusively env-var-controlled.

---

## Codex Specifications

### Task 9.1 — `EvaluationMetrics` + `compare_datasets()` + Tests

```
Agent: Codex
Phase: Sprint 9
Modules: app/research/evaluation.py, tests/unit/test_evaluation.py
Type: feature + test

Changes:
1. Add field to EvaluationMetrics:
   actionable_false_positive_rate: float = 0.0

2. Update EvaluationMetrics.to_json_dict():
   Add "actionable_false_positive_rate": self.actionable_false_positive_rate

3. Update compare_datasets():
   Track fp_actionable counter (see Computation section above)
   Pass to EvaluationMetrics constructor

4. Update evaluate-datasets CLI table (main.py):
   Add row for Actionable FP Rate

New tests in tests/unit/test_evaluation.py:
  test_compare_datasets_actionable_fp_rate_zero_when_no_false_positives:
    teacher priority=8, candidate priority=8 → fp_rate = 0.0

  test_compare_datasets_actionable_fp_rate_one_when_all_false_positives:
    teacher priority=5 (not actionable), candidate priority=8 (fires) → fp_rate = 1.0

  test_compare_datasets_actionable_fp_rate_half_when_mixed:
    Two pairs: (teacher=5, candidate=8) + (teacher=5, candidate=5) → fp_rate = 0.5

  test_compare_datasets_actionable_fp_rate_zero_when_paired_zero:
    No paired rows → fp_rate = 0.0

  test_compare_datasets_actionable_fp_rate_not_counted_for_teacher_actionable:
    teacher priority=8 (actionable), candidate priority=8 → NOT a false positive

Constraints:
  - NICHT: PromotionValidation or validate_promotion() ändern (Task 9.2)
  - NICHT: neue Module anlegen
  - NICHT: evaluate.py für andere Aufgaben nutzen
  - backward compatibility: EvaluationMetrics(... ohne actionable_fp) still works

Acceptance criteria:
  - [ ] ruff check . clean
  - [ ] pytest tests/unit/test_evaluation.py passing (all new + existing)
  - [ ] pytest tests/unit/ passing (baseline: 571, no regression)
  - [ ] new field in to_json_dict() output
```

### Task 9.2 — `PromotionValidation` + `validate_promotion()` + CLI + Tests

```
Agent: Codex
Phase: Sprint 9
Modules: app/research/evaluation.py, app/cli/main.py, tests/unit/test_evaluation.py
Type: feature + test

Changes in evaluation.py:
1. Add to PromotionValidation:
   actionable_fp_pass: bool

2. Update is_promotable:
   Add actionable_fp_pass to all([...])

3. Update validate_promotion():
   Add dataset_type: str = "internal_benchmark" parameter
   Add G6 logic (see PromotionValidation section above)

Changes in app/cli/main.py:
4. check-promotion:
   - Read dataset_type from report JSON
   - Pass dataset_type to validate_promotion()
   - Add G6 row to gate table (see CLI Contract section)
   - Replace I-34 manual note with G6 automated note

5. record-promotion:
   - Read dataset_type from report JSON
   - Pass dataset_type to validate_promotion()

New tests in tests/unit/test_evaluation.py:
  test_validate_promotion_g6_passes_for_low_fp_rate:
    metrics with actionable_false_positive_rate=0.10 → actionable_fp_pass=True

  test_validate_promotion_g6_fails_for_high_fp_rate:
    metrics with actionable_false_positive_rate=0.20 → actionable_fp_pass=False

  test_validate_promotion_g6_boundary_at_015_passes:
    actionable_false_positive_rate=0.15 → actionable_fp_pass=True (boundary=pass)

  test_validate_promotion_g6_informational_for_rule_baseline:
    dataset_type="rule_baseline", actionable_false_positive_rate=0.99
    → actionable_fp_pass=True (G6 not enforced)

  test_validate_promotion_is_promotable_requires_all_six_gates:
    Five gates pass, G6 fails → is_promotable=False

Constraints:
  - NICHT: G1–G5 thresholds ändern
  - NICHT: existing tests for validate_promotion() brechen
  - Default dataset_type="internal_benchmark" preserviert alle bestehenden Call-Sites
  - check-promotion must still show I-34 context but as "automated via G6" not "manual"

Acceptance criteria:
  - [ ] ruff check . clean
  - [ ] pytest tests/unit/ passing (baseline: 571+5 from 9.1, no regression)
  - [ ] check-promotion shows 6 gate rows
  - [ ] record-promotion blocked by G6 when fp_rate > 0.15 (internal_benchmark)
  - [ ] research --help unchanged (no new commands)
```

---

## Sprint-9 Acceptance Criteria

### 9.1 — `EvaluationMetrics` + `compare_datasets()`

| # | Criterion |
|---|-----------|
| 1 | `actionable_false_positive_rate` field added to `EvaluationMetrics` with `= 0.0` default |
| 2 | Field included in `to_json_dict()` output |
| 3 | `compare_datasets()` computes fp_rate: candidate fires (>= 7), teacher does not (< 7) |
| 4 | fp_rate = 0.0 when paired_count == 0 |
| 5 | fp_rate is NOT incremented when teacher is also actionable (true positive) |
| 6 | At least 5 new tests covering boundary and edge cases |
| 7 | `ruff check .` clean |
| 8 | `pytest tests/unit/` passing (no regression) |

### 9.2 — `PromotionValidation` + `validate_promotion()` + CLI

| # | Criterion |
|---|-----------|
| 1 | `actionable_fp_pass: bool` added to `PromotionValidation` |
| 2 | `is_promotable` requires all 6 gates (G1–G6) |
| 3 | G6 threshold: `actionable_false_positive_rate <= 0.15` |
| 4 | G6 enforced only for `dataset_type == "internal_benchmark"` |
| 5 | G6 always passes for `rule_baseline` and `custom` |
| 6 | `validate_promotion()` accepts `dataset_type` kwarg (default `"internal_benchmark"`) |
| 7 | `check-promotion` shows 6-row gate table including G6 |
| 8 | `record-promotion` blocked when G6 fails (internal_benchmark) |
| 9 | All existing `validate_promotion()` tests still pass (no regression on G1–G5) |
| 10 | At least 5 new G6 tests |
| 11 | `ruff check .` clean |
| 12 | `pytest tests/unit/` passing |

### Sprint-9 Final Sign-off Checklist

```
- [ ] 9.1: actionable_false_positive_rate in EvaluationMetrics + compare_datasets + tests
- [ ] 9.2: G6 in PromotionValidation + validate_promotion + CLI update + tests
- [ ] ruff check . clean
- [ ] pytest passing (baseline: 571 + new tests, no regression)
- [ ] G1-G5 behavior unchanged
- [ ] check-promotion shows 6 gates
- [ ] record-promotion respects G6
- [ ] evaluate-datasets shows Actionable FP Rate row
- [ ] docs/contracts.md §20 + I-46-I-50 added
- [ ] TASKLIST.md Sprint-9 tasks updated
- [ ] AGENTS.md test count updated
```

---

## Security Notes

- `actionable_false_positive_rate` is computed from JSONL priority_score fields only
  — no LLM inference, no external API calls, no network I/O
- The computation uses the same `priority_score >= 7` threshold as the alerts app —
  no new heuristic logic is introduced
- G6 does not change promotion semantics: `check-promotion` still exits 0 or 1 only,
  and no code path acts on this exit code automatically (I-36, I-49)
- Backward compatibility: reports without `actionable_false_positive_rate` default to
  `0.0` — this is a deliberate pass-by-default for pre-Sprint-9 reports (I-47)

---

## Invariant Summary (I-46 through I-50)

Full text in `docs/contracts.md §20`.

| ID | Rule |
|----|------|
| I-46 | `actionable_false_positive_rate` uses `priority_score >= 7` as actionable threshold — identical to the alerts app definition. No separate actionable label is tracked. |
| I-47 | `actionable_false_positive_rate` defaults to `0.0` in `EvaluationMetrics`. Existing JSON reports without this field are parsed with `.get("actionable_false_positive_rate", 0.0)` — they pass G6 by default (pre-Sprint-9 baseline). |
| I-48 | Gate G6 (`actionable_false_positive_rate <= 0.15`) is only enforced for `dataset_type == "internal_benchmark"`. For `rule_baseline` and `custom`, G6 is informational and always passes. |
| I-49 | `validate_promotion()` checks all six gates (G1–G6). `is_promotable` requires all six. No partial promotion. No gate weighting or bypass. |
| I-50 | The distillation flow (Steps 1–7) produces audit artifacts only. No step changes system state. Provider activation is exclusively an env-var operation by the operator (I-42). |
