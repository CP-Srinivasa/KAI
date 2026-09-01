"""Read-only CLI consumer for G5 input-contract rejects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from app.audit.input_contract_rejections import (
    DEFAULT_ANALYSIS_INPUT_REJECTIONS_PATH,
    DEFAULT_LN_INPUT_REJECTIONS_PATH,
    read_recent_input_rejections,
)
from app.cli.commands.audit import audit_app, console


@audit_app.command("input-rejections")
def audit_input_rejections(
    ln_path: Annotated[
        Path,
        typer.Option("--ln-path", help="Money-journal input rejection JSONL."),
    ] = DEFAULT_LN_INPUT_REJECTIONS_PATH,
    analysis_path: Annotated[
        Path,
        typer.Option("--analysis-path", help="Analysis-input rejection JSONL."),
    ] = DEFAULT_ANALYSIS_INPUT_REJECTIONS_PATH,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=1000, help="Maximum combined rows."),
    ] = 100,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable records instead of a table."),
    ] = False,
) -> None:
    """Print recent contract rejects for an operator decision; never mutate them."""
    rows = read_recent_input_rejections(
        ln_path=ln_path,
        analysis_path=analysis_path,
        limit=limit,
    )
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, sort_keys=True))
        return
    if not rows:
        console.print("[dim]No input-contract rejections found.[/dim]")
        return

    table = Table(title="Input-contract rejections", show_lines=False)
    table.add_column("ts")
    table.add_column("stream")
    table.add_column("contract")
    table.add_column("action / reason")
    for row in rows:
        record = row.get("record")
        safe_record = record if isinstance(record, dict) else {}
        reasons = safe_record.get("reasons")
        reason_text = (
            "; ".join(str(value) for value in reasons) if isinstance(reasons, list) else ""
        )
        if not reason_text:
            reason_text = str(safe_record.get("reason", ""))
        action = str(safe_record.get("action", ""))
        detail = f"{action}: {reason_text}" if action else reason_text
        table.add_row(
            str(safe_record.get("ts", "")),
            str(row.get("stream", "")),
            str(safe_record.get("contract", "")),
            detail,
        )
    console.print(table)


__all__ = ["audit_input_rejections"]
