"""Unit tests for the hypothesis-family registry and its codified stop rule."""

from __future__ import annotations

import pytest

from app.research.hypothesis_families import (
    FAMILIES,
    PROBATION,
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


def test_oracle_demand_family_registered_with_c1_fail() -> None:
    """Audit 2026-08-06 (P1-1): der MEMORY-Stand „oracle_demand 1/3 Richtung
    Stop-Rule" war reine Prosa — die Familie fehlte in der Registry. Das
    attestierte C1-Verdikt (seq 71) muss hier maschinell zählbar sein."""
    fam = get_family("oracle_demand")
    assert fam is not None, "oracle_demand fehlt in FAMILIES"
    assert fam.status == PROBATION
    assert fam.constructions_failed == 1
    assert any("9cab81fae4823482" in e for e in fam.evidence)
    assert any("seq 71" in e for e in fam.evidence)
    assert not is_terminal_dead("oracle_demand")


def test_news_direction_reflects_nd_v2_terminal_verdict() -> None:
    """ND-v2 (b20ef1487ccba99d) FAILED am versiegelten Gate, attestiert seq 73
    (2026-08-06). Die Registry darf die Ausnahme nicht mehr als offen führen."""
    fam = get_family("news_direction")
    assert fam is not None
    assert fam.status == TERMINAL_DEAD
    assert fam.constructions_failed == 4
    assert any("b20ef1487ccba99d" in e and "seq 73" in e for e in fam.evidence)
    assert "GESCHLOSSEN" in fam.notes
    # Die 3d-Ausnahme (seq 72 attestiert) bleibt als letzte offene benannt.
    assert "7e8d66314dd7c64e" in fam.notes
