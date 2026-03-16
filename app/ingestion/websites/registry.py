"""
Website Source Registry
=======================
Loads website sources from monitor/website_sources.txt and
populates the global SourceRegistry with SourceEntry objects.

File format: domain|name|type|language|category|status
See: app/core/utils/file_loaders.py → load_website_sources()
"""

from __future__ import annotations

from pathlib import Path

from app.core.enums import AuthMode, SourceStatus, SourceType
from app.core.logging import get_logger
from app.core.utils.file_loaders import load_news_domains, load_website_sources
from app.ingestion.source_registry import SourceEntry, SourceRegistry

logger = get_logger(__name__)

# Fallback credibility score when domain is not in news_domains.txt
_DEFAULT_CREDIBILITY = 0.5


def build_website_registry(
    sources_path: Path,
    domains_path: Path | None = None,
) -> list[SourceEntry]:
    """
    Load website_sources.txt (and optionally news_domains.txt for credibility scores).
    Returns a list of SourceEntry objects ready to register.

    Args:
        sources_path: Path to monitor/website_sources.txt
        domains_path: Optional path to monitor/news_domains.txt for credibility scores
    """
    raw_sources = load_website_sources(sources_path)

    # Load credibility scores if available
    credibility_map: dict[str, float] = {}
    if domains_path:
        credibility_map = load_news_domains(domains_path)

    entries: list[SourceEntry] = []
    for ws in raw_sources:
        domain = ws.get("domain", "").strip()
        if not domain:
            continue

        source_id = domain.replace(".", "_").replace("/", "_").replace("-", "_")

        try:
            source_type = SourceType(ws.get("type", "website"))
        except ValueError:
            logger.warning(
                "website_unknown_type",
                domain=domain,
                type_value=ws.get("type"),
            )
            source_type = SourceType.WEBSITE

        try:
            status = SourceStatus(ws.get("status", "active"))
        except ValueError:
            logger.warning(
                "website_unknown_status",
                domain=domain,
                status_value=ws.get("status"),
            )
            status = SourceStatus.MANUAL_RESOLUTION

        credibility = credibility_map.get(domain, _DEFAULT_CREDIBILITY)
        language = ws.get("language", "en").strip()
        category = ws.get("category", "general").strip()
        name = ws.get("name", domain).strip()

        entry = SourceEntry(
            source_id=source_id,
            source_name=name,
            source_type=source_type,
            status=status,
            url=f"https://{domain}",
            language=language,
            categories=[category],
            auth_mode=AuthMode.NONE,
            credibility_score=credibility,
        )
        entries.append(entry)

    logger.info(
        "website_registry_built",
        total=len(entries),
        active=sum(1 for e in entries if e.is_active),
    )
    return entries


def register_websites(
    registry: SourceRegistry,
    sources_path: Path,
    domains_path: Path | None = None,
) -> int:
    """
    Build website entries and register them into the given registry.
    Returns number of entries registered.
    """
    entries = build_website_registry(sources_path, domains_path)
    registry.register_many(entries)
    return len(entries)
