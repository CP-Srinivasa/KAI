"""In-loop funnel axes for the NEO-P-002-r3 real-generator shadow path (#175).

The Phase-2 feeder (``shadow_real_feed``) records *pre-loop* selection
(``seen/already_fed/no_symbol/non_directional/eligible``) plus terminal
``by_cycle_status``. That cannot explain **where inside the loop/generator** a
real candidate died. This module is the pure, read-only classifier that turns
each injected cycle's terminal ``CycleStatus`` (+ notes) into the in-loop funnel
axes — so a future ``real_resolved=0`` stays *explainable* (priority-gate?
generator-returned-none?), never silently read as ``EDGE_NEGATIVE``.

Pure instrumentation: this changes **no** loop behaviour. It does not loosen the
directional gate, touch priority thresholds, bypass D-182, or enable any entry.
"""

from __future__ import annotations

from typing import Any

# The in-loop funnel axes required by #175 (order = funnel order).
INLOOP_AXES: tuple[str, ...] = (
    "real_analyses_seen",
    "eligible_for_shadow",
    "priority_rejected",
    "sentiment_rejected",
    "non_directional",
    "directional_accepted",
    "reached_signal_generator",
    "generator_returned_none",
    "shadow_candidate_written",
    "resolver_resolved_real",
)

# Terminal classification buckets (one per injected cycle).
T_PRIORITY = "priority_rejected"
T_SENTIMENT = "sentiment_rejected"
T_NON_DIRECTIONAL = "non_directional"
T_GENERATOR_NONE = "generator_returned_none"
T_SHADOW_WRITTEN = "shadow_candidate_written"
T_DOWNSTREAM_REJECTED = "downstream_rejected"
T_NO_MARKET_DATA = "no_market_data"
T_ERROR = "error"

# CycleStatus string forms that mean "a signal was generated, then a gate AFTER
# the generator rejected it". Substrings so we are robust to enum-vs-value forms.
_DOWNSTREAM_HINTS = (
    "risk_rejected",
    "size_rejected",
    "consensus_rejected",
    "diversification_rejected",
    "kyt_rejected",
    "cooldown_rejected",
    "churn_rejected",
)
_SHADOW_WRITTEN_HINTS = ("entry_mode_blocked", "completed")
_NO_DATA_HINTS = ("no_market_data", "stale_data")
_PRIORITY_HINTS = ("priority_rejected",)
_NO_SIGNAL_HINTS = ("no_signal",)
_ERROR_HINTS = ("error", "order_failed")


def _norm(s: str) -> str:
    return s.strip().lower()


def classify_cycle(status: str, notes: list[str] | None = None) -> str:
    """Map one injected cycle's terminal status (+ notes) to a terminal bucket.

    ``NO_SIGNAL`` is split by note: a sentiment/neutral/non-directional note
    attributes it to that stage; otherwise the generator simply returned none.
    Unknown statuses fail to ``error`` (visible, never silently dropped).
    """
    s = _norm(status)
    note_blob = " ".join(_norm(n) for n in (notes or []))

    if any(h in s for h in _PRIORITY_HINTS):
        return T_PRIORITY
    if any(h in s for h in _SHADOW_WRITTEN_HINTS):
        return T_SHADOW_WRITTEN
    if any(h in s for h in _DOWNSTREAM_HINTS):
        return T_DOWNSTREAM_REJECTED
    if any(h in s for h in _NO_DATA_HINTS):
        return T_NO_MARKET_DATA
    if any(h in s for h in _NO_SIGNAL_HINTS):
        # generator ran but produced no signal — attribute by note where possible
        if "sentiment" in note_blob:
            return T_SENTIMENT
        if "non_directional" in note_blob or "neutral" in note_blob:
            return T_NON_DIRECTIONAL
        return T_GENERATOR_NONE
    if any(h in s for h in _ERROR_HINTS):
        return T_ERROR
    return T_ERROR


def build_inloop_funnel(
    cycles: list[tuple[str, list[str]]],
    *,
    resolver_resolved_real: int = 0,
) -> dict[str, Any]:
    """Build the in-loop funnel from a list of ``(status, notes)`` per injected
    cycle. ``resolver_resolved_real`` is supplied by the resolver/ledger layer
    (how many written shadow candidates were resolved); defaults to 0.

    Returns the cumulative axes (``INLOOP_AXES``) plus a ``rejected_funnel``
    breakdown of every non-success terminal — the bucket surfaced in the report.
    """
    terminals = [classify_cycle(status, notes) for status, notes in cycles]
    counts = {t: terminals.count(t) for t in set(terminals)}

    priority_rejected = counts.get(T_PRIORITY, 0)
    sentiment_rejected = counts.get(T_SENTIMENT, 0)
    non_directional = counts.get(T_NON_DIRECTIONAL, 0)
    generator_returned_none = counts.get(T_GENERATOR_NONE, 0)
    shadow_candidate_written = counts.get(T_SHADOW_WRITTEN, 0)
    downstream_rejected = counts.get(T_DOWNSTREAM_REJECTED, 0)
    no_market_data = counts.get(T_NO_MARKET_DATA, 0)
    error = counts.get(T_ERROR, 0)

    seen = len(cycles)
    # A candidate "reached the generator" iff it produced a signal-stage outcome:
    # written, returned-none, downstream-rejected, or a sentiment/non-directional
    # note from inside the generator.
    reached_generator = (
        shadow_candidate_written
        + generator_returned_none
        + downstream_rejected
        + sentiment_rejected
        + non_directional
    )
    # Directional-accepted = passed the pre-generator gates (priority/data) and
    # reached the generator.
    directional_accepted = reached_generator

    funnel = {
        "real_analyses_seen": seen,
        "eligible_for_shadow": seen,
        "priority_rejected": priority_rejected,
        "sentiment_rejected": sentiment_rejected,
        "non_directional": non_directional,
        "directional_accepted": directional_accepted,
        "reached_signal_generator": reached_generator,
        "generator_returned_none": generator_returned_none,
        "shadow_candidate_written": shadow_candidate_written,
        "resolver_resolved_real": max(0, resolver_resolved_real),
        "rejected_funnel": {
            "priority_rejected": priority_rejected,
            "sentiment_rejected": sentiment_rejected,
            "non_directional": non_directional,
            "generator_returned_none": generator_returned_none,
            "downstream_rejected": downstream_rejected,
            "no_market_data": no_market_data,
            "error": error,
        },
    }
    return funnel


__all__ = [
    "INLOOP_AXES",
    "build_inloop_funnel",
    "classify_cycle",
]
