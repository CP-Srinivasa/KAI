"""Operator-Digest compose contract (Sprint S6).

Pure-function tests: the message is readable German, all sections appear, and
the evaluation milestones auto-trigger EXACTLY at their thresholds (V5 review
at day >= 7, edge report at real_resolved >= 25 — Operator-Vorgabe 2026-06-11).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import operator_digest as od  # noqa: E402


def _compose(**over) -> str:
    base = {
        "today": date(2026, 6, 12),
        "runtime": {
            "entry_mode": "disabled",
            "open_routes": ["premium_paper", "real_analysis_paper"],
            "contradictions": [],
        },
        "fills_by_source": {
            "telegram_premium_channel_approved": {"fills": 2, "closes": 1, "pnl_usd": 12.5},
            "real_analysis": {"fills": 1, "closes": 0, "pnl_usd": 0.0},
        },
        "bridge_stages": {"pending": 40, "filled": 2, "rejected_entry_mode": 3},
        "shadow_funnel": {
            "enabled": True,
            "seen": 1615,
            "eligible": 596,
            "injected": 20,
            "in_loop": {"shadow_candidate_written": 1, "priority_rejected": 19},
        },
        "shadow_report": {
            "real_resolved": 3,
            "canary_probe_resolved": 110,
            "primary_class": "INSUFFICIENT_DATA",
        },
        "d227": {"raw_events_count": 2319, "distinct_document_id_count": 924},
        "v5_freshness": {"funding": 4.2, "oi": 4.0},
        "v5_activated_on": date(2026, 6, 11),
    }
    base.update(over)
    return od.compose_digest_message(**base)


def test_all_sections_present_and_readable() -> None:
    msg = _compose()
    for marker in (
        "Operator-Digest",
        "Modus:",
        "Paper 24h:",
        "Premium-Bridge 24h:",
        "Shadow-Feed:",
        "D-227:",
        "V5-Evidence:",
        "Meilensteine:",
    ):
        assert marker in msg, f"missing section: {marker}"
    assert "telegram_premium_channel_approved: 2 Fills/1 Closes" in msg
    assert "real_resolved n=3/25" in msg


def test_v5_milestone_counts_days_before_threshold() -> None:
    msg = _compose(today=date(2026, 6, 17))  # Tag 6
    assert "V5-Messphase: Tag 6/7" in msg
    assert "V5-Auswertung FÄLLIG" not in msg


def test_v5_milestone_triggers_at_day_seven() -> None:
    msg = _compose(today=date(2026, 6, 18))  # Tag 7
    assert "V5-Auswertung FÄLLIG" in msg
    assert "trust-Entscheidung" in msg


def test_edge_milestone_below_threshold_shows_progress() -> None:
    msg = _compose(
        shadow_report={
            "real_resolved": 24,
            "canary_probe_resolved": 0,
            "primary_class": "INSUFFICIENT_DATA",
        }
    )
    assert "real_resolved n=24/25" in msg
    assert "EDGE-REPORT FÄLLIG" not in msg


def test_edge_milestone_triggers_at_25() -> None:
    msg = _compose(
        shadow_report={
            "real_resolved": 25,
            "canary_probe_resolved": 0,
            "primary_class": "INSUFFICIENT_DATA",
        }
    )
    assert "EDGE-REPORT FÄLLIG" in msg
    assert "n=25" in msg


def test_contradiction_is_loud() -> None:
    msg = _compose(
        runtime={
            "entry_mode": "paper_premium_limited",
            "open_routes": [],
            "contradictions": ["fastlane_enabled_in_limited_paper_mode"],
        }
    )
    assert "KONTRADIKTION" in msg


def test_degrades_honestly_without_data() -> None:
    msg = _compose(
        fills_by_source={},
        bridge_stages={},
        shadow_funnel=None,
        shadow_report={"error": "cli timeout"},
        d227={"error": "boom"},
        v5_freshness={"funding": None, "oi": None},
    )
    assert "keine Fills/Closes" in msg
    assert "aus / noch kein armed Tick" in msg
    assert "shadow-report nicht lesbar" in msg
    assert "Cache fehlt" in msg


def test_message_respects_telegram_limit() -> None:
    huge = {f"source_{i}": {"fills": i, "closes": i, "pnl_usd": 1.0} for i in range(400)}
    msg = _compose(fills_by_source=huge)
    assert len(msg) <= 4001
    assert "gekürzt" in msg
