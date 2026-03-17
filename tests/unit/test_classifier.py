import pytest

from app.core.enums import SourceStatus, SourceType
from app.ingestion.classifier import classify_url

P = SourceType.PODCAST_PAGE
F = SourceType.PODCAST_FEED
Y = SourceType.YOUTUBE_CHANNEL
R = SourceType.RSS_FEED
W = SourceType.WEBSITE
ACT = SourceStatus.ACTIVE
API = SourceStatus.REQUIRES_API


@pytest.mark.parametrize(
    "url,expected_type,expected_status",
    [
        # YouTube variants
        ("https://www.youtube.com/@Bankless", Y, ACT),
        ("https://www.youtube.com/c/JacobCryptoBury", Y, ACT),
        ("https://www.youtube.com/channel/UCnQC_G5Ycf-cF9G8hKDZQ", Y, ACT),
        ("https://youtu.be/abc123", Y, ACT),
        # Apple Podcasts → requires_api
        ("https://podcasts.apple.com/de/podcast/bitcoin-verstehen/id1513814577", P, API),
        ("https://podcasts.apple.com/us/podcast/krypto-podcast/id1345084187", P, API),
        # Spotify → requires_api
        ("https://open.spotify.com/show/abcXYZ123", P, API),
        # Podigee → podcast_feed / active
        ("https://saschahuber.podigee.io", F, ACT),
        ("https://example.podigee.io/feed/mp3", F, ACT),
        # RSS path patterns → rss_feed
        ("https://cointelegraph.com/rss", R, ACT),
        ("https://example.com/feed", R, ACT),
        ("https://example.com/feed.xml", R, ACT),
        ("https://example.com/feed/podcast", R, ACT),
        ("https://example.com/atom.xml", R, ACT),
        ("https://epicenter.tv/feed/podcast/", R, ACT),
        # Generic websites
        ("https://cointelegraph.com", W, ACT),
        ("https://www.btc-echo.de", W, ACT),
        ("https://a16zcrypto.com/posts/article/crypto-readings-resources/", W, ACT),
        ("https://www.coinbase.com/learn", W, ACT),
    ],
)
def test_classify_url(url: str, expected_type: SourceType, expected_status: SourceStatus) -> None:
    result = classify_url(url)
    assert result.source_type == expected_type, f"URL: {url}"
    assert result.status == expected_status, f"URL: {url}"


def test_apple_podcasts_has_notes() -> None:
    result = classify_url("https://podcasts.apple.com/de/podcast/x/id123")
    assert result.notes is not None
    assert "API" in result.notes


def test_spotify_has_notes() -> None:
    result = classify_url("https://open.spotify.com/show/abc")
    assert result.notes is not None
    assert "API" in result.notes


def test_podigee_has_notes() -> None:
    result = classify_url("https://myshow.podigee.io")
    assert result.notes is not None
