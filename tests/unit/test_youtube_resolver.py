from pathlib import Path

import pytest

from app.ingestion.resolvers.youtube import load_youtube_channels, normalize_youtube_url


@pytest.mark.parametrize(
    "url,expected_normalized,expected_type,expected_handle",
    [
        (
            "https://www.youtube.com/@Bankless",
            "https://www.youtube.com/@Bankless",
            "handle",
            "Bankless",
        ),
        (
            "https://www.youtube.com/@CoinBureau",
            "https://www.youtube.com/@CoinBureau",
            "handle",
            "CoinBureau",
        ),
        (
            "https://www.youtube.com/c/JacobCryptoBury",
            "https://www.youtube.com/c/JacobCryptoBury",
            "custom",
            "JacobCryptoBury",
        ),
        (
            "https://www.youtube.com/channel/UCnQC_G5Ycf-cF9G8hKDZQ",
            "https://www.youtube.com/channel/UCnQC_G5Ycf-cF9G8hKDZQ",
            "channel_id",
            "UCnQC_G5Ycf-cF9G8hKDZQ",
        ),
        (
            "https://www.youtube.com/user/oldstyle",
            "https://www.youtube.com/user/oldstyle",
            "user",
            "oldstyle",
        ),
    ],
)
def test_normalize_youtube_url(
    url: str, expected_normalized: str, expected_type: str, expected_handle: str
) -> None:
    ch = normalize_youtube_url(url)
    assert ch.normalized_url == expected_normalized
    assert ch.channel_type == expected_type
    assert ch.handle == expected_handle


def test_unknown_url_returns_unknown_type() -> None:
    ch = normalize_youtube_url("https://example.com/not-youtube")
    assert ch.channel_type == "unknown"
    assert ch.notes is not None


def test_load_youtube_channels_deduplicates(tmp_path: Path) -> None:
    monitor = tmp_path / "monitor"
    monitor.mkdir()
    (monitor / "youtube_channels.txt").write_text(
        "https://www.youtube.com/@Bankless\n"
        "https://www.youtube.com/@Bankless\n"  # duplicate
        "https://www.youtube.com/@CoinBureau\n",
        encoding="utf-8",
    )
    channels = load_youtube_channels(monitor)
    assert len(channels) == 2
    handles = {ch.handle for ch in channels}
    assert "Bankless" in handles
    assert "CoinBureau" in handles


def test_load_youtube_channels_skips_comments(tmp_path: Path) -> None:
    monitor = tmp_path / "monitor"
    monitor.mkdir()
    (monitor / "youtube_channels.txt").write_text(
        "# This is a comment\n"
        "https://www.youtube.com/@Bankless\n"
        "\n"
        "https://www.youtube.com/@CoinBureau\n",
        encoding="utf-8",
    )
    channels = load_youtube_channels(monitor)
    assert len(channels) == 2


def test_load_youtube_channels_from_real_monitor() -> None:
    channels = load_youtube_channels(Path("monitor"))
    assert len(channels) > 0
    # All should have a normalized URL
    for ch in channels:
        assert ch.normalized_url.startswith("https://")
