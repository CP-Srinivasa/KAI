from pathlib import Path

import pytest

from app.core.enums import SourceStatus, SourceType
from app.ingestion.resolvers.podcast import load_and_resolve_podcasts, resolve_podcast_url

F = SourceType.PODCAST_FEED
P = SourceType.PODCAST_PAGE
U = SourceType.UNRESOLVED_SOURCE
ACT = SourceStatus.ACTIVE
API = SourceStatus.REQUIRES_API
UNR = SourceStatus.UNRESOLVED


@pytest.mark.parametrize(
    "url,expected_type,expected_status",
    [
        # Direct RSS feed
        ("https://epicenter.tv/feed/podcast/", F, ACT),
        ("https://example.com/feed.xml", F, ACT),
        # Podigee → resolves to feed
        ("https://saschahuber.podigee.io", F, ACT),
        # Apple → requires_api
        ("https://podcasts.apple.com/de/podcast/bitcoin-verstehen/id1513814577", P, API),
        # Spotify → requires_api
        ("https://open.spotify.com/show/abcXYZ", P, API),
        # Generic website → unresolved
        ("https://www.btc-echo.de/podcasts/", U, UNR),
    ],
)
def test_resolve_podcast_url(
    url: str, expected_type: SourceType, expected_status: SourceStatus
) -> None:
    result = resolve_podcast_url(url)
    assert result.source_type == expected_type, f"URL: {url}"
    assert result.status == expected_status, f"URL: {url}"


def test_resolve_podigee_constructs_feed_url() -> None:
    result = resolve_podcast_url("https://saschahuber.podigee.io")
    assert result.resolved_url == "https://saschahuber.podigee.io/feed/mp3"


def test_resolve_podigee_already_has_feed_url() -> None:
    result = resolve_podcast_url("https://saschahuber.podigee.io/feed/mp3")
    assert result.resolved_url == "https://saschahuber.podigee.io/feed/mp3"


def test_resolve_rss_has_resolved_url() -> None:
    result = resolve_podcast_url("https://epicenter.tv/feed/podcast/")
    assert result.resolved_url == "https://epicenter.tv/feed/podcast/"


def test_apple_has_no_resolved_url() -> None:
    result = resolve_podcast_url("https://podcasts.apple.com/de/podcast/x/id123")
    assert result.resolved_url is None


def test_load_and_resolve_returns_tuples(tmp_path: Path) -> None:
    monitor = tmp_path / "monitor"
    monitor.mkdir()
    (monitor / "podcast_feeds_raw.txt").write_text(
        "# Comment\n"
        "https://epicenter.tv/feed/podcast/\n"
        "https://podcasts.apple.com/de/podcast/x/id123\n"
        "https://saschahuber.podigee.io\n",
        encoding="utf-8",
    )
    resolved, unresolved = load_and_resolve_podcasts(monitor)
    assert len(resolved) == 2  # epicenter + podigee
    assert len(unresolved) == 1  # apple


def test_load_and_resolve_from_real_monitor() -> None:
    resolved, unresolved = load_and_resolve_podcasts(Path("monitor"))
    assert isinstance(resolved, list)
    assert isinstance(unresolved, list)
    for src in unresolved:
        assert src.status in (SourceStatus.REQUIRES_API, SourceStatus.UNRESOLVED)
