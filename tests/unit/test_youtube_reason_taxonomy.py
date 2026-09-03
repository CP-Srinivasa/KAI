"""STAB-2026-09-01 §16 — every recorded transcript attempt maps to a reason class.

The health message ended with "(+4 Zeilen ohne Grund)". Two things were wrong with
that, and only one of them was a bug.

Not a bug: the reasonless rows are pre-instrumentation. ``transcript_status`` did
not exist before #814 (7ea4637e, on the Pi 2026-08-31T13:47:12Z). Measured over
all 2675 YouTube documents on the Pi:

    field present:  15   fetched_at 2026-08-31 14:07:20 .. 2026-09-01 10:15:55
    field absent: 2660   fetched_at 2026-04-04 15:10:54 .. 2026-08-31 12:11:23
    field present but NULL: 0

The two sets do not overlap and the seam is exactly the deploy. No live write path
can emit a reasonless row; ``fetch_transcript_with_reason`` returns a non-empty
string on all six of its exits.

The bug: the message rendered that shrinking, explained backlog with the same
words it would use for a live write-path failure, so an operator could not tell
them apart. That is now two buckets, and unmapped statuses get a class instead of
falling through to nothing.
"""

from __future__ import annotations

import pytest

from app.alerts.youtube_transcript_coverage import (
    NO_REASON_PRE_INSTRUMENTATION,
    NO_REASON_RECORDED,
    TRANSCRIPT_REASON_TAXONOMY,
    TRANSCRIPT_STATUS_EPOCH_UTC,
    classify_transcript_reason,
)

CONTRACT_CLASSES = {
    "SUCCESS",
    "IP_BLOCKED",
    "TRANSCRIPTS_DISABLED",
    "VIDEO_UNPLAYABLE",
    "NO_TRANSCRIPT_FOUND",
    "API_ERROR",
    "PARSER_ERROR",
    "TIMEOUT",
    "UNKNOWN_ERROR",
}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The four statuses actually present on the Pi right now.
        ("error:IpBlocked", "IP_BLOCKED"),
        ("transcripts_disabled", "TRANSCRIPTS_DISABLED"),
        ("error:VideoUnplayable", "VIDEO_UNPLAYABLE"),
        ("none_found", "NO_TRANSCRIPT_FOUND"),
        ("ok", "SUCCESS"),
        # Shapes the adapter can emit that nobody enumerated.
        ("error:ReadTimeout", "TIMEOUT"),
        ("error:SomeBrandNewApiError", "API_ERROR"),
        ("json decode failed", "PARSER_ERROR"),
    ],
)
def test_known_statuses_map_onto_the_contract(raw: str, expected: str) -> None:
    assert classify_transcript_reason(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "something nobody has seen"])
def test_an_unmapped_status_is_never_blank(raw: str) -> None:
    """REASON_MISSING = 0 is a property of the classifier, not of the data."""
    assert classify_transcript_reason(raw) == "UNKNOWN_ERROR"


def test_every_mapping_lands_inside_the_contract_vocabulary() -> None:
    assert set(TRANSCRIPT_REASON_TAXONOMY.values()) <= CONTRACT_CLASSES


def test_placeholders_are_not_treated_as_reasons() -> None:
    for placeholder in (NO_REASON_RECORDED, NO_REASON_PRE_INSTRUMENTATION):
        assert classify_transcript_reason(placeholder) == "UNKNOWN_ERROR"


def test_epoch_is_a_deploy_constant_not_a_moving_query() -> None:
    """Deriving the epoch from min(fetched_at) of status-bearing rows would slide
    forward with the data and hide the very defect it must catch."""
    assert TRANSCRIPT_STATUS_EPOCH_UTC == "2026-08-31T13:47:12+00:00"
