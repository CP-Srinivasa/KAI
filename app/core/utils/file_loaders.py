"""
Monitor File Loaders
====================
Load keyword lists, source registries, and entity aliases
from the monitor/ directory. All loaders are pure functions
with no side effects — they return data structures.

These files are configuration, not code.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)


def load_keywords(path: Path) -> list[str]:
    """
    Load keyword list from a flat text file.
    Lines starting with '#' are comments. Blank lines are skipped.
    Returns deduplicated list preserving order.
    """
    if not path.exists():
        logger.warning("keywords_file_not_found", path=str(path))
        return []

    seen: set[str] = set()
    keywords: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key = line.lower()
            if key not in seen:
                seen.add(key)
                keywords.append(line)

    logger.info("keywords_loaded", count=len(keywords), path=str(path))
    return keywords


def load_youtube_channel_urls(path: Path) -> list[str]:
    """
    Load YouTube channel URLs from file.
    Returns raw URL strings (normalization happens in youtube/registry.py).
    """
    if not path.exists():
        logger.warning("youtube_channels_file_not_found", path=str(path))
        return []

    urls: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)

    logger.info("youtube_urls_loaded", count=len(urls), path=str(path))
    return urls


def load_website_sources(path: Path) -> list[dict[str, str]]:
    """
    Load website sources from pipe-delimited file.
    Format: domain|name|type|language|category|status
    Returns list of dicts.
    """
    if not path.exists():
        logger.warning("website_sources_file_not_found", path=str(path))
        return []

    fields = ["domain", "name", "type", "language", "category", "status"]
    sources: list[dict[str, str]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < len(fields):
                continue
            sources.append(dict(zip(fields, parts)))

    logger.info("website_sources_loaded", count=len(sources), path=str(path))
    return sources


def load_news_domains(path: Path) -> dict[str, float]:
    """
    Load news domain credibility scores.
    Format: domain|credibility_score|category|language
    Returns {domain: credibility_score} mapping.
    """
    if not path.exists():
        logger.warning("news_domains_file_not_found", path=str(path))
        return {}

    domains: dict[str, float] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < 2:
                continue
            try:
                domains[parts[0].strip()] = float(parts[1].strip())
            except ValueError:
                pass

    logger.info("news_domains_loaded", count=len(domains), path=str(path))
    return domains


def load_social_accounts(path: Path) -> list[dict[str, str]]:
    """
    Load social media account watchlist.
    Format: platform|handle|name|category
    """
    if not path.exists():
        logger.warning("social_accounts_file_not_found", path=str(path))
        return []

    fields = ["platform", "handle", "name", "category"]
    accounts: list[dict[str, str]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < len(fields):
                continue
            accounts.append(dict(zip(fields, parts)))

    logger.info("social_accounts_loaded", count=len(accounts), path=str(path))
    return accounts


def load_entity_aliases(path: Path) -> list[dict[str, Any]]:
    """
    Load entity alias groups from YAML.
    Returns list of alias group dicts.
    """
    if not path.exists():
        logger.warning("entity_aliases_file_not_found", path=str(path))
        return []

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    aliases = data.get("entity_aliases", []) if data else []
    logger.info("entity_aliases_loaded", count=len(aliases), path=str(path))
    return aliases


def load_podcast_feeds_raw(path: Path) -> list[str]:
    """
    Load raw podcast/source URLs (unclassified) from file.
    """
    if not path.exists():
        logger.warning("podcast_feeds_raw_file_not_found", path=str(path))
        return []

    urls: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)

    logger.info("podcast_feeds_raw_loaded", count=len(urls), path=str(path))
    return urls
