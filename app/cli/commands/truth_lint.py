"""``trading truth-lint`` — Invariant-Registry über die eigenen Ledger fahren.

Registriert sich wie truth_compliance auf der bestehenden ``trading``-Sub-App
(Import am Ende von ``trading.py``), damit das God-File unangetastet bleibt.
Read-/append-only: erkennt, kennzeichnet, quarantänisiert — korrigiert NIE.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from app.cli.commands.trading import trading_app


@trading_app.command("truth-lint")
def truth_lint(
    artifacts: str = typer.Option(
        "artifacts", "--artifacts", help="Artifacts-Verzeichnis (Default: ./artifacts)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Volles Ergebnis als JSON ausgeben"),
    gate: bool = typer.Option(
        False,
        "--gate",
        help="Gate-Modus: Exit 1 bei ERROR, Exit 2 bei CRITICAL (Evidence-Claim-Block; "
        "verfügbar, noch nicht systemweit verdrahtet). Ohne --gate immer Exit 0.",
    ),
    write: bool = typer.Option(
        True,
        "--write/--no-write",
        help="Ergebnis nach artifacts/truth_lint_report.jsonl (+ Quarantäne-Marker) anhängen",
    ),
) -> None:
    """Alle aktiven Truth-Invarianten prüfen (Severity: INFO→Digest · WARNING→degraded ·
    ERROR→Quarantäne-Marker · CRITICAL→Gate-Block)."""
    from app.truth.lint import Severity, run_lint, write_lint_report

    result = run_lint(Path(artifacts))
    markers = 0
    if write:
        markers = write_lint_report(result)

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        n = len(result["violations"])
        status = result["max_severity"] or "OK"
        print(
            f"truth-lint: {status} — {n} Verletzung(en) über "
            f"{result['registry_active']} aktive / {result['registry_total']} registrierte "
            f"Invarianten ({result['registry_planned']} planned)"
        )
        for v in result["violations"]:
            print(f"  [{v['severity']}] {v['invariant_id']} {v['dataset']}: {v['message']}")
        if markers:
            print(f"  → {markers} Quarantäne-Marker geschrieben (truth_quarantine.jsonl)")

    if gate and result["max_severity"]:
        sev = Severity[result["max_severity"]]
        if sev >= Severity.CRITICAL:
            raise typer.Exit(2)
        if sev >= Severity.ERROR:
            raise typer.Exit(1)


__all__ = ["truth_lint"]
