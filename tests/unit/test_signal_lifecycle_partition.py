"""STAB-2026-09-01 §10 — the signal matrix columns must partition the population.

The matrix rendered six columns beside a header total as though the columns
partitioned it. They did not, twice over:

  * the frontend bucketed with six independent ``if`` statements, so a state
    could match none of them while still incrementing the total;
  * 21 of the 45 PremiumSignalState values mapped to NO column at all.

Over the live log that is 207 of 3890 rows (5.3%) counted in the header and
rendered nowhere — 205 ``requires_review`` (TTL expired: the setup was fine but
the entry never printed) and 5 ``invalid``. The actionable class was the invisible
one, and "expired" is not the same statement as "rejected".

The partition is now a total function with an exhaustiveness guard, so a new enum
member cannot be introduced without a home.
"""

from __future__ import annotations

from collections import Counter

import pytest

from app.premium.state_machine import (
    LIFECYCLE_BUCKETS,
    LIFECYCLE_CLOSED_PAPER,
    LIFECYCLE_OPENED_PAPER,
    LIFECYCLE_RECOGNISED,
    LIFECYCLE_REJECTED,
    LIFECYCLE_REVIEW,
    LIFECYCLE_SUBMITTED_PAPER,
    PremiumSignalState,
    lifecycle_bucket,
    unmapped_lifecycle_states,
)


# --------------------------------------------------------------------------
# Totality
# --------------------------------------------------------------------------
def test_no_state_is_left_without_a_column() -> None:
    """SIGNAL_MATRIX_UNCLASSIFIED = 0, enforced rather than hoped for."""
    assert unmapped_lifecycle_states() == []


@pytest.mark.parametrize("state", sorted({m.value for m in PremiumSignalState}))
def test_every_enum_value_maps_to_exactly_one_declared_bucket(state: str) -> None:
    bucket = lifecycle_bucket(state)
    assert bucket in LIFECYCLE_BUCKETS
    assert sum(1 for b in LIFECYCLE_BUCKETS if b == bucket) == 1


def test_the_buckets_partition_a_population() -> None:
    """SUM(columns) == total, by construction, for an arbitrary mix."""
    population = [m.value for m in PremiumSignalState] * 3
    counts = Counter(lifecycle_bucket(s) for s in population)
    assert sum(counts.values()) == len(population)
    assert set(counts) <= set(LIFECYCLE_BUCKETS)


# --------------------------------------------------------------------------
# The states that used to vanish
# --------------------------------------------------------------------------
def test_ttl_expired_signals_are_visible_and_are_not_rejections() -> None:
    """The 205-row class. Nothing refused these — the window closed."""
    bucket = lifecycle_bucket(PremiumSignalState.REQUIRES_REVIEW)
    assert bucket == LIFECYCLE_REVIEW
    assert bucket != LIFECYCLE_REJECTED


def test_invalid_signals_are_visible() -> None:
    assert lifecycle_bucket(PremiumSignalState.INVALID) == LIFECYCLE_REVIEW


@pytest.mark.parametrize(
    "state",
    [m.value for m in PremiumSignalState if str(m.value).startswith("fastlane_")],
)
def test_every_fastlane_state_has_a_home(state: str) -> None:
    assert lifecycle_bucket(state) in LIFECYCLE_BUCKETS


# --------------------------------------------------------------------------
# The lifecycle reads correctly
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("parsed", LIFECYCLE_RECOGNISED),
        ("envelope_accepted", LIFECYCLE_RECOGNISED),
        ("approved", "eligible"),
        ("awaiting_approval", "eligible"),
        ("risk_rejected", LIFECYCLE_REJECTED),
        ("bridge_rejected", LIFECYCLE_REJECTED),
        ("pending_entry", LIFECYCLE_SUBMITTED_PAPER),
        ("paper_order_created", LIFECYCLE_SUBMITTED_PAPER),
        ("position_open", LIFECYCLE_OPENED_PAPER),
        ("partially_closed", LIFECYCLE_OPENED_PAPER),
        ("closed_tp", LIFECYCLE_CLOSED_PAPER),
        ("closed_sl", LIFECYCLE_CLOSED_PAPER),
    ],
)
def test_lifecycle_stage_mapping(state: str, expected: str) -> None:
    assert lifecycle_bucket(state) == expected


def test_recognised_is_not_the_same_column_as_opened() -> None:
    """§10: "erkannt" must never be presentable as "traded"."""
    assert lifecycle_bucket("parsed") != lifecycle_bucket("position_open")


# --------------------------------------------------------------------------
# Fail-closed
# --------------------------------------------------------------------------
@pytest.mark.parametrize("state", [None, "", "   ", "a_state_nobody_declared"])
def test_an_unknown_state_goes_to_review_not_to_nothing(state: str | None) -> None:
    """Fail-closed: an unrecognised state must stay in the operator's view and
    must never land in a terminal bucket where it would look resolved."""
    bucket = lifecycle_bucket(state)
    assert bucket == LIFECYCLE_REVIEW
    assert bucket not in (LIFECYCLE_CLOSED_PAPER, LIFECYCLE_REJECTED)
