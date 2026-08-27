"""CLI contract for STAB-12 worktree/claim hygiene reporting."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pytest import MonkeyPatch
from typer.testing import CliRunner

from app.cli.commands import worktree_claims_cli
from app.cli.commands.audit import audit_app

runner = CliRunner()


def _fake_report() -> dict[str, Any]:
    return {
        "schema_version": "worktree_claims_hygiene/v1",
        "generated_at_utc": datetime(2026, 8, 27, tzinfo=UTC).isoformat(),
        "read_only": True,
        "worktrees": {
            "counts": {
                "total": 2,
                "older_than_14d": 1,
                "older_than_30d": 0,
                "missing_paths": 0,
                "merged": 1,
                "open": 1,
                "unmerged": 0,
                "closed_unmerged": 0,
                "unknown": 0,
            },
            "items": [],
        },
        "claims": {
            "counts": {
                "active_valid": 1,
                "active_expired": 1,
                "active_missing_expiry": 0,
                "closed": 2,
                "expired": 0,
                "other": 0,
            },
            "items": [],
        },
    }


def test_worktree_claims_report_json_is_registered_and_read_only(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    claims = tmp_path / "ACTIVE_CLAIMS.md"
    claims.write_text(
        "| claim_id | owner | worktree | scope | created_at | expires_at | status |\n"
    )
    observed: dict[str, object] = {}

    def fake_collect_report(
        repo_path: Path, claims_path: Path, *, base_ref: str, now: datetime
    ) -> dict[str, Any]:
        observed.update(
            {
                "repo": repo_path,
                "claims": claims_path,
                "base_ref": base_ref,
                "now_is_utc": now.tzinfo is UTC,
            }
        )
        return _fake_report()

    monkeypatch.setattr(worktree_claims_cli, "collect_report", fake_collect_report)

    result = runner.invoke(
        audit_app,
        [
            "worktree-claims-report",
            "--repo",
            str(repo),
            "--claims",
            str(claims),
            "--base-ref",
            "origin/main",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"read_only": true' in result.output
    assert '"schema_version": "worktree_claims_hygiene/v1"' in result.output
    assert observed["repo"] == repo.resolve()
    assert observed["claims"] == claims.resolve()
    assert observed["base_ref"] == "origin/main"
    assert observed["now_is_utc"] is True


def test_worktree_claims_report_summary_is_operator_scannable(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    claims = tmp_path / "ACTIVE_CLAIMS.md"
    claims.write_text(
        "| claim_id | owner | worktree | scope | created_at | expires_at | status |\n"
    )
    monkeypatch.setattr(worktree_claims_cli, "collect_report", lambda *_, **__: _fake_report())

    result = runner.invoke(
        audit_app,
        ["worktree-claims-report", "--repo", str(repo), "--claims", str(claims)],
    )

    assert result.exit_code == 0, result.output
    assert "worktrees=2" in result.output
    assert "merged=1" in result.output
    assert "open=1" in result.output
    assert "claims_active_expired=1" in result.output


DESTRUCTIVE_VERBS = (
    "worktree remove",
    "worktree prune",
    "Remove-Item",
    "unlink(",
    "rmtree(",
    ".write_text(",
)


def _executable_code(source: str) -> str:
    """Der ausfuehrbare Quelltext, normalisiert fuer die Verb-Suche.

    Zwei Fallen, die eine rohe Textsuche beide nicht ueberlebt:

    1. Sie trifft den erklaerenden Kommentar. Schreibt jemand
       ``# hier wird nie `worktree prune` aufgerufen``, wird der Waechter rot,
       obwohl genau das Gegenteil dokumentiert wurde. Kommentare und Docstrings
       fliegen deshalb raus — echte String-Literale bleiben aber stehen, denn
       dort steht der gefaehrliche Aufruf ja drin.
    2. Sie ist blind fuer die argv-Listenform. ``["git", "worktree", "remove"]``
       enthaelt die Zeichenkette ``worktree remove`` nirgends am Stueck. Jede
       Liste/Tupel aus String-Literalen wird deshalb zusaetzlich zu einer
       Kommandozeile zusammengesetzt und mitdurchsucht.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]

    argv_lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.List | ast.Tuple):
            continue
        parts = [
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        if len(parts) >= 2:
            argv_lines.append(" ".join(parts))

    return "\n".join([ast.unparse(tree), *argv_lines])


def test_worktree_claims_cli_source_has_no_destructive_verbs() -> None:
    code = _executable_code(Path(worktree_claims_cli.__file__).read_text(encoding="utf-8"))

    for token in DESTRUCTIVE_VERBS:
        assert token not in code, f"melde-only verletzt: {token}"


_DESTRUCTIVE_SOURCE = """
import shutil
from pathlib import Path


def purge(p: Path) -> None:
    shutil.rmtree(p)
"""

_DOCUMENTED_SOURCE = '''
"""Dieses Modul ruft niemals rmtree( oder worktree prune auf."""


def report() -> int:
    # kein Remove-Item, kein unlink( - nur melden
    return 0
'''

_HIDDEN_IN_LITERAL_SOURCE = """
import subprocess


def purge() -> None:
    subprocess.run(["git", "worktree", "remove", "x"], check=True)
"""


def test_verb_guard_catches_a_destructive_call() -> None:
    """Positivkontrolle: ohne sie waere der Waechter nicht von einer Tautologie zu unterscheiden."""
    code = _executable_code(_DESTRUCTIVE_SOURCE)

    assert any(token in code for token in DESTRUCTIVE_VERBS)


def test_verb_guard_ignores_comments_and_docstrings() -> None:
    """Der Kommentar, der die Regel erklaert, darf sie nicht brechen."""
    code = _executable_code(_DOCUMENTED_SOURCE)

    assert not any(token in code for token in DESTRUCTIVE_VERBS)


def test_verb_guard_still_sees_a_command_hidden_in_a_string_literal() -> None:
    """String-Literale bleiben stehen - sonst waere der gefaehrlichste Fall der blinde Fleck."""
    code = _executable_code(_HIDDEN_IN_LITERAL_SOURCE)

    assert "worktree remove" in code
