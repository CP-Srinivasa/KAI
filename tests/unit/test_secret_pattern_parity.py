"""Ein Katalog, zwei Waechter -- und ein Test, der das erzwingt.

Der Vorfall vom 2026-09-01 (ein ``gho_``-Token in einem Sitzungsprotokoll) legte
eine Luecke in der CI-Wache offen; #849 schloss sie. Erst danach fiel auf, dass
dieselbe Luecke ein zweites Mal im Repository stand -- in der Laufzeit-Redaction
``app/audit/sanitization.py``, mit ``ghp_`` plus ``gho_``/``ghs_``/``ghr_`` bei
fester Laenge 36. Es fehlten dort ``ghu_``, ``github_pat_``, und jede Laenge
ausser genau 36.

Der eigentliche Defekt war nicht das eine falsche Muster. Es waren ZWEI Listen,
die dasselbe beschreiben sollten. Solche Listen driften, und zwar unbemerkt,
weil jede fuer sich plausibel aussieht.

Diese Datei prueft deshalb nicht nur, dass beide Waechter heute dasselbe tun,
sondern verbietet strukturell, dass jemand eine dritte Liste anlegt.
"""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

import pytest
from scripts.secret_guard import SECRET_PATTERNS, scan_text

from app.audit.sanitization import DEFAULT_PATTERNS, redact_secrets
from app.security.secret_patterns import (
    GITHUB_TOKEN_SHAPES,
    github_combined_regex,
    github_patterns,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Synthetische Koerper pro Praefix. Keiner dieser Werte war jemals gueltig.
#: Sie sind lang genug, um die Muster zu treffen, und beliebig getippt.
BODIES: dict[str, str] = {
    "ghp_": "L1k2J3h4G5f6D7s8A9p0O1i2U3y4T5r6E7w8",
    "gho_": "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "ghs_": "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2",
    "ghu_": "Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6J7k8",
    "ghr_": "M1n2B3v4C5x6Z7a8S9d0F1g2H3j4K5l6P7o8",
    "github_pat_": "11ABCDEFG0aB1cD2eF3gH4_i5J6k7L8m9N0oP1qR2sT3uV4wX5yZ6",
}

SHAPE_IDS = [shape.prefix for shape in GITHUB_TOKEN_SHAPES]


def _token(prefix: str) -> str:
    return prefix + BODIES[prefix]


# ==========================================================================
# Beide Waechter, dieselben sechs Praefixe
# ==========================================================================


@pytest.mark.parametrize("prefix", SHAPE_IDS)
def test_ci_guard_flags_every_prefix(prefix: str) -> None:
    findings = scan_text(f"X={_token(prefix)}\n", "docs/leak.md")
    assert findings, f"CI-Wache hat {prefix} nicht gefunden"


@pytest.mark.parametrize("prefix", SHAPE_IDS)
def test_runtime_sanitizer_redacts_every_prefix(prefix: str) -> None:
    token = _token(prefix)
    out = redact_secrets(f"auth failed for {token}", patterns=DEFAULT_PATTERNS)
    assert "[REDACTED:github_token]" in out
    assert token not in out
    assert BODIES[prefix][:12] not in out, "verwertbares Fragment ueberlebt"


@pytest.mark.parametrize("prefix", SHAPE_IDS)
def test_no_prefix_is_covered_by_only_one_of_the_two(prefix: str) -> None:
    """Genau diese Asymmetrie war der Defekt: CI kannte sechs, Laufzeit vier."""
    token = _token(prefix)
    seen_by_guard = bool(scan_text(f"X={token}\n", "docs/leak.md"))
    seen_by_runtime = "[REDACTED:github_token]" in redact_secrets(token, patterns=DEFAULT_PATTERNS)
    assert seen_by_guard is True
    assert seen_by_runtime is True


# ==========================================================================
# Die Muster stammen nachweislich aus dem gemeinsamen Katalog
# ==========================================================================


def test_guard_catalogue_starts_with_the_shared_github_patterns() -> None:
    shared = github_patterns()
    assert SECRET_PATTERNS[: len(shared)] == shared


def test_runtime_pattern_is_the_shared_combined_regex() -> None:
    github = [sp for sp in DEFAULT_PATTERNS if sp.name == "github_token"]
    assert len(github) == 1
    assert github[0].pattern.pattern == github_combined_regex()


def test_the_old_fixed_length_36_pattern_is_gone() -> None:
    """Regression: die feste Laenge 36 traf keinen fine-grained PAT."""
    github = next(sp for sp in DEFAULT_PATTERNS if sp.name == "github_token")
    assert "{36}" not in github.pattern.pattern


# ==========================================================================
# Strukturell: es darf keine dritte Liste geben
# ==========================================================================

#: Nur der Katalog selbst darf GitHub-Token-Regexe als String-Literal fuehren.
CATALOGUE = "app/security/secret_patterns.py"

#: Shape eines Regex-Literals: Praefix unmittelbar gefolgt von einer
#: Zeichenklasse, oder die alte Sammel-Alternation ``gh[osr]_``.
LITERAL_MARKERS = (
    "ghp_[",
    "gho_[",
    "ghs_[",
    "ghu_[",
    "ghr_[",
    "github_pat_[",
    "gh[osr]_",
)


def _string_literals(path: Path) -> list[str]:
    """Nur echte String-Literale -- Kommentare bleiben aussen vor.

    Das ist Absicht: ``app/audit/sanitization.py`` beschreibt in einem Kommentar,
    was dort FRUEHER stand. Historie darf man aufschreiben; ein zweites lebendes
    Muster darf man nicht anlegen. Ein reiner Textscan koennte beides nicht
    unterscheiden und wuerde damit das Dokumentieren des Vorfalls bestrafen.
    """
    out: list[str] = []
    src = path.read_text(encoding="utf-8")
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.STRING:
            out.append(tok.string)
    return out


def _python_sources() -> list[Path]:
    files: list[Path] = []
    for sub in ("app", "scripts"):
        files.extend(
            p
            for p in (REPO_ROOT / sub).rglob("*.py")
            if "__pycache__" not in p.parts and p.relative_to(REPO_ROOT).as_posix() != CATALOGUE
        )
    return files


def test_no_second_github_catalogue_under_app_or_scripts() -> None:
    offenders: list[str] = []
    for path in _python_sources():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for literal in _string_literals(path):
            if any(marker in literal for marker in LITERAL_MARKERS):
                offenders.append(rel)
                break
    assert offenders == [], (
        "GitHub-Token-Regex ausserhalb des gemeinsamen Katalogs gefunden: "
        f"{offenders}. Ergaenze das Praefix in {CATALOGUE}, nicht hier."
    )


def test_the_catalogue_itself_still_carries_the_patterns() -> None:
    """Negativkontrolle: der Test oben darf nicht deshalb gruen sein, weil
    ueberhaupt niemand mehr GitHub-Muster fuehrt."""
    literals = _string_literals(REPO_ROOT / CATALOGUE)
    assert any("[A-Za-z0-9]{20,}" in lit for lit in literals)


def test_ci_workflow_carries_no_own_github_pattern() -> None:
    text = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert not any(marker in text for marker in LITERAL_MARKERS)


# ==========================================================================
# Fehlalarme -- beide Waechter muessen dieselben Strings durchlassen
# ==========================================================================

BENIGN = [
    "das Praefix gho_ steht fuer OAuth-Tokens",
    "Das exponierte Token war gho_****.",
    "Das exponierte Token war gho_<redacted>.",
    "ghost_writer = True",
    "github_page_url ist kein Geheimnis",
    "gho_abc123",
]


@pytest.mark.parametrize("text", BENIGN)
def test_ci_guard_ignores_benign_text(text: str) -> None:
    assert scan_text(text + "\n", "docs/x.md") == []


@pytest.mark.parametrize("text", BENIGN)
def test_runtime_sanitizer_ignores_benign_text(text: str) -> None:
    assert "[REDACTED:github_token]" not in redact_secrets(text, patterns=DEFAULT_PATTERNS)


def test_redaction_marker_is_not_itself_a_match() -> None:
    """Idempotenz: zweimal redigieren darf nichts weiter veraendern."""
    once = redact_secrets(f"token {_token('gho_')}", patterns=DEFAULT_PATTERNS)
    assert redact_secrets(once, patterns=DEFAULT_PATTERNS) == once


@pytest.mark.parametrize("prefix", ["ghp_", "gho_", "ghs_", "ghu_", "ghr_"])
def test_nineteen_char_suffix_is_below_the_threshold(prefix: str) -> None:
    """Grenzbeweis: 19 Zeichen nein, 20 Zeichen ja. Ohne diese Kontrolle
    koennte ``{20,}`` unbemerkt zu ``{1,}`` verrutschen."""
    short = prefix + "A1b2C3d4E5f6G7h8I9j"
    long_enough = prefix + "A1b2C3d4E5f6G7h8I9j0"
    assert scan_text(short + "\n", "docs/x.md") == []
    assert scan_text(long_enough + "\n", "docs/x.md") != []
