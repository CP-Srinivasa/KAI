"""Shared RSS ingestion workflow for classify -> resolve -> fetch.

This module intentionally stops at FetchResult.
Deduplication and persistence stay outside ingestion to respect module contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.enums import SourceStatus, SourceType
from app.ingestion.base.interfaces import FetchResult, SourceMetadata
from app.ingestion.classifier import ClassificationResult, SourceClassifier
from app.ingestion.resolvers.rss import RSSResolveResult, resolve_rss_feed
from app.ingestion.rss.adapter import RSSFeedAdapter


@dataclass(frozen=True)
class RSSCollectedFeed:
    classification: ClassificationResult
    resolved_feed: RSSResolveResult
    fetch_result: FetchResult


async def collect_rss_feed(
    *,
    url: str,
    source_id: str,
    source_name: str,
    monitor_dir: Path,
    timeout: int = 15,
    max_retries: int = 3,
    status: SourceStatus = SourceStatus.ACTIVE,
    provider: str | None = None,
) -> RSSCollectedFeed:
    """Collect RSS documents using the canonical ingestion steps.

    Pipeline:
        classify -> guard (reject non-feed types) -> resolve/validate -> fetch -> FetchResult

    Raises an early error for YouTube channels, podcast landing pages, social sources, etc.
    Only RSS_FEED and PODCAST_FEED are permitted through this pipeline.
    """
    _rss_compatible = {SourceType.RSS_FEED, SourceType.PODCAST_FEED}

    classifier = SourceClassifier.from_monitor_dir(monitor_dir)
    classification = classifier.classify(url)

    # Guard: refuse to treat non-feed sources as RSS feeds
    if classification.source_type not in _rss_compatible:
        _dummy_resolved = RSSResolveResult(
            url=url, is_valid=False, resolved_url=None, feed_title=None, entry_count=0,
            error=f"'{classification.source_type.value}' is not an RSS/Atom feed.",
        )
        return RSSCollectedFeed(
            classification=classification,
            resolved_feed=_dummy_resolved,
            fetch_result=FetchResult(
                source_id=source_id,
                documents=[],
                fetched_at=datetime.now(UTC),
                success=False,
                error=(
                    f"Refused to fetch '{classification.source_type.value}' source as RSS. "
                    f"URL: {url}. Use the appropriate resolver for this source type."
                ),
                metadata={"classified_source_type": classification.source_type.value},
            ),
        )

    resolved_feed = await resolve_rss_feed(url, timeout=timeout)

    if not resolved_feed.is_valid:
        error = (
            "URL is not a valid RSS/Atom feed. "
            f"Classified as {classification.source_type.value} ({classification.status.value}). "
            f"{resolved_feed.error or 'Feed validation failed.'}"
        )
        return RSSCollectedFeed(
            classification=classification,
            resolved_feed=resolved_feed,
            fetch_result=FetchResult(
                source_id=source_id,
                documents=[],
                fetched_at=datetime.now(UTC),
                success=False,
                error=error,
                metadata={
                    "classified_source_type": classification.source_type.value,
                    "resolved_url": resolved_feed.resolved_url,
                    "feed_title": resolved_feed.feed_title,
                },
            ),
        )

    metadata = SourceMetadata(
        source_id=source_id,
        source_name=source_name,
        source_type=classification.source_type,  # RSS_FEED or PODCAST_FEED
        url=resolved_feed.resolved_url or resolved_feed.url,
        status=status,
        provider=provider,
        notes=classification.notes,
        metadata={"classified_source_type": classification.source_type.value},
    )
    adapter = RSSFeedAdapter(metadata, timeout=timeout, max_retries=max_retries)
    fetch_result = await adapter.fetch()
    fetch_result.metadata.update(
        {
            "classified_source_type": classification.source_type.value,
            "resolved_url": resolved_feed.resolved_url,
            "feed_title": resolved_feed.feed_title,
        }
    )

    if not fetch_result.success:
        fetch_result.error = (
            f"RSS fetch failed for {resolved_feed.resolved_url or resolved_feed.url}. "
            f"{fetch_result.error or 'Unknown error.'}"
        )

    return RSSCollectedFeed(
        classification=classification,
        resolved_feed=resolved_feed,
        fetch_result=fetch_result,
    )
