"""URL and Source Classifier.

Classifies a raw URL into one of the supported SourceTypes.

Classification priority (first match wins):
  1. YouTube                  → youtube_channel / active
  2. Spotify show             → podcast_page    / requires_api
  3. Apple Podcasts           → podcast_page    / requires_api
  4. Podigee subdomain        → podcast_feed    / active
  5. RSS/Atom path patterns   → rss_feed        / active
  6. Podcast landing patterns → podcast_page    / unresolved
  7. Known reference domain   → reference_page  / active
  8. Reference path patterns  → reference_page  / active
  9. Known news domain        → news_domain     / active
 10. Default                  → website         / active

Use SourceClassifier.from_monitor_dir() to load domain lists from disk.
Use classify_url() for a stateless fallback without domain lists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from app.core.enums import SourceStatus, SourceType

# ── RSS/Atom path patterns ────────────────────────────────────────────────────
_RSS_PATH_RE = re.compile(
    r"(/feed/?$"
    r"|/feed\.xml$"
    r"|/feed\.atom$"
    r"|/rss/?$"
    r"|/rss\.xml$"
    r"|/atom\.xml$"
    r"|/feed/rss/?$"
    r"|/feed/podcast/?$"
    r"|/feed/mp3$"
    r"|/podcast\.xml$"
    r"|\.rss$"
    r"|\.atom$"
    r")",
    re.IGNORECASE,
)

# ── Podcast landing page path patterns (NOT actual feeds) ─────────────────────
_PODCAST_LANDING_RE = re.compile(
    r"(/podcasts?/"  # /podcast/ or /podcasts/ as directory segment
    r"|/podcasts?$"  # /podcast or /podcasts at end
    r"|/episodes?/"  # /episode/ or /episodes/ as directory segment
    r"|/episodes?$"
    r"|/show/"
    r"|/show$"
    r"|/shows/"
    r"|/shows$"
    r"|/hoer(en)?/?$"
    r"|/zuhoeren/?$"
    r")",
    re.IGNORECASE,
)

# ── Reference page path patterns ──────────────────────────────────────────────
_REFERENCE_PATH_RE = re.compile(
    r"(/learn/?$"
    r"|/learn/"
    r"|/research/?$"
    r"|/research/"
    r"|/resources/?$"
    r"|/resources/"
    r"|/guides/?$"
    r"|/guides/"
    r"|/education/?$"
    r"|/knowledge/?$"
    r"|/posts/article/"
    r"|/explainer"
    r"|/glossary"
    r"|/wiki"
    r")",
    re.IGNORECASE,
)

# ── Known reference domains (always reference_page regardless of path) ─────────
_REFERENCE_DOMAINS: frozenset[str] = frozenset(
    {
        "a16zcrypto.com",
        "a16z.com",
        "coinbase.com",
        "coinledger.io",
        "coin.dance",
        "river.com",
        "unchainedcrypto.com",
    }
)


@dataclass(frozen=True)
class ClassificationResult:
    source_type: SourceType
    status: SourceStatus
    notes: str | None = None


@dataclass
class SourceClassifier:
    """Domain-list-aware URL classifier.

    Load from monitor files:
        classifier = SourceClassifier.from_monitor_dir(Path("monitor"))

    Or construct directly:
        classifier = SourceClassifier(news_domains=frozenset({"coindesk.com"}))
    """

    news_domains: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_monitor_dir(cls, monitor_dir: Path) -> SourceClassifier:
        """Build a classifier with news domains loaded from news_domains.txt."""
        news_domains: set[str] = set()
        path = monitor_dir / "news_domains.txt"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                domain = line.split("|")[0].strip().lower()
                if domain:
                    news_domains.add(domain)
        return cls(news_domains=frozenset(news_domains))

    def classify(self, url: str) -> ClassificationResult:
        """Classify a raw URL into SourceType + SourceStatus."""
        url = url.strip()
        if not url:
            return ClassificationResult(
                SourceType.UNRESOLVED_SOURCE, SourceStatus.UNRESOLVED, "Empty URL"
            )
        try:
            parsed = urlparse(url)
        except Exception:
            return ClassificationResult(
                SourceType.UNRESOLVED_SOURCE, SourceStatus.UNRESOLVED, "Invalid URL"
            )

        host = parsed.netloc.lower().removeprefix("www.")
        path = parsed.path

        # 1. YouTube
        if "youtube.com" in host or host == "youtu.be":
            return ClassificationResult(SourceType.YOUTUBE_CHANNEL, SourceStatus.ACTIVE)

        # 2. Spotify show (open.spotify.com/show/ or podcasters.spotify.com/pod/show/)
        if ("open.spotify.com" in host and "/show/" in path) or (
            "podcasters.spotify.com" in host and "/show/" in path
        ):
            return ClassificationResult(
                SourceType.PODCAST_PAGE,
                SourceStatus.REQUIRES_API,
                "Spotify requires API",
            )

        # 3. Apple Podcasts
        if "podcasts.apple.com" in host:
            return ClassificationResult(
                SourceType.PODCAST_PAGE,
                SourceStatus.REQUIRES_API,
                "Apple Podcasts requires API",
            )

        # 4. Podigee
        if host.endswith(".podigee.io"):
            return ClassificationResult(
                SourceType.PODCAST_FEED,
                SourceStatus.ACTIVE,
                "Podigee feed — resolved via subdomain pattern",
            )

        # 5. RSS/Atom path patterns
        if _RSS_PATH_RE.search(path):
            return ClassificationResult(SourceType.RSS_FEED, SourceStatus.ACTIVE)

        # 6. Podcast landing page (path matches but no actual feed URL)
        if _PODCAST_LANDING_RE.search(path):
            return ClassificationResult(
                SourceType.PODCAST_PAGE,
                SourceStatus.UNRESOLVED,
                "Podcast landing page — no feed URL detected",
            )

        # 7. Known reference domain
        if host in _REFERENCE_DOMAINS:
            return ClassificationResult(SourceType.REFERENCE_PAGE, SourceStatus.ACTIVE)

        # 8. Reference path patterns on any domain
        if _REFERENCE_PATH_RE.search(path):
            return ClassificationResult(
                SourceType.REFERENCE_PAGE,
                SourceStatus.ACTIVE,
                "Reference/educational content detected from path",
            )

        # 9. Known news domain
        if host in self.news_domains:
            return ClassificationResult(SourceType.NEWS_DOMAIN, SourceStatus.ACTIVE)

        # 10. Default: treat as generic website
        return ClassificationResult(SourceType.WEBSITE, SourceStatus.ACTIVE)


# ── Module-level default classifier (no domain lists) ─────────────────────────
_default_classifier = SourceClassifier()


def classify_url(url: str) -> ClassificationResult:
    """Stateless classify without domain list lookup.

    For full accuracy (news_domain detection) use SourceClassifier.from_monitor_dir().
    """
    return _default_classifier.classify(url)
