"""Unit tests for the hypothesis-family registry and its codified stop rule."""

from __future__ import annotations

import pytest

from app.research.hypothesis_families import (
    FAMILIES,
    STOP_RULE_FAILS,
    TERMINAL_DEAD,
    HypothesisFamily,
    get_family,
    is_terminal_dead,
)


def test_registry_invariants_hold_for_every_seeded_family() -> None:
    for name, fam in FAMILIES.items():
        assert fam.name == name
        assert fam.constructions_failed >= 0
        assert fam.evidence, f"{name}: a status without evidence is an opinion"
        if fam.status == TERMINAL_DEAD and fam.constructions_failed < STOP_RULE_FAILS:
            assert "terminal:" in fam.notes  # early terminal needs structural evidence


def test_known_falsification_history_is_encoded() -> None:
    assert is_terminal_dead("momentum")
    assert is_terminal_dead("ta_rules")
    assert is_terminal_dead("execution_alpha")
    assert is_terminal_dead("unlock_supply")
    assert is_terminal_dead("news_direction")  # stop rule hit 2026-07-02 (3 fails)
    assert not is_terminal_dead("funding_carry")
    assert not is_terminal_dead("l2_microstructure")
    assert not is_terminal_dead("nonexistent_family")


def test_get_family_is_case_and_whitespace_tolerant() -> None:
    assert get_family(" Momentum ") is not None
    assert get_family("no_such") is None


def test_terminal_below_threshold_without_note_is_rejected() -> None:
    with pytest.raises(ValueError, match="terminal_dead below"):
        HypothesisFamily(
            name="bad",
            status=TERMINAL_DEAD,
            constructions_failed=1,
            evidence=("x",),
            notes="no structural marker here",
        )


def test_invalid_status_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid status"):
        HypothesisFamily(name="bad", status="zombie", constructions_failed=0, evidence=("x",))
