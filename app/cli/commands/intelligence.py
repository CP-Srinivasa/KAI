"""CLI surface of the Local Intelligence Layer (ADR 0015 Phase 2).

Read-only shadow commands. Every run appends to artifacts/intelligence_audit.jsonl;
output is a clearly marked untrusted block. Disabled by default (KAI_LLM_*).
Exit 0 = ran (auch disabled ist ein gültiges, auditiertes Ergebnis), 1 = refused path.
"""

from __future__ import annotations

from collections.abc import Callable

import typer

from app.intelligence.context import ContextRefusedError
from app.intelligence.core import LLMResult
from app.intelligence.use_cases import (
    anomaly_explain,
    daily_review_summary,
    doc_qa,
    render_untrusted_block,
)

intelligence_app = typer.Typer(help="Shadow-only LLM analysis (untrusted, ADR 0015)")

_DOC_OPTION = typer.Option([], "--doc", help="Allowlisted Kontext-Dokumente")


def _emit(fn: Callable[..., LLMResult], *args: object) -> None:
    try:
        result = fn(*args)
    except ContextRefusedError as exc:
        typer.echo(f"refused: {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(render_untrusted_block(result))


@intelligence_app.command("daily-summary")
def cmd_daily_summary(
    review_path: str = typer.Argument(..., help="z.B. artifacts/daily_strategy/2026-07-10.md"),
) -> None:
    """Summarize a daily strategy review (shadow, read-only)."""
    _emit(daily_review_summary, review_path)


@intelligence_app.command("anomaly-explain")
def cmd_anomaly_explain(
    description: str = typer.Argument(..., help="Kurzbeschreibung der Anomalie"),
    doc: list[str] = _DOC_OPTION,
) -> None:
    """Explain a timer/source anomaly from allowlisted docs (shadow, read-only)."""
    _emit(anomaly_explain, doc, description)


@intelligence_app.command("doc-qa")
def cmd_doc_qa(
    question: str = typer.Argument(..., help="Frage an ADRs/Runbooks"),
    doc: list[str] = _DOC_OPTION,
) -> None:
    """Answer a question strictly from ADR/runbook documents (shadow, read-only)."""
    _emit(doc_qa, doc, question)
