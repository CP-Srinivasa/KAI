"""STAB-2026-09-01 §1 — the non-paper-fill exemption must be falsifiable.

Before this sprint the exemption was ``venue not in frozenset({"legacy"})`` and the
operator note was a fixed f-string reading "alle dokumentiert-benign (legacy:
epoch-fremde Mai-Closes) — kein Live-Leak". Feeding the module a synthetic fill
dated *today* produced that same sentence, because nothing in the check looked at
a timestamp, an identity, an epoch boundary or a side. ``Fill.fee_venue`` still
defaults to ``"legacy"`` (app/execution/models.py:125), so that was not a
hypothetical hole: any future row built without an explicit fee_venue would have
been auto-narrated as a benign May close forever.

These tests pin both directions: the two forensically classified rows stay exempt,
and anything else wearing the same label does not.
"""

from __future__ import annotations

from typing import Any

from app.observability.evidence_window import (
    _build_safety,
    _describe_benign_non_paper,
    is_documented_benign_non_paper,
)

# The two real rows, field-for-field as they appear in paper_execution_audit.jsonl.
ETH_LEGACY_CLOSE: dict[str, Any] = {
    "event_type": "order_filled",
    "fill_id": "fill_1b252b697674",
    "order_id": "ord_24aa77e967be",
    "symbol": "ETH/USDT",
    "side": "sell",
    "timestamp_utc": "2026-05-04T02:41:56.635101+00:00",
    "fee_venue": "legacy",
    "fee_table_version": "constructor",
    "pnl_usd": 110.35782098208222,
}
GIGGLE_LEGACY_ENTRY: dict[str, Any] = {
    "event_type": "order_filled",
    "fill_id": "fill_82cdc5b05c4e",
    "order_id": "ord_4048a7fb20f8",
    "symbol": "GIGGLE/USDT",
    "side": "buy",
    "timestamp_utc": "2026-05-04T22:48:55.698698+00:00",
    "fee_venue": "legacy",
    "fee_table_version": "constructor",
    "pnl_usd": 0.0,
}


def _paper_fill(**over: Any) -> dict[str, Any]:
    row = {
        "event_type": "order_filled",
        "fill_id": "fill_paper_0001",
        "order_id": "ord_paper_0001",
        "symbol": "BTC/USDT",
        "side": "buy",
        "timestamp_utc": "2026-09-01T08:00:00+00:00",
        "fee_venue": "paper",
        "pnl_usd": 0.0,
    }
    row.update(over)
    return row


# --------------------------------------------------------------------------
# POSITIVE CONTROL — the two classified rows stay exempt
# --------------------------------------------------------------------------
def test_the_two_classified_rows_are_exempt() -> None:
    assert is_documented_benign_non_paper(ETH_LEGACY_CLOSE) is True
    assert is_documented_benign_non_paper(GIGGLE_LEGACY_ENTRY) is True


def test_both_classified_rows_together_leave_zero_unexplained() -> None:
    safety = _build_safety([], [ETH_LEGACY_CLOSE, GIGGLE_LEGACY_ENTRY])
    assert safety.live_orders_attempted == 2
    assert safety.live_orders_unexplained == 0
    assert safety.non_paper_venues_seen == ["legacy"]


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS — the label alone must excuse nothing
# --------------------------------------------------------------------------
def test_a_legacy_labelled_fill_dated_today_is_unexplained() -> None:
    """THE regression. This synthetic row is what falsified the old contract."""
    today_legacy = _paper_fill(
        fill_id="fill_synthetic_today",
        order_id="ord_synthetic_today",
        timestamp_utc="2026-09-01T07:09:23+00:00",
        fee_venue="legacy",
    )
    assert is_documented_benign_non_paper(today_legacy) is False

    safety = _build_safety([], [today_legacy])
    assert safety.live_orders_attempted == 1
    assert safety.live_orders_unexplained == 1


def test_a_known_id_moved_after_the_epoch_is_unexplained() -> None:
    """Identity alone is not enough — the row must also predate the epoch."""
    replayed = dict(ETH_LEGACY_CLOSE)
    replayed["timestamp_utc"] = "2026-08-01T00:00:00+00:00"
    assert is_documented_benign_non_paper(replayed) is False


def test_a_pre_epoch_legacy_row_with_an_unknown_id_is_unexplained() -> None:
    """Being old is not enough either — both conditions must hold."""
    impostor = dict(ETH_LEGACY_CLOSE)
    impostor["fill_id"] = "fill_not_classified"
    impostor["order_id"] = "ord_not_classified"
    assert is_documented_benign_non_paper(impostor) is False


def test_a_missing_or_unparseable_timestamp_is_never_benign() -> None:
    no_stamp = {k: v for k, v in ETH_LEGACY_CLOSE.items() if k != "timestamp_utc"}
    assert is_documented_benign_non_paper(no_stamp) is False
    bad_stamp = dict(ETH_LEGACY_CLOSE, timestamp_utc="not-a-timestamp")
    assert is_documented_benign_non_paper(bad_stamp) is False


def test_a_real_external_venue_is_unexplained_and_trips_the_tripwire() -> None:
    external = _paper_fill(
        fill_id="fill_external",
        order_id="ord_external",
        fee_venue="binance",
        exchange_order_id="12345678",
    )
    assert is_documented_benign_non_paper(external) is False
    safety = _build_safety([], [external])
    assert safety.live_orders_unexplained == 1
    assert "binance" in safety.non_paper_venues_seen


def test_ordinary_paper_fills_are_not_counted_at_all() -> None:
    safety = _build_safety([], [_paper_fill(), _paper_fill(fee_venue="")])
    assert safety.live_orders_attempted == 0
    assert safety.live_orders_unexplained == 0


# --------------------------------------------------------------------------
# The operator note must describe the rows it actually has
# --------------------------------------------------------------------------
def test_note_is_derived_from_the_rows_not_asserted() -> None:
    safety = _build_safety([], [ETH_LEGACY_CLOSE, GIGGLE_LEGACY_ENTRY])
    note = _describe_benign_non_paper(safety)
    # It names the real symbols and both sides — the old text called both
    # fills "Closes" although GIGGLE/USDT is a buy entry with pnl_usd == 0.
    assert "ETH/USDT" in note
    assert "GIGGLE/USDT" in note
    assert "buy" in note and "sell" in note
    assert "2026-05-04" in note


def test_note_does_not_hardcode_the_may_close_sentence() -> None:
    """A different (hypothetical) exempt row must not be called a May close."""
    safety = _build_safety([], [ETH_LEGACY_CLOSE])
    note = _describe_benign_non_paper(safety)
    assert "epoch-fremde Mai-Closes" not in note
    assert "1 of 1" in note


# --------------------------------------------------------------------------
# STAB-2026-09-01 §3 — the default must not carry an exemption
# --------------------------------------------------------------------------
def test_fill_fee_venue_no_longer_defaults_to_legacy() -> None:
    """The last live remnant of this finding.

    ``PaperFill.fee_venue`` defaulted to ``"legacy"`` — the exact marker the two
    historical 2026-05-04 rows carry. A newly constructed fill without an explicit
    venue therefore inherited the label of an exempted pair. The identity/epoch
    binding above already denies the exemption, but a default that leads toward one
    at all is the wrong starting value.
    """
    import dataclasses

    from app.execution.models import FEE_VENUE_UNKNOWN, PaperFill

    default = next(f.default for f in dataclasses.fields(PaperFill) if f.name == "fee_venue")
    assert default != "legacy"
    assert default == FEE_VENUE_UNKNOWN


def test_the_default_venue_is_not_a_paper_venue() -> None:
    """NEGATIVE CONTROL: an undeclared venue must surface, not pass silently.

    If ``unknown`` counted as a paper venue the fill would vanish from
    ``live_orders_attempted`` entirely — quieter than the old bug and worse.
    """
    from app.execution.models import FEE_VENUE_UNKNOWN
    from app.observability.evidence_window import _is_paper_venue

    assert _is_paper_venue(FEE_VENUE_UNKNOWN) is False


def test_a_default_constructed_fill_is_unexplained_not_benign() -> None:
    """End to end: no explicit venue => counted AND unexplained."""
    from app.execution.models import FEE_VENUE_UNKNOWN

    row = _paper_fill(
        fill_id="fill_no_declared_venue",
        order_id="ord_no_declared_venue",
        fee_venue=FEE_VENUE_UNKNOWN,
    )
    assert is_documented_benign_non_paper(row) is False
    safety = _build_safety([], [row])
    assert safety.live_orders_attempted == 1
    assert safety.live_orders_unexplained == 1
