"""Pure-function tests for threshold-triggered milestone reminders (2026-07-01).

The daily digest used to repeat a milestone "FÄLLIG" every single day once its
threshold was crossed — zero-information noise. These cover the state-delta /
weekly-cadence trigger that replaces daily nagging (ADR-0012 attention-hygiene).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import operator_digest as od  # noqa: E402


def test_days_between_basic_and_tolerant():
    assert od._days_between("2026-06-20", "2026-07-01") == 11
    assert od._days_between("2026-07-01T12:00:00+00:00", "2026-07-01") == 0
    assert od._days_between(None, "2026-07-01") is None
    assert od._days_between("junk", "2026-07-01") is None


def test_v5_reminder_due_below_window_never_fires():
    assert od.v5_reminder_due(v5_day=6, state={}, today_iso="2026-07-01") is False


def test_v5_reminder_due_first_time_fires():
    assert od.v5_reminder_due(v5_day=7, state={}, today_iso="2026-07-01") is True


def test_v5_reminder_due_suppressed_within_cadence():
    state = {"last_iso": "2026-06-28"}
    assert od.v5_reminder_due(v5_day=20, state=state, today_iso="2026-07-01") is False


def test_v5_reminder_due_refires_after_cadence():
    state = {"last_iso": "2026-06-20"}
    assert od.v5_reminder_due(v5_day=20, state=state, today_iso="2026-07-01") is True


def test_edge_reminder_due_below_gate_never_fires():
    assert (
        od.edge_reminder_due(
            gen_resolved=29, gate=30, state={}, today_iso="2026-07-01", min_delta=15
        )
        is False
    )


def test_edge_reminder_due_first_crossing_fires():
    assert (
        od.edge_reminder_due(
            gen_resolved=30, gate=30, state={}, today_iso="2026-07-01", min_delta=15
        )
        is True
    )


def test_edge_reminder_due_suppressed_without_delta_within_cadence():
    state = {"last_iso": "2026-07-01", "last_n": 74}
    assert (
        od.edge_reminder_due(
            gen_resolved=74, gate=30, state=state, today_iso="2026-07-01", min_delta=15
        )
        is False
    )


def test_edge_reminder_due_fires_on_material_delta():
    state = {"last_iso": "2026-07-01", "last_n": 74}
    assert (
        od.edge_reminder_due(
            gen_resolved=90, gate=30, state=state, today_iso="2026-07-01", min_delta=15
        )
        is True
    )


def test_edge_reminder_due_refires_after_cadence_even_without_delta():
    state = {"last_iso": "2026-06-20", "last_n": 74}
    assert (
        od.edge_reminder_due(
            gen_resolved=75, gate=30, state=state, today_iso="2026-07-01", min_delta=15
        )
        is True
    )


def test_milestone_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    assert od._load_milestone_state(p) == {}  # absent -> {}
    od._save_milestone_state({"v5": {"last_iso": "2026-07-01", "day": 20}}, p)
    assert od._load_milestone_state(p) == {"v5": {"last_iso": "2026-07-01", "day": 20}}


def test_load_milestone_state_tolerates_corrupt(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("not json", encoding="utf-8")
    assert od._load_milestone_state(p) == {}
