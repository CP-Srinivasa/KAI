"""Tests for app.messaging.kai_phrase_engine."""

from __future__ import annotations

import pytest

from app.messaging.kai_persona import VALID_STATES, reset_persona_cache
from app.messaging.kai_phrase_engine import (
    KAI_FORBIDDEN_PHRASES_DE,
    KAI_FORBIDDEN_PHRASES_EN,
    assert_phrase_safe,
    get_kai_extra_mode_phrase,
    get_kai_phrase,
    is_phrase_safe,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_persona_cache()
    yield
    reset_persona_cache()


def test_get_phrase_returns_non_empty_for_every_state_de():
    for state in VALID_STATES:
        phrase = get_kai_phrase(state, "de", seed=0)
        assert phrase
        assert len(phrase) > 0


def test_get_phrase_returns_non_empty_for_every_state_en():
    for state in VALID_STATES:
        phrase = get_kai_phrase(state, "en", seed=0)
        assert phrase
        assert len(phrase) > 0


def test_seed_is_deterministic():
    a = get_kai_phrase("ANALYSIS", "de", seed=1)
    b = get_kai_phrase("ANALYSIS", "de", seed=1)
    assert a == b


def test_no_forbidden_phrases_in_any_state_de():
    for state in VALID_STATES:
        for seed in range(50):
            phrase = get_kai_phrase(state, "de", seed=seed).lower()
            for banned in KAI_FORBIDDEN_PHRASES_DE:
                assert banned.lower() not in phrase, (
                    f"forbidden phrase {banned!r} leaked into {state} (seed={seed})"
                )


def test_no_forbidden_phrases_in_any_state_en():
    for state in VALID_STATES:
        for seed in range(50):
            phrase = get_kai_phrase(state, "en", seed=seed).lower()
            for banned in KAI_FORBIDDEN_PHRASES_EN:
                assert banned.lower() not in phrase, (
                    f"forbidden phrase {banned!r} leaked into {state} (seed={seed})"
                )


def test_extra_mode_phrases_are_non_empty():
    assert get_kai_extra_mode_phrase("hype", "de", seed=0)
    assert get_kai_extra_mode_phrase("mockery", "de", seed=0)
    assert get_kai_extra_mode_phrase("bad_data", "de", seed=0)
    assert get_kai_extra_mode_phrase("hype", "en", seed=0)


def test_is_phrase_safe_blocks_de_claims():
    assert not is_phrase_safe("Das ist ein sicherer Gewinn", "de")
    assert not is_phrase_safe("Garantierter Gewinn auf BTC", "de")
    assert not is_phrase_safe("100% sicher", "de")


def test_is_phrase_safe_blocks_en_claims():
    assert not is_phrase_safe("This is a guaranteed profit", "en")
    assert not is_phrase_safe("risk-free profit", "en")


def test_is_phrase_safe_allows_neutral_observation():
    assert is_phrase_safe("Datenstrom stabil. Ich sehe ein Muster.", "de")
    assert is_phrase_safe("Signal alive. Risk still needs a leash.", "en")


def test_assert_phrase_safe_raises_on_violation():
    with pytest.raises(ValueError):
        assert_phrase_safe("garantierter Gewinn", "de")
    # Neutral phrase passes silently.
    assert_phrase_safe("Stille. Verdaechtig viel davon.", "de")
