"""Safety tripwire semantics: unexplained vs. documented-benign non-paper fills.

The 2 May-``legacy`` fills (epoch-foreign, documented benign — memories
kai_triple_verdict_20260701 / kai_edge_epoch_contamination_20260623) made
``live_orders_attempted > 0`` a PERMANENT condition, so the exit-2 tripwire of
``trading canonical-edge``/``evidence-window`` fired on every single run — an
alarm that always fires alarms nothing (and would turn the daily attest timer
into standing failed-unit noise). Truth stays intact: ``live_orders_attempted``
still counts and lists EVERYTHING; only the tripwire keys on
``live_orders_unexplained`` (non-paper minus the documented-benign rows).

STAB-2026-09-01 §1 — what changed and why these fixtures grew fields:
the exemption used to be ``venue not in frozenset({"legacy"})``, i.e. a bare
string match. ``Fill.fee_venue`` defaults to ``"legacy"``
(app/execution/models.py:125), so ANY future fill constructed without an explicit
fee_venue would have inherited a historical exemption forever, and the operator
note asserted "epoch-fremde Mai-Closes" for it regardless of its date. The
exemption is now pinned to the two forensically classified rows AND requires the
row to predate the paper epoch. A venue label on its own excuses nothing, which
is why the fixtures below must now carry a real identity and timestamp.
"""

from __future__ import annotations

from typing import Any

from app.observability.evidence_window import _build_safety

# The two rows the forensic classification actually covers.
_CLASSIFIED = {
    "eth": {
        "fill_id": "fill_1b252b697674",
        "order_id": "ord_24aa77e967be",
        "timestamp_utc": "2026-05-04T02:41:56.635101+00:00",
    },
    "giggle": {
        "fill_id": "fill_82cdc5b05c4e",
        "order_id": "ord_4048a7fb20f8",
        "timestamp_utc": "2026-05-04T22:48:55.698698+00:00",
    },
}


def _fill(venue: str, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"event_type": "order_filled", "fee_venue": venue}
    row.update(over)
    return row


def _classified(which: str) -> dict[str, Any]:
    return _fill("legacy", **_CLASSIFIED[which])


def test_legacy_fills_still_counted_but_explained() -> None:
    safety = _build_safety([], [_classified("eth"), _classified("giggle")])
    assert safety.live_orders_attempted == 2  # Wahrheit unangetastet
    assert safety.non_paper_venues_seen == ["legacy"]
    assert safety.live_orders_unexplained == 0  # Tripwire schweigt


def test_real_venue_fill_is_unexplained() -> None:
    safety = _build_safety([], [_classified("eth"), _fill("binance")])
    assert safety.live_orders_attempted == 2
    assert safety.live_orders_unexplained == 1  # echter Live-Leak feuert


def test_bare_legacy_label_no_longer_excuses_itself() -> None:
    """NEGATIVE CONTROL: the label without an identity is NOT documented-benign.

    This is the case the pre-STAB contract waved through, and the reason
    ``Fill.fee_venue = "legacy"`` was a live hole rather than a historical one.
    """
    safety = _build_safety([], [_fill("legacy"), _fill("legacy")])
    assert safety.live_orders_attempted == 2
    assert safety.live_orders_unexplained == 2


def test_paper_fills_are_neither() -> None:
    safety = _build_safety([], [_fill("paper"), _fill("")])
    assert safety.live_orders_attempted == 0
    assert safety.live_orders_unexplained == 0


def test_to_dict_exposes_both_counts() -> None:
    doc = _build_safety([], [_classified("eth")]).to_dict()
    assert doc["live_orders_attempted"] == 1
    assert doc["live_orders_unexplained"] == 0
