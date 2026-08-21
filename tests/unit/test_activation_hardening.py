r"""Die Aktivierung muss beweisbar sein, nicht nur befuellt.

``activate()`` verlangte bisher nur, dass ``research_code_sha`` und
``evaluator_sha256`` nicht leer sind. ``"abc"`` erfuellte das. Damit stuende
spaeter im Activation-Record eine Angabe, die WIE eine Beweiskette aussieht und
keine ist — und ein Verdikt, das sich darauf beruft, waere nicht nachpruefbar.

Ebenso stillschweigend war die Zeit: ein naives ``t0_utc`` bekam einfach
``tzinfo=UTC`` angeheftet. Ein Operator, der lokale Zeit eingibt, verschiebt so
die gesamte OOS-Epoche um Stunden, ohne dass irgendwo etwas auffaellt. Und ein
Offset wie ``+02:00`` blieb im Feld ``t0_utc`` stehen — ein Feldname, der dann
luegt.
"""

from __future__ import annotations

import pytest

from app.research.prereg_candidate import (
    activate,
    build_rsi_reentry_volume_candidate,
    candidate_sha256,
)

_UNIVERSE_SHA = "d" * 64
_CODE_SHA = "9d1502dc7c6f4f2b1a3e5c7d9b0f2a4c6e8d0b2f"
_EVALUATOR_SHA = "a" * 64


def _candidate():
    return build_rsi_reentry_volume_candidate(_UNIVERSE_SHA, 34)


def _activate(**overrides):
    kwargs = {
        "t0_utc": "2026-09-01T00:00:00+00:00",
        "research_code_sha": _CODE_SHA,
        "evaluator_sha256": _EVALUATOR_SHA,
        "operator_approved": True,
    }
    kwargs.update(overrides)
    return activate(_candidate(), **kwargs)


# ── Formate ─────────────────────────────────────────────────────────────────


def test_a_wellformed_activation_still_works() -> None:
    activation = _activate()

    assert activation.research_code_sha == _CODE_SHA
    assert activation.evaluator_sha256 == _EVALUATOR_SHA
    assert activation.candidate_sha256 == candidate_sha256(_candidate())


@pytest.mark.parametrize("bad", ["abc123", "zzzz" * 10, "9d1502d", "9D1502DC" * 5 + "!!"])
def test_a_research_code_sha_that_is_not_a_git_sha_is_refused(bad: str) -> None:
    """Ein Platzhalter darf nicht wie ein Commit-Verweis aussehen duerfen."""
    with pytest.raises(ValueError, match="research_code_sha"):
        _activate(research_code_sha=bad)


def test_a_full_git_sha_is_accepted_uppercase_is_normalised() -> None:
    activation = _activate(research_code_sha=_CODE_SHA.upper())

    assert activation.research_code_sha == _CODE_SHA


@pytest.mark.parametrize("bad", ["def456", "a" * 63, "a" * 65, "g" * 64])
def test_an_evaluator_sha_that_is_not_64_hex_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="evaluator_sha256"):
        _activate(evaluator_sha256=bad)


# ── Zeit ────────────────────────────────────────────────────────────────────


def test_a_naive_t0_is_refused_not_assumed_to_be_utc() -> None:
    """Stillschweigend UTC anzunehmen verschiebt die ganze Epoche.

    Der Operator gibt lokale Zeit ein, das Feld heisst ``t0_utc``, und niemand
    sieht den Unterschied — bis ein Signal knapp vor oder nach T0 liegt.
    """
    with pytest.raises(ValueError, match="timezone"):
        _activate(t0_utc="2026-09-01T00:00:00")


def test_an_offset_t0_is_canonicalised_to_utc() -> None:
    """``t0_utc`` muss UTC ENTHALTEN, nicht nur so heissen."""
    activation = _activate(t0_utc="2026-09-01T02:00:00+02:00")

    assert activation.t0_utc == "2026-09-01T00:00:00+00:00"
    assert activation.t1_utc.endswith("+00:00")
    assert activation.t2_utc.endswith("+00:00")


def test_an_unparseable_t0_is_refused() -> None:
    with pytest.raises(ValueError):
        _activate(t0_utc="irgendwann")


# ── Hash-Kette ──────────────────────────────────────────────────────────────


def test_a_candidate_whose_universe_sha_does_not_match_is_refused() -> None:
    """Der Candidate traegt den Universe-Hash; er wird nachgerechnet, nicht geglaubt."""
    from dataclasses import replace

    broken = replace(_candidate(), universe_sha256="f" * 64)

    with pytest.raises(ValueError, match="universe_sha256"):
        activate(
            broken,
            t0_utc="2026-09-01T00:00:00+00:00",
            research_code_sha=_CODE_SHA,
            evaluator_sha256=_EVALUATOR_SHA,
            operator_approved=True,
            expected_universe_sha256=_UNIVERSE_SHA,
        )


def test_the_expected_universe_sha_is_optional_and_matching_passes() -> None:
    activation = _activate(expected_universe_sha256=_UNIVERSE_SHA)

    assert activation.universe_sha256 == _UNIVERSE_SHA
