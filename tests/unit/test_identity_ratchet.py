"""Identity-Ratchet (D-236): KAI bezeichnet sich im Repo nicht als „Trading Bot".

Verboten ist die *Selbstbezeichnung* („AI Analyst Trading Bot", „KAI Analyst
Trading Bot", „AI-Analyst-Trading-Bot"). Erlaubt bleiben: der Legacy-Pfad-/
Paketname (``ai_analyst_trading_bot`` / ``ai-analyst-trading-bot``, bewusst
nicht umbenannt — CLAUDE.md), die Negation („kein einfacher Trading-Bot"),
der deprecierte CLI-Alias ``trading-bot`` und die Historie (Archiv,
DECISION_LOG, CHANGELOG).
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

FORBIDDEN = re.compile(r"(?i)\b(?:k?ai[ -])?analyst[ -]trading[ -]bot\b")
LEGACY_TOKENS = ("ai_analyst_trading_bot", "ai-analyst-trading-bot")
ALLOWLIST_PREFIXES = (
    "docs/archive/",
    "docs/strategy/stab_2026_08_paket.md",
    "DECISION_LOG.md",
    "CHANGELOG.md",
    "tests/unit/test_identity_ratchet.py",
)
SKIP_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".pdf",
    ".zip",
    ".gz",
    ".b64",
    ".lock",
    ".pyc",
    ".db",
    ".sqlite",
)


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, check=True, capture_output=True
    ).stdout
    files: list[Path] = []
    for rel in out.decode("utf-8", "surrogateescape").split("\0"):
        if not rel or rel.startswith(ALLOWLIST_PREFIXES):
            continue
        if rel.endswith(SKIP_SUFFIXES) or "node_modules/" in rel:
            continue
        path = REPO / rel
        if path.is_file():  # sparse checkouts lassen Index-Eintraege ohne Datei
            files.append(path)
    return files


def _strip_legacy_tokens(text: str) -> str:
    for token in LEGACY_TOKENS:
        text = text.replace(token, "")
    return text


def test_no_trading_bot_self_description_outside_history() -> None:
    hits: list[str] = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN.search(_strip_legacy_tokens(line)):
                hits.append(f"{path.relative_to(REPO).as_posix()}:{lineno}: {line.strip()[:100]}")
    assert not hits, (
        "Selbstbezeichnung als 'Trading Bot' gefunden (D-236). "
        "KAI ist eine Research-/Truth-Plattform; benenne die Stelle um oder "
        "verschiebe sie ins Archiv:\n" + "\n".join(hits)
    )


def test_cli_entry_points_kai_canonical_trading_bot_alias() -> None:
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "scripts"
    ]
    assert scripts["kai"] == "app.cli.main:app"
    assert scripts["trading-bot"] == "app.cli.main:app", (
        "Alias muss bestehende Runbooks/Units weiter tragen"
    )


def test_cli_help_names_kai_not_trading_bot() -> None:
    from typer.testing import CliRunner

    from app.cli.main import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "KAI" in result.output
    assert not FORBIDDEN.search(result.output)
