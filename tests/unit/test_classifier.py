"""Tests for SourceClassifier — all 8 classification types + edge cases."""

import pytest

from app.core.enums import SourceStatus, SourceType
from app.ingestion.classifier import SourceClassifier, classify_url

NEWS_DOMAINS = frozenset(
    {"coindesk.com", "cointelegraph.com", "reuters.com", "bloomberg.com", "theblock.co"}
)


@pytest.fixture
def classifier() -> SourceClassifier:
    return SourceClassifier(news_domains=NEWS_DOMAINS)


class TestYouTube:
    def test_youtube_handle(self, classifier):
        r = classifier.classify("https://www.youtube.com/@coinbureau")
        assert r.source_type == SourceType.YOUTUBE_CHANNEL
        assert r.status == SourceStatus.ACTIVE

    def test_youtube_channel_id(self, classifier):
        r = classifier.classify("https://www.youtube.com/channel/UCqK_GSMbpiV8spgD3ZGloSw")
        assert r.source_type == SourceType.YOUTUBE_CHANNEL

    def test_youtu_be(self, classifier):
        r = classifier.classify("https://youtu.be/dQw4w9WgXcQ")
        assert r.source_type == SourceType.YOUTUBE_CHANNEL


class TestSpotify:
    def test_spotify_show(self, classifier):
        r = classifier.classify("https://open.spotify.com/show/5As7pWAFIGJMEVRFxHOEXB")
        assert r.source_type == SourceType.PODCAST_PAGE
        assert r.status == SourceStatus.REQUIRES_API

    def test_podcasters_spotify_show(self, classifier):
        r = classifier.classify("https://podcasters.spotify.com/pod/show/teachmedefi")
        assert r.source_type == SourceType.PODCAST_PAGE
        assert r.status == SourceStatus.REQUIRES_API

    def test_spotify_episode_is_podcast_page(self, classifier):
        # Individual episode link — not subscribable, classified as podcast_page/unresolved
        r = classifier.classify("https://open.spotify.com/episode/abc123")
        assert r.source_type == SourceType.PODCAST_PAGE
        assert r.status == SourceStatus.UNRESOLVED


class TestApplePodcasts:
    def test_apple_podcast(self, classifier):
        r = classifier.classify(
            "https://podcasts.apple.com/de/podcast/bitcoin-verstehen/id1513814577"
        )
        assert r.source_type == SourceType.PODCAST_PAGE
        assert r.status == SourceStatus.REQUIRES_API


class TestPodigee:
    def test_podigee_feed(self, classifier):
        r = classifier.classify("https://einundzwanzig.podigee.io/feed/mp3")
        assert r.source_type == SourceType.PODCAST_FEED
        assert r.status == SourceStatus.ACTIVE

    def test_podigee_base_url(self, classifier):
        r = classifier.classify("https://mypodcast.podigee.io")
        assert r.source_type == SourceType.PODCAST_FEED


class TestRSSFeed:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/feed",
            "https://example.com/feed.xml",
            "https://example.com/rss",
            "https://example.com/rss.xml",
            "https://example.com/atom.xml",
            "https://example.com/feed/rss",
            "https://example.com/feed/mp3",
            "https://example.com/podcast.xml",
            "https://example.com/show.rss",
        ],
    )
    def test_rss_paths(self, classifier, url):
        r = classifier.classify(url)
        assert r.source_type == SourceType.RSS_FEED
        assert r.status == SourceStatus.ACTIVE

    def test_btc_echo_rss(self, classifier):
        r = classifier.classify("https://www.btc-echo.de/feed")
        assert r.source_type == SourceType.RSS_FEED


class TestPodcastLandingPage:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.btc-echo.de/podcasts/",
            "https://epicenter.tv/episodes/",
            "https://www.wiwo.de/podcast/boersenwoche/",
            "https://example.com/podcast",
            "https://example.com/show",
        ],
    )
    def test_podcast_landing_pages(self, classifier, url):
        r = classifier.classify(url)
        assert r.source_type == SourceType.PODCAST_PAGE
        assert r.status == SourceStatus.UNRESOLVED


class TestNewsDomain:
    def test_known_news_domain(self, classifier):
        r = classifier.classify("https://coindesk.com")
        assert r.source_type == SourceType.NEWS_DOMAIN
        assert r.status == SourceStatus.ACTIVE

    def test_www_stripped(self, classifier):
        r = classifier.classify("https://www.reuters.com")
        assert r.source_type == SourceType.NEWS_DOMAIN

    def test_unknown_domain_not_news(self, classifier):
        r = classifier.classify("https://unknown-blog.com")
        assert r.source_type != SourceType.NEWS_DOMAIN

    def test_default_classifier_has_no_news_domains(self):
        # Without domain list, news sites fall back to website
        r = classify_url("https://coindesk.com")
        assert r.source_type == SourceType.WEBSITE


class TestReferencePage:
    @pytest.mark.parametrize(
        "url",
        [
            "https://coinbase.com/learn/crypto-basics",
            "https://coinledger.io/guides/crypto-tax",
            "https://a16zcrypto.com/posts/article/crypto-reading",
            "https://example.com/research/bitcoin",
            "https://example.com/resources/",
            "https://example.com/glossary",
            "https://example.com/wiki/bitcoin",
        ],
    )
    def test_reference_pages(self, classifier, url):
        r = classifier.classify(url)
        assert r.source_type == SourceType.REFERENCE_PAGE
        assert r.status == SourceStatus.ACTIVE

    def test_reference_domain_any_path(self, classifier):
        r = classifier.classify("https://a16zcrypto.com/some-random-path")
        assert r.source_type == SourceType.REFERENCE_PAGE


class TestWebsite:
    def test_plain_domain(self, classifier):
        r = classifier.classify("https://tradingview.com")
        assert r.source_type == SourceType.WEBSITE

    def test_unknown_domain(self, classifier):
        r = classifier.classify("https://some-random-site.com")
        assert r.source_type == SourceType.WEBSITE
        assert r.status == SourceStatus.ACTIVE


class TestEdgeCases:
    def test_empty_url(self, classifier):
        r = classifier.classify("")
        assert r.source_type == SourceType.UNRESOLVED_SOURCE
        assert r.status == SourceStatus.UNRESOLVED

    def test_whitespace_url(self, classifier):
        r = classifier.classify("   ")
        assert r.source_type == SourceType.UNRESOLVED_SOURCE

    def test_url_with_trailing_slash(self, classifier):
        r = classifier.classify("https://www.youtube.com/@coinbureau/")
        assert r.source_type == SourceType.YOUTUBE_CHANNEL

    def test_from_monitor_dir_missing_file(self, tmp_path):
        c = SourceClassifier.from_monitor_dir(tmp_path)
        assert len(c.news_domains) == 0

    def test_from_monitor_dir_with_file(self, tmp_path):
        (tmp_path / "news_domains.txt").write_text(
            "# comment\ncoindesk.com|0.87|crypto|en\nreuters.com|0.98|finance|en\n"
        )
        c = SourceClassifier.from_monitor_dir(tmp_path)
        assert "coindesk.com" in c.news_domains
        assert "reuters.com" in c.news_domains
