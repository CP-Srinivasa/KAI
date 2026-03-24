"""Research module -- core types re-exported from app.core.

This module is a thin re-export shim. All types have been moved to app.core.
"""

from app.core.briefs import BriefDocument, BriefFacet, ResearchBrief, ResearchBriefBuilder
from app.core.signals import SignalCandidate, extract_signal_candidates
from app.core.watchlists import (
    WatchlistItem,
    WatchlistRegistry,
    WatchlistType,
    parse_watchlist_type,
)

__all__ = [
    "BriefDocument",
    "BriefFacet",
    "ResearchBrief",
    "ResearchBriefBuilder",
    "SignalCandidate",
    "WatchlistItem",
    "WatchlistRegistry",
    "WatchlistType",
    "extract_signal_candidates",
    "parse_watchlist_type",
]
