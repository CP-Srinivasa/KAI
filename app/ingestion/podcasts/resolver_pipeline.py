"""
Podcast Resolver Pipeline
=========================
Full pipeline: load raw podcast URLs → classify → split into
resolved (active RSS feeds) and unresolved (requires_api, manual).

Input:  monitor/podcast_feeds_raw.txt
Output:
  - Structured list of SourceEntry objects for the SourceRegistry
  - Optionally writes resolved/unresolved summary files

IMPORTANT: No RSS URLs are faked. Sources that cannot be auto-resolved
are classified with their correct status and requires_action notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.enums import AuthMode, SourceStatus, SourceType
from app.core.logging import get_logger
from app.core.utils.file_loaders import load_podcast_feeds_raw
from app.ingestion.resolvers.podcast_resolver import ClassifiedSource, classify_batch
from app.ingestion.source_registry import SourceEntry, SourceRegistry

logger = get_logger(__name__)


@dataclass
class PipelineResult:
    """Result of running the podcast resolver pipeline."""
    resolved: list[SourceEntry]       # status=ACTIVE, has RSS URL
    unresolved: list[ClassifiedSource]  # requires_api / manual_resolution
    total_input: int

    @property
    def resolved_count(self) -> int:
        return len(self.resolved)

    @property
    def unresolved_count(self) -> int:
        return len(self.unresolved)

    def summary(self) -> dict[str, int]:
        return {
            "total_input": self.total_input,
            "resolved": self.resolved_count,
            "unresolved": self.unresolved_count,
        }


def _classified_to_entry(cs: ClassifiedSource) -> SourceEntry:
    """Convert a ClassifiedSource to a SourceEntry for the registry."""
    # Use resolved RSS URL if available, otherwise original URL
    url = cs.resolved_rss_url or cs.url

    # Generate source_id if not set by classifier
    source_id = cs.source_id
    if not source_id:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "").replace(".", "_")
        path_part = parsed.path.strip("/").replace("/", "_")[:40]
        source_id = f"{domain}_{path_part}".strip("_") or "podcast_unknown"

    return SourceEntry(
        source_id=source_id,
        source_name=source_id.replace("_", " ").title(),
        source_type=cs.source_type,
        status=cs.status,
        url=url,
        auth_mode=AuthMode.NONE,
        notes=cs.notes,
        requires_action=cs.requires_action,
    )


def run_pipeline(raw_feeds_path: Path) -> PipelineResult:
    """
    Load raw podcast/source URLs, classify each, and split into
    resolved (active RSS) and unresolved groups.

    Args:
        raw_feeds_path: Path to monitor/podcast_feeds_raw.txt

    Returns:
        PipelineResult with resolved SourceEntry list and unresolved ClassifiedSource list
    """
    raw_urls = load_podcast_feeds_raw(raw_feeds_path)
    classified = classify_batch(raw_urls)

    resolved: list[SourceEntry] = []
    unresolved: list[ClassifiedSource] = []

    for cs in classified:
        if cs.status == SourceStatus.ACTIVE and cs.resolved_rss_url:
            entry = _classified_to_entry(cs)
            resolved.append(entry)
            logger.debug(
                "podcast_resolved",
                url=cs.url[:80],
                rss=cs.resolved_rss_url[:80],
                category=cs.category.value,
            )
        else:
            unresolved.append(cs)
            logger.debug(
                "podcast_unresolved",
                url=cs.url[:80],
                status=cs.status.value,
                requires_action=cs.requires_action[:80] if cs.requires_action else "",
            )

    result = PipelineResult(
        resolved=resolved,
        unresolved=unresolved,
        total_input=len(raw_urls),
    )

    logger.info(
        "podcast_pipeline_complete",
        **result.summary(),
    )
    return result


def register_resolved_podcasts(
    registry: SourceRegistry,
    raw_feeds_path: Path,
) -> PipelineResult:
    """
    Run the pipeline and register all resolved podcast sources into the registry.
    Unresolved sources are NOT registered (they cannot be fetched).

    Returns the full PipelineResult for inspection/logging.
    """
    result = run_pipeline(raw_feeds_path)
    if result.resolved:
        registry.register_many(result.resolved)
    return result


def print_unresolved_report(result: PipelineResult) -> None:
    """
    Print a human-readable report of unresolved sources to stdout.
    Useful for CLI commands and debugging.
    """
    if not result.unresolved:
        print("All sources resolved.")
        return

    print(f"\n{'='*60}")
    print(f"UNRESOLVED SOURCES ({result.unresolved_count})")
    print(f"{'='*60}")

    by_status: dict[str, list[ClassifiedSource]] = {}
    for cs in result.unresolved:
        key = cs.status.value
        by_status.setdefault(key, []).append(cs)

    for status_val, items in sorted(by_status.items()):
        print(f"\n[{status_val.upper()}] — {len(items)} source(s)")
        for cs in items:
            print(f"  URL:    {cs.url}")
            if cs.notes:
                print(f"  Notes:  {cs.notes}")
            if cs.requires_action:
                print(f"  Action: {cs.requires_action}")
            print()
