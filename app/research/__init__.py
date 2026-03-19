"""Research & Signal Generation module.

Public API for Sprint 4 downstream consumers (CLI, API, Antigravity workflows).

Layer contract:
- Input:  list[CanonicalDocument] — must have status=ANALYZED and is_analyzed=True
- Output: ResearchBrief | list[SignalCandidate] — in-memory, never written to DB
- WatchlistRegistry — loaded from monitor/watchlists.yml, used to scope input documents

No code outside this module may instantiate ResearchBrief or SignalCandidate directly.
All construction goes through ResearchBriefBuilder.build() and extract_signal_candidates().
"""

from app.research.briefs import BriefDocument, ResearchBrief, ResearchBriefBuilder
from app.research.signals import SignalCandidate, extract_signal_candidates
from app.research.watchlists import WatchlistRegistry

__all__ = [
    "BriefDocument",
    "ResearchBrief",
    "ResearchBriefBuilder",
    "SignalCandidate",
    "WatchlistRegistry",
    "extract_signal_candidates",
]
