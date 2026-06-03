"""NEO-P-128-INSTR-01 — confidence variance guard + dedup counts.

Complementary to #137/#140 (which already exclude canary_probe + unattributed
rows from the headline edge stats). These tests cover the additional
operator-mandated guards: a constant signal_confidence (the hardcoded canary
0.85) must be flagged non-informative so no confidence-based edge conclusion is
drawn, and duplicate scan inflation must be visible via raw_count vs
deduped_count. Pure/offline — no network, no execution.
"""

from __future__ import annotations

from app.observability.shadow_candidate_ledger import (
    CONF_INFORMATIVE,
    CONF_NO_DATA,
    CONF_NON_INFORMATIVE_CONSTANT,
    build_shadow_report,
)


def _canary_row(i: int) -> dict[str, object]:
    # All canary rows are geometry-identical (the real degeneracy on the Pi).
    return {
        "candidate_id": f"k{i}",
        "symbol": "BTC/USDT",
        "side": "long",
        "regime": "neutral",
        "source": "canary_probe",
        "signal_confidence": 0.85,
        "stop_dist_bps": 200.0,
        "take_dist_bps": 400.0,
        "mae_bps": -50.0,
        "mfe_bps": 60.0,
        "mfe_before_mae": True,
        "reached_take": False,
        "reached_stop": False,
        "gate_would_reject": False,
    }


def _real_row(i: int, *, conf: float) -> dict[str, object]:
    return {
        "candidate_id": f"r{i}",
        "symbol": "ETH/USDT",
        "side": "long",
        "regime": "bull",
        # NEO-P-002 (Weg B): a real row is now autonomous_generator + v2
        # (autonomous_loop is legacy and no longer counts as real edge).
        "source": "autonomous_generator",
        "schema_version": "v2",
        "candidate_kind": "signal_candidate",
        "signal_confidence": conf,
        "stop_dist_bps": 150.0 + i,  # vary so dedup keeps them distinct
        "take_dist_bps": 300.0,
        "mae_bps": -40.0,
        "mfe_bps": 80.0,
        "mfe_before_mae": True,
        "reached_take": True,
        "reached_stop": False,
        "gate_would_reject": False,
    }


def test_constant_confidence_is_non_informative() -> None:
    """Zero-variance signal_confidence disables confidence analysis."""
    report = build_shadow_report([_canary_row(i) for i in range(30)])
    assert report["confidence_analysis_status"] == CONF_NON_INFORMATIVE_CONSTANT
    assert report["confidence_buckets_enabled"] is False
    assert report["signal_confidence_constant_value"] == 0.85
    assert report["signal_confidence_distinct_count"] == 1


def test_varying_confidence_is_informative() -> None:
    rows = [_real_row(i, conf=0.5 + i * 0.01) for i in range(25)]
    report = build_shadow_report(rows)
    assert report["confidence_analysis_status"] == CONF_INFORMATIVE
    assert report["confidence_buckets_enabled"] is True
    assert report["signal_confidence_distinct_count"] >= 2


def test_no_confidence_data_status() -> None:
    rows = [{"source": "autonomous_loop", "symbol": "X", "side": "long"} for _ in range(5)]
    report = build_shadow_report(rows)
    assert report["confidence_analysis_status"] == CONF_NO_DATA
    assert report["confidence_buckets_enabled"] is False


def test_dedup_counts_collapse_identical_canary() -> None:
    """Identical canary rows → deduped_count < raw_count, both surfaced."""
    report = build_shadow_report([_canary_row(i) for i in range(40)])
    assert report["raw_count"] == 40
    assert report["deduped_count"] < report["raw_count"]
    assert report["deduped_count"] >= 1


def test_dedup_keeps_distinct_real_rows() -> None:
    """Real rows with distinct geometry are not collapsed."""
    rows = [_real_row(i, conf=0.5 + i * 0.01) for i in range(20)]
    report = build_shadow_report(rows)
    assert report["raw_count"] == 20
    assert report["deduped_count"] == 20


def test_guards_do_not_disturb_existing_keys() -> None:
    """Headline keys from #137/#140 remain present and consistent."""
    rows = [_canary_row(i) for i in range(30)] + [_real_row(i, conf=0.7) for i in range(25)]
    report = build_shadow_report(rows)
    assert report["real_resolved"] == 25
    assert report["canary_probe_resolved"] == 30
    assert report["raw_count"] == 55
