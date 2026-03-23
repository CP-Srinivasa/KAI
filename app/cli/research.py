"""Research and signal generation commands for KAI CLI."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from app.core.settings import get_settings

console = Console()
research_app = typer.Typer(help="Research and signal generation commands", no_args_is_help=True)

# ── research ──────────────────────────────────────────────────────────────────


@research_app.command("brief")
def research_brief(
    watchlist: str = typer.Option(..., help="Watchlist/cluster to generate brief for (e.g. defi)"),
    watchlist_type: str = typer.Option(
        "assets",
        "--type",
        help="Watchlist type: assets, persons, topics, sources",
    ),
    limit: int = typer.Option(100, help="Number of recent documents to process"),
    output_format: str = typer.Option("md", "--format", help="Output format: md or json"),
) -> None:
    """Generate a Research Brief summarizing documents for a specific cluster."""
    import asyncio
    import json

    async def run() -> None:
        from app.research.briefs import ResearchBriefBuilder
        from app.research.watchlists import WatchlistRegistry, parse_watchlist_type
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        settings = get_settings()
        monitor_dir = Path(settings.monitor_dir)
        session_factory = build_session_factory(settings.db)

        registry = WatchlistRegistry.from_monitor_dir(monitor_dir)
        try:
            resolved_type = parse_watchlist_type(watchlist_type)
        except ValueError as err:
            console.print(f"[red]Error:[/red] {err}")
            raise typer.Exit(1) from err

        watchlist_items = registry.get_watchlist(watchlist, item_type=resolved_type)

        if not watchlist_items:
            console.print(
                f"[yellow]Warning: Watchlist '{watchlist}' produced no {resolved_type}.[/yellow]"
            )

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            # We fetch more than limit to ensure enough matches after symbol filtering
            docs = await repo.list(is_analyzed=True, limit=limit * 5)

        if watchlist_items:
            docs = registry.filter_documents(docs, watchlist, item_type=resolved_type)

        docs = docs[:limit]

        builder = ResearchBriefBuilder(cluster_name=watchlist)
        brief = builder.build(docs)

        if output_format.lower() == "json":
            console.print(json.dumps(brief.to_json_dict(), indent=2))
        else:
            console.print(brief.to_markdown())

    asyncio.run(run())


@research_app.command("watchlists")
def research_watchlists(
    watchlist_type: str = typer.Option(
        "assets",
        "--type",
        help="Watchlist type: assets, persons, topics, sources",
    ),
    watchlist: str | None = typer.Argument(None, help="Optional watchlist tag to inspect"),
) -> None:
    """List available research watchlists or show the members of one watchlist."""
    from app.research.watchlists import WatchlistRegistry, parse_watchlist_type

    settings = get_settings()
    registry = WatchlistRegistry.from_monitor_dir(Path(settings.monitor_dir))

    try:
        resolved_type = parse_watchlist_type(watchlist_type)
    except ValueError as err:
        console.print(f"[red]Error:[/red] {err}")
        raise typer.Exit(1) from err

    if watchlist:
        items = registry.get_watchlist(watchlist, item_type=resolved_type)
        if not items:
            console.print(
                f"[yellow]No watchlist entries found for '{watchlist}' ({resolved_type}).[/yellow]"
            )
            return

        console.print(
            f"[bold]{watchlist}[/bold] "
            f"([{resolved_type}] {len(items)} entries)"
        )
        for item in items:
            console.print(f"  - {item}")
        return

    all_watchlists = registry.get_all_watchlists(item_type=resolved_type)
    if not all_watchlists:
        console.print(f"[yellow]No watchlists found for type '{resolved_type}'.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Watchlist")
    table.add_column("Count", justify="right")
    table.add_column("Preview")

    for name, items in sorted(all_watchlists.items()):
        preview = ", ".join(items[:3])
        if len(items) > 3:
            preview += ", ..."
        table.add_row(name, str(len(items)), preview)

    console.print(table)
    console.print(f"\n[bold]{len(all_watchlists)} watchlists[/bold] ({resolved_type})")


@research_app.command("signals")
def research_signals(
    limit: int = typer.Option(100, help="Number of recent documents to search"),
    min_priority: int = typer.Option(8, help="Minimum priority score for signals"),
    watchlist: str = typer.Option(None, help="Watchlist name to boost priority of matching assets"),
    provider: str = typer.Option(None, help="Filter by provider (e.g. openai, fallback)"),
) -> None:
    """Extract and list strict Signal Candidates for automated trading."""
    import asyncio

    async def run() -> None:
        from app.research.signals import extract_signal_candidates
        from app.research.watchlists import WatchlistRegistry
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        settings = get_settings()
        monitor_dir = Path(settings.monitor_dir)
        session_factory = build_session_factory(settings.db)

        # Resolve watchlist boosts
        watchlist_boosts = {}
        if watchlist:
            registry = WatchlistRegistry.from_monitor_dir(monitor_dir)
            symbols = registry.get_watchlist(watchlist)
            if symbols:
                # Flat boost of +2 for matching watchlist items
                watchlist_boosts = {s.upper(): 2 for s in symbols}
                console.print(
                    f"[dim]Applying +2 priority boost to {len(symbols)} "
                    f"assets from '{watchlist}'[/dim]"
                )

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=True, limit=limit)

        if provider:
            docs = [d for d in docs if d.provider == provider]

        signals = extract_signal_candidates(
            docs,
            min_priority=min_priority,
            watchlist_boosts=watchlist_boosts
        )

        if not signals:
            console.print(f"[yellow]No signal candidates found in the last {limit} docs.[/yellow]")
            return

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("SigID", width=12)
        table.add_column("Dir", width=6)
        table.add_column("Pri", width=3)
        table.add_column("Asset", width=10)
        table.add_column("Conf", width=5)
        table.add_column("Evidence", width=60)

        for sig in signals:
            direction_color = (
                "green"
                if sig.direction_hint == "bullish"
                else "red"
                if sig.direction_hint == "bearish"
                else "yellow"
            )
            dir_str = f"[{direction_color}]{sig.direction_hint.upper()}[/{direction_color}]"

            # Truncate evidence text cleanly
            evidence = sig.supporting_evidence.replace("\n", " ")
            evidence = evidence[:60] + ("..." if len(evidence) > 60 else "")

            table.add_row(
                sig.signal_id[:12],
                dir_str,
                str(sig.priority),
                sig.target_asset,
                f"{sig.confidence:.2f}",
                evidence,
            )

        console.print(table)
        console.print(f"\n[bold]{len(signals)} Actionable Signals[/bold] ready for execution.")

    asyncio.run(run())


@research_app.command("dataset-export")
def research_dataset_export(
    output_file: str = typer.Argument(..., help="Path to output JSONL file"),
    source_type: str = typer.Option(
        "external_llm",
        help="Filter by analysis source, e.g. external_llm, internal, rule",
    ),
    teacher_only: bool = typer.Option(
        False,
        "--teacher-only",
        help="Export only EXTERNAL_LLM rows (strict mode, I-27)",
    ),
    limit: int = typer.Option(1000, help="Max documents to export"),
) -> None:
    """Export analyzed documents to JSONL for Companion Model tuning."""
    import asyncio
    from pathlib import Path

    async def run() -> None:
        from app.research.datasets import export_training_data
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        settings = get_settings()
        session_factory = build_session_factory(settings.db)

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=True, limit=limit)

        if source_type and source_type != "all":
            docs = [d for d in docs if d.effective_analysis_source.value == source_type]

        if not docs:
            console.print(
                f"[yellow]No analyzed documents found for source_type {source_type}.[/yellow]"
            )
            return

        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        count = export_training_data(docs, out_path, teacher_only=teacher_only)
        console.print(
            f"[green]Successfully exported {count} documents to {out_path.absolute()}[/green]"
        )

    asyncio.run(run())


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


@research_app.command("evaluate-datasets")
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


@research_app.command("benchmark-companion")
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
    """[EXPERIMENTAL] Benchmark companion outputs against teacher datasets.

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


@research_app.command("benchmark-companion-run")
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


@research_app.command("check-promotion")
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


@research_app.command("evaluate")
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



@research_app.command("shadow-report")
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


# ---------------------------------------------------------------------------
# Sprint 16: signal-handoff
# ---------------------------------------------------------------------------


@research_app.command("signal-handoff")
def research_signal_handoff(
    output: str = typer.Option(
        "artifacts/signal_handoff.json",
        "--output",
        help="Output path for the signal handoff artifact",
    ),
    limit: int = typer.Option(10, help="Max signals to include in handoff"),
) -> None:
    """Export top signal candidates as a read-only handoff artifact (Sprint 16)."""
    import asyncio
    from pathlib import Path

    async def run() -> None:
        from app.research.execution_handoff import create_signal_handoff, save_signal_handoff
        from app.research.signals import extract_signal_candidates
        from app.storage.db.session import build_session_factory
        from app.storage.repositories.document_repo import DocumentRepository

        settings = get_settings()
        session_factory = build_session_factory(settings.db)

        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=True, limit=limit * 5)

        candidates = extract_signal_candidates(docs)[:limit]
        if not candidates:
            console.print("[yellow]No signal candidates found.[/yellow]")
            return

        handoff = create_signal_handoff(candidates[0])
        out_path = Path(output)
        save_signal_handoff(handoff, out_path)

        console.print(f"[green]Signal handoff saved to {out_path.resolve()}[/green]")
        console.print(f"handoff_id={handoff.handoff_id}")
        console.print(f"target_asset={handoff.target_asset}")
        console.print("execution_enabled=False")

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Sprint 20: handoff-acknowledge / handoff-summary / consumer-ack
# ---------------------------------------------------------------------------


@research_app.command("handoff-acknowledge")
def research_handoff_acknowledge(
    handoff_path: str = typer.Argument(..., help="Path to signal handoff JSON artifact"),
    handoff_id: str = typer.Argument(..., help="handoff_id from the artifact to acknowledge"),
    consumer_agent_id: str = typer.Option(
        ..., "--consumer-agent-id", help="Identifier of the acknowledging consumer agent"
    ),
    notes: str = typer.Option("", "--notes", help="Optional audit notes"),
    output: str = typer.Option(
        "artifacts/consumer_acknowledgements.jsonl",
        "--output",
        help="Output path for the acknowledgement audit JSONL",
    ),
) -> None:
    """Audit-only acknowledgement of a consumer-visible signal handoff (Sprint 20)."""
    from pathlib import Path

    from app.research.execution_handoff import (
        append_handoff_acknowledgement_jsonl,
        create_handoff_acknowledgement,
        get_signal_handoff_by_id,
        load_signal_handoffs,
    )

    try:
        handoffs = load_signal_handoffs(Path(handoff_path))
    except FileNotFoundError as exc:
        console.print(f"[red]Signal handoff file not found: {handoff_path}[/red]")
        raise typer.Exit(1) from exc

    try:
        handoff = get_signal_handoff_by_id(handoffs, handoff_id)
    except (KeyError, ValueError) as exc:
        console.print(f"[red]handoff_id not found: {handoff_id}[/red]")
        raise typer.Exit(1) from exc

    if handoff.consumer_visibility != "visible":
        console.print(
            f"[red]Only consumer-visible handoffs can be acknowledged "
            f"(consumer_visibility={handoff.consumer_visibility!r})[/red]"
        )
        raise typer.Exit(1)

    ack = create_handoff_acknowledgement(
        handoff,
        consumer_agent_id=consumer_agent_id,
        notes=notes,
    )
    out_path = Path(output)
    append_handoff_acknowledgement_jsonl(ack, out_path)

    console.print(f"[green]Acknowledgement appended to {out_path.resolve()}[/green]")
    console.print(f"handoff_id={ack.handoff_id}")
    console.print("status=acknowledged_in_audit_only")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_app.command("handoff-summary")
@research_app.command("handoff-collector-summary")
def research_handoff_summary(
    handoff_path: str = typer.Argument(..., help="Path to signal handoff artifact"),
    acknowledgement_path: str = typer.Option(
        "artifacts/consumer_acknowledgements.jsonl",
        "--ack-path",
        help="Path to consumer acknowledgements JSONL",
    ),
) -> None:
    """Summarize pending and acknowledged handoffs from existing artifacts (Sprint 20)."""
    from pathlib import Path

    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_handoff_acknowledgements, load_signal_handoffs

    try:
        handoffs = load_signal_handoffs(Path(handoff_path))
    except FileNotFoundError as exc:
        console.print(f"[red]Signal handoff file not found: {handoff_path}[/red]")
        raise typer.Exit(1) from exc

    ack_path = Path(acknowledgement_path)
    acknowledgements = load_handoff_acknowledgements(ack_path) if ack_path.exists() else []

    report = build_handoff_collector_summary(handoffs, acknowledgements)
    payload = report.to_json_dict()

    table = Table(title="Handoff Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Total Handoffs", str(payload.get("total_count", 0)))
    table.add_row("Pending", str(payload.get("pending_count", 0)))
    table.add_row("Acknowledged", str(payload.get("acknowledged_count", 0)))
    table.add_row("Execution Enabled", "False")
    console.print(table)


@research_app.command("consumer-ack")
def research_consumer_ack(
    handoff_path: str = typer.Argument(..., help="Path to signal handoff JSON artifact"),
    handoff_id: str = typer.Argument(..., help="handoff_id to acknowledge"),
    consumer_agent_id: str = typer.Option(
        ..., "--consumer-agent-id", help="Consumer agent identifier"
    ),
    output: str = typer.Option(
        "artifacts/consumer_acknowledgements.jsonl",
        "--output",
        help="Output path for the acknowledgement JSONL",
    ),
) -> None:
    """Alias for handoff-acknowledge — audit-only consumer acknowledgement (Sprint 20)."""
    from pathlib import Path

    from app.research.execution_handoff import (
        append_handoff_acknowledgement_jsonl,
        create_handoff_acknowledgement,
        get_signal_handoff_by_id,
        load_signal_handoffs,
    )

    try:
        handoffs = load_signal_handoffs(Path(handoff_path))
    except FileNotFoundError as exc:
        console.print(f"[red]Signal handoff file not found: {handoff_path}[/red]")
        raise typer.Exit(1) from exc

    try:
        handoff = get_signal_handoff_by_id(handoffs, handoff_id)
    except (KeyError, ValueError) as exc:
        console.print(f"[red]handoff_id not found: {handoff_id}[/red]")
        raise typer.Exit(1) from exc

    if handoff.consumer_visibility != "visible":
        console.print("[red]Handoff not consumer-visible.[/red]")
        raise typer.Exit(1)

    ack = create_handoff_acknowledgement(handoff, consumer_agent_id=consumer_agent_id)
    out_path = Path(output)
    append_handoff_acknowledgement_jsonl(ack, out_path)

    console.print(f"[green]Consumer ack appended to {out_path.resolve()}[/green]")
    console.print("execution_enabled=False")


# ---------------------------------------------------------------------------
# Sprint 21: readiness-summary
# ---------------------------------------------------------------------------


@research_app.command("readiness-summary")
def research_readiness_summary(
    handoff_path: str | None = typer.Option(
        None, "--handoff-path", help="Signal handoff artifact path"
    ),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state path",
    ),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir", help="Alert audit dir"),
    out: str | None = typer.Option(None, "--out", help="Optional path to save the JSON report"),
) -> None:
    """Print read-only operational readiness summary (Sprint 21)."""
    from pathlib import Path

    from app.alerts.audit import load_alert_audits
    from app.research.active_route import load_active_route_state
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_handoff_acknowledgements, load_signal_handoffs
    from app.research.operational_readiness import (
        ArtifactRef,
        OperationalArtifactRefs,
        build_operational_readiness_report,
        save_operational_readiness_report,
    )

    handoffs = []
    resolved_handoff: Path | None = None
    if handoff_path:
        resolved_handoff = Path(handoff_path)
        if resolved_handoff.exists():
            handoffs = load_signal_handoffs(resolved_handoff)

    ack_path = Path("artifacts/consumer_acknowledgements.jsonl")
    acknowledgements = load_handoff_acknowledgements(ack_path) if ack_path.exists() else []
    collector_summary = build_handoff_collector_summary(handoffs, acknowledgements)

    resolved_state = Path(state_path)
    active_route_state = (
        load_active_route_state(resolved_state) if resolved_state.exists() else None
    )

    alert_dir = Path(alert_audit_dir)
    alert_audits = load_alert_audits(alert_dir) if alert_dir.exists() else []

    artifacts = OperationalArtifactRefs(
        handoff=ArtifactRef(
            path=str(resolved_handoff) if resolved_handoff else None,
            present=bool(resolved_handoff and resolved_handoff.exists()),
        ),
        acknowledgements=ArtifactRef(path=str(ack_path), present=ack_path.exists()),
        active_route_state=ArtifactRef(path=str(resolved_state), present=resolved_state.exists()),
        alert_audit_dir=ArtifactRef(path=str(alert_dir), present=alert_dir.exists()),
    )
    report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=alert_audits,
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=artifacts,
    )

    payload = report.to_json_dict()
    table = Table(title="Operational Readiness Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Status", str(payload.get("readiness_status", "")))
    table.add_row("Issues", str(payload.get("issue_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)

    if out:
        out_path = Path(out)
        save_operational_readiness_report(report, out_path)
        console.print(f"[dim]Saved readiness report to {out_path.resolve()}[/dim]")


# ---------------------------------------------------------------------------
# Sprint 22: provider-health / drift-summary
# ---------------------------------------------------------------------------


@research_app.command("provider-health")
def research_provider_health(
    handoff_path: str | None = typer.Option(
        None, "--handoff-path", help="Signal handoff artifact path"
    ),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state path",
    ),
) -> None:
    """Print read-only provider health derived from readiness artifacts (Sprint 22)."""
    from pathlib import Path

    from app.research.active_route import load_active_route_state
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_signal_handoffs
    from app.research.operational_readiness import (
        OperationalArtifactRefs,
        build_operational_readiness_report,
    )

    handoffs = []
    if handoff_path and Path(handoff_path).exists():
        handoffs = load_signal_handoffs(Path(handoff_path))

    collector_summary = build_handoff_collector_summary(handoffs, [])

    resolved_state = Path(state_path)
    active_route_state = (
        load_active_route_state(resolved_state) if resolved_state.exists() else None
    )
    alert_audits: list[Any] = []

    report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=alert_audits,
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=OperationalArtifactRefs(),
    )
    health = report.provider_health_summary.to_json_dict()
    table = Table(title="Provider Health")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Provider Count", str(health.get("provider_count", 0)))
    table.add_row("Healthy", str(health.get("healthy_count", 0)))
    table.add_row("Degraded", str(health.get("degraded_count", 0)))
    table.add_row("Unavailable", str(health.get("unavailable_count", 0)))
    console.print(table)
    console.print("execution_enabled=False")


@research_app.command("drift-summary")
def research_drift_summary(
    handoff_path: str | None = typer.Option(
        None, "--handoff-path", help="Signal handoff artifact path"
    ),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state path",
    ),
) -> None:
    """Print distribution drift summary derived from readiness artifacts (Sprint 22)."""
    from pathlib import Path

    from app.research.active_route import load_active_route_state
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_signal_handoffs
    from app.research.operational_readiness import (
        OperationalArtifactRefs,
        build_operational_readiness_report,
    )

    handoffs = []
    if handoff_path and Path(handoff_path).exists():
        handoffs = load_signal_handoffs(Path(handoff_path))

    collector_summary = build_handoff_collector_summary(handoffs, [])
    resolved_state = Path(state_path)
    active_route_state = (
        load_active_route_state(resolved_state) if resolved_state.exists() else None
    )

    report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=[],
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=OperationalArtifactRefs(),
    )
    drift = report.distribution_drift_summary.to_json_dict()
    table = Table(title="Distribution Drift Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Status", str(drift.get("status", "")))
    table.add_row("Production Handoffs", str(drift.get("production_handoff_count", 0)))
    console.print(table)


# ---------------------------------------------------------------------------
# Sprint 23: gate-summary / remediation-recommendations
# ---------------------------------------------------------------------------


@research_app.command("gate-summary")
def research_gate_summary(
    handoff_path: str | None = typer.Option(
        None, "--handoff-path", help="Signal handoff artifact path"
    ),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state path",
    ),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir", help="Alert audit dir"),
) -> None:
    """Print read-only protective gate summary derived from readiness (Sprint 23)."""
    from pathlib import Path

    from app.alerts.audit import load_alert_audits
    from app.research.active_route import load_active_route_state
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_handoff_acknowledgements, load_signal_handoffs
    from app.research.operational_readiness import (
        OperationalArtifactRefs,
        build_operational_readiness_report,
    )

    handoffs = []
    if handoff_path and Path(handoff_path).exists():
        handoffs = load_signal_handoffs(Path(handoff_path))

    ack_path = Path("artifacts/consumer_acknowledgements.jsonl")
    acknowledgements = load_handoff_acknowledgements(ack_path) if ack_path.exists() else []
    collector_summary = build_handoff_collector_summary(handoffs, acknowledgements)

    resolved_state = Path(state_path)
    active_route_state = (
        load_active_route_state(resolved_state) if resolved_state.exists() else None
    )

    alert_dir = Path(alert_audit_dir)
    alert_audits = load_alert_audits(alert_dir) if alert_dir.exists() else []

    report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=alert_audits,
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=OperationalArtifactRefs(),
    )
    gate = report.protective_gate_summary.to_json_dict()
    table = Table(title="Protective Gate Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Gate Status", str(gate.get("gate_status", "")))
    table.add_row("Blocking Count", str(gate.get("blocking_count", 0)))
    table.add_row("Execution Enabled", str(gate.get("execution_enabled", False)))
    console.print(table)


@research_app.command("remediation-recommendations")
def research_remediation_recommendations(
    handoff_path: str | None = typer.Option(
        None, "--handoff-path", help="Signal handoff artifact path"
    ),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state path",
    ),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir", help="Alert audit dir"),
) -> None:
    """Print read-only remediation recommendations from protective gate items (Sprint 23)."""
    from pathlib import Path

    from app.alerts.audit import load_alert_audits
    from app.research.active_route import load_active_route_state
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_handoff_acknowledgements, load_signal_handoffs
    from app.research.operational_readiness import (
        OperationalArtifactRefs,
        build_operational_readiness_report,
    )

    handoffs = []
    if handoff_path and Path(handoff_path).exists():
        handoffs = load_signal_handoffs(Path(handoff_path))

    ack_path = Path("artifacts/consumer_acknowledgements.jsonl")
    acknowledgements = load_handoff_acknowledgements(ack_path) if ack_path.exists() else []
    collector_summary = build_handoff_collector_summary(handoffs, acknowledgements)

    resolved_state = Path(state_path)
    active_route_state = (
        load_active_route_state(resolved_state) if resolved_state.exists() else None
    )

    alert_dir = Path(alert_audit_dir)
    alert_audits = load_alert_audits(alert_dir) if alert_dir.exists() else []

    report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=alert_audits,
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=OperationalArtifactRefs(),
    )
    gate = report.protective_gate_summary

    console.print("[bold]Remediation Recommendations[/bold]")
    console.print(f"gate_status={gate.gate_status}")
    console.print(f"blocking_count={gate.blocking_count}")
    console.print("execution_enabled=False")
    for item in gate.items:
        for action in item.recommended_actions:
            console.print(f"  - {action}")


# ---------------------------------------------------------------------------
# Sprint 24: artifact-inventory
# ---------------------------------------------------------------------------


@research_app.command("artifact-inventory")
def research_artifact_inventory(
    artifacts_dir: str = typer.Option(
        "artifacts",
        "--artifacts-dir",
        help="Path to artifacts directory",
    ),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days", help="Stale threshold"),
    out: str | None = typer.Option(None, "--out", help="Optional path to save the JSON report"),
) -> None:
    """Print read-only artifact inventory (Sprint 24). execution_enabled always False (I-150)."""
    from pathlib import Path

    from app.research.artifact_lifecycle import build_artifact_inventory, save_artifact_inventory

    artifacts_path = Path(artifacts_dir)
    report = build_artifact_inventory(artifacts_path, stale_after_days=stale_after_days)
    payload = report.to_json_dict()

    table = Table(title="Artifact Inventory")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Artifacts Dir", str(payload.get("artifacts_dir", "")))
    table.add_row("Total Files", str(payload.get("entry_count", 0)))
    table.add_row("Stale", str(payload.get("stale_count", 0)))
    table.add_row("Current", str(payload.get("current_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)

    if out:
        out_path = Path(out)
        save_artifact_inventory(report, out_path)
        console.print(f"[dim]Saved artifact inventory to {out_path.resolve()}[/dim]")


# ---------------------------------------------------------------------------
# Sprint 25: artifact-rotate
# ---------------------------------------------------------------------------


@research_app.command("artifact-rotate")
def research_artifact_rotate(
    artifacts_dir: str = typer.Option(
        "artifacts",
        "--artifacts-dir",
        help="Path to artifacts directory",
    ),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days", help="Stale threshold"),
    dry_run: bool = typer.Option(
        True, "--dry-run/--no-dry-run", help="Dry-run mode (default True, I-152)"
    ),
    out: str | None = typer.Option(
        None, "--out", help="Optional path to save the rotation summary JSON"
    ),
) -> None:
    """Archive stale artifact files. Dry-run by default (I-152). Protected files skipped (I-155)."""
    from pathlib import Path

    from app.research.artifact_lifecycle import (
        rotate_stale_artifacts,
        save_artifact_rotation_summary,
    )

    artifacts_path = Path(artifacts_dir)
    summary = rotate_stale_artifacts(
        artifacts_path, stale_after_days=stale_after_days, dry_run=dry_run
    )

    table = Table(title="Artifact Rotation Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Dry Run", str(summary.dry_run))
    table.add_row("Archived Count", str(summary.archived_count))
    table.add_row("Skipped Count", str(summary.skipped_count))
    table.add_row("Archive Dir", summary.archive_dir)
    console.print(table)

    if dry_run:
        console.print("[yellow]Dry-run mode: no files were moved.[/yellow]")
    else:
        console.print(f"[green]Archived {summary.archived_count} file(s).[/green]")

    if out:
        out_path = Path(out)
        save_artifact_rotation_summary(summary, out_path)
        console.print(f"[dim]Saved rotation summary to {out_path.resolve()}[/dim]")


# ---------------------------------------------------------------------------
# Sprint 26: artifact-retention / cleanup-eligibility-summary /
#            protected-artifact-summary / review-required-summary
# ---------------------------------------------------------------------------


@research_app.command("artifact-retention")
def research_artifact_retention(
    artifacts_dir: str = typer.Option(
        "artifacts",
        "--artifacts-dir",
        help="Path to artifacts directory",
    ),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days", help="Stale threshold"),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state file path",
    ),
    out: str | None = typer.Option(None, "--out", help="Optional path to save the JSON report"),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON instead of table"),
) -> None:
    """Classify artifact files into retention categories (Sprint 26). No mutations (I-160).

    execution_enabled=False, delete_eligible=False are guaranteed invariants (I-154, I-161).
    Protected artifacts are marked as 'protected' and skipped in rotation (I-155).
    """
    import json as _json
    from pathlib import Path

    from app.research.artifact_lifecycle import build_retention_report

    artifacts_path = Path(artifacts_dir)
    resolved_state = Path(state_path)
    active_route_active = resolved_state.exists()

    report = build_retention_report(
        artifacts_path,
        stale_after_days=stale_after_days,
        active_route_active=active_route_active,
    )
    payload = report.to_json_dict()

    if json_output:
        typer.echo(_json.dumps(payload, indent=2))
    else:
        table = Table(title="Artifact Retention Report")
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        table.add_row("Total", str(payload.get("total_count", 0)))
        table.add_row("Protected", str(payload.get("protected_count", 0)))
        table.add_row("Rotatable", str(payload.get("rotatable_count", 0)))
        table.add_row("Review Required", str(payload.get("review_required_count", 0)))
        table.add_row("execution_enabled", str(payload.get("execution_enabled", False)))
        table.add_row("delete_eligible_count", str(payload.get("delete_eligible_count", 0)))
        console.print(table)

        for entry in report.entries:
            if entry.protected:
                console.print(f"  [cyan]protected[/cyan]: {entry.name}")

    if out:
        from pathlib import Path as _Path
        out_path = _Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_json.dumps(payload, indent=2), encoding="utf-8")
        console.print(f"[dim]Saved retention report to {out_path.resolve()}[/dim]")


@research_app.command("cleanup-eligibility-summary")
def research_cleanup_eligibility_summary(
    artifacts_dir: str = typer.Option(
        "artifacts",
        "--artifacts-dir",
        help="Path to artifacts directory",
    ),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days", help="Stale threshold"),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
        help="Active route state path",
    ),
) -> None:
    """Print cleanup/archive eligibility derived from the retention report (Sprint 26)."""
    from pathlib import Path

    from app.research.artifact_lifecycle import (
        build_cleanup_eligibility_summary,
        build_retention_report,
    )

    artifacts_path = Path(artifacts_dir)
    resolved_state = Path(state_path)
    report = build_retention_report(
        artifacts_path,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    summary = build_cleanup_eligibility_summary(report)
    payload = summary.to_json_dict()

    table = Table(title="Cleanup Eligibility Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Cleanup Eligible", str(payload.get("cleanup_eligible_count", 0)))
    table.add_row("Protected", str(payload.get("protected_count", 0)))
    table.add_row("Review Required", str(payload.get("review_required_count", 0)))
    table.add_row("Dry Run Default", str(payload.get("dry_run_default", True)))
    table.add_row("Delete Eligible", str(payload.get("delete_eligible_count", 0)))
    console.print(table)
    for candidate in summary.candidates:
        console.print(f"  eligible: {candidate.name}")


@research_app.command("protected-artifact-summary")
def research_protected_artifact_summary(
    artifacts_dir: str = typer.Option(
        "artifacts",
        "--artifacts-dir",
        help="Path to artifacts directory",
    ),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
    ),
) -> None:
    """Print protected artifact summary derived from the retention report (Sprint 26)."""
    from pathlib import Path

    from app.research.artifact_lifecycle import (
        build_protected_artifact_summary,
        build_retention_report,
    )

    artifacts_path = Path(artifacts_dir)
    resolved_state = Path(state_path)
    report = build_retention_report(
        artifacts_path,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    summary = build_protected_artifact_summary(report)
    payload = summary.to_json_dict()

    table = Table(title="Protected Artifact Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Protected Count", str(payload.get("protected_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)
    for entry in summary.entries:
        console.print(f"  protected: {entry.name}")


@research_app.command("review-required-summary")
def research_review_required_summary(
    artifacts_dir: str = typer.Option(
        "artifacts",
        "--artifacts-dir",
        help="Path to artifacts directory",
    ),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
    state_path: str = typer.Option(
        "artifacts/active_route_profile.json",
        "--state-path",
    ),
) -> None:
    """Print review-required artifact summary derived from the retention report (Sprint 26)."""
    from pathlib import Path

    from app.research.artifact_lifecycle import (
        build_retention_report,
        build_review_required_summary,
    )

    artifacts_path = Path(artifacts_dir)
    resolved_state = Path(state_path)
    report = build_retention_report(
        artifacts_path,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    summary = build_review_required_summary(report)
    payload = summary.to_json_dict()

    table = Table(title="Review Required Artifact Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Review Required Count", str(payload.get("review_required_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)
    for entry in summary.entries:
        console.print(f"  review_required: {entry.name}")


# ---------------------------------------------------------------------------
# Sprint 27: escalation-summary / blocking-summary / operator-action-summary
# ---------------------------------------------------------------------------


def _build_escalation_from_readiness_artifacts(
    handoff_path: str | None = None,
    state_path: str = "artifacts/active_route_profile.json",
    alert_audit_dir: str = "artifacts",
    artifacts_dir: str = "artifacts",
    stale_after_days: float = 30.0,
) -> Any:
    """Shared helper: build escalation summary from readiness artifacts."""
    from pathlib import Path

    from app.alerts.audit import load_alert_audits
    from app.research.active_route import load_active_route_state
    from app.research.artifact_lifecycle import (
        build_retention_report,
        build_review_required_summary,
    )
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_handoff_acknowledgements, load_signal_handoffs
    from app.research.operational_readiness import (
        ArtifactRef,
        OperationalArtifactRefs,
        build_operational_escalation_summary,
        build_operational_readiness_report,
    )

    handoffs = []
    if handoff_path and Path(handoff_path).exists():
        handoffs = load_signal_handoffs(Path(handoff_path))

    ack_path = Path("artifacts/consumer_acknowledgements.jsonl")
    acknowledgements = load_handoff_acknowledgements(ack_path) if ack_path.exists() else []
    collector_summary = build_handoff_collector_summary(handoffs, acknowledgements)

    resolved_state = Path(state_path)
    active_route_state = (
        load_active_route_state(resolved_state) if resolved_state.exists() else None
    )

    alert_dir = Path(alert_audit_dir)
    alert_audits = load_alert_audits(alert_dir) if alert_dir.exists() else []

    report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=alert_audits,
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=OperationalArtifactRefs(
            active_route_state=ArtifactRef(
                path=str(resolved_state), present=resolved_state.exists()
            ),
            alert_audit_dir=ArtifactRef(path=str(alert_dir), present=alert_dir.exists()),
        ),
    )

    artifacts_path = Path(artifacts_dir)
    retention_report = build_retention_report(
        artifacts_path,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    review_required_summary = build_review_required_summary(retention_report)
    return build_operational_escalation_summary(
        report, review_required_summary=review_required_summary
    )


@research_app.command("escalation-summary")
def research_escalation_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print operational escalation summary (Sprint 27)."""
    escalation = _build_escalation_from_readiness_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    payload = escalation.to_json_dict()
    table = Table(title="Escalation Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Escalation Status", payload.get("escalation_status", ""))
    table.add_row("Blocking Count", str(payload.get("blocking_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


@research_app.command("blocking-summary")
def research_blocking_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print blocking-only slice of the escalation summary (Sprint 27)."""
    from app.research.operational_readiness import build_blocking_summary

    escalation = _build_escalation_from_readiness_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    summary = build_blocking_summary(escalation)
    payload = summary.to_json_dict()
    table = Table(title="Blocking Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Escalation Status", str(payload.get("escalation_status", "")))
    table.add_row("Blocking Count", str(payload.get("blocking_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


@research_app.command("operator-action-summary")
def research_operator_action_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print operator-action-required slice of the escalation summary (Sprint 27)."""
    from app.research.operational_readiness import build_operator_action_summary

    escalation = _build_escalation_from_readiness_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    summary = build_operator_action_summary(escalation)
    payload = summary.to_json_dict()
    table = Table(title="Operator Action Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Operator Action Count", str(payload.get("operator_action_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


# ---------------------------------------------------------------------------
# Sprint 28: action-queue-summary / blocking-actions / prioritized-actions /
#            review-required-actions
# ---------------------------------------------------------------------------


def _build_action_queue_from_escalation(
    handoff_path: str | None = None,
    state_path: str = "artifacts/active_route_profile.json",
    alert_audit_dir: str = "artifacts",
    artifacts_dir: str = "artifacts",
    stale_after_days: float = 30.0,
) -> Any:
    """Shared helper: build action queue summary from escalation."""
    from app.research.operational_readiness import build_action_queue_summary

    escalation = _build_escalation_from_readiness_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    return build_action_queue_summary(escalation)


@research_app.command("action-queue-summary")
def research_action_queue_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print prioritized operator action queue (Sprint 28)."""
    action_queue = _build_action_queue_from_escalation(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    payload = action_queue.to_json_dict()
    table = Table(title="Action Queue Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Queue Status", payload.get("queue_status", ""))
    table.add_row("Total", str(payload.get("total_count", 0)))
    table.add_row("Blocking", str(payload.get("blocking_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


@research_app.command("blocking-actions")
def research_blocking_actions(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print blocking-only action queue items (Sprint 28)."""
    from app.research.operational_readiness import build_blocking_actions

    action_queue = _build_action_queue_from_escalation(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    summary = build_blocking_actions(action_queue)
    payload = summary.to_json_dict()
    table = Table(title="Blocking Actions")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Blocking Count", str(payload.get("blocking_count", 0)))
    table.add_row("Queue Status", str(payload.get("queue_status", "")))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


@research_app.command("prioritized-actions")
def research_prioritized_actions(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print operator action queue in priority order (Sprint 28)."""
    from app.research.operational_readiness import build_prioritized_actions

    action_queue = _build_action_queue_from_escalation(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    summary = build_prioritized_actions(action_queue)
    payload = summary.to_json_dict()
    table = Table(title="Prioritized Actions")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Queue Status", str(payload.get("queue_status", "")))
    table.add_row("Action Count", str(payload.get("action_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


@research_app.command("review-required-actions")
def research_review_required_actions(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print review-required items from the operator action queue (Sprint 28)."""
    from app.research.operational_readiness import build_review_required_actions

    action_queue = _build_action_queue_from_escalation(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    summary = build_review_required_actions(action_queue)
    payload = summary.to_json_dict()
    table = Table(title="Review Required Actions")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Review Required Count", str(payload.get("review_required_count", 0)))
    table.add_row("Queue Status", str(payload.get("queue_status", "")))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)


# ---------------------------------------------------------------------------
# Sprint 29: decision-pack-summary
# ---------------------------------------------------------------------------


def _build_decision_pack_from_artifacts(
    handoff_path: str | None = None,
    state_path: str = "artifacts/active_route_profile.json",
    alert_audit_dir: str = "artifacts",
    artifacts_dir: str = "artifacts",
    stale_after_days: float = 30.0,
) -> Any:
    """Shared helper: build operator decision pack from readiness artifacts."""
    from pathlib import Path

    from app.research.artifact_lifecycle import (
        build_retention_report,
        build_review_required_summary,
    )
    from app.research.operational_readiness import (
        build_action_queue_summary,
        build_blocking_summary,
        build_operator_decision_pack,
    )

    escalation = _build_escalation_from_readiness_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    blocking_summary = build_blocking_summary(escalation)
    action_queue_summary = build_action_queue_summary(escalation)

    resolved_state = Path(state_path)
    artifacts_path = Path(artifacts_dir)
    retention_report = build_retention_report(
        artifacts_path,
        stale_after_days=stale_after_days,
        active_route_active=resolved_state.exists(),
    )
    review_required_summary = build_review_required_summary(retention_report)

    # Build a minimal readiness report for the pack
    from app.alerts.audit import load_alert_audits
    from app.research.active_route import load_active_route_state
    from app.research.distribution import build_handoff_collector_summary
    from app.research.execution_handoff import load_handoff_acknowledgements, load_signal_handoffs
    from app.research.operational_readiness import (
        ArtifactRef,
        OperationalArtifactRefs,
        build_operational_readiness_report,
    )

    handoffs = []
    if handoff_path and Path(handoff_path).exists():
        handoffs = load_signal_handoffs(Path(handoff_path))
    ack_path = Path("artifacts/consumer_acknowledgements.jsonl")
    acknowledgements = load_handoff_acknowledgements(ack_path) if ack_path.exists() else []
    collector_summary = build_handoff_collector_summary(handoffs, acknowledgements)
    r_state = Path(state_path)
    active_route_state = (
        load_active_route_state(r_state) if r_state.exists() else None
    )
    alert_dir = Path(alert_audit_dir)
    alert_audits = load_alert_audits(alert_dir) if alert_dir.exists() else []

    readiness_report = build_operational_readiness_report(
        handoffs=handoffs,
        collector_summary=collector_summary,
        alert_audits=alert_audits,
        active_route_state=active_route_state,
        envelopes=[],
        artifacts=OperationalArtifactRefs(
            active_route_state=ArtifactRef(path=str(r_state), present=r_state.exists()),
        ),
    )

    return build_operator_decision_pack(
        readiness_summary=readiness_report,
        blocking_summary=blocking_summary,
        action_queue_summary=action_queue_summary,
        review_required_summary=review_required_summary,
    )


@research_app.command("operator-decision-pack")
@research_app.command("decision-pack-summary")
def research_decision_pack_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
    out: str | None = typer.Option(None, "--out", help="Optional path to save the JSON report"),
) -> None:
    """Print operator decision pack summary (Sprint 29). Advisory only — no execution authority."""
    from pathlib import Path

    from app.research.operational_readiness import save_operator_decision_pack

    pack = _build_decision_pack_from_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    payload = pack.to_json_dict()

    table = Table(title="Operator Decision Pack Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Overall Status", payload.get("overall_status", ""))
    table.add_row("Blocking Count", str(payload.get("blocking_count", 0)))
    table.add_row("Review Required", str(payload.get("review_required_count", 0)))
    table.add_row("Action Queue Count", str(payload.get("action_queue_count", 0)))
    table.add_row("Execution Enabled", str(payload.get("execution_enabled", False)))
    console.print(table)

    if out:
        out_path = Path(out)
        save_operator_decision_pack(pack, out_path)
        console.print(f"[dim]Saved decision pack to {out_path.resolve()}[/dim]")


# ---------------------------------------------------------------------------
# Sprint 45: daily-summary
# ---------------------------------------------------------------------------


@research_app.command("daily-summary")
def research_daily_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_hours: int = typer.Option(24, "--stale-after-hours"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
    loop_audit_path: str = typer.Option(
        "artifacts/trading_loop_audit.jsonl",
        "--loop-audit-path",
    ),
    loop_last_n: int = typer.Option(50, "--loop-last-n"),
    portfolio_audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--portfolio-audit-path",
    ),
    market_data_provider: str = typer.Option("coingecko", "--market-data-provider"),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
    ),
    timeout_seconds: int = typer.Option(10, "--timeout-seconds"),
    review_journal_path: str = typer.Option(
        "artifacts/operator_review_journal.jsonl",
        "--review-journal-path",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print canonical JSON payload"),
) -> None:
    """Print canonical daily operator summary derived from existing read-only surfaces."""
    import asyncio
    import json as _json

    from app.agents.mcp_server import get_daily_operator_summary

    payload = asyncio.run(
        get_daily_operator_summary(
            handoff_path=handoff_path,
            state_path=state_path,
            alert_audit_dir=alert_audit_dir,
            artifacts_dir=artifacts_dir,
            stale_after_hours=stale_after_hours,
            retention_stale_after_days=stale_after_days,
            loop_audit_path=loop_audit_path,
            loop_last_n=loop_last_n,
            portfolio_audit_path=portfolio_audit_path,
            market_data_provider=market_data_provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
            review_journal_path=review_journal_path,
        )
    )

    if as_json:
        console.print(_json.dumps(payload, indent=2))
        return

    cycle_status = payload.get("last_cycle_status")
    cycle_symbol = payload.get("last_cycle_symbol")
    cycle_at = payload.get("last_cycle_at")
    cycle_suffix = "last: none"
    if isinstance(cycle_status, str) and cycle_status:
        cycle_suffix = f"last: {cycle_status}"
        if isinstance(cycle_symbol, str) and cycle_symbol:
            cycle_suffix += f" | {cycle_symbol}"
        if isinstance(cycle_at, str) and cycle_at:
            cycle_suffix += f" | {cycle_at}"

    exposure_pct = payload.get("total_exposure_pct", 0.0)
    if isinstance(exposure_pct, (int, float)):
        exposure_text = f"{float(exposure_pct):.2f}%"
    else:
        exposure_text = "0.00%"

    console.print("[bold]Daily Operator View[/bold]")
    console.print(f"Readiness:      {payload.get('readiness_status', 'unknown')}")
    console.print(
        f"Cycles today:   {payload.get('cycle_count_today', 0)}  ({cycle_suffix})"
    )
    console.print(
        "Portfolio:      "
        f"{payload.get('position_count', 0)} positions"
        f" | {exposure_text} exposure"
        f" | MTM: {payload.get('mark_to_market_status', 'unknown')}"
    )
    console.print(
        f"Decision Pack:  {payload.get('decision_pack_status', 'unknown')}"
    )
    console.print(f"Incidents:      {payload.get('open_incidents', 0)} open")
    console.print(f"Aggregated at:  {payload.get('aggregated_at', 'unknown')}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


# ---------------------------------------------------------------------------
# Sprint 30: operator-runbook / runbook-summary / runbook-next-steps
# ---------------------------------------------------------------------------


FINAL_RESEARCH_COMMAND_NAMES: tuple[str, ...] = (
    "signal-handoff",
    "handoff-acknowledge",
    "handoff-collector-summary",
    "readiness-summary",
    "provider-health",
    "drift-summary",
    "gate-summary",
    "remediation-recommendations",
    "artifact-inventory",
    "artifact-rotate",
    "artifact-retention",
    "cleanup-eligibility-summary",
    "protected-artifact-summary",
    "review-required-summary",
    "escalation-summary",
    "blocking-summary",
    "operator-action-summary",
    "action-queue-summary",
    "blocking-actions",
    "prioritized-actions",
    "review-required-actions",
    "decision-pack-summary",
    "daily-summary",
    "operator-runbook",
    "runbook-summary",
    "runbook-next-steps",
    "review-journal-append",
    "review-journal-summary",
    "resolution-summary",
    "market-data-quote",
    "market-data-snapshot",
    "paper-portfolio-snapshot",
    "paper-positions-summary",
    "paper-exposure-summary",
    "trading-loop-status",
    "trading-loop-recent-cycles",
    "trading-loop-run-once",
    "alert-audit-summary",
)

RESEARCH_COMMAND_ALIASES: dict[str, str] = {
    "consumer-ack": "handoff-acknowledge",
    "handoff-summary": "handoff-collector-summary",
    "operator-decision-pack": "decision-pack-summary",
    "loop-cycle-summary": "trading-loop-recent-cycles",
}

SUPERSEDED_RESEARCH_COMMAND_NAMES: tuple[str, ...] = ("governance-summary",)


def get_research_command_inventory() -> dict[str, object]:
    """Return the locked research command inventory for contract tests."""
    return {
        "final_commands": list(FINAL_RESEARCH_COMMAND_NAMES),
        "aliases": dict(RESEARCH_COMMAND_ALIASES),
        "superseded_commands": list(SUPERSEDED_RESEARCH_COMMAND_NAMES),
        "provisional_commands": list(get_provisional_research_command_names()),
    }


def get_registered_research_command_names() -> set[str]:
    """Return all currently registered research command names."""
    names: set[str] = set()
    for command in research_app.registered_commands:
        name = getattr(command, "name", None)
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def get_provisional_research_command_names() -> tuple[str, ...]:
    """Return registered research commands outside the locked final/alias set."""

    classified = (
        set(FINAL_RESEARCH_COMMAND_NAMES)
        | set(RESEARCH_COMMAND_ALIASES)
        | set(SUPERSEDED_RESEARCH_COMMAND_NAMES)
    )
    provisional = sorted(get_registered_research_command_names() - classified)
    return tuple(provisional)


def extract_runbook_command_refs(payload: dict[str, Any]) -> list[str]:
    """Extract all command_refs from a runbook payload (used by MCP server validation)."""
    refs: list[str] = []
    for step in payload.get("steps", []):
        refs.extend(step.get("command_refs", []))
    for step in payload.get("next_steps", []):
        refs.extend(step.get("command_refs", []))
    refs.extend(payload.get("command_refs", []))
    return list(dict.fromkeys(refs))  # deduplicated, order preserved


def get_invalid_research_command_refs(refs: list[str]) -> list[str]:
    """Return any command refs that are not registered research sub-commands."""
    registered = get_registered_research_command_names()
    invalid_refs: list[str] = []
    for ref in refs:
        parts = ref.strip().split()
        if len(parts) != 2 or parts[0] != "research" or parts[1] not in registered:
            invalid_refs.append(ref)
    return invalid_refs


def _require_valid_runbook_command_refs(payload: dict[str, Any]) -> None:
    """Fail closed when runbook payload references non-canonical CLI commands."""
    invalid_refs = get_invalid_research_command_refs(extract_runbook_command_refs(payload))
    if invalid_refs:
        console.print(
            f"[red]Runbook contains invalid command references: {invalid_refs}[/red]"
        )
        raise typer.Exit(1)


def _build_runbook_from_artifacts(
    handoff_path: str | None = None,
    state_path: str = "artifacts/active_route_profile.json",
    alert_audit_dir: str = "artifacts",
    artifacts_dir: str = "artifacts",
    stale_after_days: float = 30.0,
) -> Any:
    """Shared helper: build operator runbook from readiness artifacts."""
    from app.research.operational_readiness import build_operator_runbook

    pack = _build_decision_pack_from_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    return build_operator_runbook(decision_pack=pack)


def _load_review_journal_summary(
    journal_path: str = "artifacts/operator_review_journal.jsonl",
) -> Any:
    from app.research.operational_readiness import (
        build_review_journal_summary,
        load_review_journal_entries,
    )

    path = Path(journal_path)
    entries = load_review_journal_entries(path)
    return build_review_journal_summary(entries, journal_path=path)


def _load_review_resolution_summary(
    journal_path: str = "artifacts/operator_review_journal.jsonl",
) -> Any:
    from app.research.operational_readiness import build_review_resolution_summary

    summary = _load_review_journal_summary(journal_path=journal_path)
    return build_review_resolution_summary(summary)


@research_app.command("operator-runbook")
def research_operator_runbook(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
    out: str | None = typer.Option(None, "--out", help="Optional path to save the JSON runbook"),
) -> None:
    """Print canonical operator runbook with validated commands (Sprint 30)."""
    from pathlib import Path

    from app.research.operational_readiness import save_operator_runbook

    runbook = _build_runbook_from_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    payload = runbook.to_json_dict()
    _require_valid_runbook_command_refs(payload)

    console.print("[bold]Operator Runbook[/bold]")
    console.print(f"status={runbook.overall_status}")
    console.print(f"steps={len(runbook.steps)}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")

    for i, step in enumerate(runbook.steps, 1):
        console.print(f"\n[cyan]{i}. priority={step.priority}[/cyan]  {step.title}")
        console.print(f"   {step.summary}")
        for ref in step.command_refs:
            console.print(f"   Command: {ref}")

    if out:
        out_path = Path(out)
        save_operator_runbook(runbook, out_path)
        console.print(f"\n[dim]Saved runbook to {out_path.resolve()}[/dim]")


@research_app.command("runbook-summary")
def research_runbook_summary(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print a compact operator runbook summary (Sprint 30)."""
    runbook = _build_runbook_from_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    _require_valid_runbook_command_refs(runbook.to_json_dict())
    console.print("[bold]Operator Runbook Summary[/bold]")
    console.print(f"status={runbook.overall_status}")
    console.print(f"steps={len(runbook.steps)}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_app.command("runbook-next-steps")
def research_runbook_next_steps(
    handoff_path: str | None = typer.Option(None, "--handoff-path"),
    state_path: str = typer.Option("artifacts/active_route_profile.json", "--state-path"),
    alert_audit_dir: str = typer.Option("artifacts", "--alert-audit-dir"),
    artifacts_dir: str = typer.Option("artifacts", "--artifacts-dir"),
    stale_after_days: float = typer.Option(30.0, "--stale-after-days"),
) -> None:
    """Print the next-steps runbook surface (Sprint 30)."""
    runbook = _build_runbook_from_artifacts(
        handoff_path=handoff_path,
        state_path=state_path,
        alert_audit_dir=alert_audit_dir,
        artifacts_dir=artifacts_dir,
        stale_after_days=stale_after_days,
    )
    _require_valid_runbook_command_refs(runbook.to_json_dict())
    console.print("[bold]Operator Runbook Next Steps[/bold]")
    console.print(f"status={runbook.overall_status}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")
    for i, step in enumerate(runbook.next_steps, 1):
        console.print(f"\n{i}. priority={step.priority}  {step.title}")
        for ref in step.command_refs:
            console.print(f"   Command: {ref}")


@research_app.command("review-journal-append")
def research_review_journal_append(
    source_ref: str = typer.Argument(..., help="Referenced runbook/action/decision-pack source"),
    operator_id: str = typer.Option(..., "--operator-id", help="Operator identifier"),
    review_action: str = typer.Option(..., "--review-action", help="One of: note, defer, resolve"),
    review_note: str = typer.Option(..., "--review-note", help="Append-only review note"),
    evidence_refs: Annotated[
        list[str] | None,
        typer.Option(
            "--evidence-ref",
            help="Optional evidence reference; repeat flag for multiple values",
        ),
    ] = None,
    journal_path: str = typer.Option(
        "artifacts/operator_review_journal.jsonl",
        "--journal-path",
        help="Append-only review journal JSONL path",
    ),
) -> None:
    """Append a review journal entry without mutating core operator state."""
    from app.research.operational_readiness import (
        append_review_journal_entry_jsonl,
        create_review_journal_entry,
    )

    entry = create_review_journal_entry(
        source_ref=source_ref,
        operator_id=operator_id,
        review_action=review_action,
        review_note=review_note,
        evidence_refs=list(evidence_refs or []),
    )
    out_path = Path(journal_path)
    append_review_journal_entry_jsonl(entry, out_path)

    console.print(f"[green]Review journal appended to {out_path.resolve()}[/green]")
    console.print(f"review_id={entry.review_id}")
    console.print(f"journal_status={entry.journal_status}")
    console.print("core_state_unchanged=True")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_app.command("review-journal-summary")
def research_review_journal_summary(
    journal_path: str = typer.Option(
        "artifacts/operator_review_journal.jsonl",
        "--journal-path",
        help="Append-only review journal JSONL path",
    ),
) -> None:
    """Print the append-only review journal summary."""
    summary = _load_review_journal_summary(journal_path=journal_path)
    console.print("[bold]Operator Review Journal Summary[/bold]")
    console.print(f"journal_status={summary.journal_status}")
    console.print(f"total_count={summary.total_count}")
    console.print(f"open_count={summary.open_count}")
    console.print(f"resolved_count={summary.resolved_count}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_app.command("resolution-summary")
def research_resolution_summary(
    journal_path: str = typer.Option(
        "artifacts/operator_review_journal.jsonl",
        "--journal-path",
        help="Append-only review journal JSONL path",
    ),
) -> None:
    """Print latest per-source resolution state from the review journal."""
    summary = _load_review_resolution_summary(journal_path=journal_path)
    console.print("[bold]Operator Resolution Summary[/bold]")
    console.print(f"journal_status={summary.journal_status}")
    console.print(f"open_count={summary.open_count}")
    console.print(f"resolved_count={summary.resolved_count}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")
    for source_ref in summary.open_source_refs:
        console.print(f"open={source_ref}")
    for source_ref in summary.resolved_source_refs:
        console.print(f"resolved={source_ref}")


@research_app.command("alert-audit-summary")
def research_alert_audit_summary(
    audit_dir: str = typer.Option(
        "artifacts",
        "--audit-dir",
        help="Directory containing alert_audit.jsonl",
    ),
) -> None:
    """Print operator-facing alert audit summary (read-only)."""
    import asyncio

    from app.agents.mcp_server import get_alert_audit_summary

    result = asyncio.run(get_alert_audit_summary(audit_dir=audit_dir))
    console.print("[bold]Operator Alert Audit Summary[/bold]")
    console.print(f"total_count={result.get('total_count', 0)}")
    console.print(f"digest_count={result.get('digest_count', 0)}")
    console.print(f"latest_dispatched_at={result.get('latest_dispatched_at', 'none')}")
    by_channel = result.get("by_channel", {})
    if isinstance(by_channel, dict):
        for channel, count in by_channel.items():
            console.print(f"channel_{channel}={count}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_app.command("market-data-quote")
def research_market_data_quote(
    symbol: str = typer.Argument(..., help="Market symbol, e.g. BTC/USDT"),
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only provider: coingecko or mock",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Data age threshold for stale flagging",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Provider request timeout in seconds",
    ),
) -> None:
    """Fetch one read-only market quote snapshot from the canonical adapter path."""
    import asyncio

    from app.market_data.service import get_market_data_snapshot

    snapshot = asyncio.run(
        get_market_data_snapshot(
            symbol=symbol,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    )

    console.print("[bold]Market Data Quote[/bold]")
    console.print(f"symbol={snapshot.symbol}")
    console.print(f"provider={snapshot.provider}")
    console.print(f"retrieved_at={snapshot.retrieved_at_utc}")
    console.print(f"source_timestamp={snapshot.source_timestamp_utc}")
    console.print(f"price={snapshot.price}")
    console.print(f"is_stale={snapshot.is_stale}")
    console.print(f"freshness_seconds={snapshot.freshness_seconds}")
    console.print(f"available={snapshot.available}")
    console.print(f"error={snapshot.error}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")

    if not snapshot.available:
        raise typer.Exit(1)


@research_app.command("market-data-snapshot")
def research_market_data_snapshot(
    symbol: str = typer.Argument(..., help="Market symbol, e.g. BTC/USDT"),
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only provider: coingecko or mock",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Data age threshold for stale flagging",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Provider request timeout in seconds",
    ),
) -> None:
    """Print the full read-only market data snapshot payload as JSON."""
    import asyncio
    import json as _json

    from app.market_data.service import get_market_data_snapshot

    snapshot = asyncio.run(
        get_market_data_snapshot(
            symbol=symbol,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    )
    console.print(_json.dumps(snapshot.to_json_dict(), indent=2))

    if not snapshot.available:
        raise typer.Exit(1)


@research_app.command("paper-portfolio-snapshot")
def research_paper_portfolio_snapshot(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Append-only paper execution audit JSONL path",
    ),
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only market data provider for mark-to-market",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Market data age threshold for stale flagging",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Provider request timeout in seconds",
    ),
) -> None:
    """Print canonical read-only paper portfolio snapshot as JSON."""
    import asyncio
    import json as _json

    from app.execution.portfolio_read import build_portfolio_snapshot

    snapshot = asyncio.run(
        build_portfolio_snapshot(
            audit_path=audit_path,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    )
    console.print(_json.dumps(snapshot.to_json_dict(), indent=2))

    if not snapshot.available:
        raise typer.Exit(1)


@research_app.command("paper-positions-summary")
def research_paper_positions_summary(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Append-only paper execution audit JSONL path",
    ),
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only market data provider for mark-to-market",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Market data age threshold for stale flagging",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Provider request timeout in seconds",
    ),
) -> None:
    """Print canonical read-only paper positions summary."""
    import asyncio

    from app.execution.portfolio_read import (
        build_portfolio_snapshot,
        build_positions_summary,
    )

    snapshot = asyncio.run(
        build_portfolio_snapshot(
            audit_path=audit_path,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    )
    payload = build_positions_summary(snapshot)

    console.print("[bold]Paper Positions Summary[/bold]")
    console.print(f"position_count={payload['position_count']}")
    console.print(f"mark_to_market_status={payload['mark_to_market_status']}")
    console.print(f"available={payload['available']}")
    console.print(f"error={payload['error']}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")

    raw_positions = payload.get("positions", [])
    positions = raw_positions if isinstance(raw_positions, list) else []
    for position in positions:
        if not isinstance(position, dict):
            continue
        console.print(
            " | ".join(
                [
                    f"symbol={position.get('symbol')}",
                    f"qty={position.get('quantity')}",
                    f"avg={position.get('avg_entry_price')}",
                    f"price={position.get('market_price')}",
                    f"stale={position.get('market_data_is_stale')}",
                    f"available={position.get('market_data_available')}",
                ]
            )
        )

    if not snapshot.available:
        raise typer.Exit(1)


@research_app.command("paper-exposure-summary")
def research_paper_exposure_summary(
    audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--audit-path",
        help="Append-only paper execution audit JSONL path",
    ),
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only market data provider for mark-to-market",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Market data age threshold for stale flagging",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Provider request timeout in seconds",
    ),
) -> None:
    """Print canonical read-only paper exposure summary."""
    import asyncio

    from app.execution.portfolio_read import (
        build_exposure_summary,
        build_portfolio_snapshot,
    )

    snapshot = asyncio.run(
        build_portfolio_snapshot(
            audit_path=audit_path,
            provider=provider,
            freshness_threshold_seconds=freshness_threshold_seconds,
            timeout_seconds=timeout_seconds,
        )
    )
    payload = build_exposure_summary(snapshot)

    console.print("[bold]Paper Exposure Summary[/bold]")
    console.print(f"mark_to_market_status={payload['mark_to_market_status']}")
    console.print(f"gross_exposure_usd={payload['gross_exposure_usd']}")
    console.print(f"net_exposure_usd={payload['net_exposure_usd']}")
    console.print(f"priced_position_count={payload['priced_position_count']}")
    console.print(f"stale_position_count={payload['stale_position_count']}")
    console.print(f"unavailable_price_count={payload['unavailable_price_count']}")
    console.print(f"largest_position_symbol={payload['largest_position_symbol']}")
    console.print(f"largest_position_weight_pct={payload['largest_position_weight_pct']}")
    console.print(f"available={payload['available']}")
    console.print(f"error={payload['error']}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")

    if not snapshot.available:
        raise typer.Exit(1)


@research_app.command("backtest-run")
def research_backtest_run(
    signals_path: str = typer.Option(
        "artifacts/signal_candidates.jsonl",
        "--signals-path",
        help="JSONL file with signal candidates (one JSON dict per line)",
    ),
    out: str = typer.Option(
        "artifacts/backtest_result.json",
        "--out",
        help="Output path for backtest result JSON",
    ),
    initial_equity: float = typer.Option(10_000.0, "--initial-equity"),
    stop_loss_pct: float = typer.Option(2.0, "--stop-loss-pct"),
    take_profit_mult: float = typer.Option(2.0, "--take-profit-mult"),
    min_confidence: float = typer.Option(0.7, "--min-confidence"),
    max_positions: int = typer.Option(5, "--max-positions"),
    max_risk_pct: float = typer.Option(2.0, "--max-risk-pct"),
    long_only: bool = typer.Option(True, "--long-only/--no-long-only"),
    audit_path: str = typer.Option(
        "artifacts/backtest_audit.jsonl", "--audit-path"
    ),
) -> None:
    """Run a paper backtest from a signal candidate JSONL file."""
    import asyncio
    import json as _json
    from pathlib import Path as _Path

    from app.execution.backtest_engine import BacktestConfig, BacktestEngine
    from app.market_data.mock_adapter import MockMarketDataAdapter
    from app.research.signals import SignalCandidate

    # Load signals
    sp = _Path(signals_path)
    if not sp.exists():
        console.print(f"[red]Signals file not found: {signals_path}[/red]")
        raise typer.Exit(1)

    signals: list[SignalCandidate] = []
    for raw in sp.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            signals.append(SignalCandidate.model_validate(_json.loads(raw), strict=False))
        except Exception:
            pass  # skip malformed rows

    if not signals:
        console.print("[yellow]No valid signals found. Exiting.[/yellow]")
        raise typer.Exit(0)

    # Fetch prices for unique assets via MockAdapter (A-012)
    adapter = MockMarketDataAdapter()
    unique_assets = {s.target_asset for s in signals}
    prices: dict[str, float] = {}
    for asset in unique_assets:
        p = asyncio.run(adapter.get_price(asset))
        if p is None:
            p = asyncio.run(adapter.get_price(f"{asset}/USDT"))
        if p:
            prices[asset] = p
            prices[f"{asset}/USDT"] = p

    cfg = BacktestConfig(
        initial_equity=initial_equity,
        stop_loss_pct=stop_loss_pct,
        take_profit_multiplier=take_profit_mult,
        min_signal_confidence=min_confidence,
        max_open_positions=max_positions,
        max_risk_per_trade_pct=max_risk_pct,
        long_only=long_only,
        audit_log_path=audit_path,
    )
    engine = BacktestEngine(cfg)
    result = engine.run(signals, prices)

    out_path = _Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        _json.dumps(result.to_json_dict(), indent=2), encoding="utf-8"
    )

    console.print("[bold]Backtest Result[/bold]")
    console.print(f"signals_received={result.signals_received}")
    console.print(f"signals_executed={result.signals_executed}")
    console.print(f"signals_skipped={result.signals_skipped}")
    console.print(f"trade_count={result.trade_count}")
    console.print(f"final_equity={result.final_equity:.4f}")
    console.print(f"total_return_pct={result.total_return_pct:.4f}")
    console.print(f"max_drawdown_pct={result.max_drawdown_pct:.4f}")
    console.print(f"realized_pnl_usd={result.realized_pnl_usd:.4f}")
    console.print(f"kill_switch_triggered={result.kill_switch_triggered}")
    console.print(f"result_written={out}")


@research_app.command("decision-journal-append")
def research_decision_journal_append(
    symbol: str = typer.Argument(..., help="Trading symbol (e.g. BTC/USDT)"),
    thesis: str = typer.Option(..., "--thesis", help="Trading thesis (min 10 chars)"),
    market: str = typer.Option("crypto", "--market"),
    venue: str = typer.Option("paper", "--venue"),
    mode: str = typer.Option(
        "research",
        "--mode",
        help="One of: research, backtest, paper, shadow, live",
    ),
    confidence: float = typer.Option(0.5, "--confidence", help="Confidence 0.0-1.0"),
    supporting: Annotated[
        list[str] | None,
        typer.Option("--supporting", help="Supporting factor; repeat for multiple"),
    ] = None,
    contradictory: Annotated[
        list[str] | None,
        typer.Option("--contradictory", help="Contradictory factor; repeat for multiple"),
    ] = None,
    entry_logic: str = typer.Option("manual_entry", "--entry-logic"),
    exit_logic: str = typer.Option("manual_exit", "--exit-logic"),
    stop_loss: float = typer.Option(0.0, "--stop-loss"),
    invalidation: str = typer.Option("thesis_invalidated", "--invalidation"),
    model_version: str = typer.Option("manual", "--model-version"),
    prompt_version: str = typer.Option("v0", "--prompt-version"),
    data_source: Annotated[
        list[str] | None,
        typer.Option("--data-source", help="Data source; repeat for multiple"),
    ] = None,
    journal_path: str = typer.Option(
        "artifacts/decision_journal.jsonl",
        "--journal-path",
        help="Append-only decision journal JSONL path",
    ),
) -> None:
    """Append a validated decision instance to the decision journal."""
    from app.decisions.journal import (
        RiskAssessment,
        append_decision_jsonl,
        create_decision_instance,
    )

    try:
        risk = RiskAssessment(
            risk_level="unassessed",
            max_position_pct=0.0,
            drawdown_remaining_pct=100.0,
        )
        decision = create_decision_instance(
            symbol=symbol,
            market=market,
            venue=venue,
            mode=mode,
            thesis=thesis,
            supporting_factors=list(supporting or ["manual_observation"]),
            contradictory_factors=list(contradictory or []),
            confidence_score=confidence,
            market_regime="unknown",
            volatility_state="unknown",
            liquidity_state="unknown",
            risk_assessment=risk,
            entry_logic=entry_logic,
            exit_logic=exit_logic,
            stop_loss=stop_loss,
            invalidation_condition=invalidation,
            position_size_rationale="manual sizing",
            max_loss_estimate=0.0,
            data_sources_used=list(data_source or ["operator_input"]),
            model_version=model_version,
            prompt_version=prompt_version,
        )
        out_path = Path(journal_path)
        append_decision_jsonl(decision, out_path)
    except ValueError as exc:
        console.print(f"[red]Decision journal append failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]Decision appended to {out_path.resolve()}[/green]")
    console.print(f"decision_id={decision.decision_id}")
    console.print(f"mode={decision.mode.value}")
    console.print(f"approval_state={decision.approval_state.value}")
    console.print(f"execution_state={decision.execution_state.value}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_app.command("decision-journal-summary")
def research_decision_journal_summary(
    journal_path: str = typer.Option(
        "artifacts/decision_journal.jsonl",
        "--journal-path",
        help="Append-only decision journal JSONL path",
    ),
) -> None:
    """Print a read-only summary of the decision journal."""
    from app.decisions.journal import (
        build_decision_journal_summary,
        load_decision_journal,
    )

    path = Path(journal_path)
    try:
        entries = load_decision_journal(path)
        summary = build_decision_journal_summary(entries, journal_path=path)
    except ValueError as exc:
        console.print(f"[red]Decision journal summary failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print("[bold]Decision Journal Summary[/bold]")
    console.print(f"total_count={summary.total_count}")
    console.print(f"symbols={summary.symbols}")
    console.print(f"by_mode={summary.by_mode}")
    console.print(f"by_approval={summary.by_approval}")
    console.print(f"by_execution={summary.by_execution}")
    if summary.avg_confidence is not None:
        console.print(f"avg_confidence={summary.avg_confidence}")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_app.command("trading-loop-status")
def research_trading_loop_status(
    audit_path: str = typer.Option(
        "artifacts/trading_loop_audit.jsonl",
        "--audit-path",
        help="Trading loop JSONL audit path",
    ),
    mode: str = typer.Option(
        "paper",
        "--mode",
        help="Execution mode hint for run-once guard evaluation",
    ),
) -> None:
    """Print canonical read-only trading-loop status and run-once guard state."""
    from app.orchestrator.trading_loop import build_loop_status_summary

    try:
        summary = build_loop_status_summary(audit_path=audit_path, mode=mode)
    except ValueError as exc:
        console.print(f"[red]Trading loop status failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    payload = summary.to_json_dict()
    console.print("[bold]Trading Loop Status[/bold]")
    console.print(f"mode={payload['mode']}")
    console.print(f"run_once_allowed={payload['run_once_allowed']}")
    console.print(f"run_once_block_reason={payload['run_once_block_reason']}")
    console.print(f"total_cycles={payload['total_cycles']}")
    console.print(f"last_cycle_id={payload['last_cycle_id']}")
    console.print(f"last_cycle_status={payload['last_cycle_status']}")
    console.print(f"last_cycle_symbol={payload['last_cycle_symbol']}")
    console.print(f"last_cycle_completed_at={payload['last_cycle_completed_at']}")
    console.print(f"audit_path={payload['audit_path']}")
    console.print("auto_loop_enabled=False")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_app.command("loop-cycle-summary")
@research_app.command("trading-loop-recent-cycles")
def research_trading_loop_recent_cycles(
    audit_path: str = typer.Option(
        "artifacts/trading_loop_audit.jsonl",
        "--audit-path",
        help="Trading loop JSONL audit path",
    ),
    last_n: int = typer.Option(20, "--last-n", help="Show last N cycle records"),
) -> None:
    """Print canonical read-only summary of recent trading loop cycles."""
    from app.orchestrator.trading_loop import build_recent_cycles_summary

    summary = build_recent_cycles_summary(audit_path=audit_path, last_n=last_n)
    payload = summary.to_json_dict()

    console.print(
        f"[bold]Trading Loop Recent Cycles[/bold] ({payload['total_cycles']} total)"
    )
    console.print(f"status_counts={payload['status_counts']}")
    console.print(
        "showing last "
        f"{len(payload['recent_cycles'])} of {payload['total_cycles']} cycles:"  # type: ignore[arg-type]
    )

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("cycle_id", width=16)
    table.add_column("status", width=16)
    table.add_column("symbol", width=12)
    table.add_column("sig", width=4)
    table.add_column("risk", width=4)
    table.add_column("fill", width=4)

    raw_recent_cycles = payload.get("recent_cycles", [])
    recent_cycles = raw_recent_cycles if isinstance(raw_recent_cycles, list) else []
    for rec in recent_cycles:
        if not isinstance(rec, dict):
            continue
        table.add_row(
            str(rec.get("cycle_id", "—"))[:16],
            str(rec.get("status", "—")),
            str(rec.get("symbol", "—")),
            "Y" if rec.get("signal_generated") else "N",
            "Y" if rec.get("risk_approved") else "N",
            "Y" if rec.get("fill_simulated") else "N",
        )

    console.print(table)
    console.print("audit_path=" + str(payload.get("audit_path")))
    console.print("auto_loop_enabled=False")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")


@research_app.command("trading-loop-run-once")
def research_trading_loop_run_once(
    symbol: str = typer.Option("BTC/USDT", "--symbol", help="Trading symbol"),
    mode: str = typer.Option(
        "paper",
        "--mode",
        help="Allowed run modes: paper or shadow (live fails closed)",
    ),
    provider: str = typer.Option(
        "coingecko",
        "--provider",
        help="Read-only market-data provider: coingecko (default, real data) or mock (dev/test)",
    ),
    analysis_profile: str = typer.Option(
        "conservative",
        "--analysis-profile",
        help="conservative, bullish, or bearish control profile",
    ),
    loop_audit_path: str = typer.Option(
        "artifacts/trading_loop_audit.jsonl",
        "--loop-audit-path",
        help="Append-only loop cycle audit path",
    ),
    execution_audit_path: str = typer.Option(
        "artifacts/paper_execution_audit.jsonl",
        "--execution-audit-path",
        help="Append-only paper execution audit path",
    ),
    freshness_threshold_seconds: float = typer.Option(
        120.0,
        "--freshness-threshold-seconds",
        help="Market data stale threshold",
    ),
    timeout_seconds: int = typer.Option(
        10,
        "--timeout-seconds",
        help="Market data request timeout",
    ),
) -> None:
    """Run one guarded paper/shadow cycle and append cycle audit output."""
    import asyncio

    from app.orchestrator.trading_loop import run_trading_loop_once

    try:
        cycle = asyncio.run(
            run_trading_loop_once(
                symbol=symbol,
                mode=mode,
                provider=provider,
                analysis_profile=analysis_profile,
                loop_audit_path=loop_audit_path,
                execution_audit_path=execution_audit_path,
                freshness_threshold_seconds=freshness_threshold_seconds,
                timeout_seconds=timeout_seconds,
            )
        )
    except ValueError as exc:
        console.print(f"[red]Trading loop run-once blocked:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print("[bold]Trading Loop Run Once[/bold]")
    console.print(f"cycle_id={cycle.cycle_id}")
    console.print(f"status={cycle.status.value}")
    console.print(f"symbol={cycle.symbol}")
    console.print(f"mode={mode}")
    console.print(f"provider={provider}")
    console.print(f"analysis_profile={analysis_profile}")
    console.print(f"market_data_fetched={cycle.market_data_fetched}")
    console.print(f"signal_generated={cycle.signal_generated}")
    console.print(f"risk_approved={cycle.risk_approved}")
    console.print(f"order_created={cycle.order_created}")
    console.print(f"fill_simulated={cycle.fill_simulated}")
    console.print(f"notes={list(cycle.notes)}")
    console.print("auto_loop_enabled=False")
    console.print("execution_enabled=False")
    console.print("write_back_allowed=False")
