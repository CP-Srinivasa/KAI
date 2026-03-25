"""Seed active RSS sources into the database.

Usage:
    python -m scripts.seed_rss_sources
    python scripts/seed_rss_sources.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.enums import AuthMode, SourceStatus, SourceType  # noqa: E402
from app.core.settings import get_settings  # noqa: E402
from app.storage.db.session import build_session_factory  # noqa: E402
from app.storage.repositories.source_repo import SourceRepository  # noqa: E402
from app.storage.schemas.source import SourceCreate  # noqa: E402

# --- Curated Crypto RSS Feeds ---
# Each feed is a known, reliable crypto news source with a working RSS endpoint.
FEEDS: list[dict[str, str]] = [
    {
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "provider": "coindesk",
        "notes": "CoinDesk - leading crypto news",
    },
    {
        "url": "https://cointelegraph.com/rss",
        "provider": "cointelegraph",
        "notes": "CoinTelegraph - crypto and blockchain news",
    },
    {
        "url": "https://www.theblock.co/rss.xml",
        "provider": "theblock",
        "notes": "The Block - crypto research and news",
    },
    {
        "url": "https://bitcoinmagazine.com/feed",
        "provider": "bitcoin_magazine",
        "notes": "Bitcoin Magazine - Bitcoin-focused news",
    },
    {
        "url": "https://decrypt.co/feed",
        "provider": "decrypt",
        "notes": "Decrypt - crypto and Web3 news",
    },
    {
        "url": "https://cryptoslate.com/feed/",
        "provider": "cryptoslate",
        "notes": "CryptoSlate - crypto news and data",
    },
]


async def seed() -> None:
    settings = get_settings()
    session_factory = build_session_factory(settings.db)

    created = 0
    skipped = 0

    async with session_factory() as session:
        repo = SourceRepository(session)

        # Check existing sources to avoid duplicates
        existing = await repo.list(source_type=SourceType.RSS_FEED)
        existing_urls = {s.original_url for s in existing}

        for feed in FEEDS:
            if feed["url"] in existing_urls:
                print(f"  SKIP  {feed['provider']:20s} (already registered)")
                skipped += 1
                continue

            source = SourceCreate(
                source_type=SourceType.RSS_FEED,
                original_url=feed["url"],
                provider=feed["provider"],
                status=SourceStatus.ACTIVE,
                auth_mode=AuthMode.NONE,
                notes=feed["notes"],
            )
            result = await repo.create(source)
            print(f"  ADD   {feed['provider']:20s} -> {result.source_id}")
            created += 1

        await session.commit()

    print(f"\nDone: {created} created, {skipped} skipped")


if __name__ == "__main__":
    asyncio.run(seed())
