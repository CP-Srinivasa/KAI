from pathlib import Path

import yaml

MONITOR_DIR = Path("monitor")


def test_keywords_file_exists():
    assert (MONITOR_DIR / "keywords.txt").exists()


def test_keywords_not_empty():
    content = (MONITOR_DIR / "keywords.txt").read_text(encoding="utf-8")
    lines = [ln.strip() for ln in content.splitlines() if ln.strip() and not ln.startswith("#")]
    assert len(lines) > 10


def test_hashtags_file_exists():
    assert (MONITOR_DIR / "hashtags.txt").exists()


def test_youtube_channels_file_exists():
    assert (MONITOR_DIR / "youtube_channels.txt").exists()


def test_news_domains_file_exists():
    assert (MONITOR_DIR / "news_domains.txt").exists()


def test_website_sources_file_exists():
    assert (MONITOR_DIR / "website_sources.txt").exists()


def test_social_accounts_file_exists():
    assert (MONITOR_DIR / "social_accounts.txt").exists()


def test_podcast_feeds_raw_exists():
    assert (MONITOR_DIR / "podcast_feeds_raw.txt").exists()


def test_entity_aliases_valid_yaml():
    path = MONITOR_DIR / "entity_aliases.yml"
    assert path.exists()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert len(data) > 0
