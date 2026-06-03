"""Canary-probe + legacy attribution must not pollute the real-edge shadow report.

History:
* #137 introduced the canary/real split.
* #140 quarantined source-less pre-V1 rows as ``unattributed_resolved`` and kept
  ``autonomous_loop`` as a REAL source.
* #139 added the confidence-informativeness guard + raw/deduped counts.
* NEO-P-002 (Weg B) tightens the headline cohort: a REAL row is now
  ``schema_version="v2"`` + ``source="autonomous_generator"`` ONLY. The 644
  legacy ``autonomous_loop`` v1 rows are NO LONGER real — they are fenced into
  ``legacy_counts`` (legacy_canary_suspect / legacy_unattributed) and stay
  inside #140's ``unattributed_resolved`` total. This is a deliberate behaviour
  change relative to #140 (operator requirement: legacy must not be headline).
"""

from __future__ import annotations

from app.observability.shadow_candidate_ledger import (
    CLASS_ADVERSE,
    build_shadow_report,
)


def _real_row(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "ETH/USDT",
        "side": "long",
        "regime": "chop_quiet/vol_low",
        # NEO-P-002 (Weg B): a real generator candidate is v2 + autonomous_generator.
        "source": "autonomous_generator",
        "candidate_kind": "signal_candidate",
        "schema_version": "v2",
        "stop_dist_bps": 72.0,
        "take_dist_bps": 125.0,
        "gate_would_reject": False,
        "fwd_60s_bps": -5.0,
        "fwd_300s_bps": -12.0,
        "fwd_900s_bps": -20.0,
        "fwd_3600s_bps": -40.0,
        "mae_bps": -90.0,
        "mfe_bps": 5.0,
        "mfe_before_mae": False,
        "reached_take": False,
        "reached_stop": True,
    }
    base.update(over)
    return base


def _canary_row(**over: object) -> dict[str, object]:
    # A constant-confidence probe that, if counted, would look "fine".
    return _real_row(
        source="canary_probe",
        signal_confidence=0.85,
        mae_bps=-10.0,
        mfe_bps=200.0,
        mfe_before_mae=True,
        reached_take=True,
        reached_stop=False,
        fwd_300s_bps=50.0,
        fwd_3600s_bps=80.0,
        **over,
    )


def _legacy_loop_row(**over: object) -> dict[str, object]:
    """A pre-NEO-P-002 alt-ledger row: old hardcoded source, no schema_version."""
    row = _real_row(source="autonomous_loop", **over)
    row.pop("schema_version", None)
    row.pop("candidate_kind", None)
    return row


def test_canary_excluded_from_headline_counts() -> None:
    rep = build_shadow_report([_real_row(), _real_row(), _canary_row()], total_candidates=3)
    assert rep["real_resolved"] == 2
    assert rep["canary_probe_resolved"] == 1
    assert rep["n_resolved"] == 2  # headline counts REAL only


def test_canary_does_not_flip_primary_class() -> None:
    # 25 adverse real rows -> ADVERSE; pile on canary "winners" that must NOT rescue it.
    rows = [_real_row() for _ in range(25)] + [_canary_row() for _ in range(25)]
    rep = build_shadow_report(rows, total_candidates=50)
    assert rep["primary_class"] == CLASS_ADVERSE
    assert rep["canary_probe_resolved"] == 25
    assert rep["real_resolved"] == 25


def test_by_source_split_present() -> None:
    rep = build_shadow_report([_real_row(), _canary_row()], total_candidates=2)
    by_source = rep["by_source"]
    assert isinstance(by_source, dict)
    assert "canary_probe" in by_source
    assert "autonomous_generator" in by_source


def test_missing_source_is_unattributed_not_real() -> None:
    # #140 contract preserved: source-less pre-V1 rows are quarantined, not real.
    legacy = _real_row()
    del legacy["source"]
    del legacy["schema_version"]
    rep = build_shadow_report([legacy, legacy], total_candidates=10)
    assert rep["real_resolved"] == 0
    assert rep["unattributed_resolved"] == 2
    assert rep["canary_probe_resolved"] == 0
    assert rep["n_resolved"] == 0
    assert rep["primary_class"] == "INSUFFICIENT_DATA"


def test_legacy_autonomous_loop_not_real_anymore() -> None:
    # NEO-P-002 (Weg B) behaviour change vs #140: autonomous_loop v1 rows are NO
    # LONGER counted as real edge — they go to legacy_counts, not the headline.
    rep = build_shadow_report([_legacy_loop_row(), _legacy_loop_row()], total_candidates=10)
    assert rep["real_resolved"] == 0
    assert rep["n_resolved"] == 0
    assert rep["unattributed_resolved"] == 2  # both legacy
    legacy = rep["legacy_counts"]
    assert isinstance(legacy, dict)
    # conf absent + rr 125/72 != 2.0 -> not the suspect fingerprint
    assert legacy["legacy_unattributed"] == 2
    assert legacy["legacy_canary_suspect"] == 0


def test_legacy_canary_suspect_fingerprint() -> None:
    # autonomous_loop + conf 0.85 + rr exactly 2.0 + gate not rejecting + no
    # candidate_kind -> legacy_canary_suspect (conservative).
    suspect = _legacy_loop_row(
        signal_confidence=0.85,
        stop_dist_bps=50.0,
        take_dist_bps=100.0,  # rr = 2.0
        gate_would_reject=False,
    )
    rep = build_shadow_report([suspect], total_candidates=1)
    legacy = rep["legacy_counts"]
    assert legacy["legacy_canary_suspect"] == 1
    assert legacy["legacy_unattributed"] == 0
    assert rep["n_resolved"] == 0  # never in headline
