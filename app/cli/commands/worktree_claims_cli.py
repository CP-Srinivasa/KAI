"""Read-only STAB-12 worktree/claim hygiene CLI surface."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from app.cli.commands.audit import audit_app
from app.observability.worktree_claims_report import collect_report, render_json

DEFAULT_REPO_PATH = Path(".")
DEFAULT_CLAIMS_PATH = Path.home() / "KAI-mirror" / "ACTIVE_CLAIMS.md"
DEFAULT_BASE_REF = "origin/claude/p7/reentry-ia-codex-cycle"


@audit_app.command("worktree-claims-report")
def audit_worktree_claims_report(
    repo: Annotated[
        Path,
        typer.Option(
            "--repo",
            help="Git repository whose worktree list should be inspected.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            resolve_path=True,
        ),
    ] = DEFAULT_REPO_PATH,
    claims: Annotated[
        Path,
        typer.Option(
            "--claims",
            help="ACTIVE_CLAIMS.md ledger to inspect.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
        ),
    ] = DEFAULT_CLAIMS_PATH,
    base_ref: Annotated[
        str,
        typer.Option(
            "--base-ref",
            help="Reference used for git-ancestry fallback when no PR metadata exists.",
        ),
    ] = DEFAULT_BASE_REF,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print the full machine-readable hygiene report."),
    ] = False,
) -> None:
    """Report worktree/claim hygiene only; never delete, prune, or rewrite state."""
    report = collect_report(
        repo,
        claims,
        base_ref=base_ref,
        now=datetime.now(UTC),
    )
    if as_json:
        print(render_json(report))
        return

    worktree_counts = report["worktrees"]["counts"]
    claim_counts = report["claims"]["counts"]
    print(
        f"worktrees={worktree_counts['total']} "
        f"merged={worktree_counts['merged']} "
        f"open={worktree_counts['open']} "
        f"unmerged={worktree_counts['unmerged']} "
        f"old>14d={worktree_counts['older_than_14d']} "
        f"missing_paths={worktree_counts['missing_paths']} "
        f"claims_active_valid={claim_counts['active_valid']} "
        f"claims_active_expired={claim_counts['active_expired']} "
        f"claims_missing_expiry={claim_counts['active_missing_expiry']}"
    )
