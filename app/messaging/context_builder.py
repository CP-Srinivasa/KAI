"""Build LLM context from recent KAI analyses for the Telegram bot.

Queries the database for recently analyzed documents and formats them
into a concise context string that can be injected into the LLM prompt.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.storage.repositories.document_repo import DocumentRepository

logger = logging.getLogger(__name__)

# Maximum documents to include in context (keeps token usage reasonable)
_MAX_CONTEXT_DOCS = 10


def _format_doc(doc: Any) -> str:
    """Format a single CanonicalDocument into a concise context line."""
    parts: list[str] = []

    # Title
    title = (doc.title or "Ohne Titel")[:120]
    parts.append(f"- {title}")

    # Sentiment + Priority
    sentiment = doc.sentiment_label.value if doc.sentiment_label else "?"
    priority = doc.priority_score or 0
    parts.append(f"  Sentiment: {sentiment} | Prioritaet: {priority}/10")

    # Assets
    assets: list[str] = []
    if doc.crypto_assets:
        assets.extend(doc.crypto_assets[:5])
    if doc.tickers:
        assets.extend(doc.tickers[:3])
    if assets:
        parts.append(f"  Assets: {', '.join(assets)}")

    # Summary
    if doc.summary:
        parts.append(f"  Zusammenfassung: {doc.summary[:200]}")

    return "\n".join(parts)


async def build_analysis_context(session_factory: async_sessionmaker[AsyncSession]) -> str:
    """Query recent analyses and return a formatted context string.

    Returns an empty string on error (fail-open for context — the bot
    still works, just without data enrichment).
    """
    try:
        async with session_factory() as session:
            repo = DocumentRepository(session)
            docs = await repo.get_recent_analyzed(limit=_MAX_CONTEXT_DOCS)

        if not docs:
            return ""

        # Count sentiments
        sentiments = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0, "MIXED": 0}
        high_priority = []
        for doc in docs:
            label = doc.sentiment_label.value if doc.sentiment_label else "NEUTRAL"
            sentiments[label] = sentiments.get(label, 0) + 1
            if (doc.priority_score or 0) >= 7:
                high_priority.append(doc)

        # Build context
        lines: list[str] = []
        lines.append(f"Letzte {len(docs)} analysierte Nachrichten:")
        lines.append(
            f"Sentiment-Verteilung: "
            f"{sentiments.get('BULLISH', 0)} bullish, "
            f"{sentiments.get('BEARISH', 0)} bearish, "
            f"{sentiments.get('NEUTRAL', 0)} neutral, "
            f"{sentiments.get('MIXED', 0)} mixed"
        )

        if high_priority:
            lines.append("\nTop-Prioritaet (>= 7/10):")
            for doc in high_priority[:5]:
                lines.append(_format_doc(doc))

        # Add remaining recent docs (lower priority)
        remaining = [d for d in docs if d not in high_priority][:5]
        if remaining:
            lines.append("\nWeitere aktuelle Analysen:")
            for doc in remaining:
                lines.append(_format_doc(doc))

        return "\n".join(lines)

    except Exception as exc:  # noqa: BLE001
        logger.error("[CONTEXT] Failed to build analysis context: %s", exc)
        return ""


def make_context_provider(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], Any]:
    """Create an async context provider callback for the bot."""

    async def provider() -> str:
        return await build_analysis_context(session_factory)

    return provider
