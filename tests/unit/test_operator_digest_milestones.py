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
    assert od.v5_reminder_due(v5_day=6) is False


def test_v5_reminder_due_first_time_fires():
    assert od.v5_reminder_due(v5_day=7) is True


def test_v5_reminder_due_due_without_verdict_fires_daily():
    # Daily 07-10 V3: a due, sealed evaluation date must NOT go quiet for a
    # week — FÄLLIG nudges every day until a verdict exists. Day 20 with a
    # reminder sent yesterday still fires.
    assert od.v5_reminder_due(v5_day=20) is True
    assert od.v5_reminder_due(v5_day=20, verdict_exists=False) is True


def test_v5_reminder_due_verdict_exists_never_fires():
    # Once the question is answered (attested verdict report on record), the
    # nudge stops entirely — a truth platform does not nag about settled facts.
    assert od.v5_reminder_due(v5_day=20, verdict_exists=True) is False
    assert od.v5_reminder_due(v5_day=7, verdict_exists=True) is False


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


def test_edge_verdict_is_terminal_no_go_only():
    # Decisive negative suppresses the edge-report nudge...
    assert od.edge_verdict_is_terminal("NO_GO") is True
    assert od.edge_verdict_is_terminal("no_go") is True
    assert od.edge_verdict_is_terminal("  No_Go  ") is True


def test_edge_verdict_is_terminal_go_and_insufficient_not_terminal():
    # ...but a live edge (GO) must stay loud, and INSUFFICIENT still accumulates.
    assert od.edge_verdict_is_terminal("GO") is False
    assert od.edge_verdict_is_terminal("INSUFFICIENT") is False


def test_edge_verdict_is_terminal_tolerates_non_str():
    assert od.edge_verdict_is_terminal(None) is False
    assert od.edge_verdict_is_terminal("") is False
    assert od.edge_verdict_is_terminal(42) is False


def _write_verdict(tmp_path, hypothesis: str, ts: str) -> None:
    from datetime import datetime

    from app.research.verdict_report import build_verdict_report, write_verdict_report

    report = build_verdict_report(
        {"n": 1},
        hypothesis=hypothesis,
        prereg_id="testpreregid0000",
        verdict="NOT_MET at pre-registered criteria",
        params={},
        code_version="test",
        generated_at=datetime.fromisoformat(ts),
    )
    write_verdict_report(report, tmp_path)


def test_collect_v5_verdict_picks_latest_funding_family(tmp_path):
    _write_verdict(tmp_path, "directional_news_hedged_1d_drift", "2026-07-02T05:51:00+00:00")
    _write_verdict(tmp_path, "funding_premium_meanrev_1h", "2026-07-10T18:50:15+00:00")
    _write_verdict(tmp_path, "funding_carry_probe", "2026-06-20T10:00:00+00:00")
    got = od.collect_v5_verdict(tmp_path)
    assert got is not None
    assert got["hypothesis"] == "funding_premium_meanrev_1h"
    assert got["verdict"].startswith("NOT_MET")


def test_collect_v5_verdict_none_without_family_match(tmp_path):
    # News verdicts do NOT answer the funding/oi evidence question.
    _write_verdict(tmp_path, "directional_news_micro_1m", "2026-07-02T05:51:00+00:00")
    assert od.collect_v5_verdict(tmp_path) is None


def test_collect_v5_verdict_none_on_empty_or_missing_dir(tmp_path):
    assert od.collect_v5_verdict(tmp_path) is None
    assert od.collect_v5_verdict(tmp_path / "nope") is None


def test_milestone_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    assert od._load_milestone_state(p) == {}  # absent -> {}
    od._save_milestone_state({"v5": {"last_iso": "2026-07-01", "day": 20}}, p)
    assert od._load_milestone_state(p) == {"v5": {"last_iso": "2026-07-01", "day": 20}}


def test_load_milestone_state_tolerates_corrupt(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("not json", encoding="utf-8")
    assert od._load_milestone_state(p) == {}
