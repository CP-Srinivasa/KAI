"""UC-5 — sovereign fee/mempool time series (verifiable FACTS, no forecast).

Served from KAI's OWN L1 fee-shadow stream. Deterministic aggregation only — raw
records + min/median/max — NEVER an "expected fee tomorrow" prediction (doctrine:
the oracle sells facts, not predictions).
"""

from __future__ import annotations

from app.chain.fee_series import build_fee_series


def test_build_returns_raw_facts_and_deterministic_summary() -> None:
    recs = [
        {"ts": "t0", "blocks": 1, "fee_sat_vb": 1.0, "mempool_tx": 100},
        {"ts": "t1", "blocks": 2, "fee_sat_vb": 3.0, "mempool_tx": 200},
    ]
    out = build_fee_series(recs)
    assert out["count"] == 2
    assert out["series"][-1]["fee_sat_vb"] == 3.0
    assert out["fee_sat_vb_min"] == 1.0
    assert out["fee_sat_vb_median"] == 2.0  # median of [1,3]
    assert out["fee_sat_vb_max"] == 3.0
    assert out["oldest_ts"] == "t0" and out["newest_ts"] == "t1"


def test_honours_limit_last_n() -> None:
    recs = [{"ts": f"t{i}", "blocks": i, "fee_sat_vb": float(i), "mempool_tx": i} for i in range(5)]
    out = build_fee_series(recs, limit=2)
    assert out["count"] == 2
    assert [r["ts"] for r in out["series"]] == ["t3", "t4"]


def test_empty_is_honest_no_fabricated_stats() -> None:
    out = build_fee_series([])
    assert out["count"] == 0 and out["series"] == []
    assert out["fee_sat_vb_median"] is None


def test_skips_none_fee_in_summary_but_keeps_in_series() -> None:
    recs = [
        {"ts": "t0", "blocks": 1, "fee_sat_vb": None, "mempool_tx": 100},
        {"ts": "t1", "blocks": 2, "fee_sat_vb": 4.0, "mempool_tx": 200},
    ]
    out = build_fee_series(recs)
    assert out["count"] == 2  # both rows in series
    assert out["fee_sat_vb_median"] == 4.0  # None excluded from stats
