"""Entity matcher utilities for the normalization/analysis boundary.

Converts keyword hits into canonical EntityMention records without re-running
text matching.
"""

from __future__ import annotations

from app.analysis.keywords.engine import KeywordHit
from app.core.domain.document import EntityMention

_CATEGORY_TO_ENTITY_TYPE: dict[str, str] = {
    "crypto": "crypto_asset",
    "equity": "equity",
    "etf": "etf",
    "macro": "macro",
    "keyword": "topic",
    # person/org categories pass through as-is
}


def hits_to_entity_mentions(
    hits: list[KeywordHit],
    source: str = "rule",
) -> list[EntityMention]:
    """Convert KeywordHit list to EntityMention list."""
    mentions: list[EntityMention] = []
    for hit in hits:
        entity_type = _CATEGORY_TO_ENTITY_TYPE.get(hit.category, hit.category)
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

