"""Unit tests for L2 on-chain flow features (Sprint 2, shadow-only, B-003).

DIRECTION-AGNOSTIC by design: we compute RAW percentile features (fee, mempool)
of the current on-chain state within a recent window of KAI's OWN L1 fee-shadow
stream, and log them with the candidate signal context. We do NOT pre-choose a
contrarian/pro-trend direction — that is learned later by evaluate_l2_evidence.py.
"""

from __future__ import annotations

import json

from app.signals.l2_features import (
    OnchainFlowFeatures,
    append_l2_shadow_log,
    compute_l2_features,
    percentile_rank,
    read_onchain_fee_shadow,
)

# --- percentile_rank -------------------------------------------------------------


def test_percentile_rank_empty_window_is_none() -> None:
    assert percentile_rank(5.0, []) is None


def test_percentile_rank_none_value_is_none() -> None:
    assert percentile_rank(None, [1.0, 2.0]) is None


def test_percentile_rank_fraction_leq() -> None:
    window = [1.0, 2.0, 3.0, 4.0]
    assert percentile_rank(2.0, window) == 0.5  # 2 of 4 are <= 2.0
    assert percentile_rank(0.0, window) == 0.0  # below all
    assert percentile_rank(4.0, window) == 1.0  # at/above all


# --- compute_l2_features ---------------------------------------------------------


def test_compute_features_percentiles_within_history() -> None:
    history = [
        {"fee_sat_vb": 1.0, "mempool_tx": 1000},
        {"fee_sat_vb": 2.0, "mempool_tx": 2000},
        {"fee_sat_vb": 3.0, "mempool_tx": 3000},
        {"fee_sat_vb": 4.0, "mempool_tx": 4000},
    ]
    feats = compute_l2_features(history, fee_sat_vb=3.0, mempool_tx=2000)
    assert isinstance(feats, OnchainFlowFeatures)
    assert feats.fee_sat_vb == 3.0
    assert feats.mempool_tx == 2000
    assert feats.fee_percentile == 0.75  # 3 of 4 <= 3.0
    assert feats.mempool_percentile == 0.5  # 2 of 4 <= 2000
    assert feats.window_n == 4


def test_compute_features_empty_history_percentiles_none() -> None:
    feats = compute_l2_features([], fee_sat_vb=2.0, mempool_tx=1500)
    assert feats.fee_percentile is None
    assert feats.mempool_percentile is None
    assert feats.window_n == 0


def test_compute_features_skips_none_fee_in_history() -> None:
    history = [
        {"fee_sat_vb": None, "mempool_tx": 1000},
        {"fee_sat_vb": 2.0, "mempool_tx": 2000},
    ]
    feats = compute_l2_features(history, fee_sat_vb=2.0, mempool_tx=2000)
    # fee window only has the 2.0 sample (None skipped) → 1.0; mempool has both
    assert feats.fee_percentile == 1.0
    assert feats.mempool_percentile == 1.0


# --- read_onchain_fee_shadow -----------------------------------------------------


def test_read_stream_missing_file_returns_empty(tmp_path) -> None:
    assert read_onchain_fee_shadow(tmp_path / "nope.jsonl") == []


def test_read_stream_tolerant_tail(tmp_path) -> None:
    p = tmp_path / "onchain_fee_shadow.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"fee_sat_vb": 1.0, "mempool_tx": 100}) + "\n")
        fh.write("not json\n")  # corrupt line skipped
        fh.write("\n")  # blank skipped
        fh.write(json.dumps({"fee_sat_vb": 2.0, "mempool_tx": 200}) + "\n")
    recs = read_onchain_fee_shadow(p)
    assert [r["fee_sat_vb"] for r in recs] == [1.0, 2.0]


def test_read_stream_honours_limit_last_n(tmp_path) -> None:
    p = tmp_path / "s.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for i in range(5):
            fh.write(json.dumps({"fee_sat_vb": float(i), "mempool_tx": i}) + "\n")
    recs = read_onchain_fee_shadow(p, limit=2)
    assert [r["fee_sat_vb"] for r in recs] == [3.0, 4.0]


# --- shadow log ------------------------------------------------------------------


def test_append_shadow_log_writes_raw_features_and_context(tmp_path) -> None:
    out = tmp_path / "l2_shadow.jsonl"
    feats = OnchainFlowFeatures(
        fee_sat_vb=2.5, mempool_tx=2000, fee_percentile=0.75, mempool_percentile=0.5, window_n=4
    )
    append_l2_shadow_log(out, symbol="BTC/USDT", direction="LONG", features=feats, source_trust=0.5)
    line = json.loads(out.read_text(encoding="utf-8").strip())
    assert line["symbol"] == "BTC/USDT"
    assert line["direction"] == "LONG"
    assert line["fee_sat_vb"] == 2.5
    assert line["mempool_tx"] == 2000
    assert line["fee_percentile"] == 0.75
    assert line["mempool_percentile"] == 0.5
    assert line["window_n"] == 4
    assert line["source_trust"] == 0.5
    assert "ts" in line
    # B-003: NO pre-chosen direction-aligned strength is recorded — raw only.
    assert "evidence_direction_aligned" not in line
    assert "evidence_value" not in line
