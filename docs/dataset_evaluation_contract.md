# Dataset & Evaluation Contract — Sprint 6

> **Canonical reference** for Sprint-6 CLI implementation (tasks 6.2, 6.3).
> Runtime implementation: `app/research/datasets.py`, `app/research/evaluation.py`.
> Contract invariants: `docs/contracts.md §16–17`, I-27 through I-33.

---

## Purpose

Sprint 6 prepares KAI for dataset construction, offline evaluation, and distillation readiness
without changing the runtime analysis contract.

All Sprint 6 artifacts reuse the existing analyzed-document boundary:

```
CanonicalDocument → export_training_data() → JSONL row → compare_datasets() → EvaluationReport
```

No new provider architecture, no new analysis schema, no training pipeline.

---

## Implementation Status

| Component | Implementation | Status |
|-----------|---------------|--------|
| `export_training_data(teacher_only=True)` | `app/research/datasets.py` | ✅ done |
| `compare_datasets()` | `app/research/evaluation.py` | ✅ done |
| `EvaluationMetrics` / `EvaluationReport` | `app/research/evaluation.py` | ✅ done |
| `load_jsonl()` | `app/research/evaluation.py` | ✅ done |
| `dataset-export --teacher-only` CLI flag | `app/cli/main.py` | ⏳ task 6.2 |
| `evaluate-datasets` CLI command | `app/cli/main.py` | ⏳ task 6.3 |

---

## Dataset Roles

Role is determined exclusively by `analysis_source` (I-29, I-31).
No other field (provider, ensemble_chain, source name, URL) may determine role.

| Role | `analysis_source` | Fine-tuning? | Evaluation use |
|------|-------------------|:------------:|----------------|
| **Teacher corpus** | `EXTERNAL_LLM` | ✅ yes | ground truth |
| **Internal benchmark** | `INTERNAL` | ❌ no | compare against teacher |
| **Rule baseline** | `RULE` | ❌ no | deterministic floor |

Non-negotiable rules:
- `INTERNAL` rows are benchmark-only — NEVER teacher signal (I-30)
- `RULE` rows are baseline-only — NEVER teacher signal (I-19, I-30)
- Legacy rows with `analysis_source=None` are excluded in strict mode (I-27, §16c)
- `provider`, `ensemble_chain`, and all other metadata are trace fields only (I-31)

---

## Export Row Format

Sprint 6 reuses the existing JSONL format from `export_training_data()`. No second schema.

```json
{
  "messages": [
    {"role": "system", "content": "You are a highly precise financial AI analyst."},
    {"role": "user", "content": "Title: ...\nSource: ...\nContent:\n..."},
    {"role": "assistant", "content": "{\"sentiment_label\":\"bullish\", \"priority_score\":8, ...}"}
  ],
  "metadata": {
    "document_id": "<uuid>",
    "provider": "openai",
    "analysis_source": "external_llm"
  }
}
```

Required assistant target fields for Sprint-6 evaluation comparison:

| Field | Type | Scale |
|-------|------|-------|
| `sentiment_label` | str | `"bullish"` / `"bearish"` / `"neutral"` |
| `priority_score` | int | 1–10 |
| `relevance_score` | float | 0.0–1.0 |
| `impact_score` | float | 0.0–1.0 |
| `tags` | list[str] | deduplicated |

---

## Two Evaluation Commands — Design Separation

These are two distinct commands. They MUST NOT be merged.

| | `research evaluate` | `research evaluate-datasets` |
|--|---------------------|-------------------------------|
| **Input** | Live DB (`is_analyzed=True`) | Two JSONL files |
| **Comparison** | Re-runs rule pipeline in-memory | `compare_datasets()` joins by `document_id` |
| **Metrics** | MSE (`EvaluationResult`) | MAE (`EvaluationMetrics`) |
| **DB required** | ✅ yes | ❌ no — fully offline |
| **Use case** | Live quality check | Offline audit, CI, floor-gap analysis |
| **Status** | ✅ existing, unchanged | ⏳ task 6.3 |

`research evaluate` (existing):
- loads teacher docs from DB, re-runs `AnalysisPipeline(run_llm=False)`, calls `compare_outputs()`
- output: `EvaluationResult` (priority_mse, relevance_mse, impact_mse, novelty_mse, sentiment_accuracy)
- requires live DB and monitor directory

`research evaluate-datasets` (new, task 6.3):
- loads two JSONL files, calls `compare_datasets()`, prints `EvaluationReport`
- output: `EvaluationMetrics` (priority_mae, relevance_mae, impact_mae, tag_overlap_mean, sentiment_agreement)
- no DB, no model, no network calls

---

## CLI Contract — `dataset-export --teacher-only` (task 6.2)

### Current signature (before 6.2)

```python
def research_dataset_export(
    output_file: str = typer.Argument(...),
    source_type: str = typer.Option("external_llm", ...),
    limit: int = typer.Option(1000, ...),
) -> None:
    ...
    count = export_training_data(docs, out_path)          # no teacher_only flag yet
```

### Required change (task 6.2)

Add one parameter and update one call:

```python
def research_dataset_export(
    output_file: str = typer.Argument(..., help="Path to output JSONL file"),
    source_type: str = typer.Option(
        "external_llm",
        help="Filter by analysis source: external_llm, internal, rule, all",
    ),
    teacher_only: bool = typer.Option(
        False,
        "--teacher-only",
        help="Strict teacher guard: only export analysis_source=EXTERNAL_LLM rows (I-27)",
    ),
    limit: int = typer.Option(1000, help="Max documents to export"),
) -> None:
    """Export analyzed documents to JSONL for Companion Model tuning."""
    ...
    count = export_training_data(docs, out_path, teacher_only=teacher_only)
```

### Usage examples

```bash
# Teacher corpus — maximum safety (CLI pre-filter + function-level guard)
research dataset-export teacher.jsonl --source-type external_llm --teacher-only

# Teacher corpus — function-level guard only (no CLI pre-filter)
research dataset-export teacher.jsonl --teacher-only

# Internal benchmark
research dataset-export benchmark.jsonl --source-type internal

# Rule baseline
research dataset-export baseline.jsonl --source-type rule

# All tiers (no filter)
research dataset-export full.jsonl --source-type all
```

### Constraints for Codex

- Do NOT change `export_training_data()` — already implemented and tested
- Do NOT change DB loading or source_type pre-filter logic
- Do NOT add new modules or files
- `--teacher-only` is purely additive — existing callers without the flag are unaffected
- The docstring MUST explain the teacher-only mode is a strict guard (references I-27)

---

## CLI Contract — `research evaluate-datasets` (task 6.3)

### Typer signature (exact)

```python
@research_app.command("evaluate-datasets")
def research_evaluate_datasets(
    teacher_file: str = typer.Argument(
        ..., help="Path to teacher JSONL file (analysis_source=external_llm)"
    ),
    baseline_file: str = typer.Argument(
        ..., help="Path to baseline JSONL file (rule or internal tier)"
    ),
    dataset_type: str = typer.Option(
        "rule_baseline",
        help="Comparison type: rule_baseline | internal_benchmark | custom",
    ),
) -> None:
    """Compare two exported JSONL datasets offline. No DB required."""
```

### Required implementation logic

```python
from pathlib import Path
from app.research.evaluation import compare_datasets, load_jsonl

teacher_path = Path(teacher_file)
baseline_path = Path(baseline_file)

if not teacher_path.exists():
    console.print(f"[red]Teacher file not found: {teacher_path}[/red]")
    raise typer.Exit(1)
if not baseline_path.exists():
    console.print(f"[red]Baseline file not found: {baseline_path}[/red]")
    raise typer.Exit(1)

teacher_rows = load_jsonl(teacher_path)
baseline_rows = load_jsonl(baseline_path)

if not teacher_rows:
    console.print("[yellow]Teacher file is empty.[/yellow]")
    raise typer.Exit(1)

report = compare_datasets(teacher_rows, baseline_rows, dataset_type=dataset_type)
m = report.metrics

table = Table(title=f"Dataset Evaluation — {report.dataset_type}")
table.add_column("Metric", style="cyan")
table.add_column("Value", justify="right")
table.add_column("Notes", style="dim")

table.add_row("Teacher rows",    str(report.teacher_count),   "")
table.add_row("Baseline rows",   str(report.baseline_count),  "")
table.add_row("Paired rows",     str(report.paired_count),    "")
table.add_row("Missing pairs",   str(m.missing_pairs),        "baseline rows without teacher match")
table.add_row("Sentiment Agr.",  f"{m.sentiment_agreement:.1%}", "≥0.70 good, <0.50 concerning")
table.add_row("Priority MAE",    f"{m.priority_mae:.2f}",     "1–10 scale, ≤1.5 good")
table.add_row("Relevance MAE",   f"{m.relevance_mae:.3f}",    "0–1 scale, ≤0.15 good")
table.add_row("Impact MAE",      f"{m.impact_mae:.3f}",       "0–1 scale, ≤0.20 good")
table.add_row("Tag Overlap",     f"{m.tag_overlap_mean:.3f}", "0–1 Jaccard, ≥0.30 good")

console.print(table)
```

### Constraints for Codex

- No LLM calls, no model loading, no imports of `openai` / `anthropic` / `torch`
- No DB connection (`build_session_factory` not needed)
- Do NOT modify `compare_datasets()` or `load_jsonl()` — already implemented and tested
- Exit code 1 on missing or empty teacher file (not a silent skip)
- `dataset_type` is passed through as-is — validation is at contract level, not CLI level
- `Table` import is already available in `main.py` from `rich.table`

---

## Sprint-6 Acceptance Criteria

### 6.2 — `dataset-export --teacher-only`

| # | Criterion |
|---|-----------|
| 1 | `research dataset-export out.jsonl --teacher-only` passes `teacher_only=True` to `export_training_data()` |
| 2 | Output contains only rows with `metadata.analysis_source == "external_llm"` |
| 3 | INTERNAL and RULE documents are excluded when `--teacher-only` is set |
| 4 | Legacy rows with `analysis_source=None` are excluded when `--teacher-only` is set |
| 5 | Without `--teacher-only`: all analyzed rows exported (unchanged behavior) |
| 6 | `research dataset-export benchmark.jsonl --source-type internal` exports internal rows |
| 7 | `ruff check .` clean |
| 8 | `pytest tests/unit/` passing (no regression) |

### 6.3 — `research evaluate-datasets`

| # | Criterion |
|---|-----------|
| 1 | `research evaluate-datasets teacher.jsonl rule.jsonl` runs without DB connection |
| 2 | Rich table shows all 5 mandatory metrics + teacher/baseline/paired/missing counts |
| 3 | Exit code 1 + error message when teacher file does not exist |
| 4 | Exit code 1 + error message when baseline file does not exist |
| 5 | `--dataset-type internal_benchmark` appears in table title |
| 6 | `research --help` lists the new `evaluate-datasets` command |
| 7 | `ruff check .` clean |
| 8 | `pytest tests/unit/` passing (no regression) |

### Sprint-6 Final Sign-off Checklist

```
- [ ] 6.2 implemented and tests passing
- [ ] 6.3 implemented and tests passing
- [ ] ruff check . clean
- [ ] pytest passing (baseline: 542 tests)
- [ ] research evaluate (DB-based) still works unchanged
- [ ] research evaluate-datasets (file-based) visible in `research --help`
- [ ] docs/contracts.md §17 status → ✅
- [ ] TASKLIST.md 6.2, 6.3, 6.7 → ✅
- [ ] AGENTS.md test count updated
```

---

## Evaluation Output Reference

### `EvaluationMetrics` (MAE-based, `compare_datasets()` output)

```python
@dataclass
class EvaluationMetrics:
    sentiment_agreement: float  # fraction matching sentiment_label (0.0–1.0)
    priority_mae: float         # MAE on priority_score (1–10 scale)
    relevance_mae: float        # MAE on relevance_score (0.0–1.0)
    impact_mae: float           # MAE on impact_score (0.0–1.0)
    tag_overlap_mean: float     # avg Jaccard similarity of tags (0.0–1.0)
    sample_count: int           # paired rows evaluated
    missing_pairs: int          # baseline rows without matching teacher row
```

### `EvaluationResult` (MSE-based, `compare_outputs()` / `research evaluate` output)

```python
@dataclass
class EvaluationResult:
    document_count: int
    matched_sentiments: int
    matched_actionable: int
    sentiment_accuracy: float
    actionable_accuracy: float
    priority_mse: float
    relevance_mse: float
    impact_mse: float
    novelty_mse: float
```

These two types are intentionally distinct. Do not merge.

---

## Distillation Readiness Thresholds (Sprint 7+)

Sprint 6 establishes measurement infrastructure. Companion model results require
a live `InternalCompanionProvider` endpoint. Rule baseline results will show high MAE — expected.

| Metric | Threshold for companion promotion |
|--------|-----------------------------------|
| `sentiment_agreement` | ≥ 0.85 |
| `priority_mae` | ≤ 1.5 |
| `relevance_mae` | ≤ 0.15 |
| `impact_mae` | ≤ 0.20 |
| `tag_overlap_mean` | ≥ 0.30 |

All five gates must pass before companion promotion. No partial promotions.

---

## Security Notes

- No API keys, no external network calls in any dataset or evaluation path
- `--teacher-only` enforced at function level (I-27) — cannot be bypassed by CLI callers
- JSONL files are read-only inputs — `json.loads()` only, no `eval()`, no pickle
- File existence check before loading — no silent empty-dataset evaluation
- `load_jsonl()` handles empty lines gracefully (skips blank lines)
