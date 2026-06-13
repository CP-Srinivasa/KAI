"""Tests für den 5-n-Assembler (Dali 2026-06-13).

Der Assembler ist die SSOT für die Zuordnung „welches n misst was" — diese
Tests pinnen Gate-Hervorhebung, Suffizienz-Schwelle, Nicht-Real-Note und die
ehrliche None-Degradation, damit die UX-Falle (alle heißen „resolved") nicht
zurückkehrt.
"""

from __future__ import annotations

from app.observability.n_overview import RE_RUN_THRESHOLD, build_n_overview


def _build(**overrides):
    base = {
        "resolved_real": 77,
        "resolved_ledger_lines": 629,
        "total_resolved": 118,
        "paper_trades_all_time": 164,
        "resolved_directional_alerts": 390,
    }
    base.update(overrides)
    return build_n_overview(**base)


def test_gate_is_resolved_real_with_threshold_100() -> None:
    out = _build()
    assert out["gate"]["key"] == "resolved_real"
    assert out["gate"]["value"] == 77
    assert out["gate"]["threshold"] == RE_RUN_THRESHOLD == 100
    assert out["gate"]["ratio_pct"] == 77.0
    assert out["gate"]["sufficient"] is False


def test_gate_sufficient_at_threshold() -> None:
    assert _build(resolved_real=100)["gate"]["sufficient"] is True
    assert _build(resolved_real=99)["gate"]["sufficient"] is False
    assert _build(resolved_real=140)["gate"]["ratio_pct"] == 140.0


def test_others_carry_the_four_secondary_n_in_order() -> None:
    keys = [o["key"] for o in _build()["others"]]
    assert keys == [
        "total_resolved",
        "resolved_ledger_lines",
        "resolved_directional_alerts",
        "paper_trades_all_time",
    ]


def test_non_real_note_is_ledger_minus_real() -> None:
    others = {o["key"]: o for o in _build()["others"]}
    assert others["resolved_ledger_lines"]["note"] == "552 Nicht-Real (geskippt)"


def test_none_values_degrade_without_fabrication() -> None:
    out = build_n_overview(
        resolved_real=None,
        resolved_ledger_lines=None,
        total_resolved=None,
        paper_trades_all_time=None,
        resolved_directional_alerts=None,
    )
    assert out["gate"]["value"] is None
    assert out["gate"]["ratio_pct"] is None
    assert out["gate"]["sufficient"] is False
    assert all(o["value"] is None for o in out["others"])
    # Note bleibt None, wenn Ledger/Real fehlen (keine erfundene Differenz).
    others = {o["key"]: o for o in out["others"]}
    assert others["resolved_ledger_lines"]["note"] is None


def test_trap_note_present_and_mentions_gate() -> None:
    note = _build()["trap_note"]
    assert "resolved_real" in note
    assert "#167" in note
