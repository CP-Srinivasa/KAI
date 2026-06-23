"""Operator-Digest compose contract (Sprint S6).

Pure-function tests: the message is readable German, all sections appear, and
the evaluation milestones auto-trigger EXACTLY at their thresholds (V5 review
at day >= 7, edge report when the autonomous_generator cohort reaches the
Edge-Gate min_resolved — Operator-Vorgabe 2026-06-14, switched off shadow-n).
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
            "real_resolved": 86,
            "canary_probe_resolved": 110,
            "primary_class": "INSUFFICIENT_DATA",
        },
        "generator_edge": {
            "min_resolved": 30,
            "autonomous_generator_resolved": 6,
            "autonomous_generator_verdict": "INSUFFICIENT",
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
    # Edge-Meilenstein hängt jetzt an autonomous_generator (n=6/30), shadow-n
    # bleibt als Kontext sichtbar.
    assert "autonomous_generator resolved n=6/30" in msg
    assert "shadow-resolved n=86" in msg
    assert "EDGE-REPORT FÄLLIG" not in msg


def test_v5_milestone_counts_days_before_threshold() -> None:
    msg = _compose(today=date(2026, 6, 17))  # Tag 6
    assert "V5-Messphase: Tag 6/7" in msg
    assert "V5-Auswertung FÄLLIG" not in msg


def test_v5_milestone_triggers_at_day_seven() -> None:
    msg = _compose(today=date(2026, 6, 18))  # Tag 7
    assert "V5-Auswertung FÄLLIG" in msg
    assert "trust-Entscheidung" in msg


def test_edge_milestone_below_threshold_shows_progress() -> None:
    # Hohe shadow-n darf NICHT triggern, solange die ausgeführten
    # Generator-Closes das Gate nicht erreichen (Kern der 06-14-Umstellung).
    msg = _compose(
        shadow_report={"real_resolved": 86, "primary_class": "INSUFFICIENT_DATA"},
        generator_edge={
            "min_resolved": 30,
            "autonomous_generator_resolved": 29,
            "autonomous_generator_verdict": "INSUFFICIENT",
        },
    )
    assert "autonomous_generator resolved n=29/30" in msg
    assert "Verdict: INSUFFICIENT" in msg
    assert "shadow-resolved n=86" in msg
    assert "EDGE-REPORT FÄLLIG" not in msg


def test_edge_milestone_triggers_at_gate() -> None:
    msg = _compose(
        shadow_report={"real_resolved": 90, "primary_class": "INSUFFICIENT_DATA"},
        generator_edge={
            "min_resolved": 30,
            "autonomous_generator_resolved": 30,
            "autonomous_generator_verdict": "PASS",
        },
    )
    assert "EDGE-REPORT FÄLLIG" in msg
    assert "n=30≥30" in msg
    assert "shadow-resolved n=90" in msg


def test_edge_milestone_degrades_when_generator_edge_unreadable() -> None:
    msg = _compose(
        shadow_report={"real_resolved": 86},
        generator_edge={"error": "cli timeout"},
    )
    assert "generator-edge nicht lesbar (cli timeout)" in msg
    assert "EDGE-REPORT FÄLLIG" not in msg


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
        generator_edge={"error": "cli timeout"},
        d227={"error": "boom"},
        v5_freshness={"funding": None, "oi": None},
    )
    assert "keine Fills/Closes" in msg
    assert "aus / noch kein armed Tick" in msg
    assert "generator-edge nicht lesbar" in msg
    assert "shadow-n n/a" in msg
    assert "Cache fehlt" in msg


def test_message_respects_telegram_limit() -> None:
    huge = {f"source_{i}": {"fills": i, "closes": i, "pnl_usd": 1.0} for i in range(400)}
    msg = _compose(fills_by_source=huge)
    assert len(msg) <= 4001
    assert "gekürzt" in msg


def test_promotion_gate_line_allowed_and_blocked() -> None:
    msg = _compose(promotion={"target": "paper", "allowed": True, "reason_codes": []})
    assert "Promotion-Gate (→paper):* ALLOWED" in msg
    msg = _compose(
        promotion={
            "target": "paper",
            "allowed": False,
            "reason_codes": ["UNREALIZED_BLEED", "DATA_UNKNOWN"],
        }
    )
    assert "Promotion-Gate (→paper):* BLOCKED — UNREALIZED_BLEED, DATA_UNKNOWN" in msg


def test_weekly_d227_review_only_on_mondays_with_sufficient_n() -> None:
    d227 = {
        "raw_events_count": 100,
        "distinct_document_id_count": 50,
        "hit_miss_by_block_reason": [
            {
                "block_reason": "bearish_directional_disabled",
                "hit": 6,
                "miss": 4,
                "resolved": 10,
                "precision_pct": 60.0,
            },
            {
                "block_reason": "not_actionable",
                "hit": 1,
                "miss": 19,
                "resolved": 20,
                "precision_pct": 5.0,
            },
            {
                "block_reason": "tiny_bucket",
                "hit": 1,
                "miss": 1,
                "resolved": 2,
                "precision_pct": 50.0,
            },
        ],
    }
    # Montag 2026-06-15 → Review-Sektion, n>=5-Buckets, größte zuerst, tiny raus
    msg = _compose(today=date(2026, 6, 15), d227=d227)
    assert "D-227-Wochenreview" in msg
    assert msg.index("not_actionable") < msg.index("bearish_directional_disabled")
    assert "tiny_bucket" not in msg
    # Precision-Zeile ist ehrlicher Caveat, KEIN Gate-Review-Trigger
    # (blocked-cohort-vetting 2026-06-22: ~28h-Batch-Horizont, kein handelbarer Edge).
    assert "Kandidat für Gate-Review" not in msg
    assert "KEIN handelbarer Edge" in msg
    # Dienstag → keine Review-Sektion
    msg = _compose(today=date(2026, 6, 16), d227=d227)
    assert "D-227-Wochenreview" not in msg


def test_telegram_safe_escapes_markdown_breaking_entities() -> None:
    # Unterstriche (paper_learning/autonomous_generator) und [..]-Klammern brechen
    # sonst den Telegram-Markdown-Parser ("can't parse entities"); *bold* und
    # `code` (strukturell) bleiben unangetastet.
    safe = od._telegram_safe(
        "*Modus:* paper_learning · autonomous_generator n=6/30 "
        "[shadow-resolved n=88] `trading edge-report`"
    )
    assert r"paper\_learning" in safe
    assert r"autonomous\_generator" in safe
    assert r"\[shadow-resolved n=88\]" in safe
    assert "*Modus:*" in safe  # bold-Delimiter unverändert
    assert "`trading edge-report`" in safe  # code-Span unverändert


def test_edge_discovery_no_run_is_shown_honestly() -> None:
    msg = _compose()  # edge_discovery defaults to None
    assert "Edge-Discovery:" in msg
    assert "noch kein Lauf" in msg


def test_edge_discovery_no_edge_verdict() -> None:
    msg = _compose(
        edge_discovery={
            "available": True,
            "timeframe": "1h",
            "lookback_days": 180,
            "n_symbols": 5,
            "n_hypotheses": 6,
            "survivors": 0,
            "cumulative_tested": 6,
            "best_name": "adx_trend",
            "best_mean_bps": -15.1,
        }
    )
    assert "Edge-Discovery:* 1h/180d, 5 Symbole · 0/6 Survivors · 6 Configs kumulativ" in msg
    assert "kein robuster Edge — beste Regel adx_trend -15.1bps netto" in msg
    assert "KANDIDAT" not in msg


def test_edge_discovery_candidate_is_flagged() -> None:
    msg = _compose(
        edge_discovery={
            "available": True,
            "timeframe": "4h",
            "lookback_days": 90,
            "n_symbols": 3,
            "n_hypotheses": 6,
            "survivors": 2,
            "cumulative_tested": 12,
            "best_name": "macd_trend",
            "best_mean_bps": 8.3,
        }
    )
    assert "2/6 Survivors" in msg
    assert "KANDIDAT(EN) PRÜFEN" in msg
    assert "macd_trend +8.3bps netto" in msg


def test_edge_discovery_degrades_on_error() -> None:
    msg = _compose(edge_discovery={"error": "boom"})
    assert "Edge-Discovery:* nicht lesbar — boom" in msg


def test_collect_edge_discovery_reads_latest_run(tmp_path: Path) -> None:
    import json

    (tmp_path / "edge_search_20260101T000000Z.json").write_text(
        json.dumps(
            {
                "timeframe": "1h",
                "lookback_days": 180,
                "symbols": [{}, {}],
                "hypotheses": [{"name": "a", "symbols_survived": 0, "mean_net_bps": -20.0}],
                "hypotheses_tested_cumulative": 6,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "edge_search_20260201T000000Z.json").write_text(
        json.dumps(
            {
                "timeframe": "4h",
                "lookback_days": 90,
                "symbols": [{}],
                "hypotheses": [
                    {"name": "b", "symbols_survived": 1, "mean_net_bps": 3.0},
                    {"name": "c", "symbols_survived": 0, "mean_net_bps": -1.0},
                ],
                "hypotheses_tested_cumulative": 12,
            }
        ),
        encoding="utf-8",
    )
    out = od.collect_edge_discovery(research_dir=tmp_path)
    assert out["available"] is True
    assert out["timeframe"] == "4h"  # latest by filename
    assert out["survivors"] == 1
    assert out["best_name"] == "b"  # highest mean_net_bps
    assert out["cumulative_tested"] == 12


def test_collect_edge_discovery_no_runs(tmp_path: Path) -> None:
    assert od.collect_edge_discovery(research_dir=tmp_path) == {"available": False}
