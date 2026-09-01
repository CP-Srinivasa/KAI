"""STAB-2026-09-01 §5/§6 — the source populations must reconcile.

The dashboard published "0/15 Quellen-Treffsicherheit" next to "0/12 trusted"
with both denominators labelled simply "Quellen", as though they described the
same set. They do not, and nothing said so. §5 requires the difference to be
mechanically comparable and every element of it to carry a reason.

§6 is the neighbouring risk: if one real-world source can be spelled two ways,
its evidence splits across two rows and each half can fall under the gate. The
live audit currently uses a single spelling per source, so no merge is pending —
but canonicalisation now happens BEFORE aggregation, so a second spelling cannot
silently halve a source's n later. Reader-level only; no historical row is
rewritten.
"""

from __future__ import annotations

import pytest

from app.alerts.hold_metrics import (
    LEGACY_UNKNOWN_SOURCE,
    canonical_source_id,
    reconcile_source_populations,
)


# --------------------------------------------------------------------------
# §6 — canonicalisation before aggregation
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    ["tradingview", "TradingView", "TRADINGVIEW", " tv ", "TV", "tradingview-webhook",
     "tradingview_webhook", "tradingview  webhook"],
)
def test_alias_spellings_fold_onto_one_canonical_id(raw: str) -> None:
    """NEGATIVE TEST from the brief: two aliases must be indistinguishable after
    normalisation, or the same source is counted twice at half strength."""
    assert canonical_source_id(raw) == "tradingview_webhook"


def test_case_and_separator_variants_never_split_a_source() -> None:
    for a, b in (("Crypto Banter", "crypto_banter"), ("The-Block", "the_block")):
        assert canonical_source_id(a) == canonical_source_id(b)


def test_an_empty_source_is_the_declared_unknown_not_a_new_bucket() -> None:
    for raw in (None, "", "   "):
        assert canonical_source_id(raw) == LEGACY_UNKNOWN_SOURCE


def test_canonicalisation_is_idempotent() -> None:
    for raw in ("TradingView", "coindesk", "Crypto Banter", ""):
        once = canonical_source_id(raw)
        assert canonical_source_id(once) == once


# --------------------------------------------------------------------------
# §5 — the reconciliation
# --------------------------------------------------------------------------
def test_the_two_counts_are_named_separately() -> None:
    """"15 outcome-bearing sources" and "12 reliability-managed sources" — never
    twice just "Quellen"."""
    rec = reconcile_source_populations(
        accuracy={f"src_{i}": {} for i in range(15)},
        stability={f"src_{i}": {} for i in range(12)},
        reliability={f"src_{i}": {} for i in range(12)},
    )
    assert rec["outcome_bearing_source_count"] == 15
    assert rec["reliability_managed_source_count"] == 12
    assert rec["stability_evaluated_source_count"] == 12


def test_every_difference_carries_a_reason() -> None:
    """UNEXPLAINED_SOURCE_POPULATION_DIFF = 0 is asserted, not assumed."""
    rec = reconcile_source_populations(
        accuracy={"a": {}, "b": {}, "c": {}},
        stability={"a": {}},
        reliability={"a": {}, "d": {}},
    )
    diffs = set(rec["accuracy_only"]) | set(rec["stability_only"]) | set(rec["reliability_only"])
    for src in diffs:
        assert src in rec["difference_reasons"]
        assert rec["difference_reasons"][src]
    assert rec["unexplained_population_diff_count"] == 0


def test_the_sets_are_reconciled_after_canonicalisation_not_before() -> None:
    """An alias pair must land in `common`, never as a spurious difference."""
    rec = reconcile_source_populations(
        accuracy={"TradingView": {}},
        stability={"tv": {}},
    )
    assert rec["accuracy_only"] == []
    assert rec["stability_only"] == []
    assert "tradingview_webhook" in rec["common"]


def test_identical_populations_produce_no_difference() -> None:
    same = {"a": {}, "b": {}}
    rec = reconcile_source_populations(accuracy=same, stability=same)
    assert rec["accuracy_only"] == []
    assert rec["stability_only"] == []
    assert rec["unexplained_population_diff_count"] == 0


def test_the_payload_publishes_all_three_sets() -> None:
    rec = reconcile_source_populations(accuracy={"a": {}}, stability={"b": {}})
    assert rec["source_accuracy_set"] == ["a"]
    assert rec["source_stability_set"] == ["b"]
    assert rec["source_reliability_set"] == []
