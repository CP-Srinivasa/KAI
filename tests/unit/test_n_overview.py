"""Tests für den 5-n-Assembler (Dali 2026-06-13).

Der Assembler ist die SSOT für die Zuordnung „welches n misst was" — diese
Tests pinnen Gate-Hervorhebung, EV-Gate (der Engpass), Suffizienz-Schwellen,
Status-Tags und die ehrliche None-Degradation, damit die UX-Falle (alle heißen
„resolved") nicht zurückkehrt.
"""

from __future__ import annotations

from app.observability.n_overview import RE_RUN_THRESHOLD, build_n_overview


def _build(**overrides):
    base = {
        "resolved_real": 79,
        "resolved_ledger_lines": 631,
        "total_resolved": 115,
        "paper_trades_all_time": 171,
        "resolved_directional_alerts": 391,
        "generator_executed": 5,
        "generator_threshold": 30,
        "generator_verdict": "INSUFFICIENT",
        "generator_ev_bps": -2.9061,
    }
    base.update(overrides)
    return build_n_overview(**base)


def test_gate_is_resolved_real_with_threshold_100() -> None:
    g = _build()["gate"]
    assert g["key"] == "resolved_real"
    assert g["value"] == 79
    assert g["threshold"] == RE_RUN_THRESHOLD == 100
    assert g["ratio_pct"] == 79.0
    assert g["sufficient"] is False


def test_gate_sufficient_at_threshold() -> None:
    assert _build(resolved_real=100)["gate"]["sufficient"] is True
    assert _build(resolved_real=99)["gate"]["sufficient"] is False


def test_ev_gate_is_executed_generator_trades() -> None:
    ev = _build()["ev_gate"]
    assert ev["key"] == "autonomous_generator_executed"
    assert ev["value"] == 5
    assert ev["threshold"] == 30
    assert ev["ratio_pct"] == round(100.0 * 5 / 30, 1)
    assert ev["sufficient"] is False
    assert ev["verdict"] == "INSUFFICIENT"
    assert ev["ev_after_costs_bps"] == -2.9  # gerundet


def test_ev_gate_threshold_falls_back_to_30() -> None:
    ev = _build(generator_threshold=None)["ev_gate"]
    assert ev["threshold"] == 30


def test_total_resolved_tag_warns_only_real_generator() -> None:
    others = {o["key"]: o for o in _build()["others"]}
    tr = others["total_resolved"]
    assert "nur 5 echter Generator" in tr["status_tag"]
    assert tr["status_tone"] == "warn"


def test_ledger_tag_is_diagnostic_with_non_real_count() -> None:
    others = {o["key"]: o for o in _build()["others"]}
    led = others["resolved_ledger_lines"]
    assert "552 Nicht-Real" in led["status_tag"]  # 631 - 79
    assert led["status_tone"] == "muted"


def test_d227_has_no_trading_gate_tag() -> None:
    others = {o["key"]: o for o in _build()["others"]}
    assert others["resolved_directional_alerts"]["status_tag"] == "kein Trading-Gate"


def test_paper_fills_reached_tag() -> None:
    others = {o["key"]: o for o in _build()["others"]}
    paper = others["paper_trades_all_time"]
    assert paper["status_tone"] == "pos"
    assert "✓" in paper["status_tag"]
    # Unter der Schwelle → warn + Fortschritt statt Haken.
    low = {o["key"]: o for o in _build(paper_trades_all_time=2)["others"]}
    assert low["paper_trades_all_time"]["status_tone"] == "warn"


def test_others_order_stable() -> None:
    keys = [o["key"] for o in _build()["others"]]
    assert keys == [
        "total_resolved",
        "resolved_ledger_lines",
        "resolved_directional_alerts",
        "paper_trades_all_time",
    ]


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
    assert out["ev_gate"]["value"] is None
    assert out["ev_gate"]["sufficient"] is False
    assert all(o["value"] is None for o in out["others"])
    others = {o["key"]: o for o in out["others"]}
    # Ohne Generator-Zahl keine erfundene „nur X"-Warnung.
    assert others["total_resolved"]["status_tag"] is None
    assert others["resolved_ledger_lines"]["status_tag"] == "Diagnose · kein Gate"


def test_trap_note_names_both_gates() -> None:
    note = _build()["trap_note"]
    assert "resolved_real" in note
    assert "Generator-Trades" in note
