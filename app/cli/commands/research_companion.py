"""Experimental Companion-ML CLI commands.

[EXPERIMENTAL] These commands are part of the future companion model training pipeline.
No companion model is currently deployed.

Commands: evaluate-datasets, benchmark-companion, benchmark-companion-run,
          check-promotion, prepare-tuning-artifact, record-promotion, evaluate,
          shadow-report

All commands register on research_core_app imported from research_core.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from app.cli.commands.research_core import (  # noqa: F401
    console,
    research_core_app,
)
from app.core.settings import get_settings


def _normalize_dataset_type(dataset_type: str) -> str:
    normalized_type = dataset_type.strip().lower()
    allowed_types = {"rule_baseline", "internal_benchmark", "custom"}
    if normalized_type not in allowed_types:
        console.print(
            f"[red]Error:[/red] Unsupported dataset type '{dataset_type}'. "
            "Use rule_baseline, internal_benchmark, or custom."
        )
        raise typer.Exit(1)
    return normalized_type


def _load_dataset_rows(label: str, path_str: str) -> list[dict[str, object]]:
    import json

    from app.research.evaluation import load_jsonl

    try:
        return load_jsonl(path_str)
    except FileNotFoundError as err:
        console.print(f"[red]Error:[/red] {label} dataset file not found: {path_str}")
        raise typer.Exit(1) from err
    except json.JSONDecodeError as err:
        console.print(
            f"[red]Error:[/red] Invalid JSONL content in {label} dataset "
            f"'{path_str}': {err.msg}"
        )
        raise typer.Exit(1) from err
    except OSError as err:
        console.print(
            f"[red]Error:[/red] Could not read {label} dataset '{path_str}': {err}"
        )
        raise typer.Exit(1) from err


def _build_dataset_evaluation_table(title: str, report: Any) -> Table:
    metrics = report.metrics

    table = Table(title=title)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Dataset Type", report.dataset_type)
    table.add_row("Teacher Rows", str(report.teacher_count))
    table.add_row("Candidate Rows", str(report.baseline_count))
    table.add_row("Paired Documents", str(report.paired_count))
    table.add_row("Missing Pairs", str(metrics.missing_pairs))
    table.add_row("Sentiment Agreement", f"{metrics.sentiment_agreement:.2%}")
    table.add_row("Priority MAE", f"{metrics.priority_mae:.4f}")
    table.add_row("Relevance MAE", f"{metrics.relevance_mae:.4f}")
    table.add_row("Impact MAE", f"{metrics.impact_mae:.4f}")
    table.add_row("Tag Overlap Mean", f"{metrics.tag_overlap_mean:.4f}")
    return table


def _print_dataset_warnings(
    teacher_rows: list[dict[str, object]],
    candidate_rows: list[dict[str, object]],
    paired_count: int,
) -> None:
    if not teacher_rows:
        console.print("[yellow]Teacher dataset is empty.[/yellow]")
    if not candidate_rows:
        console.print("[yellow]Candidate dataset is empty.[/yellow]")
    if paired_count == 0:
        console.print("[yellow]No overlapping document_id pairs found.[/yellow]")


def _get_companion_provider(settings: Any, endpoint_override: str | None = None) -> Any:
    from app.analysis.factory import create_provider
    from app.core.settings import ProviderSettings

    effective_settings = settings.model_copy(deep=True)
    if endpoint_override:
        validated = ProviderSettings(companion_model_endpoint=endpoint_override)
        effective_settings.providers.companion_model_endpoint = validated.companion_model_endpoint

    provider = create_provider("companion", effective_settings)
    if provider is None:
        console.print(
            "[red]Error:[/red] No local companion endpoint configured. "
            "Set COMPANION_MODEL_ENDPOINT or pass --endpoint."
        )
        raise typer.Exit(1)
    return provider


def _print_companion_promotion_readiness(report: Any) -> None:
    if report.dataset_type != "internal_benchmark" or report.paired_count == 0:
        return

    from app.research.evaluation import validate_promotion

    promotion = validate_promotion(report.metrics)

    prom_table = Table(title="Companion Promotion Readiness (Sprint 7 Gates)")
    prom_table.add_column("Gate", style="cyan")
    prom_table.add_column("Required", style="yellow")
    prom_table.add_column("Status", style="bold")

    def status_str(passed: bool) -> str:
        return "[green]PASS[/green]" if passed else "[red]FAIL[/red]"

    prom_table.add_row("Sentiment", ">= 0.85", status_str(promotion.sentiment_pass))
    prom_table.add_row("Priority MAE", "<= 1.50", status_str(promotion.priority_pass))
    prom_table.add_row("Relevance MAE", "<= 0.15", status_str(promotion.relevance_pass))
    prom_table.add_row("Impact MAE", "<= 0.20", status_str(promotion.impact_pass))
    prom_table.add_row("Tag Overlap", ">= 0.30", status_str(promotion.tag_overlap_pass))

    console.print(prom_table)

    if promotion.is_promotable:
        console.print("\n[bold green]PROMOTABLE[/bold green] — quantitative gates passed.")
    else:
        console.print("\n[bold red]NOT PROMOTABLE[/bold red] — one or more gates failed.")

    console.print(
        "[dim]Manual I-34 verification remains required; no automatic promotion occurs.[/dim]"
    )


@research_core_app.command("evaluate-datasets")
def research_evaluate_datasets(
    teacher_file: str = typer.Argument(..., help="Path to teacher JSONL file"),
    candidate_file: str = typer.Argument(..., help="Path to candidate JSONL file"),
    dataset_type: str = typer.Option(
        "rule_baseline",
        "--dataset-type",
        help="Dataset comparison type: rule_baseline, internal_benchmark, custom",
    ),
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
    """Compare two exported JSONL datasets and print offline evaluation metrics."""
    import json

    from app.research.evaluation import compare_datasets, load_jsonl

    normalized_type = dataset_type.strip().lower()
    allowed_types = {"rule_baseline", "internal_benchmark", "custom"}
    if normalized_type not in allowed_types:
        console.print(
            f"[red]Error:[/red] Unsupported dataset type '{dataset_type}'. "
            "Use rule_baseline, internal_benchmark, or custom."
        )
        raise typer.Exit(1)

    def load_rows(label: str, path_str: str) -> list[dict[str, object]]:
        try:
            return load_jsonl(path_str)
        except FileNotFoundError as err:
            console.print(f"[red]Error:[/red] {label} dataset file not found: {path_str}")
            raise typer.Exit(1) from err
        except json.JSONDecodeError as err:
            console.print(
                f"[red]Error:[/red] Invalid JSONL content in {label} dataset "
                f"'{path_str}': {err.msg}"
            )
            raise typer.Exit(1) from err
        except OSError as err:
            console.print(
                f"[red]Error:[/red] Could not read {label} dataset '{path_str}': {err}"
            )
            raise typer.Exit(1) from err

    teacher_rows = load_rows("Teacher", teacher_file)
    candidate_rows = load_rows("Candidate", candidate_file)

    if not teacher_rows:
        console.print("[yellow]Teacher dataset is empty.[/yellow]")
    if not candidate_rows:
        console.print("[yellow]Candidate dataset is empty.[/yellow]")

    report = compare_datasets(teacher_rows, candidate_rows, dataset_type=normalized_type)
    if report.paired_count == 0:
        console.print("[yellow]No overlapping document_id pairs found.[/yellow]")

    metrics = report.metrics

    table = Table(title="Dataset Evaluation Metrics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Dataset Type", report.dataset_type)
    table.add_row("Teacher Rows", str(report.teacher_count))
    table.add_row("Candidate Rows", str(report.baseline_count))
    table.add_row("Paired Documents", str(report.paired_count))
    table.add_row("Missing Pairs", str(metrics.missing_pairs))
    table.add_row("Sentiment Agreement", f"{metrics.sentiment_agreement:.2%}")
    table.add_row("Priority MAE", f"{metrics.priority_mae:.4f}")
    table.add_row("Relevance MAE", f"{metrics.relevance_mae:.4f}")
    table.add_row("Impact MAE", f"{metrics.impact_mae:.4f}")
    table.add_row("Tag Overlap Mean", f"{metrics.tag_overlap_mean:.4f}")

    console.print(table)

    if report.dataset_type == "internal_benchmark" and report.paired_count > 0:
        from app.research.evaluation import validate_promotion
        promotion = validate_promotion(metrics)

        prom_table = Table(title="Companion Promotion Readiness (Sprint 7 Gates)")
        prom_table.add_column("Gate", style="cyan")
        prom_table.add_column("Required", style="yellow")
        prom_table.add_column("Status", style="bold")

        def status_str(passed: bool) -> str:
            return "[green]PASS[/green]" if passed else "[red]FAIL[/red]"

        prom_table.add_row("Sentiment", ">= 0.85", status_str(promotion.sentiment_pass))
        prom_table.add_row("Priority MAE", "<= 1.50", status_str(promotion.priority_pass))
        prom_table.add_row("Relevance MAE", "<= 0.15", status_str(promotion.relevance_pass))
        prom_table.add_row("Impact MAE", "<= 0.20", status_str(promotion.impact_pass))
        prom_table.add_row("Tag Overlap", ">= 0.30", status_str(promotion.tag_overlap_pass))

        console.print(prom_table)

        if promotion.is_promotable:
            console.print("\n[bold green]PROMOTABLE — all gates passed.[/bold green]")
        else:
            console.print("\n[bold red]NOT PROMOTABLE — one or more gates failed.[/bold red]")

    if save_report:
        from app.research.evaluation import save_evaluation_report
        saved = save_evaluation_report(
            report,
            save_report,
            teacher_dataset=teacher_file,
            candidate_dataset=candidate_file,
        )
        console.print(f"[dim]Evaluation report saved: {saved}[/dim]")

    if save_artifact:
        from app.research.evaluation import save_benchmark_artifact
        artifact = save_benchmark_artifact(
            save_artifact,
            teacher_dataset=teacher_file,
            candidate_dataset=candidate_file,
            report=report,
            report_path=save_report,
        )
        console.print(f"[dim]Benchmark artifact saved: {artifact}[/dim]")


@research_core_app.command("benchmark-companion")
def research_benchmark_companion(
    teacher_file: str = typer.Argument(..., help="Path to teacher JSONL file"),
    candidate_file: str = typer.Argument(..., help="Path to candidate/internal JSONL file"),
    dataset_type: str = typer.Option(
        "internal_benchmark",
        "--dataset-type",
        help="Dataset comparison type: internal_benchmark, rule_baseline, custom",
    ),
    report_out: str | None = typer.Option(
        None,
        "--report-out",
        help="Optional path to save a structured benchmark report JSON",
    ),
    artifact_out: str | None = typer.Option(
        None,
        "--artifact-out",
        help="Optional path to save a benchmark artifact manifest JSON",
    ),
) -> None:
    """[EXPERIMENTAL] Benchmark companion outputs
    against teacher datasets.

    Requires COMPANION_MODEL_ENDPOINT to be set. No companion model is currently deployed.
    """

    from app.research.evaluation import (
        compare_datasets,
        save_benchmark_artifact,
        save_evaluation_report,
    )

    normalized_type = _normalize_dataset_type(dataset_type)
    teacher_rows = _load_dataset_rows("Teacher", teacher_file)
    candidate_rows = _load_dataset_rows("Candidate", candidate_file)

    report = compare_datasets(teacher_rows, candidate_rows, dataset_type=normalized_type)
    _print_dataset_warnings(teacher_rows, candidate_rows, report.paired_count)
    console.print(_build_dataset_evaluation_table("Companion Benchmark Metrics", report))
    _print_companion_promotion_readiness(report)

    saved_report_path: Path | None = None
    if report_out:
        saved_report_path = save_evaluation_report(
            report,
            report_out,
            teacher_dataset=teacher_file,
            candidate_dataset=candidate_file,
        )
        console.print(f"[green]Saved benchmark report to {saved_report_path.resolve()}[/green]")

    if artifact_out:
        saved_artifact_path = save_benchmark_artifact(
            artifact_out,
            teacher_dataset=teacher_file,
            candidate_dataset=candidate_file,
            report=report,
            report_path=saved_report_path,
        )
        console.print(
            f"[green]Saved benchmark artifact to {saved_artifact_path.resolve()}[/green]"
        )


@research_core_app.command("benchmark-companion-run")
def research_benchmark_companion_run(
    teacher_file: str = typer.Argument(..., help="Path to teacher JSONL file"),
    candidate_out: str = typer.Argument(
        ..., help="Path to save the generated companion candidate JSONL"
    ),
    endpoint: str | None = typer.Option(
        None,
        "--endpoint",
        help="Optional localhost companion endpoint override",
    ),
    dataset_type: str = typer.Option(
        "internal_benchmark",
        "--dataset-type",
        help="Dataset comparison type: internal_benchmark, rule_baseline, custom",
    ),
    report_out: str | None = typer.Option(
        None,
        "--report-out",
        help="Optional path to save a structured benchmark report JSON",
    ),
    artifact_out: str | None = typer.Option(
        None,
        "--artifact-out",
        help="Optional path to save a benchmark artifact manifest JSON",
    ),
) -> None:
    """[EXPERIMENTAL] Run local companion inference on a teacher dataset and benchmark the output.

    Requires COMPANION_MODEL_ENDPOINT to be set. No companion model is currently deployed.
    """
    import asyncio

    async def run() -> None:
        from app.research.evaluation import (
            build_candidate_dataset_rows,
            compare_datasets,
            save_benchmark_artifact,
            save_evaluation_report,
            save_jsonl_rows,
        )

        normalized_type = _normalize_dataset_type(dataset_type)
        settings = get_settings()
        provider = _get_companion_provider(settings, endpoint)
        teacher_rows = _load_dataset_rows("Teacher", teacher_file)

        if not teacher_rows:
            console.print("[yellow]Teacher dataset is empty.[/yellow]")
            raise typer.Exit(1)

        try:
            candidate_rows = await build_candidate_dataset_rows(
                teacher_rows,
                provider.analyze,
                provider_name=provider.provider_name,
                analysis_source="internal",
            )
        except ValueError as err:
            console.print(f"[red]Error:[/red] Invalid teacher dataset row: {err}")
            raise typer.Exit(1) from err
        except RuntimeError as err:
            console.print(f"[red]Error:[/red] Companion inference failed: {err}")
            raise typer.Exit(1) from err

        try:
            saved_candidate_path = save_jsonl_rows(candidate_rows, candidate_out)
        except OSError as err:
            console.print(
                f"[red]Error:[/red] Could not write candidate dataset '{candidate_out}': {err}"
            )
            raise typer.Exit(1) from err

        console.print(
            f"[green]Saved generated companion dataset to {saved_candidate_path.resolve()}[/green]"
        )

        report = compare_datasets(teacher_rows, candidate_rows, dataset_type=normalized_type)
        _print_dataset_warnings(teacher_rows, candidate_rows, report.paired_count)
        console.print(_build_dataset_evaluation_table("Companion Benchmark Metrics", report))
        _print_companion_promotion_readiness(report)

        saved_report_path: Path | None = None
        if report_out:
            try:
                saved_report_path = save_evaluation_report(
                    report,
                    report_out,
                    teacher_dataset=teacher_file,
                    candidate_dataset=saved_candidate_path,
                )
            except OSError as err:
                console.print(
                    f"[red]Error:[/red] Could not write benchmark report '{report_out}': {err}"
                )
                raise typer.Exit(1) from err
            console.print(
                f"[green]Saved benchmark report to {saved_report_path.resolve()}[/green]"
            )

        if artifact_out:
            try:
                saved_artifact_path = save_benchmark_artifact(
                    artifact_out,
                    teacher_dataset=teacher_file,
                    candidate_dataset=saved_candidate_path,
                    report=report,
                    report_path=saved_report_path,
                )
            except OSError as err:
                console.print(
                    f"[red]Error:[/red] Could not write benchmark artifact '{artifact_out}': {err}"
                )
                raise typer.Exit(1) from err
            console.print(
                f"[green]Saved benchmark artifact to {saved_artifact_path.resolve()}[/green]"
            )

    asyncio.run(run())


@research_core_app.command("check-promotion")
def research_check_promotion(
    report_file: str = typer.Argument(
        ..., help="Path to evaluation_report.json produced by evaluate-datasets --save-report"
    ),
) -> None:
    """[EXPERIMENTAL] Check whether a saved evaluation report meets companion promotion thresholds.

    No companion model is currently deployed. This command is part of the future
    companion model training pipeline.
    Exits 0 if all five quantitative gates pass (promotable).
    Exits 1 if any gate fails — human review required.

    Note: Gate I-34 (false-actionable rate) requires separate manual verification
    via `research evaluate`. See docs/benchmark_promotion_contract.md.
    """
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

    gate_table = Table(title="Promotion Gate Check")
    gate_table.add_column("Gate", style="cyan")
    gate_table.add_column("Threshold", justify="right")
    gate_table.add_column("Actual", justify="right")
    gate_table.add_column("Status", justify="center")

    def _gate_status(passed: bool) -> str:
        return "[green]PASS[/green]" if passed else "[red]FAIL[/red]"

    gate_table.add_row(
        "Sentiment Agreement", ">= 0.850",
        f"{metrics.sentiment_agreement:.3f}",
        _gate_status(validation.sentiment_pass),
    )
    gate_table.add_row(
        "Priority MAE", "<= 1.500",
        f"{metrics.priority_mae:.3f}",
        _gate_status(validation.priority_pass),
    )
    gate_table.add_row(
        "Relevance MAE", "<= 0.150",
        f"{metrics.relevance_mae:.3f}",
        _gate_status(validation.relevance_pass),
    )
    gate_table.add_row(
        "Impact MAE", "<= 0.200",
        f"{metrics.impact_mae:.3f}",
        _gate_status(validation.impact_pass),
    )
    gate_table.add_row(
        "Tag Overlap", ">= 0.300",
        f"{metrics.tag_overlap_mean:.3f}",
        _gate_status(validation.tag_overlap_pass),
    )

    console.print(gate_table)
    console.print(f"\nSamples evaluated: {metrics.sample_count}")
    console.print(
        "[yellow]Note: Gate I-34 (actionable false-positive rate) requires manual "
        "verification via `research evaluate`. See benchmark_promotion_contract.md.[/yellow]"
    )

    if validation.is_promotable:
        console.print("\n[bold green]PROMOTABLE[/bold green] - all quantitative gates passed.")
        console.print(
            "[dim]Reminder: Manual I-34 verification still required before promotion.[/dim]"
        )
    else:
        failed = sum([
            not validation.sentiment_pass,
            not validation.priority_pass,
            not validation.relevance_pass,
            not validation.impact_pass,
            not validation.tag_overlap_pass,
        ])
        console.print(f"\n[bold red]NOT PROMOTABLE[/bold red] - {failed} gate(s) failed.")
        raise typer.Exit(1)


@research_core_app.command("prepare-tuning-artifact")
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
    """[EXPERIMENTAL] Record a training-ready manifest for external fine-tuning.

    Part of the companion model pipeline. No companion model is currently deployed.
    Does NOT train a model. Does NOT call any external API.
    Use this before handing the teacher dataset to an external training process.

    Sprint-8 contract: docs/tuning_promotion_contract.md
    """
    from pathlib import Path

    from app.research.evaluation import load_jsonl
    from app.research.tuning import save_tuning_artifact

    teacher_path = Path(teacher_file)
    if not teacher_path.exists():
        console.print(f"[red]Teacher file not found: {teacher_path}[/red]")
        raise typer.Exit(1)

    rows = load_jsonl(teacher_path)
    if not rows:
        console.print("[yellow]Teacher file is empty - tuning manifest requires data.[/yellow]")
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


@research_core_app.command("record-promotion")
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
    """[EXPERIMENTAL] Record a manual companion promotion decision as an immutable audit artifact.

    Part of the companion model pipeline. No companion model is currently deployed.
    Does NOT change provider routing. The operator must update APP_LLM_PROVIDER
    and companion_model_endpoint separately after this step.

    Reversal: set APP_LLM_PROVIDER to the previous value.

    Sprint-8 contract: docs/tuning_promotion_contract.md
    Invariants: I-40-I-45
    """
    import json
    from pathlib import Path

    from app.research.evaluation import EvaluationMetrics, validate_promotion
    from app.research.tuning import save_promotion_record

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
    console.print("[dim]To reverse: set APP_LLM_PROVIDER to the previous value.[/dim]")


@research_core_app.command("evaluate")
def research_evaluate(
    teacher_source: str = typer.Option("external_llm", help="The baseline extraction source"),
    limit: int = typer.Option(50, help="Number of documents to evaluate over"),
) -> None:
    """[EXPERIMENTAL] Run the internal companion model against teacher outputs and print metrics.

    Requires COMPANION_MODEL_ENDPOINT to be set. No companion model is currently deployed.
    """
    import asyncio

    async def run() -> None:
        from app.analysis.keywords.engine import KeywordEngine
        from app.analysis.pipeline import AnalysisPipeline
        from app.research.evaluation import compare_outputs
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        settings = get_settings()
        monitor_dir = Path(settings.monitor_dir)
        session_factory = build_session_factory(settings.db)

        console.print(f"[bold]Loading {limit} teacher documents...[/bold]")
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=True, limit=limit)

        teacher_docs = [d for d in docs if d.effective_analysis_source.value == teacher_source]
        if not teacher_docs:
            console.print(
                f"[yellow]No documents analyzed by source '{teacher_source}' found.[/yellow]"
            )
            return

        keyword_engine = KeywordEngine.from_monitor_dir(monitor_dir)
        pipeline = AnalysisPipeline(keyword_engine, provider=None, run_llm=False)

        companion_docs = []
        for d in teacher_docs:
            comp_doc = d.model_copy()
            # Erase existing scores so we know we are running fresh
            comp_doc.is_analyzed = False
            comp_doc.sentiment_score = None
            comp_doc.priority_score = None

            res = await pipeline.run(comp_doc)
            res.apply_to_document()
            companion_docs.append(res.document)

        console.print(f"[bold]Evaluating {len(teacher_docs)} outputs...[/bold]")
        metrics = compare_outputs(teacher_docs, companion_docs)

        from rich.table import Table
        table = Table(title="Companion Evaluation Metrics")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green", justify="right")

        table.add_row("Document Count", str(metrics.document_count))
        table.add_row("Matched Sentiments", str(metrics.matched_sentiments))
        table.add_row("Matched Actionable", str(metrics.matched_actionable))
        table.add_row("Sentiment Accuracy", f"{metrics.sentiment_accuracy:.2%}")
        table.add_row("Actionable Accuracy", f"{metrics.actionable_accuracy:.2%}")
        table.add_row("Priority MSE", f"{metrics.priority_mse:.4f}")
        table.add_row("Relevance MSE", f"{metrics.relevance_mse:.4f}")
        table.add_row("Impact MSE", f"{metrics.impact_mse:.4f}")
        table.add_row("Novelty MSE", f"{metrics.novelty_mse:.4f}")

        console.print(table)

    asyncio.run(run())



@research_core_app.command("shadow-report")
def research_shadow_report() -> None:
    """shadow-report shows divergence row when primary and shadow differ (I-55)."""
    import asyncio

    from rich.console import Console
    from rich.table import Table

    from app.core.settings import get_settings
    from app.storage.db.session import build_session_factory
    from app.storage.repositories.document_repo import DocumentRepository

    console = Console()
    settings = get_settings()
    session_factory = build_session_factory(settings.db)

    async def _fetch() -> list[Any]:
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            return await repo.list(is_analyzed=True, limit=5000)

    docs = asyncio.run(_fetch())
    shadow_docs = [
        d for d in docs
        if d.metadata and "shadow_analysis" in d.metadata
    ]

    if not shadow_docs:
        console.print("No documents with shadow analysis")
        return

    table = Table(title="Shadow Divergence Report")
    table.add_column("Doc ID")
    table.add_column("Diverged", style="bold red")

    divergence_count = 0
    for doc in shadow_docs:
        primary_sent = doc.sentiment_label.value if doc.sentiment_label else ""
        shadow_sent = doc.metadata["shadow_analysis"].get("sentiment_label")
        diverged = "YES" if primary_sent != shadow_sent else "NO"
        if diverged == "YES":
            divergence_count += 1
        table.add_row(str(doc.id), diverged)

    console.print(table)
    console.print(f"Total: {len(shadow_docs)}, Diverged: {divergence_count}")


