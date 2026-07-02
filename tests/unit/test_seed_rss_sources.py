"""Guards the curated RSS seed list (scripts/seed_rss_sources.py).

Cheap structural contract: every feed is well-formed and unique, and the
source-scout's regulatory primary source (SEC EDGAR, 2026-06-14) stays wired —
so a future edit can't silently drop or duplicate a feed.
"""

from __future__ import annotations

from urllib.parse import urlparse

from scripts.seed_rss_sources import FEEDS


def test_feeds_are_well_formed() -> None:
    assert FEEDS, "seed feed list must not be empty"
    for feed in FEEDS:
        assert set(feed) >= {"url", "provider", "notes"}, f"missing keys: {feed}"
        parsed = urlparse(feed["url"])
        assert parsed.scheme == "https", f"non-https feed: {feed['url']}"
        assert parsed.netloc, f"unparseable feed url: {feed['url']}"
        assert feed["provider"].strip(), f"empty provider: {feed}"
        assert feed["notes"].strip(), f"empty notes: {feed}"


def test_feed_urls_and_providers_are_unique() -> None:
    urls = [f["url"] for f in FEEDS]
    providers = [f["provider"] for f in FEEDS]
    assert len(urls) == len(set(urls)), "duplicate feed URL in seed list"
    assert len(providers) == len(set(providers)), "duplicate provider in seed list"


def test_sec_edgar_regulatory_source_is_wired() -> None:
    # source-scout 2026-06-14 Top-Pick: ETF-Approvals/Enforcement als
    # directional Primärquelle für den Edge-Funnel.
    sec = [f for f in FEEDS if f["provider"] == "sec_edgar"]
    assert len(sec) == 1, "SEC EDGAR feed must be present exactly once"
    assert "sec.gov" in sec[0]["url"]
