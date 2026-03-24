# Tuning & Promotion Contract — Sprint 8

> **Canonical reference** for Sprint-8 controlled companion inference,
> tuning artifact flow, and manual promotion record.
>
> New module: `app/research/tuning.py` — `TuningArtifact`, `PromotionRecord`,
> `save_tuning_artifact()`, `save_promotion_record()`.
>
> Upstream contracts: `docs/contracts.md §19`, invariants I-40–I-45.
> Upstream Sprint-7: `docs/benchmark_promotion_contract.md`.
> Additive Sprint-12 extension: `docs/sprint12_training_job_contract.md` (`training_job_record` audit linkage).

---

## Purpose

Sprint 8 closes the loop between evaluation and controlled promotion.

Sprint 7 established how to measure whether companion is ready.
Sprint 8 establishes what happens next: recording the tuning dataset manifest and writing an
immutable promotion record if the operator decides to proceed.

**Four explicit separations** — non-negotiable:

| Concept | Meaning | What it is NOT |
|---------|---------|----------------|
| **Benchmark** | Run harness, produce `EvaluationReport` (Sprint 7) | Not tuning, not training |
| **Tuning** | Record what dataset + model base would be used for training | Not training itself, not model weights |
| **Training** | External process running gradient descent on a local model | Not in this platform — operator runs this |
| **Promotion** | Immutable audit record that companion was promoted to production use | Not a code change, not automatic routing |

No Sprint-8 code path trains a model, modifies weights, calls an external training API,
or changes provider routing. Companion remains `analysis_source=INTERNAL` until the
operator changes `APP_LLM_PROVIDER` + `companion_model_endpoint` in their environment.

---

## Sprint-8 Scope

### What Sprint 8 delivers

1. **New module `app/research/tuning.py`**: `TuningArtifact`, `PromotionRecord`,
   `save_tuning_artifact()`, `save_promotion_record()`.

2. **CLI: `research prepare-tuning-artifact <teacher_file> <model_base>`** — records a training-
   ready manifest (row count, format, dataset path, model target) without running training.

3. **CLI: `research record-promotion <report_file> <model_id>`** — writes an immutable
   `PromotionRecord` after the operator confirms all gates are met. Requires `--operator-note`.

4. **Tests** for all new functions in `app/research/tuning.py` + CLI commands.

5. **This contract document** + `docs/contracts.md §19` + I-40–I-45.

### What Sprint 8 does NOT deliver

- No model training, no fine-tuning API calls, no weight updates
- No automatic routing change
- No new analysis provider or tier
- No changes to `evaluation.py`, `companion.py`, or `pipeline.py`
- No trading execution

---

## Controlled Companion Inference Contract

`InternalCompanionProvider` is already fully implemented (Sprint 4D/5A). Sprint 8 does not
change the provider. The following constraints remain in force:

| Constraint | Invariant |
|-----------|-----------|
| Endpoint must be localhost or allowlisted | I-15 |
| `impact_score` capped at 0.8 | I-17 |
| `analysis_source=INTERNAL` always | I-18, I-39 |
| Not teacher-eligible | I-16, I-26 |
| No external API key required | I-15 |

Companion becomes "inference-ready" when:
1. A local Ollama / llama.cpp / vLLM endpoint is running
2. `APP_LLM_PROVIDER=companion` and `companion_model_endpoint=http://localhost:11434`
   are set in the operator's environment
3. `analyze-pending` runs — companion docs accumulate with `analysis_source=INTERNAL`

This is a **purely operational step** — no code change required. Sprint 8 simply documents
the readiness path and the tuning artifact that supports a trained companion model.

---

## Tuning Artifact Contract

### Purpose

A `TuningArtifact` is a manifest that records:
- Which teacher dataset will be used for fine-tuning
- Which model base is the training target
- How many training rows exist
- Whether the dataset passed evaluation gates

It does NOT contain model weights. It does NOT run training. It is a pre-training checkpoint
that the operator can hand to an external fine-tuning process (Ollama, Hugging Face, etc.).

### `TuningArtifact` Schema

```json
{
  "artifact_type": "tuning_manifest",
  "generated_at": "<ISO-8601 UTC>",
  "teacher_dataset": "<absolute path to teacher JSONL>",
  "model_base": "<e.g. llama3.2:3b or kai-analyst-v1>",
  "training_format": "openai_chat",
  "row_count": 0,
  "evaluation_report": "<absolute path | null>",
  "notes": []
}
```

**`training_format`** is always `"openai_chat"` in Sprint 8 — the format already produced by
`export_training_data()`. This field is a declaration, not a conversion trigger.

**`row_count`** is the number of rows in the teacher JSONL file. Zero rows → manifest is
invalid for training.

### `save_tuning_artifact()` Signature

```python
def save_tuning_artifact(
    output_path: Path | str,
    *,
    teacher_dataset: Path | str,
    model_base: str,
    row_count: int,
    evaluation_report: Path | str | None = None,
    notes: list[str] | None = None,
) -> Path:
    """Write a training-ready manifest for an external fine-tuning process.

    Does NOT train a model. Does NOT call any training API.
    This is a pre-training record only.

    Contract reference: docs/tuning_promotion_contract.md §TuningArtifact
    """
```

---

## Promotion Record Contract

### Purpose

A `PromotionRecord` is an immutable audit record that captures the operator's decision to
promote companion to production use. It is a JSON file, not a code change.

Promotion means: the operator has decided to set `APP_LLM_PROVIDER=companion` (or similar)
in their production environment. The platform does not enforce or detect this change.
The `PromotionRecord` is proof that the operator made an informed, gate-verified decision.

### `PromotionRecord` Schema

```json
{
  "record_type": "companion_promotion",
  "generated_at": "<ISO-8601 UTC>",
  "promoted_model": "<companion model identifier, e.g. kai-analyst-v1>",
  "promoted_endpoint": "<companion_model_endpoint, e.g. http://localhost:11434>",
  "evaluation_report": "<absolute path to the evaluation_report.json that passed gates>",
  "tuning_artifact": "<absolute path to tuning_manifest.json | null>",
  "training_job_record": "<absolute path to training_job_record.json | null>",
  "operator_note": "<required ??? human-readable confirmation>",
  "gates_summary": {
    "sentiment_pass": true,
    "priority_pass": true,
    "relevance_pass": true,
    "impact_pass": true,
    "tag_overlap_pass": true,
    "false_actionable_pass": true
  },
  "reversal_instructions": "Set APP_LLM_PROVIDER to previous value to revert companion"
}
```

**`operator_note`** is required and must be non-empty. It forces the operator to acknowledge
the promotion explicitly (I-43).

**`reversal_instructions`** is hardcoded — promotion is reversible by env var only (I-44).

### `save_promotion_record()` Signature

```python
def save_promotion_record(
    output_path: Path | str,
    *,
    promoted_model: str,
    promoted_endpoint: str,
    evaluation_report: Path | str,
    tuning_artifact: Path | str | None = None,
    operator_note: str,
    gates_summary: dict[str, bool] | None = None,
) -> Path:
    """Write an immutable promotion record for audit trail.

    Does NOT change provider routing. Does NOT modify any system state.
    The operator must change APP_LLM_PROVIDER separately.

    Contract reference: docs/tuning_promotion_contract.md §PromotionRecord
    Invariants: I-40–I-45
    """
```

**Validation inside `save_promotion_record()`:**
- `operator_note.strip()` must be non-empty → `ValueError` if blank (I-43)
- `evaluation_report` path must exist → `FileNotFoundError` if missing (I-45)

---

## New Module: `app/research/tuning.py`

Minimal new file. No imports from `evaluation.py`, no circular dependencies.

```python
"""Tuning artifact and promotion record management.

Sprint 8 — companion tuning flow and manual promotion gate.
Contract reference: docs/tuning_promotion_contract.md
Invariants: docs/contracts.md §19, I-40–I-45.
"""
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class TuningArtifact:
    """Manifest describing a dataset ready for external fine-tuning."""
    teacher_dataset: str
    model_base: str
    training_format: str          # always "openai_chat"
    row_count: int
    evaluation_report: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "artifact_type": "tuning_manifest",
            "generated_at": datetime.now(UTC).isoformat(),
            "teacher_dataset": self.teacher_dataset,
            "model_base": self.model_base,
            "training_format": self.training_format,
            "row_count": self.row_count,
            "evaluation_report": self.evaluation_report,
            "notes": list(self.notes),
        }


@dataclass
class PromotionRecord:
    """Immutable audit record of a manual companion promotion decision."""
    promoted_model: str
    promoted_endpoint: str
    evaluation_report: str
    operator_note: str
    tuning_artifact: str | None = None
    training_job_record: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "record_type": "companion_promotion",
            "generated_at": datetime.now(UTC).isoformat(),
            "promoted_model": self.promoted_model,
            "promoted_endpoint": self.promoted_endpoint,
            "evaluation_report": self.evaluation_report,
            "tuning_artifact": self.tuning_artifact,
            "operator_note": self.operator_note,
            "reversal_instructions": (
                "Set APP_LLM_PROVIDER to previous value to revert companion"
            ),
        }


def save_tuning_artifact(
    output_path: Path | str,
    *,
    teacher_dataset: Path | str,
    model_base: str,
    row_count: int,
    evaluation_report: Path | str | None = None,
    notes: list[str] | None = None,
) -> Path:
    """Write a training-ready manifest. Does NOT train a model."""
    artifact = TuningArtifact(
        teacher_dataset=str(Path(teacher_dataset).resolve()),
        model_base=model_base,
        training_format="openai_chat",
        row_count=row_count,
        evaluation_report=(
            str(Path(evaluation_report).resolve()) if evaluation_report else None
        ),
        notes=list(notes or []),
    )
    resolved = Path(output_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(artifact.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved


def save_promotion_record(
    output_path: Path | str,
    *,
    promoted_model: str,
    promoted_endpoint: str,
    evaluation_report: Path | str,
    tuning_artifact: Path | str | None = None,
    training_job_record: Path | str | None = None,
    operator_note: str,
    gates_summary: dict[str, bool] | None = None,
) -> Path:
    """Write an immutable promotion record. Does NOT change provider routing."""
    if not operator_note.strip():
        raise ValueError(
            "operator_note must not be blank — explicit acknowledgement required (I-43)"
        )
    eval_path = Path(evaluation_report)
    if not eval_path.exists():
        raise FileNotFoundError(
            f"Evaluation report not found: {eval_path} — promotion requires a valid report (I-45)"
        )
    record = PromotionRecord(
        promoted_model=promoted_model,
        promoted_endpoint=promoted_endpoint,
        evaluation_report=str(eval_path.resolve()),
        tuning_artifact=(
            str(Path(tuning_artifact).resolve()) if tuning_artifact else None
        ),
        operator_note=operator_note.strip(),
    )
    resolved = Path(output_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(record.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return resolved
```

---

## CLI Contract — Sprint 8

### 8.2 — `research prepare-tuning-artifact <teacher_file> <model_base>`

```python
@research_app.command("prepare-tuning-artifact")
def research_prepare_tuning_artifact(
    teacher_file: str = typer.Argument(
        ..., help="Path to teacher JSONL (produced by dataset-export --teacher-only)"
    ),
    model_base: str = typer.Argument(
        ..., help="Target model base for fine-tuning, e.g. llama3.2:3b"
    ),
    eval_report: str | None = typer.Option(
        None,
        "--eval-report",
        help="Path to evaluation_report.json confirming dataset quality (optional)",
    ),
    out: str = typer.Option(
        "tuning_manifest.json",
        "--out",
        help="Output path for the tuning manifest JSON",
    ),
) -> None:
    """Record a training-ready manifest for external fine-tuning.

    Does NOT train a model. Does NOT call any external API.
    Use this before handing the teacher dataset to an external training process.

    Sprint-8 contract: docs/tuning_promotion_contract.md §CLI-Contract-8.2
    """
```

Implementation logic:

```python
from pathlib import Path
from app.research.tuning import save_tuning_artifact
from app.research.evaluation import load_jsonl

teacher_path = Path(teacher_file)
if not teacher_path.exists():
    console.print(f"[red]Teacher file not found: {teacher_path}[/red]")
    raise typer.Exit(1)

rows = load_jsonl(teacher_path)
if not rows:
    console.print("[yellow]Teacher file is empty — tuning manifest requires data.[/yellow]")
    raise typer.Exit(1)

artifact_path = save_tuning_artifact(
    out,
    teacher_dataset=teacher_path,
    model_base=model_base,
    row_count=len(rows),
    evaluation_report=eval_report,
)

table = Table(title="Tuning Manifest")
table.add_column("Field", style="cyan")
table.add_column("Value")
table.add_row("Teacher Dataset", str(teacher_path.resolve()))
table.add_row("Model Base", model_base)
table.add_row("Training Format", "openai_chat")
table.add_row("Row Count", str(len(rows)))
table.add_row("Evaluation Report", eval_report or "not provided")
table.add_row("Manifest Path", str(artifact_path.resolve()))
console.print(table)
console.print(
    "\n[dim]This manifest is a record only. "
    "Run your fine-tuning process separately with the teacher dataset.[/dim]"
)
```

### 8.3 — `research record-promotion <report_file> <model_id>`

```python
@research_app.command("record-promotion")
def research_record_promotion(
    report_file: str = typer.Argument(
        ..., help="Path to evaluation_report.json that passed check-promotion"
    ),
    model_id: str = typer.Argument(
        ..., help="Companion model identifier (e.g. kai-analyst-v1)"
    ),
    endpoint: str = typer.Option(
        ..., "--endpoint",
        help="Companion model endpoint (must match companion_model_endpoint setting)",
    ),
    operator_note: str = typer.Option(
        ..., "--operator-note",
        help="Required: human-readable acknowledgement of the promotion decision",
    ),
    tuning_artifact: str | None = typer.Option(
        None, "--tuning-artifact",
        help="Path to tuning_manifest.json if fine-tuning was performed",
    ),
    out: str = typer.Option(
        "promotion_record.json", "--out",
        help="Output path for the promotion record JSON",
    ),
) -> None:
    """Record a manual companion promotion decision as an immutable audit artifact.

    Does NOT change provider routing. The operator must update APP_LLM_PROVIDER
    and companion_model_endpoint separately after this step.

    Reversal: set APP_LLM_PROVIDER to the previous value.

    Sprint-8 contract: docs/tuning_promotion_contract.md §CLI-Contract-8.3
    Invariants: I-40–I-45
    """
```

Implementation logic:

```python
import json
from pathlib import Path
from app.research.tuning import save_promotion_record
from app.research.evaluation import EvaluationMetrics, validate_promotion

# Verify report passes gates before allowing record
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
        actionable_accuracy=float(m_raw["actionable_accuracy"]),
        false_actionable_rate=float(m_raw["false_actionable_rate"]),
        sample_count=int(m_raw.get("sample_count", 0)),
        missing_pairs=int(m_raw.get("missing_pairs", 0)),
    )
except (KeyError, ValueError, json.JSONDecodeError) as e:
    console.print(f"[red]Could not parse report file:[/red] {e}")
    raise typer.Exit(1) from e

validation = validate_promotion(metrics)
if not validation.is_promotable:
    console.print(
        "[red]Promotion blocked: evaluation report does not pass all gates.[/red]"
    )
    console.print("[dim]Run `research check-promotion` to see which gates failed.[/dim]")
    raise typer.Exit(1)

try:
    record_path = save_promotion_record(
        out,
        promoted_model=model_id,
        promoted_endpoint=endpoint,
        evaluation_report=report_path,
        tuning_artifact=tuning_artifact,
        operator_note=operator_note,
    )
except ValueError as e:
    console.print(f"[red]Promotion record error:[/red] {e}")
    raise typer.Exit(1) from e

console.print(f"[green]Promotion record written to {record_path.resolve()}[/green]")
console.print(
    "\n[bold yellow]IMPORTANT[/bold yellow]: Provider routing has NOT been changed.\n"
    "To activate companion: set APP_LLM_PROVIDER=companion and\n"
    f"companion_model_endpoint={endpoint} in your environment."
)
console.print(
    "[dim]To reverse: set APP_LLM_PROVIDER to the previous value.[/dim]"
)
```

---

## Sprint-8 Acceptance Criteria

### 8.1 — `app/research/tuning.py` + Tests

| # | Criterion |
|---|-----------|
| 1 | `TuningArtifact.to_json_dict()` contains `artifact_type == "tuning_manifest"` |
| 2 | `TuningArtifact.training_format` is always `"openai_chat"` |
| 3 | `save_tuning_artifact()` creates file, valid JSON, correct fields |
| 4 | `save_tuning_artifact()` with `evaluation_report=None` → field is `null` in JSON |
| 5 | `PromotionRecord.to_json_dict()` contains `record_type == "companion_promotion"` |
| 6 | `PromotionRecord` contains `reversal_instructions` field (hardcoded text) |
| 7 | `save_promotion_record()` creates file, valid JSON, all fields present |
| 8 | `save_promotion_record()` raises `ValueError` when `operator_note` is blank |
| 9 | `save_promotion_record()` raises `FileNotFoundError` when `evaluation_report` missing |
| 10 | `pytest tests/unit/` passing (no regression) |
| 11 | `ruff check .` clean |

### 8.2 — `research prepare-tuning-artifact`

| # | Criterion |
|---|-----------|
| 1 | Loads teacher JSONL, counts rows, calls `save_tuning_artifact()` |
| 2 | Prints summary table: dataset path, model base, row count, manifest path |
| 3 | Exit 1 + error when teacher file not found |
| 4 | Exit 1 + warning when teacher file is empty |
| 5 | `--eval-report` path stored in manifest when provided |
| 6 | Disclaimer printed: "record only, run fine-tuning separately" |
| 7 | `research --help` lists `prepare-tuning-artifact` in research group |
| 8 | No DB calls, no LLM calls, no model inference |
| 9 | `ruff check .` clean |
| 10 | `pytest tests/unit/` passing |

### 8.3 — `research record-promotion`

| # | Criterion |
|---|-----------|
| 1 | Verifies evaluation report passes all 6 quantitative gates before writing record |
| 2 | Exit 1 + error when evaluation report does not pass gates |
| 3 | Exit 1 + error when evaluation report file not found |
| 4 | Exit 1 + error when `--operator-note` is blank or whitespace-only |
| 5 | `--tuning-artifact` path stored when provided, `null` otherwise |
| 6 | Prints activation instructions: `APP_LLM_PROVIDER=companion` + endpoint |
| 7 | Prints reversal instructions |
| 8 | Does NOT change any system state (no env write, no config write) |
| 9 | `research --help` lists `record-promotion` in research group |
| 10 | `ruff check .` clean |
| 11 | `pytest tests/unit/` passing |

### Sprint-8 Final Sign-off Checklist

```
- [ ] 8.1: app/research/tuning.py implemented + fully tested
- [ ] 8.2: prepare-tuning-artifact CLI implemented + tested
- [ ] 8.3: record-promotion CLI implemented + tested
- [ ] ruff check . clean
- [ ] pytest passing (baseline: 561 tests, no regression)
- [ ] check-promotion, benchmark-companion, evaluate-datasets unchanged
- [ ] docs/contracts.md §19 added
- [ ] TASKLIST.md Sprint-8 tasks updated
- [ ] AGENTS.md test count updated
- [ ] tuning_promotion_contract.md complete and consistent
```

---

## Security Notes

- `save_promotion_record()` validates `evaluation_report` exists before writing — no phantom records
- `operator_note` is required and validated non-empty — forces deliberate human confirmation
- No external API calls in any Sprint-8 code path
- No secrets, no credentials stored in any artifact
- JSONL files are read-only inputs (load via `load_jsonl()`)
- Artifact files are write-once by convention (I-38) — re-runs produce new files
- Provider routing is controlled by env vars only — no Sprint-8 code writes to env or config files

---

## Invariant Summary (I-40 through I-45)

Full text in `docs/contracts.md §19`.

| ID | Rule |
|----|------|
| I-40 | No Sprint-8 code path trains a model, modifies weights, or calls an external training API. |
| I-41 | `promotion_record.json` is an audit artifact only — it does NOT change provider routing. Routing is controlled exclusively by env vars. |
| I-42 | Provider routing is controlled exclusively by `APP_LLM_PROVIDER` and `companion_model_endpoint` env vars. No platform code writes to these. |
| I-43 | `save_promotion_record()` requires a non-empty `operator_note`. Blank notes are a `ValueError`. Operators must acknowledge the promotion explicitly. |
| I-44 | Promotion is reversible by changing `APP_LLM_PROVIDER` to the previous value. No database migration or code change required. |
| I-45 | `record-promotion` and `save_promotion_record()` require the evaluation report file to exist and pass all 6 quantitative gates. Non-passing reports block record creation. |
