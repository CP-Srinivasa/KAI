# Benchmark & Promotion Contract — Sprint 7

> **Canonical reference** for Sprint-7 Companion Benchmarking, Promotion Readiness Gate,
> and Artifact Path contract.
>
> Runtime stubs: `app/research/evaluation.py` — `PromotionValidation`, `validate_promotion()`,
> `save_evaluation_report()`, `save_benchmark_artifact()`.
>
> Upstream contracts: `docs/contracts.md §18`, invariants I-34–I-39.
> Upstream Sprint-6: `docs/dataset_evaluation_contract.md`.

---

## Purpose

Sprint 7 wires the already-implemented Sprint-6 evaluation stubs into a usable benchmark harness
with a controlled, human-gated promotion readiness check.

**Three explicit separations** — non-negotiable:

| Concept | Meaning | What it is NOT |
|---------|---------|----------------|
| **Benchmark** | Run evaluation harness, produce `EvaluationReport` + artifacts | Not training, not inference tuning |
| **Evaluation** | Measure metric gap (MAE / agreement) between candidate and teacher | Not a promotion decision |
| **Promotion** | Human-reviewed gate: all 5 metric thresholds pass + manual FP check | Not automatic, not triggered by any pipeline |

No code path in this sprint leads to training data modification, model weight updates,
or automatic provider switching. Companion remains `analysis_source=INTERNAL` until
a human operator explicitly promotes it (a future sprint with its own separate gate spec).

---

## Sprint-7 Scope

### What Sprint 7 delivers

1. **Tests** for all Sprint-6 evaluation stubs (`validate_promotion`, `save_evaluation_report`,
   `save_benchmark_artifact`) — these functions are already implemented but have zero test coverage.

2. **CLI: `evaluate-datasets --save-report` / `--save-artifact`** — optional persistence flags
   for offline audit trail. No behavior change when flags are omitted.

3. **CLI: `research check-promotion <report.json>`** — reads a saved `EvaluationReport` JSON,
   calls `validate_promotion()`, prints a per-gate pass/fail table, exits 0 (promotable) or 1 (not).

4. **This contract document** + `contracts.md §18` formal spec.

### What Sprint 7 does NOT deliver

- No training pipeline, no fine-tuning calls, no weight updates
- No new provider or new analysis tier
- No automatic promotion trigger
- No external API calls in the evaluation path
- No trading execution

---

## Promotion Gate — Five Quantitative Criteria

All five gates must pass. No partial promotion. No exceptions.

| Gate | Metric | Threshold | Direction | Notes |
|------|--------|-----------|-----------|-------|
| G1 | `sentiment_agreement` | ≥ 0.85 | ↑ higher better | Fraction of exact label matches |
| G2 | `priority_mae` | ≤ 1.5 | ↓ lower better | 1–10 scale; ≤1.5 = within 1.5 priority steps on avg |
| G3 | `relevance_mae` | ≤ 0.15 | ↓ lower better | 0.0–1.0 scale; ≤0.15 = within 15% on avg |
| G4 | `impact_mae` | ≤ 0.20 | ↓ lower better | 0.0–1.0 scale; ≤0.20 = within 20% on avg |
| G5 | `tag_overlap_mean` | ≥ 0.30 | ↑ higher better | Jaccard similarity; ≥0.30 = reasonable tag alignment |

Already implemented: `validate_promotion(metrics: EvaluationMetrics) → PromotionValidation`
in `app/research/evaluation.py`.

### Sixth Gate — False-Positive Actionable Rate (I-34, manual)

The quantitative gates do not capture actionable false positives. An automated metric
would require tracking `actionable=True` on candidate where teacher had `actionable=False`.
This is not yet in `EvaluationMetrics`.

**Sprint-7 handling**: Documented as I-34 (human-verified). The operator must manually inspect
`research evaluate` output (which includes `actionable_accuracy` in `EvaluationResult`) before
executing any promotion decision. Sprint-7B will automate this metric.

> **I-34 (manual gate)**: Before promotion, the companion's false-actionable rate (predicted
> `actionable=True` where teacher had `actionable=False`) MUST be verified manually via
> `research evaluate`. If companion over-fires actionable signals relative to teacher, promotion
> is blocked regardless of gate G1–G5 passing.

---

## Artifact Contract

### Standard Paths

Sprint 7 does not enforce a fixed output directory. The CLI flags `--save-report` and
`--save-artifact` accept arbitrary file paths. Convention (recommended, not enforced):

```
artifacts/benchmarks/<date>_<dataset_type>/
  evaluation_report.json     ← save_evaluation_report() output
  benchmark.json             ← save_benchmark_artifact() output
```

### `evaluation_report.json` Schema

Produced by `save_evaluation_report(report, path, *, teacher_dataset, candidate_dataset)`.

```json
{
  "report_type": "dataset_evaluation",
  "generated_at": "<ISO-8601 UTC>",
  "inputs": {
    "teacher_dataset": "<absolute path>",
    "candidate_dataset": "<absolute path>"
  },
  "dataset_type": "internal_benchmark | rule_baseline | custom",
  "teacher_count": 0,
  "baseline_count": 0,
  "paired_count": 0,
  "metrics": {
    "sentiment_agreement": 0.0,
    "priority_mae": 0.0,
    "relevance_mae": 0.0,
    "impact_mae": 0.0,
    "tag_overlap_mean": 0.0,
    "sample_count": 0,
    "missing_pairs": 0
  },
  "notes": []
}
```

Required fields for `check-promotion` to parse:
- `metrics.sentiment_agreement`
- `metrics.priority_mae`
- `metrics.relevance_mae`
- `metrics.impact_mae`
- `metrics.tag_overlap_mean`

### `benchmark.json` Schema

Produced by `save_benchmark_artifact(path, *, teacher_dataset, candidate_dataset, report, report_path)`.

```json
{
  "artifact_type": "companion_benchmark",
  "generated_at": "<ISO-8601 UTC>",
  "status": "benchmark_ready | needs_more_data",
  "dataset_type": "internal_benchmark | rule_baseline | custom",
  "teacher_dataset": "<absolute path>",
  "candidate_dataset": "<absolute path>",
  "evaluation_report": "<absolute path | null>",
  "metrics": { "...": "..." },
  "paired_count": 0
}
```

`status` is `"benchmark_ready"` when `paired_count > 0`, else `"needs_more_data"`.

---

## CLI Contract — Sprint 7

### 7.2 — `evaluate-datasets --save-report` / `--save-artifact` (optional flags)

Extend the existing `research_evaluate_datasets()` command with two optional output flags.
No behavior change when flags are omitted.

```python
@research_app.command("evaluate-datasets")
def research_evaluate_datasets(
    teacher_file: str = typer.Argument(...),
    candidate_file: str = typer.Argument(...),
    dataset_type: str = typer.Option("rule_baseline", "--dataset-type"),
    save_report: str | None = typer.Option(
        None,
        "--save-report",
        help="Path to persist EvaluationReport as JSON (for check-promotion and audit trail)",
    ),
    save_artifact: str | None = typer.Option(
        None,
        "--save-artifact",
        help="Path to persist companion benchmark manifest JSON",
    ),
) -> None:
    """Compare two exported JSONL datasets offline. No DB required."""
```

Implementation additions (after `console.print(table)`):

```python
from app.research.evaluation import save_evaluation_report, save_benchmark_artifact

if save_report:
    saved = save_evaluation_report(
        report,
        save_report,
        teacher_dataset=teacher_file,
        candidate_dataset=candidate_file,
    )
    console.print(f"[dim]Evaluation report saved: {saved}[/dim]")

if save_artifact:
    artifact = save_benchmark_artifact(
        save_artifact,
        teacher_dataset=teacher_file,
        candidate_dataset=candidate_file,
        report=report,
        report_path=save_report,
    )
    console.print(f"[dim]Benchmark artifact saved: {artifact}[/dim]")
```

Constraints:
- Flags are strictly optional — omitting both preserves current behavior exactly
- Do NOT change `compare_datasets()`, `load_jsonl()`, `save_evaluation_report()`, or
  `save_benchmark_artifact()` — all already implemented
- Do NOT import `torch`, `openai`, `anthropic`, or any ML framework
- Do NOT add DB calls

### 7.3 — `research check-promotion <report_file>` (new command)

```python
@research_app.command("check-promotion")
def research_check_promotion(
    report_file: str = typer.Argument(
        ..., help="Path to evaluation_report.json produced by evaluate-datasets --save-report"
    ),
) -> None:
    """Check whether a saved evaluation report meets companion promotion thresholds.

    Exits 0 if all five quantitative gates pass (promotable).
    Exits 1 if any gate fails — human review required.

    Note: Gate I-34 (false-actionable rate) requires separate manual verification
    via `research evaluate`. See docs/benchmark_promotion_contract.md.
    """
```

Implementation:

```python
import json
from pathlib import Path
from app.research.evaluation import EvaluationMetrics, validate_promotion

report_path = Path(report_file)
if not report_path.exists():
    console.print(f"[red]Report file not found: {report_path}[/red]")
    raise typer.Exit(1)

try:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    m_raw = data["metrics"]
    metrics = EvaluationMetrics(
        sentiment_agreement=float(m_raw["sentiment_agreement"]),
        priority_mae=float(m_raw["priority_mae"]),
        relevance_mae=float(m_raw["relevance_mae"]),
        impact_mae=float(m_raw["impact_mae"]),
        tag_overlap_mean=float(m_raw["tag_overlap_mean"]),
        sample_count=int(m_raw.get("sample_count", 0)),
        missing_pairs=int(m_raw.get("missing_pairs", 0)),
    )
except (KeyError, ValueError, json.JSONDecodeError) as e:
    console.print(f"[red]Could not parse report file:[/red] {e}")
    raise typer.Exit(1) from e

validation = validate_promotion(metrics)

table = Table(title="Promotion Gate Check")
table.add_column("Gate", style="cyan")
table.add_column("Threshold", justify="right")
table.add_column("Actual", justify="right")
table.add_column("Status", justify="center")

PASS = "[green]PASS[/green]"
FAIL = "[red]FAIL[/red]"

table.add_row("Sentiment Agreement", "≥ 0.850",
              f"{metrics.sentiment_agreement:.3f}",
              PASS if validation.sentiment_pass else FAIL)
table.add_row("Priority MAE",        "≤ 1.500",
              f"{metrics.priority_mae:.3f}",
              PASS if validation.priority_pass else FAIL)
table.add_row("Relevance MAE",       "≤ 0.150",
              f"{metrics.relevance_mae:.3f}",
              PASS if validation.relevance_pass else FAIL)
table.add_row("Impact MAE",          "≤ 0.200",
              f"{metrics.impact_mae:.3f}",
              PASS if validation.impact_pass else FAIL)
table.add_row("Tag Overlap",         "≥ 0.300",
              f"{metrics.tag_overlap_mean:.3f}",
              PASS if validation.tag_overlap_pass else FAIL)

console.print(table)
console.print(f"\nSamples evaluated: {metrics.sample_count}")
console.print("[yellow]Note: Gate I-34 (actionable false-positive rate) requires manual "
              "verification via `research evaluate`. See benchmark_promotion_contract.md.[/yellow]")

if validation.is_promotable:
    console.print("\n[bold green]PROMOTABLE[/bold green] — all quantitative gates passed.")
    console.print("[dim]Reminder: Manual I-34 verification still required before promotion.[/dim]")
else:
    failed = sum([
        not validation.sentiment_pass,
        not validation.priority_pass,
        not validation.relevance_pass,
        not validation.impact_pass,
        not validation.tag_overlap_pass,
    ])
    console.print(f"\n[bold red]NOT PROMOTABLE[/bold red] — {failed} gate(s) failed.")
    raise typer.Exit(1)
```

---

## Sprint-7 Acceptance Criteria

### 7.1 — Tests

| # | Criterion |
|---|-----------|
| 1 | `validate_promotion()` — all gates pass → `is_promotable=True` |
| 2 | `validate_promotion()` — each gate fails individually (5 separate tests) |
| 3 | `validate_promotion()` — boundary values exactly at threshold (boundary = pass) |
| 4 | `save_evaluation_report()` — creates file, valid JSON, all required fields present |
| 5 | `save_evaluation_report()` — `report_type == "dataset_evaluation"`, `inputs` contains both dataset paths |
| 6 | `save_benchmark_artifact()` — creates file, valid JSON, `artifact_type == "companion_benchmark"` |
| 7 | `save_benchmark_artifact()` — `status == "benchmark_ready"` when `paired_count > 0` |
| 8 | `save_benchmark_artifact()` — `status == "needs_more_data"` when `paired_count == 0` |
| 9 | `pytest tests/unit/` passing (no regression) |
| 10 | `ruff check .` clean |

### 7.2 — `evaluate-datasets --save-report` / `--save-artifact`

| # | Criterion |
|---|-----------|
| 1 | Without `--save-report` / `--save-artifact`: behavior identical to Sprint-6 (no regression) |
| 2 | `--save-report <path>` creates JSON file at given path |
| 3 | Saved JSON is parseable and contains all `EvaluationReport.to_json_dict()` fields |
| 4 | `--save-artifact <path>` creates benchmark manifest JSON |
| 5 | Benchmark manifest contains `artifact_type == "companion_benchmark"` |
| 6 | Both flags work together: report and artifact both saved in one invocation |
| 7 | `ruff check .` clean |
| 8 | `pytest tests/unit/` passing |

### 7.3 — `research check-promotion`

| # | Criterion |
|---|-----------|
| 1 | Loads saved `evaluation_report.json`, reconstructs `EvaluationMetrics` |
| 2 | Prints per-gate pass/fail table with threshold + actual value |
| 3 | Exit 0 when all 5 gates pass |
| 4 | Exit 1 when any gate fails |
| 5 | Exit 1 + error message when report file not found |
| 6 | Exit 1 + error message when JSON is malformed or missing required fields |
| 7 | I-34 manual-verification reminder shown in all cases |
| 8 | `research --help` lists `check-promotion` in research group |
| 9 | `ruff check .` clean |
| 10 | `pytest tests/unit/` passing |

### Sprint-7 Final Sign-off Checklist

```
- [ ] 7.1: validate_promotion + save_* functions fully tested
- [ ] 7.2: --save-report / --save-artifact CLI flags wired
- [ ] 7.3: check-promotion CLI command implemented
- [ ] ruff check . clean
- [ ] pytest passing (baseline: 547 tests, no regression)
- [ ] evaluate-datasets (existing behavior) unchanged
- [ ] evaluate (DB-based, Sprint 5) unchanged
- [ ] docs/contracts.md §18 added
- [ ] TASKLIST.md Sprint-7 tasks updated
- [ ] AGENTS.md test count updated
```

---

## Security Notes

- `check-promotion` reads JSON files only — `json.loads()`, no `eval()`, no pickle
- No external network calls in benchmark or promotion check path
- No API keys, no model inference in the evaluation/promotion surface
- File existence checked before read — no silent empty-report evaluation
- Artifact paths are operator-specified — no hardcoded writes outside project dir
- I-34 is a human gate by design: automated false-positive detection would require
  tracking `actionable` ground truth from teacher, which is in `EvaluationResult`
  (Sprint-5 surface) — cross-surface tracking deferred to Sprint-7B

---

## Invariant Summary (I-34 through I-39)

Full text in `docs/contracts.md §18`.

| ID | Rule |
|----|------|
| I-34 | Before companion promotion, the false-actionable rate MUST be manually verified via `research evaluate`. Automated FP tracking deferred to Sprint-7B. |
| I-35 | `check-promotion` reads a saved `evaluation_report.json` — it MUST NOT trigger any new analysis, DB read, or model inference. |
| I-36 | Promotion is not automatic. `check-promotion` exiting 0 does NOT change any system state. A human operator must act on the result. |
| I-37 | `--save-report` / `--save-artifact` are audit trail only — they do NOT change evaluation semantics or metrics values. |
| I-38 | Benchmark artifacts are read-only once written. No process may modify them in-place. A re-run must produce a new file. |
| I-39 | Companion remains `analysis_source=INTERNAL` until an operator explicitly reconfigures the provider. Passing promotion gates does not change provider routing. |
