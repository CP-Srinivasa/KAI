"""Operator-Digest compose contract (Sprint S6).

Pure-function tests: the message is readable German, all sections appear, and
the evaluation milestones auto-trigger EXACTLY at their thresholds (V5 review
at day >= 7, edge report when the autonomous_generator cohort reaches the
Edge-Gate min_resolved — Operator-Vorgabe 2026-06-14, switched off shadow-n).
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

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


# ── Threshold-getriggerte Meilenstein-Nudges (2026-07-01) ────────────────────
# Ohne milestone_state feuert alles wie früher (Backward-Compat, oben getestet).
# MIT State feuert der FÄLLIG-Nudge nur bei materieller Änderung / Wochenkadenz.


def test_v5_faellig_nudges_daily_even_when_recently_reminded() -> None:
    # Daily 07-10 V3: FÄLLIG nudges EVERY day until a verdict exists. The
    # weekly cadence let the due V5 eval go quiet for 7 days while the eval
    # harness was silently broken — a due, sealed date must stay loud.
    state = {"v5": {"last_iso": "2026-06-20", "day": 9}}
    msg = _compose(today=date(2026, 6, 21), milestone_state=state)
    assert "V5-Auswertung FÄLLIG" in msg
    assert "V5-Auswertung ruht" not in msg
    assert state["v5"]["last_iso"] == "2026-06-21"  # advanced on fire


def test_v5_faellig_refires_after_cadence_and_advances_state() -> None:
    state = {"v5": {"last_iso": "2026-06-01", "day": 0}}
    msg = _compose(today=date(2026, 6, 21), milestone_state=state)
    assert "V5-Auswertung FÄLLIG" in msg
    assert state["v5"]["last_iso"] == "2026-06-21"  # advanced on fire


def test_v5_verdict_on_record_settles_milestone() -> None:
    # An attested verdict answers the question — no nudge, state the verdict.
    msg = _compose(
        today=date(2026, 7, 10),
        v5_verdict={
            "hypothesis": "funding_premium_meanrev_1h",
            "verdict": "NOT_MET at pre-registered criteria (n=308)",
            "generated_at_utc": "2026-07-10T18:50:15+00:00",
            "prereg_id": "f676bcf5a7a1bfb6",
        },
    )
    assert "V5-Auswertung FÄLLIG" not in msg
    assert "V5-Verdikt liegt vor" in msg
    assert "NOT_MET" in msg
    assert "Prä-Registrierung" in msg


# The FÄLLIG/ruht cadence only governs a NON-terminal verdict (GO). A terminal
# NO_GO short-circuits to a "kein Report-Nudge" line — covered separately below.
def test_edge_faellig_suppressed_without_material_delta() -> None:
    state = {"edge": {"last_iso": "2026-06-12", "last_n": 74}}
    msg = _compose(
        generator_edge={
            "min_resolved": 30,
            "autonomous_generator_resolved": 74,
            "autonomous_generator_verdict": "GO",
        },
        milestone_state=state,
    )
    assert "EDGE-REPORT FÄLLIG" not in msg
    assert "Edge-Report ruht" in msg
    assert state["edge"]["last_n"] == 74  # unchanged


def test_edge_faellig_refires_on_material_delta_and_advances_state() -> None:
    state = {"edge": {"last_iso": "2026-06-12", "last_n": 74}}
    msg = _compose(
        generator_edge={
            "min_resolved": 30,
            "autonomous_generator_resolved": 90,  # +16 >= gate//2 (15)
            "autonomous_generator_verdict": "GO",
        },
        milestone_state=state,
    )
    assert "EDGE-REPORT FÄLLIG" in msg
    assert "n=90≥30" in msg
    assert state["edge"]["last_n"] == 90  # advanced on fire


def test_edge_terminal_verdict_suppresses_nudge_and_ruht() -> None:
    # A decisive NO_GO at/above the gate: state the terminal verdict once, and
    # emit NEITHER the FÄLLIG nudge NOR the "ruht … Nudge bei +N Closes" cadence
    # line — the report was already run and NO_GO IS its answer (V2 truth-härtung).
    state = {"edge": {"last_iso": "2026-06-12", "last_n": 74}}
    msg = _compose(
        generator_edge={
            "min_resolved": 30,
            "autonomous_generator_resolved": 74,
            "autonomous_generator_verdict": "NO_GO",
        },
        milestone_state=state,
    )
    assert "Edge-Verdikt: NO_GO (terminal, n=74≥30)" in msg
    assert "EDGE-REPORT FÄLLIG" not in msg
    assert "Edge-Report ruht" not in msg


def test_edge_terminal_verdict_suppresses_even_with_material_delta() -> None:
    # Even a large NEW-closes delta must not re-fire once the verdict is terminal.
    state = {"edge": {"last_iso": "2026-06-12", "last_n": 74}}
    msg = _compose(
        generator_edge={
            "min_resolved": 30,
            "autonomous_generator_resolved": 90,
            "autonomous_generator_verdict": "NO_GO",
        },
        milestone_state=state,
    )
    assert "Edge-Verdikt: NO_GO (terminal, n=90≥30)" in msg
    assert "EDGE-REPORT FÄLLIG" not in msg


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


# ── source-lifecycle digest hook ───────────────────────────────────────────


def test_source_lifecycle_line_present_with_data() -> None:
    msg = _compose(
        source_lifecycle={
            "available": True,
            "counts": {"ranked": 12, "provisional": 11, "pinned": 0, "rotation_flagged": 6},
            "top_name": "thedefiant",
            "top_wilson": 0.63,
            "top_provisional": True,
        }
    )
    assert "Quellen-Lifecycle:" in msg
    assert "12 gerankt" in msg
    assert "6 Rotation-Flag" in msg
    assert "thedefiant" in msg
    assert "provisorisch" in msg


def test_source_lifecycle_line_honest_when_missing() -> None:
    assert "noch kein Ranking" in _compose(source_lifecycle=None)


def test_source_lifecycle_line_degrades_on_error() -> None:
    msg = _compose(source_lifecycle={"error": "boom"})
    assert "Quellen-Lifecycle:" in msg
    assert "nicht lesbar" in msg


def test_collect_source_lifecycle_reads_file(tmp_path: Path) -> None:
    p = tmp_path / "source_ranking.json"
    p.write_text(
        json.dumps(
            {
                "counts": {"ranked": 3, "provisional": 3},
                "ranked": [{"source_name": "x", "wilson_lower_95": 0.5, "provisional": False}],
            }
        ),
        encoding="utf-8",
    )
    out = od.collect_source_lifecycle(ranking_path=p)
    assert out["available"] is True
    assert out["counts"]["ranked"] == 3
    assert out["top_name"] == "x"
    assert out["top_provisional"] is False


def test_collect_source_lifecycle_unavailable_when_missing(tmp_path: Path) -> None:
    assert od.collect_source_lifecycle(ranking_path=tmp_path / "nope.json") == {"available": False}


# ── source-discovery digest hook ────────────────────────────────────────────


def test_source_discovery_line_present_when_armed() -> None:
    msg = _compose(
        source_discovery={
            "available": True,
            "discovery_enabled": True,
            "scout_enabled": True,
            "proposals": 11,
            "probation": 11,
            "near_graduation": 2,
            "last_mode": "live",
            "last_onboarded": 11,
            "last_swaps": 0,
        }
    )
    assert "Quellen-Discovery (scharf):" in msg
    assert "11 in Probation" in msg
    # V5 (Daily 07-10): „nahe Graduation" war eine Vanity-Metrik — Graduation
    # ist unter ADR-0012 strukturell geschlossen (kein Edge als Ziel).
    assert "nahe Graduation" not in msg
    assert "Graduation strukturell geschlossen" in msg
    assert "11 Vorschläge" in msg
    assert "11 onboardet" in msg


def test_source_discovery_line_frozen_label_when_disabled() -> None:
    # Seed-Freeze (V5): discovery_enabled=false heißt eingefroren, nicht
    # „Beobachtung" — die Zeile muss den Freeze-Zustand ehrlich benennen.
    msg = _compose(
        source_discovery={
            "available": True,
            "discovery_enabled": False,
            "scout_enabled": False,
            "proposals": 0,
            "probation": 28,
            "near_graduation": 11,
            "last_mode": "live",
            "last_onboarded": 0,
            "last_swaps": 0,
        }
    )
    assert "Quellen-Discovery (eingefroren — Seed-Freeze):" in msg
    assert "28 in Probation" in msg
    assert "Graduation strukturell geschlossen" in msg


def test_source_discovery_line_honest_when_unavailable() -> None:
    assert "kein Lauf / Loop aus" in _compose()  # source_discovery default None


def test_collect_source_discovery_reads_files(tmp_path: Path) -> None:
    (tmp_path / "proposals.jsonl").write_text(
        json.dumps({"url": "https://a.com/feed", "provider": "a"}) + "\n", encoding="utf-8"
    )
    (tmp_path / "runs.jsonl").write_text(
        json.dumps({"mode": "live", "onboarded": 3, "swaps_executed": 1}) + "\n", encoding="utf-8"
    )
    (tmp_path / "state.json").write_text(json.dumps({"runs": {"a": 4, "b": 1}}), encoding="utf-8")
    out = od.collect_source_discovery(
        proposals_path=tmp_path / "proposals.jsonl",
        runs_path=tmp_path / "runs.jsonl",
        state_path=tmp_path / "state.json",
    )
    assert out["available"] is True
    assert out["proposals"] == 1
    assert out["probation"] == 2
    assert out["near_graduation"] == 1  # only "a" (runs 4 >= 3)
    assert out["last_onboarded"] == 3
    assert out["last_swaps"] == 1


def test_collect_source_discovery_empty_when_no_files(tmp_path: Path) -> None:
    out = od.collect_source_discovery(
        proposals_path=tmp_path / "no.jsonl",
        runs_path=tmp_path / "no.jsonl",
        state_path=tmp_path / "no.json",
    )
    assert out["available"] is False


# ── Truth-Lint-Zeile (Invariant-Registry, Operator-Direktive 07-11) ──────────


def test_truth_lint_line_absent_run_is_honest() -> None:
    assert "Truth-Lint:* noch kein Lauf" in _compose()  # default None


def test_truth_lint_line_ok_when_clean() -> None:
    # Abdeckung ehrlich: Registry-Zahl darf nie "11 Invarianten schützen"
    # suggerieren — aktiv/geplant/Prozent werden explizit ausgewiesen.
    msg = _compose(
        truth_lint={
            "violations": [],
            "max_severity": None,
            "registry_active": 5,
            "registry_total": 11,
            "registry_planned": 6,
        }
    )
    assert "Registry 11" in msg
    assert "aktiv 5" in msg
    assert "geplant 6" in msg
    assert "aktive Abdeckung 45%" in msg


def test_truth_lint_critical_blocks_loudly() -> None:
    msg = _compose(
        truth_lint={
            "violations": [
                {
                    "invariant_id": "TL-011",
                    "severity": "CRITICAL",
                    "message": "Attestation-Hash stimmt nicht mit Payload überein",
                }
            ],
            "max_severity": "CRITICAL",
            "registry_active": 5,
            "registry_total": 11,
        }
    )
    # Kein Overclaim: Gate ist verfügbar, aber noch nicht systemweit enforced.
    assert "Evidence-Claim-Block fällig" in msg
    assert "noch nicht systemweit verdrahtet" in msg
    assert "TL-011" in msg


def test_truth_lint_warning_reads_degraded() -> None:
    msg = _compose(
        truth_lint={
            "violations": [
                {"invariant_id": "TL-002", "severity": "WARNING", "message": "Preisband"}
            ],
            "max_severity": "WARNING",
            "registry_active": 5,
            "registry_total": 11,
        }
    )
    assert "Status DEGRADED" in msg


def test_truth_lint_info_only_reads_ok_but_discloses_findings() -> None:
    """TL-004/TL-008-Zweiteilung (Audit 2026-08-06): nur eingefrorene
    Alt-Befunde ⇒ Status NICHT degraded, Befunde bleiben wörtlich sichtbar."""
    msg = _compose(
        truth_lint={
            "violations": [
                {"invariant_id": "TL-004", "severity": "INFO", "message": "eingefroren"},
                {"invariant_id": "TL-008", "severity": "INFO", "message": "legacy"},
            ],
            "max_severity": "INFO",
            "registry_active": 6,
            "registry_total": 12,
        }
    )
    assert "Status DEGRADED" not in msg
    assert "eingefrorene Alt-Befunde offengelegt" in msg
    assert "2 Verletzung(en)" in msg
    assert "TL-004" in msg
    assert "TL-008" in msg


# ── Truth-Anchor-Zeile (Voll-Audit 2026-08-06, WP7 / Blindstelle #2) ─────────
# Die Kette wuchs bisher ohne jede Digest-Sichtbarkeit — ein still nie mehr
# verankerter Tip fiel niemandem auf.


def test_truth_anchor_line_healthy() -> None:
    msg = _compose(
        truth_anchor={
            "available": True,
            "tip_seq": 76,
            "records": 76,
            "chain_ok": True,
            "tip_anchored": True,
        }
    )
    assert "Truth-Anchor:* seq 76" in msg
    assert "Kette ok" in msg
    assert "Tip OTS-verankert" in msg


def test_truth_anchor_line_broken_chain_and_unanchored_are_loud() -> None:
    msg = _compose(
        truth_anchor={
            "available": True,
            "tip_seq": 76,
            "records": 40,
            "chain_ok": False,
            "tip_anchored": False,
        }
    )
    assert "KETTE GEBROCHEN" in msg
    assert "NICHT verankert" in msg


def test_truth_anchor_collector_failure_is_honest() -> None:
    msg = _compose(truth_anchor=None)
    assert "Truth-Anchor:* Status nicht lesbar" in msg


def test_collect_truth_anchor_reads_real_ledger(tmp_path: Path) -> None:
    from app.truth.ledger import append_attestation

    ledger = tmp_path / "truth.jsonl"
    append_attestation("verdict", "s-1", {"a": 1}, path=ledger, mirror_audit=False)
    rec2 = append_attestation("verdict", "s-2", {"a": 2}, path=ledger, mirror_audit=False)
    proofs = tmp_path / "proofs"
    proofs.mkdir()

    unanchored = od.collect_truth_anchor(ledger_path=ledger, proofs_dir=proofs)
    assert unanchored is not None
    assert unanchored["available"] is True
    assert unanchored["tip_seq"] == 2
    assert unanchored["chain_ok"] is True
    assert unanchored["tip_anchored"] is False

    (proofs / f"truthledger-{rec2['record_hash'][:16]}.ots").write_bytes(b"proof")
    anchored = od.collect_truth_anchor(ledger_path=ledger, proofs_dir=proofs)
    assert anchored is not None and anchored["tip_anchored"] is True

    missing = od.collect_truth_anchor(ledger_path=tmp_path / "nope.jsonl", proofs_dir=proofs)
    assert missing == {"available": False}


def test_collect_truth_lint_reads_last_run(tmp_path: Path) -> None:
    p = tmp_path / "truth_lint_report.jsonl"
    p.write_text(
        json.dumps({"ts_utc": "1", "violations": []})
        + "\n"
        + json.dumps({"ts_utc": "2", "violations": [], "max_severity": None})
        + "\n",
        encoding="utf-8",
    )
    got = od.collect_truth_lint(p)
    assert got is not None and got["ts_utc"] == "2"
    assert od.collect_truth_lint(tmp_path / "missing.jsonl") is None


# ── Paper-24h: Tier-Gewinne sichtbar + TL-003-fail-closed (Plan 08-08, PR-1) ─
# Getierte Multi-Target-Gewinner enden als position_partial_closed (der letzte
# Tier schliesst die Restmenge dort) — der Digest zaehlte nur position_closed:
# Verluste voll, getierte Gewinne unsichtbar. Und: Fallback auf KUMULATIVES
# realized_pnl_usd war die TL-003-Falle (in paper_quality_snapshot laengst
# gefixt, hier noch offen).


def _write_audit(tmp_path: Path, rows: list[dict]) -> None:
    (tmp_path / "paper_execution_audit.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_paper_fills_24h_counts_partial_closes_and_their_pnl(tmp_path, monkeypatch) -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    ts = now.isoformat()
    monkeypatch.setattr(od, "_ARTIFACTS", tmp_path)
    _write_audit(
        tmp_path,
        [
            # Tier-Leiter: 2 Teil-Gewinne + finaler Tier-Close als partial.
            {
                "event_type": "position_partial_closed",
                "symbol": "ETH/USDT",
                "signal_source": "telegram_premium_channel_approved",
                "trade_pnl_usd": 5.0,
                "timestamp_utc": ts,
            },
            {
                "event_type": "position_partial_closed",
                "symbol": "ETH/USDT",
                "signal_source": "telegram_premium_channel_approved",
                "trade_pnl_usd": 3.0,
                "timestamp_utc": ts,
            },
            # SL-Vollclose derselben Quelle.
            {
                "event_type": "position_closed",
                "symbol": "BTC/USDT",
                "signal_source": "telegram_premium_channel_approved",
                "trade_pnl_usd": -4.0,
                "timestamp_utc": ts,
            },
        ],
    )
    out = od.collect_paper_fills_24h(now=now)
    b = out["telegram_premium_channel_approved"]
    assert b["closes"] == 1
    assert b["partial_closes"] == 2
    assert b["pnl_usd"] == pytest.approx(4.0)  # 5 + 3 - 4 — Gewinne nicht mehr unsichtbar


def test_paper_fills_24h_never_uses_cumulative_realized_pnl(tmp_path, monkeypatch) -> None:
    """TL-003: Zeile ohne trade_pnl_usd darf NIE mit dem kumulativen
    realized_pnl_usd einfliessen — sichtbar zaehlen statt still verfaelschen."""
    from datetime import UTC, datetime

    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(od, "_ARTIFACTS", tmp_path)
    _write_audit(
        tmp_path,
        [
            {
                "event_type": "position_closed",
                "symbol": "BTC/USDT",
                "signal_source": "real_analysis",
                "realized_pnl_usd": 1977.92,  # kumulativ — Gift
                "timestamp_utc": now.isoformat(),
            }
        ],
    )
    out = od.collect_paper_fills_24h(now=now)
    b = out["real_analysis"]
    assert b["pnl_usd"] == pytest.approx(0.0)
    assert b["rows_missing_trade_pnl"] == 1
    assert b["closes"] == 1


def test_compose_renders_partial_closes_and_missing_pnl_warning() -> None:
    msg = _compose(
        fills_by_source={
            "telegram_premium_channel_approved": {
                "fills": 1,
                "closes": 1,
                "partial_closes": 2,
                "pnl_usd": 4.0,
                "rows_missing_trade_pnl": 0,
            },
            "real_analysis": {
                "fills": 0,
                "closes": 1,
                "partial_closes": 0,
                "pnl_usd": 0.0,
                "rows_missing_trade_pnl": 1,
            },
        }
    )
    assert "+2 Teil" in msg
    assert "ohne trade_pnl" in msg


# ── Asset-Rotation-Sichtbarkeit (Plan 08-08, PR-5) ───────────────────────────
# Die Rotation bewertete täglich und war für den Operator KOMPLETT unsichtbar
# (kein Digest, keine Freshness) — „Diagnose ohne Wirkung" blieb wochenlang
# unbemerkt.


def test_collect_asset_rotation_reads_state_and_last_run(tmp_path: Path) -> None:
    state = tmp_path / "asset_rotation_state.json"
    state.write_text(
        json.dumps(
            {
                "A/USDT": {"status": "active", "flagged_runs": 0},
                "B/USDT": {"status": "archived", "flagged_runs": 3},
                "C/USDT": {"status": "archived", "flagged_runs": 4},
            }
        ),
        encoding="utf-8",
    )
    shadow = tmp_path / "asset_rotation_shadow.jsonl"
    shadow.write_text(
        json.dumps({"ts": "2026-08-08T13:41:00+00:00", "evaluated": 39, "changes": 2}) + "\n",
        encoding="utf-8",
    )
    got = od.collect_asset_rotation(state_path=state, shadow_path=shadow)
    assert got is not None and got["available"] is True
    assert got["symbols"] == 3
    assert got["distribution"] == {"active": 1, "archived": 2}
    assert got["last_run_changes"] == 2
    missing = od.collect_asset_rotation(state_path=tmp_path / "nope.json", shadow_path=shadow)
    assert missing == {"available": False}


def test_collect_rotation_gate_counts_by_action_and_route(tmp_path, monkeypatch) -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(od, "_ARTIFACTS", tmp_path)
    rows = [
        {
            "event_type": "rotation_gate_would_block",
            "route": "technical_paper",
            "timestamp_utc": now.isoformat(),
        },
        {
            "event_type": "rotation_gate_would_block",
            "route": "technical_paper",
            "timestamp_utc": now.isoformat(),
        },
        {
            "event_type": "rotation_gate_block",
            "route": "autonomous_loop",
            "timestamp_utc": now.isoformat(),
        },
        {  # zu alt — fällt raus
            "event_type": "rotation_gate_block",
            "route": "autonomous_loop",
            "timestamp_utc": "2026-08-01T00:00:00+00:00",
        },
    ]
    (tmp_path / "paper_execution_audit.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    got = od.collect_rotation_gate_24h(now=now)
    assert got == {"would_block:technical_paper": 2, "block:autonomous_loop": 1}


def test_compose_renders_rotation_sections() -> None:
    msg = _compose(
        asset_rotation={
            "available": True,
            "symbols": 69,
            "distribution": {"active": 2, "archived": 19, "probation": 47},
            "last_run_ts": "2026-08-08T13:41:00+00:00",
            "last_run_evaluated": 39,
            "last_run_changes": 1,
        },
        rotation_gate_24h={"would_block:technical_paper": 5, "block:autonomous_loop": 2},
    )
    assert "Asset-Rotation:* 69 Symbole" in msg
    assert "archived: 19" in msg
    assert "Rotation-Gate 24h:" in msg
    assert "would_block:technical_paper: 5" in msg
