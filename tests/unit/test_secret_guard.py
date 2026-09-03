"""Secret guard — alle sechs GitHub-Praefixe, und kein Wert im Log.

Der Guard kannte fuer GitHub genau ein Muster: ``ghp_[A-Za-z0-9]{36}``. GitHub
vergibt sechs Praefixe; fuenf davon fielen durch, darunter ``gho_`` — das Format
der GitHub CLI und damit die Klasse, die auf einer Entwicklermaschine am
haeufigsten herumliegt.

Aufgefallen ist das am 2026-09-01, als ein ``gho_``-Token mit ``repo``- und
``workflow``-Scopes in einem Sitzungsprotokoll landete. Es lag nicht in einer
getrackten Datei — aber haette es dort gelegen, haette der Guard geschwiegen.

Die Negativkontrollen sind hier so wichtig wie die Positivkontrollen: eine Wache,
die bei jedem Vorkommen der Zeichenfolge ``gho_`` anschlaegt, wird nach dem
dritten Fehlalarm abgeschaltet — und dann faellt das Echte mit durch.
"""

from __future__ import annotations

import pytest
from scripts.secret_guard import (
    ALLOWLISTED_PATHS,
    SECRET_PATTERNS,
    Finding,
    redact,
    scan_text,
)

# Synthetische Fixtures. Keiner dieser Werte war jemals gueltig; sie sind lang
# genug, um die Muster zu treffen, und beliebig getippt.
FAKE = {
    "gho": "gho_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "ghs": "ghs_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2",
    "ghu": "ghu_" + "Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6J7k8",
    "ghr": "ghr_" + "M1n2B3v4C5x6Z7a8S9d0F1g2H3j4K5l6P7o8",
    "ghp": "ghp_" + "L1k2J3h4G5f6D7s8A9p0O1i2U3y4T5r6E7w8",
    "pat": "github_pat_" + "11ABCDEFG0aB1cD2eF3gH4_i5J6k7L8m9N0oP1qR2sT3uV4wX5yZ6",
}


# --------------------------------------------------------------------------
# POSITIVKONTROLLEN — jedes Praefix muss FAIL erzeugen
# --------------------------------------------------------------------------
@pytest.mark.parametrize("key", sorted(FAKE))
def test_every_github_prefix_is_caught(key: str) -> None:
    findings = scan_text(f"TOKEN={FAKE[key]}\n", "some/file.txt")
    assert findings, f"{key} prefix slipped through the guard"
    assert findings[0].file == "some/file.txt"
    assert findings[0].line == 1


def test_the_prefix_set_is_complete() -> None:
    """TOKEN_PREFIX_COVERAGE = gho/ghs/ghu/ghr/ghp/github_pat."""
    patterns = " ".join(p for _name, p in SECRET_PATTERNS)
    for prefix in ("ghp_", "gho_", "ghs_", "ghu_", "ghr_", "github_pat_"):
        assert prefix in patterns, f"{prefix} missing from the pattern catalogue"


def test_a_token_is_found_on_the_right_line() -> None:
    text = "harmless\nstill fine\nTOKEN=" + FAKE["gho"] + "\ntrailing\n"
    (finding,) = scan_text(text, "report.txt")
    assert finding.line == 3
    assert finding.file == "report.txt"


def test_the_non_github_patterns_still_fire() -> None:
    """Die Erweiterung darf die bestehenden Muster nicht verdraengen."""
    assert scan_text("AKIA" + "ABCDEFGHIJKLMNOP", "f.txt")
    assert scan_text("xoxb-" + "1234567890123", "f.txt")


# --------------------------------------------------------------------------
# NEGATIVKONTROLLEN — Fehlalarme sind genauso schaedlich
# --------------------------------------------------------------------------
def test_a_bare_prefix_without_a_token_is_not_a_finding() -> None:
    """ "gho_" allein ist Prosa, kein Geheimnis."""
    assert scan_text("das Praefix gho_ steht fuer OAuth-Tokens\n", "docs/security.md") == []


def test_a_redacted_token_in_documentation_passes() -> None:
    """Genau die Form, in der dieser Vorfall dokumentiert werden MUSS."""
    for redacted in ("gho_****", "gho_…", "gho_<redacted>", "ghp_***"):
        assert scan_text(f"Das exponierte Token war {redacted}.\n", "docs/incident.md") == [], (
            f"{redacted} must not be flagged — otherwise the incident cannot be written up"
        )


def test_a_regex_literal_describing_the_pattern_passes() -> None:
    """``app/audit/sanitization.py`` traegt die Muster als Regex-Literale."""
    assert scan_text(r'pattern=re.compile(r"\bghp_[A-Za-z0-9]{36}\b")' + "\n", "app/x.py") == []


def test_ordinary_random_strings_pass() -> None:
    for text in (
        "commit 9293c4239b80ebbfec42a39cda289ba4f60a1610\n",
        "sha256 a6974b985746272e8ec7ac08d65fe5bd158f4fa4ee6ffd075c02ba5537fcb727\n",
        "ghost_writer = True\n",
        "github_page_url = 'https://example.invalid'\n",
        "the quick brown fox jumps over the lazy dog\n",
    ):
        assert scan_text(text, "f.py") == [], f"false positive on: {text!r}"


def test_a_short_suffix_is_below_the_threshold() -> None:
    assert scan_text("gho_abc123\n", "f.txt") == []


@pytest.mark.parametrize("path", sorted(ALLOWLISTED_PATHS))
def test_allowlisted_fixture_files_are_not_flagged(path: str) -> None:
    """Sonst schlaegt die Wache ihre eigenen Testdaten an und wird abgeschaltet."""
    assert scan_text(f"TOKEN={FAKE['gho']}\n", path) == []


def test_a_file_that_is_not_allowlisted_is_still_flagged() -> None:
    """NEGATIVKONTROLLE zur Allowlist: sie darf nicht alles durchlassen."""
    assert scan_text(f"TOKEN={FAKE['gho']}\n", "app/some_module.py")


# --------------------------------------------------------------------------
# SECRET_VALUE_ECHO = 0
# --------------------------------------------------------------------------
@pytest.mark.parametrize("key", sorted(FAKE))
def test_the_value_never_appears_in_the_report(key: str) -> None:
    """Ein Wachhund, der den Fund ins Log schreibt, verlegt das Leck nur."""
    token = FAKE[key]
    (finding,) = scan_text(f"TOKEN={token}\n", "report.txt")
    rendered = finding.render()
    assert token not in rendered
    assert token not in finding.redacted_prefix
    # Auch kein brauchbares Fragment: nach dem Trennzeichen nur Sterne.
    assert rendered.count("****") == 1
    body = token.split("_", 1)[1]
    assert body[:8] not in rendered


def test_the_redaction_keeps_the_class_and_drops_the_secret() -> None:
    assert redact(FAKE["gho"]) == "gho_****"
    assert redact(FAKE["pat"]) == "github_****"
    assert redact("xoxb-1234567890123") == "xoxb-****"


def test_the_rendered_line_carries_type_file_line_and_prefix() -> None:
    f = Finding("GitHub OAuth token", "report.txt", 42, "gho_****")
    assert f.render() == "GitHub OAuth token · report.txt:42 · gho_****"


# --------------------------------------------------------------------------
# Der Bestand ist sauber
# --------------------------------------------------------------------------
def test_the_repository_is_clean_under_the_extended_catalogue() -> None:
    from pathlib import Path

    from scripts.secret_guard import scan_tracked

    root = Path(__file__).resolve().parents[2]
    findings = scan_tracked(root)
    assert findings == [], "\n".join(f.render() for f in findings)
