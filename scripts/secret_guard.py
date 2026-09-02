#!/usr/bin/env python3
"""Secret guard — findet geleakte Credentials, ohne sie auszugeben.

WARUM DIESES MODUL EXISTIERT (2026-09-02)

Der Guard lag als Bash-Schleife in ``.github/workflows/ci.yml`` und kannte fuer
GitHub genau EIN Muster: ``ghp_[A-Za-z0-9]{36}`` — Personal Access Tokens.

GitHub vergibt aber sechs Praefixe, und der Rest fiel durch:

    ghp_          Personal Access Token          (abgedeckt)
    gho_          OAuth-Token, u. a. GitHub CLI  (NICHT abgedeckt)
    ghs_          Server-to-Server / App         (NICHT abgedeckt)
    ghu_          User-to-Server                 (NICHT abgedeckt)
    ghr_          Refresh-Token                  (NICHT abgedeckt)
    github_pat_   Fine-grained PAT               (NICHT abgedeckt)

Aufgefallen ist die Luecke am 2026-09-01, als ein ``gho_``-Token der GitHub CLI
(Scopes ``repo``, ``workflow``) in einem Sitzungsprotokoll landete. Es lag nicht
in einer getrackten Datei — aber haette es dort gelegen, haette der Guard
geschwiegen. Ausgerechnet die Klasse, die auf einer Entwicklermaschine am
haeufigsten herumliegt, war die einzige ohne Wache.

Zwei weitere Entscheidungen, beide aus demselben Vorfall:

1.  **Der Guard gibt niemals einen Treffer im Klartext aus.** Ein Wachhund, der
    das gefundene Geheimnis in ein CI-Log schreibt, verlegt das Leck nur — vom
    Repository in ein Build-Protokoll, das oft breiter lesbar ist. Gemeldet
    werden Typ, Datei, Zeile und ein redigierter Praefix.

2.  **Die Laenge ist ``{20,}``, nicht ``{36}``.** GitHub hat die Tokenlaenge
    ueber die Jahre veraendert und fine-grained PATs sind deutlich laenger. Ein
    exaktes ``{36}`` ist eine Wette auf ein Format, das der Emittent jederzeit
    aendern darf — und eine verlorene Wette bedeutet hier: kein Alarm.

Aufruf::

    python scripts/secret_guard.py --tracked
    python scripts/secret_guard.py --staged
    python scripts/secret_guard.py --workspace
    python scripts/secret_guard.py --tracked --staged --workspace

Exit 0 = sauber, Exit 1 = Fund.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Musterkatalog
# ---------------------------------------------------------------------------
# ``{20,}`` statt einer exakten Laenge: siehe Modul-Docstring. Der Emittent darf
# das Format aendern, unsere Wache darf davon nicht abhaengen.
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    # --- GitHub, alle sechs Praefixe ------------------------------------
    ("GitHub Personal Access Token", r"ghp_[A-Za-z0-9]{20,}"),
    ("GitHub OAuth token", r"gho_[A-Za-z0-9]{20,}"),
    ("GitHub server-to-server token", r"ghs_[A-Za-z0-9]{20,}"),
    ("GitHub user-to-server token", r"ghu_[A-Za-z0-9]{20,}"),
    ("GitHub refresh token", r"ghr_[A-Za-z0-9]{20,}"),
    ("GitHub fine-grained PAT", r"github_pat_[A-Za-z0-9_]{20,}"),
    # --- unveraendert aus dem bisherigen CI-Guard ------------------------
    ("OpenAI project key", r"sk-proj-[A-Za-z0-9_-]{20,}"),
    ("OpenAI legacy key", r"sk-[A-Za-z0-9]{40,}"),
    ("Google API key", r"AIzaSy[A-Za-z0-9_-]{33}"),
    ("NewsData.io key", r"pub_[a-f0-9]{20,}"),
    ("Slack bot token", r"xoxb-[0-9]{10,}"),
    ("AWS access key", r"AKIA[A-Z0-9]{16}"),
    ("Telegram bot token", r"[0-9]{8,10}:[A-Za-z0-9_-]{35}"),
)

#: Dateien, die realistisch aussehende FIXTURES brauchen, um Redaction ueberhaupt
#: testen zu koennen. Eine Wache, die ihre eigenen Testdaten anschlaegt, wird
#: abgeschaltet — und dann faellt das Echte mit durch.
ALLOWLISTED_PATHS: frozenset[str] = frozenset(
    {
        ".github/workflows/ci.yml",
        "scripts/secret_guard.py",
        "tests/unit/test_secret_guard.py",
        "tests/unit/test_audit_sanitization.py",
        "tests/unit/test_structured_reasoning.py",
        "tests/unit/test_bayes_journal_sanitize.py",
    }
)

#: Verzeichnisse, die beim Workspace-Scan nicht betreten werden.
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "htmlcov",
    }
)

_COMPILED = tuple((name, re.compile(pat)) for name, pat in SECRET_PATTERNS)


@dataclass(frozen=True)
class Finding:
    """Ein Fund — bewusst OHNE den gefundenen Wert."""

    secret_type: str
    file: str
    line: int
    redacted_prefix: str

    def render(self) -> str:
        return f"{self.secret_type} · {self.file}:{self.line} · {self.redacted_prefix}"


def redact(match: str) -> str:
    """Ein Treffer wird zu ``praefix_****`` — nie mehr.

    Genug, um die Klasse zu erkennen und die Stelle zu finden; zu wenig, um das
    Geheimnis zu benutzen. Das ist der ganze Zweck: der Fund darf das Leck nicht
    vergroessern, indem er es ins Build-Protokoll schreibt.
    """
    for sep in ("_", "-", ":"):
        head, found, _ = match.partition(sep)
        if found:
            return f"{head}{sep}****"
    return f"{match[:4]}****"


def scan_text(text: str, path: str) -> list[Finding]:
    """Alle Funde in einem Text. Reine Funktion, damit sie testbar bleibt."""
    if path in ALLOWLISTED_PATHS:
        return []
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for secret_type, pattern in _COMPILED:
            for m in pattern.finditer(line):
                findings.append(
                    Finding(
                        secret_type=secret_type,
                        file=path,
                        line=lineno,
                        redacted_prefix=redact(m.group(0)),
                    )
                )
    return findings


def _git(*args: str, cwd: Path) -> str:
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


def scan_tracked(root: Path) -> list[Finding]:
    """Alle von git getrackten Dateien — inklusive committeter Reports."""
    findings: list[Finding] = []
    for rel in _git("ls-files", cwd=root).splitlines():
        rel = rel.strip()
        if not rel:
            continue
        findings.extend(scan_text(_read(root / rel), rel))
    return findings


def scan_staged(root: Path) -> list[Finding]:
    """Der gestagte Diff — faengt ein Geheimnis VOR dem Commit."""
    diff = _git("diff", "--cached", "--unified=0", cwd=root)
    findings: list[Finding] = []
    current = "<staged diff>"
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        findings.extend(
            Finding(f.secret_type, current, f.line, f.redacted_prefix)
            for f in scan_text(line[1:], current)
        )
    return findings


def scan_workspace(root: Path) -> list[Finding]:
    """Der Arbeitsbaum, auch ungetrackt — der CI-Workspace und lokale Artefakte.

    Ein generierter, noch nicht committeter Report ist genau die Stelle, an der
    ein Geheimnis am leichtesten unbemerkt landet.
    """
    findings: list[Finding] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        findings.extend(scan_text(_read(path), rel))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracked", action="store_true", help="git-getrackte Dateien")
    parser.add_argument("--staged", action="store_true", help="gestagter Diff")
    parser.add_argument("--workspace", action="store_true", help="ganzer Arbeitsbaum")
    parser.add_argument("--root", default=".", help="Repo-Wurzel")
    args = parser.parse_args(argv)

    if not (args.tracked or args.staged or args.workspace):
        args.tracked = True

    root = Path(args.root).resolve()
    findings: list[Finding] = []
    if args.tracked:
        findings += scan_tracked(root)
    if args.staged:
        findings += scan_staged(root)
    if args.workspace:
        findings += scan_workspace(root)

    seen: set[tuple[str, str, int]] = set()
    unique: list[Finding] = []
    for f in findings:
        key = (f.secret_type, f.file, f.line)
        if key not in seen:
            seen.add(key)
            unique.append(f)

    if not unique:
        print("secret-guard: clean")
        return 0

    print(f"secret-guard: {len(unique)} finding(s) — values are NEVER printed")
    for f in sorted(unique, key=lambda x: (x.file, x.line)):
        print(f"  {f.render()}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
