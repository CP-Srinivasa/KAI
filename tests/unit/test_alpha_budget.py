"""Familienweites α-Budget über die pra-registrierte Claim-Familie.

Befund 2026-08-17: ``prereg_gate.check_gate`` prüft jeden Claim ISOLIERT gegen
sein versiegeltes ``p_min``. ``benjamini_hochberg`` existiert, wird aber nur im
Discovery-Pfad benutzt, nie im Prä-Reg-Pfad. Werden mehrere Claims parallel
gefahren, ist die Wahrscheinlichkeit für mindestens einen Zufalls-PASS deutlich
höher als das einzelne α suggeriert.

Die versiegelten Gates werden dadurch NICHT verändert — das wäre eine
nachträgliche Kriteriumsänderung. Das Budget ist eine nicht-gatende Auskunft,
die einen PASS einordnet.

Gemessener Live-Stand (Pi, 2026-08-17): 19 Prä-Reg-Zeilen, davon nur 4 mit
maschinellem ``p_min`` (m=4, P(>=1 Falsch-PASS) = 26,9 %). Die im Audit
genannten "m=12 / 71,8 %" waren zu hoch gegriffen.
"""

from __future__ import annotations

import pytest

from app.research.alpha_budget import family_alpha_budget

_CLAIMS = [
    {"prereg_id": "a1", "name": "drift_v2", "gate": {"p_min": 0.95}},
    {"prereg_id": "a2", "name": "tech_precision", "gate": {"p_min": 0.95}},
    {"prereg_id": "a3", "name": "h2", "gate": {"p_min": 0.90}},
    {"prereg_id": "a4", "name": "h2_v2", "gate": {"p_min": 0.90}},
    {"prereg_id": "b1", "name": "c1_demand", "gate": None},
    {"prereg_id": "b2", "name": "analyst_probe"},
]


def test_family_counts_only_machine_gated_claims() -> None:
    rep = family_alpha_budget(_CLAIMS, resolved_ids=set())

    assert rep["m_registered"] == 6
    assert rep["m_machine_gated"] == 4
    # Ein Claim ohne maschinelles p_min ist nicht BH-fähig und darf die
    # Familienrechnung weder verwässern noch stillschweigend fehlen.
    assert rep["claims_without_machine_gate"] == ["c1_demand", "analyst_probe"]


def test_familywise_error_matches_hand_computed_product() -> None:
    rep = family_alpha_budget(_CLAIMS, resolved_ids=set())

    # 1 - (0.95 * 0.95 * 0.90 * 0.90) = 0.269...
    assert rep["familywise_error_upper_bound"] == pytest.approx(0.2690, abs=5e-4)


def test_open_family_excludes_already_resolved_claims() -> None:
    rep = family_alpha_budget(_CLAIMS, resolved_ids={"a1", "a3"})

    assert rep["m_open"] == 2
    # 1 - (0.95 * 0.90) = 0.145
    assert rep["familywise_error_open"] == pytest.approx(0.1450, abs=5e-4)


def test_empty_family_is_zero_risk_not_a_crash() -> None:
    rep = family_alpha_budget([], resolved_ids=set())

    assert rep["m_machine_gated"] == 0
    assert rep["familywise_error_upper_bound"] == 0.0
    assert rep["bh_threshold_for_next_pass"] is None


def test_bh_threshold_is_the_strictest_rank_one_bar() -> None:
    """A single PASS among m claims must clear the rank-1 BH bar, not its own."""
    rep = family_alpha_budget(_CLAIMS, resolved_ids=set())

    # Strengstes α der Familie = 0.05; rank-1-Schranke = (1/m)*alpha = 0.05/4.
    assert rep["bh_threshold_for_next_pass"] == pytest.approx(0.0125, abs=1e-6)
    # Als p_positive gelesen: ein erster PASS müsste 1-0.0125 erreichen.
    assert rep["bh_p_positive_for_next_pass"] == pytest.approx(0.9875, abs=1e-6)


def test_report_carries_the_non_gating_disclaimer() -> None:
    rep = family_alpha_budget(_CLAIMS, resolved_ids=set())

    assert rep["gating"] is False
    assert "versiegelt" in rep["note"].lower() or "sealed" in rep["note"].lower()
