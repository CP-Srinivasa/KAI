"""Operator CLI: mechanical prereg verdicts, maturity tracking, verdict anchoring.

Registers on the existing ``trading`` sub-app (imported at the bottom of
``trading.py``, same pattern as ``truth_compliance``) so the CLI god-file stays
untouched. Everything here is read/append-only research tooling — no order, no
capital movement, no gate is weakened.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from app.cli.commands.trading import console, trading_app
from app.research.prereg_ledger import DEFAULT_PREREG_LEDGER_PATH


@trading_app.command("prereg-check")
def trading_prereg_check(
    prereg_id: str = typer.Option(..., "--prereg-id", help="Registered claim to judge"),
    from_json: str = typer.Option(
        ..., "--from-json", help="Evaluator JSON output file (news-eval --json > f.json)"
    ),
    report: bool = typer.Option(
        False, "--report", help="Also write the attested verdict report (recommended)"
    ),
    out_dir: str = typer.Option(
        "artifacts/research/verdicts", "--out-dir", help="Report output directory"
    ),
    ledger_path: str = typer.Option(
        str(DEFAULT_PREREG_LEDGER_PATH), "--ledger-path", help="Pre-registration ledger JSONL"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of text"),
) -> None:
    """Mechanically judge an evaluator result against a REGISTERED gate (ADR 0012).

    Closes the human-transcription gap: the pass bar comes from the ledger (fixed
    before data, hashed into the ``prereg_id``), the numbers come from the
    evaluator's JSON, PASS/FAIL is computed — never read off a terminal. With
    ``--report`` the machine verdict is written as an attested report in one step.
    Exit 0 = judged (PASS or FAIL), 2 = not judgeable (unknown id / no gate /
    malformed input).
    """
    from datetime import UTC, datetime

    from app.research.prereg_gate import check_gate
    from app.research.prereg_ledger import PreRegistrationLedger
    from app.research.verdict_report import (
        build_verdict_report,
        resolve_code_version,
        write_verdict_report,
    )

    entries = [
        e for e in PreRegistrationLedger(Path(ledger_path)).entries() if e.prereg_id == prereg_id
    ]
    if not entries:
        console.print(f"[red]prereg-check:[/red] unknown prereg_id {prereg_id!r}")
        raise typer.Exit(2)
    entry = entries[-1]
    if not entry.gate:
        console.print(
            f"[red]prereg-check:[/red] claim {prereg_id!r} ({entry.name}) is a "
            "free-text-era registration without a machine-readable gate — judge it "
            "manually against its success_criteria and use `trading verdict-report`."
        )
        raise typer.Exit(2)

    src = Path(from_json)
    if not src.is_file():
        console.print(f"[red]prereg-check:[/red] no such file: {from_json}")
        raise typer.Exit(2)
    try:
        eval_result = json.loads(src.read_text(encoding="utf-8"))
    except ValueError as exc:
        console.print(f"[red]prereg-check:[/red] invalid JSON: {exc}")
        raise typer.Exit(2) from exc

    outcome = check_gate(entry.gate, eval_result)

    report_paths: dict[str, str] = {}
    if report:
        rep = build_verdict_report(
            eval_result,
            hypothesis=entry.name,
            prereg_id=entry.prereg_id,
            verdict=outcome["verdict"],
            params={"gate": entry.gate, "checks": outcome["checks"], "source": str(src)},
            code_version=resolve_code_version(),
            generated_at=datetime.now(UTC),
        )
        json_path, md_path = write_verdict_report(rep, Path(out_dir))
        report_paths = {
            "report_json": str(json_path),
            "report_md": str(md_path),
            "attestation_hash": rep["attestation"]["hash"],
        }

    if as_json:
        print(json.dumps({**outcome, "prereg_id": prereg_id, **report_paths}, indent=2))
        return

    color = "green" if outcome["passed"] else "red"
    console.print(f"[{color}]{outcome['verdict']}[/{color}]  ({entry.name} / {prereg_id})")
    for c in outcome["checks"]:
        mark = "✅" if c["ok"] else "❌"
        console.print(f"  {mark} {c['name']}: required={c['required']} actual={c['actual']}")
    for k, v in report_paths.items():
        console.print(f"{k}: {v}")


@trading_app.command("prereg-maturity")
def trading_prereg_maturity(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of text"),
) -> None:
    """Count out-of-sample cohorts of open pre-registrations; flag DUE ones.

    Read-only (document store only); the count is an upper-bound proxy — DUE means
    "run the eval now", never "the claim passed". Wired to a weekly systemd timer
    (``kai-prereg-maturity.timer``) so maturation is infrastructure, not memory.
    """
    import asyncio

    from app.core.settings import get_settings
    from app.research.prereg_maturity import compute_maturity
    from app.storage.db.session import build_session_factory

    async def _run() -> list[dict[str, Any]]:
        factory = build_session_factory(get_settings().db)
        async with factory() as session:
            return await compute_maturity(session)

    rows = asyncio.run(_run())
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    for r in rows:
        state = "[green]FÄLLIG — Eval jetzt fahren[/green]" if r["due"] else "reift"
        console.print(
            f"{r['name']}: n≈{r['n_proxy']}/{r['n_target']} (seit {r['since_utc']}) "
            f"{r['per_source']} → {state}"
        )


@trading_app.command("verdict-anchor")
def trading_verdict_anchor(
    json_path: str = typer.Option(
        ..., "--json-path", help="Attested verdict report (.json) to anchor"
    ),
) -> None:
    """Anchor a verdict report's attestation hash via the configured stamper (OTS).

    Verifies the report's attestation first (tamper check), then hands the hash to
    :func:`app.integrity.anchor.anchor_record_digest` (respects
    ``APP_INTEGRITY_ENABLED``; fail-soft). Exit 0 on anchored/recorded/disabled,
    1 on a verification failure or anchor error.
    """
    from app.core.integrity_settings import IntegritySettings
    from app.integrity.anchor import anchor_record_digest
    from app.truth.attestation import verify_attestation

    src = Path(json_path)
    if not src.is_file():
        console.print(f"[red]verdict-anchor:[/red] no such file: {json_path}")
        raise typer.Exit(1)
    try:
        report = json.loads(src.read_text(encoding="utf-8"))
        payload, attestation = report["payload"], report["attestation"]
    except (ValueError, KeyError) as exc:
        console.print(f"[red]verdict-anchor:[/red] not a verdict report: {exc}")
        raise typer.Exit(1) from exc
    if not verify_attestation(payload, attestation):
        console.print("[red]verdict-anchor:[/red] attestation does NOT verify — refusing")
        raise typer.Exit(1)

    result = anchor_record_digest(
        str(attestation["hash"]), settings=IntegritySettings(), prefix="newsverdict"
    )
    console.print(
        f"anchor state={result.state} digest={result.digest} "
        f"proof={getattr(result, 'proof_path', None)}"
    )
    if result.state == "error":
        raise typer.Exit(1)
