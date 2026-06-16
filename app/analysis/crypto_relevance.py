"""Pre-analysis crypto-relevance gate (2026-06-16).

Pure, fail-open verdict used by :class:`app.analysis.pipeline.AnalysisPipeline`
to skip the LLM for documents that carry no tradable crypto signal at all.

Why: an empirical sweep of 3000 recently-analyzed documents found ~59% with no
resolved crypto asset (``no_symbol``), dominated by the cryptobriefing site-wide
feed (70% of its docs asset-less: sports / geopolitics / general-finance news).
Of those ``no_symbol`` docs, ~99% had ZERO crypto-category keyword hits — they
were never going to become a crypto trade, yet still consumed LLM analysis
budget. This gate skips that spend.

Fail-OPEN by design: the gate only declares a document *irrelevant* when it has
NO crypto signal whatsoever — no resolved ticker, no crypto asset tag, and not a
single crypto-category keyword hit. Any crypto signal keeps the document. The
goal is to drop obvious non-crypto noise, never to risk dropping a genuine
signal; equity/etf/macro hits do NOT count as crypto-relevance (they are not
tradable crypto assets in this pipeline).
"""

from __future__ import annotations

from collections.abc import Iterable

from app.analysis.keywords.engine import KeywordHit
from app.core.domain.document import CanonicalDocument


def crypto_relevance_verdict(
    doc: CanonicalDocument,
    keyword_hits: Iterable[KeywordHit],
) -> tuple[bool, str]:
    """Return ``(relevant, reason)`` for the pre-analysis crypto-relevance gate.

    ``relevant`` is True (fail-open) when ANY of these hold:
      - the document already has a resolved ticker (``doc.tickers``), or
      - a crypto asset tag (``doc.crypto_assets``), or
      - at least one keyword hit in the ``crypto`` category.

    Otherwise ``relevant`` is False with reason ``"no_crypto_signal"`` — the
    document carries no tradable crypto signal and is a candidate to skip.
    """
    if list(getattr(doc, "tickers", None) or []):
        return True, "has_tickers"
    if list(getattr(doc, "crypto_assets", None) or []):
        return True, "has_crypto_assets"
    for hit in keyword_hits:
        if getattr(hit, "category", None) == "crypto":
            return True, "crypto_keyword_hit"
    return False, "no_crypto_signal"


__all__ = ["crypto_relevance_verdict"]
