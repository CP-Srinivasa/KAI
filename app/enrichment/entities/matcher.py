"""Entity Matcher — converts KeywordHits into EntityMention objects.

Bridges the gap between the keyword engine's hits and the CanonicalDocument's
entity_mentions list. The matcher is a thin adapter: it does not re-run text
matching — it consumes already-computed KeywordHits and produces typed mentions.
"""

from __future__ import annotations

from app.analysis.keywords.engine import KeywordHit
from app.core.domain.document import EntityMention

# Map keyword engine categories to EntityMention entity_type values
_CATEGORY_TO_ENTITY_TYPE: dict[str, str] = {
    "crypto": "crypto_asset",
    "equity": "equity",
    "etf": "etf",
    "macro": "macro",
    "keyword": "topic",
    # person/org categories from entity_aliases.yml pass through as-is
}


def hits_to_entity_mentions(
    hits: list[KeywordHit],
    source: str = "rule",
) -> list[EntityMention]:
    """Convert KeywordHit list to EntityMention list.

    Args:
        hits:   KeywordHit results from KeywordEngine.match()
        source: extraction method — "rule" | "llm" | "manual"

    Returns:
        List of EntityMention, one per hit, confidence derived from occurrence count.
    """
    mentions: list[EntityMention] = []
    for hit in hits:
        entity_type = _CATEGORY_TO_ENTITY_TYPE.get(hit.category, hit.category)
        # Confidence scales with occurrences, capped at 1.0
        # 1 occurrence → 0.7, 3+ → 1.0
        confidence = min(0.6 + hit.occurrences * 0.15, 1.0)
        mentions.append(
            EntityMention(
                name=hit.canonical,
                entity_type=entity_type,
                confidence=confidence,
                source=source,
            )
        )
    return mentions
