"""Unit tests for the disruptive focus-field taxonomy (app/trading/focus_fields.py).

Covers: explicit override precedence, keyword inference per field, precedence
tie-break, invalid-explicit degradation, and the honest ``unknown`` for assets
with no curated signal (never guessed).
"""

from __future__ import annotations

import pytest

from app.trading.focus_fields import (
    FOCUS_FIELD_IDS,
    UNKNOWN,
    all_focus_fields,
    classify_focus_field,
    get_focus_field,
    is_valid_focus_field,
)


def test_taxonomy_ids_are_unique_and_registered() -> None:
    fields = all_focus_fields()
    ids = [f.field_id for f in fields]
    assert len(ids) == len(set(ids))  # no duplicate canonical IDs
    for f in fields:
        assert get_focus_field(f.field_id) is f
        assert is_valid_focus_field(f.field_id)
        assert f.field_id in FOCUS_FIELD_IDS
    assert UNKNOWN in FOCUS_FIELD_IDS
    assert not is_valid_focus_field(UNKNOWN)  # unknown is the sentinel, not a field


def test_explicit_valid_override_wins() -> None:
    # XRP-style: sector "payments" would infer fintech, explicit forces blockchain.
    assert (
        classify_focus_field(explicit="blockchain", sector="payments", narrative="x")
        == "blockchain"
    )


def test_invalid_explicit_degrades_to_inference() -> None:
    # A typo'd explicit value is NOT honoured — we fall back to inference.
    assert (
        classify_focus_field(explicit="blockchian", sector="semiconductors", narrative="ai_compute")
        == "ai"
    )


@pytest.mark.parametrize(
    ("sector", "narrative", "expected"),
    [
        ("semiconductors", "ai_compute", "ai"),
        ("genomics", "dna_sequencing", "dna_sequencing"),
        ("gene_editing", "crispr_therapeutics", "gene_editing"),
        ("proteomics", "single_cell", "multiomics"),
        ("robotics", "surgical_robotics", "robotics"),
        ("electric_vehicles", "ev_drivetrain", "ev"),
        ("aerospace", "small_launch", "space"),
        ("telecom", "5g_connectivity", "communications"),
        ("battery", "grid_storage", "energy_storage"),
        ("fintech", "payments_fintech", "fintech"),
        ("additive_manufacturing", "3d_printing", "additive_manufacturing"),
        ("smart_contract_l1", "high_throughput_l1", "blockchain"),
        ("store_of_value", "digital_gold", "blockchain"),
    ],
)
def test_inference_per_focus_field(sector: str, narrative: str, expected: str) -> None:
    assert classify_focus_field(sector=sector, narrative=narrative) == expected


def test_inference_from_tags() -> None:
    assert classify_focus_field(tags=["robotics", "automation"]) == "robotics"


def test_no_signal_is_unknown_not_guessed() -> None:
    assert classify_focus_field() == UNKNOWN
    assert classify_focus_field(sector="unknown", narrative="unknown") == UNKNOWN
    assert classify_focus_field(sector="something_unmapped") == UNKNOWN


def test_precedence_specific_before_broad() -> None:
    # "ai" sits before "blockchain": an AI-crypto name with both signals → ai.
    assert classify_focus_field(sector="ai_crypto", narrative="smart_contract_l1") == "ai"
