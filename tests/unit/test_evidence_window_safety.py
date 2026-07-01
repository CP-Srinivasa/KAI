"""Safety tripwire semantics: unexplained vs. documented-benign non-paper fills.

The 2 May-``legacy`` fills (epoch-foreign, documented benign — memories
kai_triple_verdict_20260701 / kai_edge_epoch_contamination_20260623) made
``live_orders_attempted > 0`` a PERMANENT condition, so the exit-2 tripwire of
``trading canonical-edge``/``evidence-window`` fired on every single run — an
alarm that always fires alarms nothing (and would turn the daily attest timer
into standing failed-unit noise). Truth stays intact: ``live_orders_attempted``
still counts and lists EVERYTHING; only the tripwire keys on
``live_orders_unexplained`` (non-paper minus the documented-benign marker).
"""

from __future__ import annotations

from app.observability.evidence_window import _build_safety


def _fill(venue: str) -> dict[str, str]:
    return {"event_type": "order_filled", "fee_venue": venue}


def test_legacy_fills_still_counted_but_explained() -> None:
    safety = _build_safety([], [_fill("legacy"), _fill("legacy")])
    assert safety.live_orders_attempted == 2  # Wahrheit unangetastet
    assert safety.non_paper_venues_seen == ["legacy"]
    assert safety.live_orders_unexplained == 0  # Tripwire schweigt


def test_real_venue_fill_is_unexplained() -> None:
    safety = _build_safety([], [_fill("legacy"), _fill("binance")])
    assert safety.live_orders_attempted == 2
    assert safety.live_orders_unexplained == 1  # echter Live-Leak feuert


def test_paper_fills_are_neither() -> None:
    safety = _build_safety([], [_fill("paper"), _fill("")])
    assert safety.live_orders_attempted == 0
    assert safety.live_orders_unexplained == 0


def test_to_dict_exposes_both_counts() -> None:
    doc = _build_safety([], [_fill("legacy")]).to_dict()
    assert doc["live_orders_attempted"] == 1
    assert doc["live_orders_unexplained"] == 0
