"""``trading bundle-create`` / ``bundle-verify`` — Verdict-Bundle v0.1.

Registriert sich wie truth_compliance/truth_lint auf der trading-App
(Import am Ende von ``trading.py``). ``bundle-verify`` ist nur ein dünner
Komfort-Wrapper um den EIGENSTÄNDIGEN Verifier ``tools/verifier/kai_verify.py``
— Dritte nutzen den direkt und müssen diesem CLI nie vertrauen.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import typer

from app.cli.commands.trading import trading_app


@trading_app.command("bundle-create")
def bundle_create(
    prereg_file: str = typer.Option(
        ...,
        "--prereg-file",
        help="Prä-Reg-JSON (braucht prereg_id + bundle_eval; Pass-Latte VOR den Daten)",
    ),
    slice_file: list[str] = typer.Option(  # noqa: B008 — typer-Idiom wie im Repo üblich
        ..., "--slice", help="role=pfad (mehrfach); Dateien wandern nach data_slice/"
    ),
    lock_file: str = typer.Option(
        ..., "--lock", help="Dependency-Lock-Datei für evidence/ (Umgebungs-Anker)"
    ),
    out: str = typer.Option(..., "--out", help="Ziel-Verzeichnis (darf nicht existieren)"),
) -> None:
    """Bundle exakt nach versiegeltem Schema v0.1 erzeugen (Seal 836b1c7e28eed49a)."""
    from app.research.verdict_bundle import build_bundle

    prereg = json.loads(Path(prereg_file).read_text(encoding="utf-8"))
    pairs: list[tuple[str, Path]] = []
    for spec in slice_file:
        role, _, raw = spec.partition("=")
        if not raw:
            typer.echo(f"bundle-create: --slice erwartet role=pfad, bekam {spec!r}")
            raise typer.Exit(2)
        pairs.append((role, Path(raw)))
    code_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30, check=True
    ).stdout.strip()
    bundle = build_bundle(
        Path(out),
        preregistration=prereg,
        slice_files=pairs,
        code_sha=code_sha,
        dependency_lock_source=Path(lock_file),
    )
    typer.echo(f"bundle-create: {bundle} (code_sha {code_sha[:12]})")
    typer.echo("verify: python tools/verifier/kai_verify.py " + str(bundle) + " --code-dir .")


@trading_app.command("bundle-verify")
def bundle_verify(
    bundle: str = typer.Argument(..., help="Bundle-Verzeichnis"),
    code_dir: str = typer.Option(
        ".", "--code-dir", help="Repo-Checkout bei manifest.code_sha (für Reproduktion)"
    ),
) -> None:
    """Dünner Wrapper: ruft den eigenständigen Offline-Verifier auf (Exit 0/1/2/3)."""
    verifier = Path(__file__).resolve().parents[3] / "tools" / "verifier" / "kai_verify.py"
    proc = subprocess.run(
        [sys.executable, str(verifier), bundle, "--code-dir", code_dir],
        text=True,
        capture_output=True,
    )
    typer.echo(proc.stdout.rstrip())
    if proc.stderr.strip():
        typer.echo(proc.stderr.rstrip())
    raise typer.Exit(proc.returncode)


__all__ = ["bundle_create", "bundle_verify"]
