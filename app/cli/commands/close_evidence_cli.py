"""Close-evidence shadow CLI surface.

Registered on ``trading_app`` as a side module so the trading God-file does not
grow.  The command is read-only and intentionally wraps the STAB-09 shadow
reporter without touching close classification or paper books.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from app.cli.commands.trading import console, trading_app


@trading_app.command("close-evidence-shadow")
def trading_close_evidence_shadow(
    audit: Annotated[
        Path,
        typer.Option(
            "--audit",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Paper execution audit JSONL input",
        ),
    ],
    shadow: Annotated[
        bool,
        typer.Option(
            "--shadow",
            help="Mandatory read-only guard; no mutating mode exists",
        ),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            file_okay=True,
            dir_okay=False,
            writable=True,
            help="Optional canonical JSON report path; default stdout",
        ),
    ] = None,
) -> None:
    """Measure close evidence against Binance+Bybit without classifying closes.

    Read-only STAB-09 operator surface.  The command fetches public 1m candles
    only through the close-evidence shadow module and emits a decomposed report
    (N, verdict/reason counts, venue divergence).  It never publishes evidence,
    never changes books, and never wires the result into close classification.
    """
    from app.execution.close_evidence import canonical_bytes
    from app.execution.close_evidence_shadow import build_shadow_report
    from app.execution.venues.candle_fetchers import BinanceCandleFetcher, BybitCandleFetcher

    if not shadow:
        console.print("[red]refused:[/red] --shadow is mandatory; no mutating mode exists")
        raise typer.Exit(2)

    rows: list[dict[str, object]] = []
    for line_number, raw in enumerate(audit.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            console.print(f"[red]invalid JSON at line {line_number}:[/red] {exc.msg}")
            raise typer.Exit(2) from exc
        if not isinstance(payload, dict):
            console.print(f"[red]invalid JSON object at line {line_number}[/red]")
            raise typer.Exit(2)
        rows.append(payload)

    report = build_shadow_report(
        rows,
        fetchers={"binance": BinanceCandleFetcher(), "bybit": BybitCandleFetcher()},
        now_utc=datetime.now(UTC),
    )
    encoded = canonical_bytes(report) + b"\n"
    if output is None:
        print(encoded.decode("utf-8"), end="")
    else:
        output.write_bytes(encoded)
