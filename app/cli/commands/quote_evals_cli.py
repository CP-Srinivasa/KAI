"""Operator-CLI für die Quoten-Evaluatoren H1/H2 (Prä-Regs vom 2026-07-29).

Registriert auf ``trading_app`` (Import am Ende von ``trading.py``, gleiches
Muster wie ``research_verdicts``), damit der God-File nicht wächst. Beide
Kommandos sind read-only und mechanisch an den Ledger gebunden: Stichtag
(``created_at_utc``) und Horizont kommen aus dem REGISTRIERTEN Eintrag, nie
aus Operator-Erinnerung. Verdikt-Kette danach unverändert:
``… --json > f.json`` → ``trading prereg-check --prereg-id X --from-json f.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from app.cli.commands.trading import console, trading_app
from app.research.prereg_ledger import DEFAULT_PREREG_LEDGER_PATH
from app.research.quote_evals import (
    EXEC_TRANSLATION_PREREG_ID,
    TECH_PRECISION_PREREG_ID,
)

_DEFAULT_OUTCOMES = "artifacts/alert_outcomes.jsonl"
_DEFAULT_EXEC_AUDIT = "artifacts/paper_execution_audit.jsonl"


def _load_entry(ledger_path: str, prereg_id: str) -> Any:
    from app.research.prereg_ledger import PreRegistrationLedger

    entries = [
        e for e in PreRegistrationLedger(Path(ledger_path)).entries() if e.prereg_id == prereg_id
    ]
    if not entries:
        console.print(f"[red]quote-eval:[/red] unknown prereg_id {prereg_id!r}")
        raise typer.Exit(2)
    return entries[-1]


def _emit(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2))
        return
    pop = result["population"]
    horizon = str(result["horizon_s"])
    row = result["overall"]["horizons"][horizon]
    console.print(f"[bold]{result['hypothesis']}[/bold] (reg {result['registered_at_utc']})")
    for k, v in pop.items():
        console.print(f"  {k}: {v}")
    console.print(
        f"  overall@{horizon}s: n={row['n']} mean_x={row['mean_x']} "
        f"positive_rate={row['positive_rate']} p_positive={row['p_positive']}"
    )


@trading_app.command("tech-precision-eval")
def trading_tech_precision_eval(
    prereg_id: str = typer.Option(TECH_PRECISION_PREREG_ID, "--prereg-id"),
    outcomes_path: str = typer.Option(_DEFAULT_OUTCOMES, "--outcomes-path"),
    exec_audit_path: str = typer.Option(_DEFAULT_EXEC_AUDIT, "--exec-audit-path"),
    ledger_path: str = typer.Option(str(DEFAULT_PREREG_LEDGER_PATH), "--ledger-path"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of text"),
) -> None:
    """H1-Evaluator: FORWARD-Precision eigener technischer Signale (±1-Kodierung).

    Read-only über Outcome- und Paper-Audit-JSONL; emittiert den
    ``overall``-Block für das versiegelte Gate ``fd6f5f7842f49244``
    (n≥200, p_positive≥0,95). Kein Verdikt — das fällt ``prereg-check``.
    """
    from app.research.quote_evals import evaluate_technical_paper_precision

    entry = _load_entry(ledger_path, prereg_id)
    horizon_s = int((entry.gate or {}).get("horizon_s", 604800))
    result = evaluate_technical_paper_precision(
        outcomes_path=Path(outcomes_path),
        exec_audit_path=Path(exec_audit_path),
        registered_at_utc=entry.created_at_utc,
        horizon_s=horizon_s,
    )
    result["prereg_id"] = prereg_id
    _emit(result, as_json=as_json)


@trading_app.command("exec-translation-eval")
def trading_exec_translation_eval(
    prereg_id: str = typer.Option(EXEC_TRANSLATION_PREREG_ID, "--prereg-id"),
    outcomes_path: str = typer.Option(_DEFAULT_OUTCOMES, "--outcomes-path"),
    exec_audit_path: str = typer.Option(_DEFAULT_EXEC_AUDIT, "--exec-audit-path"),
    ledger_path: str = typer.Option(str(DEFAULT_PREREG_LEDGER_PATH), "--ledger-path"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of text"),
) -> None:
    """H2-Evaluator: Übersetzung hit→Gewinn-Trade (±1 auf ``trade_pnl_usd``-Summe).

    Join direkt oder per sha256-Rekonstruktion (``tv:{event_id}`` →
    ``SIG-TVP-…``); Gate ``0c7ead764621dd17`` (n≥50, p_positive≥0,90,
    low prior — FAIL ist der erwartete, informative Ausgang).
    """
    from app.research.quote_evals import evaluate_execution_translation

    entry = _load_entry(ledger_path, prereg_id)
    horizon_s = int((entry.gate or {}).get("horizon_s", 86400))
    result = evaluate_execution_translation(
        outcomes_path=Path(outcomes_path),
        exec_audit_path=Path(exec_audit_path),
        registered_at_utc=entry.created_at_utc,
        horizon_s=horizon_s,
    )
    result["prereg_id"] = prereg_id
    _emit(result, as_json=as_json)
