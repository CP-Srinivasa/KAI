"""Unit tests for app.observability.outcome_dedupe_report.build_episode_dedupe_report.

Daily-Review 2026-07-07 V1: the 2026-07-06 backlog batch annotated ~150
hit/miss rows from parallel tradingview_webhook signal paths that all
resolved on the same BTC move — correlated observations counted as
independent. Episode dedup clusters doc-deduped resolved outcomes by
(asset, direction, horizon) with a dispatch-gap chain rule so one market
episode counts once.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.observability.outcome_dedupe_report import build_episode_dedupe_report

_NOTE_4H = "auto@4h: bullish BTC/USDT $61,917.25->$63,566.62 (+2.66% over 4.0h, thr=0.42%)"


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _outcome(
    doc: str,
    outcome: str,
    *,
    asset: str = "BTC/USDT",
    note: str | None = _NOTE_4H,
    annotated_at: str = "2026-07-06T18:30:00+00:00",
) -> dict:
    row: dict = {
        "document_id": doc,
        "outcome": outcome,
        "annotated_at": annotated_at,
        "asset": asset,
    }
    if note is not None:
        row["note"] = note
    return row


def _audit(doc: str, dispatched_at: str, sentiment: str = "bullish") -> dict:
    return {
        "document_id": doc,
        "channel": "telegram",
        "message_id": None,
        "is_digest": False,
        "dispatched_at": dispatched_at,
        "sentiment_label": sentiment,
    }


def test_missing_files_return_zero_report(tmp_path: Path) -> None:
    report = build_episode_dedupe_report(
        audit_path=tmp_path / "missing.jsonl",
        alert_audit_path=tmp_path / "missing_audit.jsonl",
    )
    assert report.resolved_rows == 0
    assert report.episode_total == 0
    assert report.episode_precision_str == "n/a"


def test_parallel_paths_on_same_move_collapse_to_one_episode(tmp_path: Path) -> None:
    """The 2026-07-06 pattern: many paths, minutes apart, one move."""
    outcomes = tmp_path / "alert_outcomes.jsonl"
    audit = tmp_path / "alert_audit.jsonl"
    _write_rows(
        outcomes,
        [_outcome(f"tv:d{i}", "hit") for i in range(10)],
    )
    _write_rows(
        audit,
        [_audit(f"tv:d{i}", f"2026-07-06T10:{i * 5:02d}:00+00:00") for i in range(10)],
    )

    report = build_episode_dedupe_report(audit_path=outcomes, alert_audit_path=audit)
    assert report.resolved_rows == 10
    assert report.episode_total == 1
    assert report.episode_hit == 1
    assert report.episode_miss == 0
    assert report.largest_episode_size == 10
    assert "100.0% (1/1)" in report.episode_precision_str


def test_gap_larger_than_horizon_starts_new_episode(tmp_path: Path) -> None:
    outcomes = tmp_path / "alert_outcomes.jsonl"
    audit = tmp_path / "alert_audit.jsonl"
    _write_rows(
        outcomes,
        [_outcome("d1", "hit"), _outcome("d2", "miss")],
    )
    # 5h apart with a 4h horizon -> two distinct episodes.
    _write_rows(
        audit,
        [
            _audit("d1", "2026-07-06T10:00:00+00:00"),
            _audit("d2", "2026-07-06T15:00:00+00:00"),
        ],
    )

    report = build_episode_dedupe_report(audit_path=outcomes, alert_audit_path=audit)
    assert report.episode_total == 2
    assert report.episode_hit == 1
    assert report.episode_miss == 1
    assert "50.0% (1/2)" in report.episode_precision_str


def test_chain_rule_merges_overlapping_windows(tmp_path: Path) -> None:
    """Gap measured to the previous row, not the episode start: an 8h
    dispatch span with sub-horizon gaps stays one episode."""
    outcomes = tmp_path / "alert_outcomes.jsonl"
    audit = tmp_path / "alert_audit.jsonl"
    _write_rows(outcomes, [_outcome(f"d{i}", "hit") for i in range(5)])
    _write_rows(
        audit,
        [_audit(f"d{i}", f"2026-07-06T{10 + i * 2:02d}:00:00+00:00") for i in range(5)],
    )

    report = build_episode_dedupe_report(audit_path=outcomes, alert_audit_path=audit)
    assert report.episode_total == 1


def test_direction_and_asset_split_groups(tmp_path: Path) -> None:
    outcomes = tmp_path / "alert_outcomes.jsonl"
    audit = tmp_path / "alert_audit.jsonl"
    bearish_note = _NOTE_4H.replace("bullish", "bearish")
    _write_rows(
        outcomes,
        [
            _outcome("d1", "hit"),
            _outcome("d2", "hit", note=bearish_note),
            _outcome("d3", "hit", asset="ETH/USDT"),
        ],
    )
    _write_rows(
        audit,
        [
            _audit("d1", "2026-07-06T10:00:00+00:00"),
            _audit("d2", "2026-07-06T10:01:00+00:00", sentiment="bearish"),
            _audit("d3", "2026-07-06T10:02:00+00:00"),
        ],
    )

    report = build_episode_dedupe_report(audit_path=outcomes, alert_audit_path=audit)
    assert report.episode_total == 3


def test_majority_vote_and_tie_counts_as_miss(tmp_path: Path) -> None:
    outcomes = tmp_path / "alert_outcomes.jsonl"
    audit = tmp_path / "alert_audit.jsonl"
    _write_rows(
        outcomes,
        [
            # Episode A (BTC): 2 hit vs 1 miss -> hit.
            _outcome("a1", "hit"),
            _outcome("a2", "hit"),
            _outcome("a3", "miss"),
            # Episode B (ETH): 1 hit vs 1 miss -> tie -> miss (conservative).
            _outcome("b1", "hit", asset="ETH/USDT"),
            _outcome("b2", "miss", asset="ETH/USDT"),
        ],
    )
    _write_rows(
        audit,
        [
            _audit("a1", "2026-07-06T10:00:00+00:00"),
            _audit("a2", "2026-07-06T10:05:00+00:00"),
            _audit("a3", "2026-07-06T10:10:00+00:00"),
            _audit("b1", "2026-07-06T10:00:00+00:00"),
            _audit("b2", "2026-07-06T10:05:00+00:00"),
        ],
    )

    report = build_episode_dedupe_report(audit_path=outcomes, alert_audit_path=audit)
    assert report.episode_total == 2
    assert report.episode_hit == 1
    assert report.episode_miss == 1


def test_latest_row_per_document_wins_before_clustering(tmp_path: Path) -> None:
    """Multi-Window rows: only the final state per document_id enters;
    documents ending inconclusive are excluded entirely."""
    outcomes = tmp_path / "alert_outcomes.jsonl"
    audit = tmp_path / "alert_audit.jsonl"
    _write_rows(
        outcomes,
        [
            _outcome("d1", "inconclusive"),
            _outcome("d1", "hit"),
            _outcome("d2", "hit"),
            _outcome("d2", "inconclusive"),
        ],
    )
    _write_rows(
        audit,
        [
            _audit("d1", "2026-07-06T10:00:00+00:00"),
            _audit("d2", "2026-07-06T10:01:00+00:00"),
        ],
    )

    report = build_episode_dedupe_report(audit_path=outcomes, alert_audit_path=audit)
    assert report.resolved_rows == 1  # d2 ended inconclusive -> out
    assert report.episode_total == 1


def test_missing_audit_row_falls_back_to_annotated_at(tmp_path: Path) -> None:
    outcomes = tmp_path / "alert_outcomes.jsonl"
    audit = tmp_path / "alert_audit.jsonl"
    _write_rows(
        outcomes,
        [
            _outcome("d1", "hit", annotated_at="2026-07-06T14:00:00+00:00"),
            _outcome("d2", "hit", annotated_at="2026-07-06T14:01:00+00:00"),
        ],
    )
    _write_rows(audit, [])  # no dispatch info at all

    report = build_episode_dedupe_report(audit_path=outcomes, alert_audit_path=audit)
    assert report.unanchored_rows == 2
    assert report.episode_total == 1  # same annotated_at window -> one episode


def test_horizon_parsed_from_note(tmp_path: Path) -> None:
    outcomes = tmp_path / "alert_outcomes.jsonl"
    audit = tmp_path / "alert_audit.jsonl"
    note_24h = "auto@24h: bullish BTC/USDT $100.00->$104.00 (+4.00% over 24.0h, thr=0.42%)"
    _write_rows(
        outcomes,
        [
            _outcome("d1", "hit", note=note_24h),
            _outcome("d2", "hit", note=note_24h),
        ],
    )
    # 5h apart: separate under 4h horizon, same episode under 24h.
    _write_rows(
        audit,
        [
            _audit("d1", "2026-07-06T10:00:00+00:00"),
            _audit("d2", "2026-07-06T15:00:00+00:00"),
        ],
    )

    report = build_episode_dedupe_report(audit_path=outcomes, alert_audit_path=audit)
    assert report.episode_total == 1


def test_to_dict_roundtrip_keys(tmp_path: Path) -> None:
    outcomes = tmp_path / "alert_outcomes.jsonl"
    audit = tmp_path / "alert_audit.jsonl"
    _write_rows(outcomes, [_outcome("d1", "hit")])
    _write_rows(audit, [_audit("d1", "2026-07-06T10:00:00+00:00")])

    d = build_episode_dedupe_report(audit_path=outcomes, alert_audit_path=audit).to_dict()
    for key in (
        "resolved_rows",
        "episode_total",
        "episode_hit",
        "episode_miss",
        "episode_precision",
        "unanchored_rows",
        "largest_episode_size",
    ):
        assert key in d
