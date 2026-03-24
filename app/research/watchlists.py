"""Typed research watchlists backed by monitor/watchlists.yml.

The research layer intentionally reuses the existing monitor file instead of
introducing a second persistence path. Assets, persons, topics, and sources all
share the same tag-based grouping model so CLI and future API callers can use a
single access pattern.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse

import yaml  # type: ignore[import-untyped]

from app.analysis.keywords.watchlist import WatchlistEntry, load_watchlist
from app.core.domain.document import CanonicalDocument
from app.normalization.cleaner import normalize_url

type WatchlistType = Literal["assets", "persons", "topics", "sources"]
SUPPORTED_WATCHLIST_TYPES: tuple[WatchlistType, ...] = (
    "assets",
    "persons",
    "topics",
    "sources",
)

_ASSET_SECTION_BY_CATEGORY = {
    "crypto": "crypto",
    "equity": "equities",
    "etf": "etfs",
    "macro": "macro",
}


@dataclass(frozen=True)
class WatchlistItem:
    """Single watchlist item from monitor/watchlists.yml."""

    item_type: WatchlistType
    identifier: str
    display_name: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    section: str
    category: str | None = None
    score: float | None = None

    def normalized_terms(self) -> set[str]:
        terms = {_normalize_text(self.identifier), _normalize_text(self.display_name)}
        terms.update(_normalize_text(alias) for alias in self.aliases)
        return {term for term in terms if term}


def parse_watchlist_type(value: str) -> WatchlistType:
    normalized = value.strip().lower()
    if normalized not in SUPPORTED_WATCHLIST_TYPES:
        supported = ", ".join(SUPPORTED_WATCHLIST_TYPES)
        raise ValueError(f"Unsupported watchlist type '{value}'. Expected one of: {supported}.")
    return cast(WatchlistType, normalized)


def _normalize_text(value: str) -> str:
    return value.strip().lower()


def _normalize_domain(value: str) -> str:
    raw_value = value.strip()
    if not raw_value:
        return ""
    candidate = raw_value if "://" in raw_value else f"https://{raw_value}"
    normalized = normalize_url(candidate)
    parsed = urlparse(normalized)
    return (parsed.netloc or parsed.path).strip().lower()


def _load_watchlists_data(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return cast(dict[str, object], yaml.safe_load(handle) or {})


def _coerce_tags(raw_tags: object) -> tuple[str, ...]:
    if not isinstance(raw_tags, list):
        return ()
    tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
    return tuple(tags)


def _coerce_aliases(raw_aliases: object) -> tuple[str, ...]:
    if not isinstance(raw_aliases, list):
        return ()
    aliases = [str(alias).strip() for alias in raw_aliases if str(alias).strip()]
    return tuple(aliases)


def _parse_named_items(
    raw_items: object,
    *,
    item_type: WatchlistType,
    section: str,
) -> tuple[WatchlistItem, ...]:
    if not isinstance(raw_items, list):
        return ()

    items: list[WatchlistItem] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        identifier = str(raw_item.get("name", "")).strip()
        if not identifier:
            continue
        items.append(
            WatchlistItem(
                item_type=item_type,
                identifier=identifier,
                display_name=identifier,
                aliases=_coerce_aliases(raw_item.get("aliases")),
                tags=_coerce_tags(raw_item.get("tags")),
                section=section,
            )
        )
    return tuple(items)


def _parse_source_items(raw_items: object) -> tuple[WatchlistItem, ...]:
    if not isinstance(raw_items, list):
        return ()

    items: list[WatchlistItem] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        domain = str(raw_item.get("domain", "")).strip()
        if not domain:
            continue
        raw_score = raw_item.get("credibility")
        score = float(raw_score) if isinstance(raw_score, int | float) else None
        items.append(
            WatchlistItem(
                item_type="sources",
                identifier=domain,
                display_name=domain,
                aliases=(),
                tags=_coerce_tags(raw_item.get("tags")),
                section="domains",
                score=score,
            )
        )
    return tuple(items)


def _asset_item_from_entry(entry: WatchlistEntry) -> WatchlistItem:
    section = _ASSET_SECTION_BY_CATEGORY.get(entry.category, "crypto")
    return WatchlistItem(
        item_type="assets",
        identifier=entry.symbol,
        display_name=entry.name or entry.symbol,
        aliases=tuple(sorted(entry.aliases)),
        tags=tuple(entry.tags),
        section=section,
        category=entry.category,
    )


class WatchlistRegistry:
    """Manages programmatic access to tag-based watchlists across research types."""

    def __init__(
        self,
        entries: list[WatchlistEntry],
        *,
        persons: Sequence[WatchlistItem] = (),
        topics: Sequence[WatchlistItem] = (),
        sources: Sequence[WatchlistItem] = (),
    ) -> None:
        self._entries = entries
        self._items_by_type: dict[WatchlistType, tuple[WatchlistItem, ...]] = {
            "assets": tuple(_asset_item_from_entry(entry) for entry in entries),
            "persons": tuple(persons),
            "topics": tuple(topics),
            "sources": tuple(sources),
        }
        self._tag_to_items = self._build_index()

    @classmethod
    def from_monitor_dir(cls, monitor_dir: Path | str) -> WatchlistRegistry:
        monitor_path = Path(monitor_dir)
        return cls.from_file(monitor_path / "watchlists.yml")

    @classmethod
    def from_file(cls, path: Path | str) -> WatchlistRegistry:
        watchlist_path = Path(path)
        data = _load_watchlists_data(watchlist_path)
        entries = load_watchlist(watchlist_path)
        persons = _parse_named_items(data.get("persons"), item_type="persons", section="persons")
        topics = _parse_named_items(data.get("topics"), item_type="topics", section="topics")
        sources = _parse_source_items(data.get("domains"))
        return cls(entries, persons=persons, topics=topics, sources=sources)

    def save_to_monitor_dir(self, monitor_dir: Path | str) -> None:
        monitor_path = Path(monitor_dir)
        self.save(monitor_path / "watchlists.yml")

    def save(self, path: Path | str) -> None:
        watchlist_path = Path(path)
        watchlist_path.parent.mkdir(parents=True, exist_ok=True)
        with watchlist_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(
                self.to_dict(),
                handle,
                sort_keys=False,
                allow_unicode=True,
            )

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "crypto": [],
            "equities": [],
            "etfs": [],
            "macro": [],
            "persons": [],
            "topics": [],
            "domains": [],
        }

        for item in self._items_by_type["assets"]:
            section = item.section if item.section in data else "crypto"
            section_entries = cast(list[dict[str, object]], data[section])
            section_entries.append(
                {
                    "symbol": item.identifier,
                    "name": item.display_name,
                    "aliases": list(item.aliases),
                    "tags": list(item.tags),
                }
            )

        for item in self._items_by_type["persons"]:
            person_entries = cast(list[dict[str, object]], data["persons"])
            person_entries.append(
                {
                    "name": item.identifier,
                    "aliases": list(item.aliases),
                    "tags": list(item.tags),
                }
            )

        for item in self._items_by_type["topics"]:
            topic_entries = cast(list[dict[str, object]], data["topics"])
            topic_entries.append(
                {
                    "name": item.identifier,
                    "aliases": list(item.aliases),
                    "tags": list(item.tags),
                }
            )

        for item in self._items_by_type["sources"]:
            source_entries = cast(list[dict[str, object]], data["domains"])
            payload: dict[str, object] = {
                "domain": item.identifier,
                "tags": list(item.tags),
            }
            if item.score is not None:
                payload["credibility"] = item.score
            source_entries.append(payload)

        return data

    def _build_index(self) -> dict[WatchlistType, dict[str, tuple[WatchlistItem, ...]]]:
        index: dict[WatchlistType, dict[str, tuple[WatchlistItem, ...]]] = {
            "assets": {},
            "persons": {},
            "topics": {},
            "sources": {},
        }
        mutable_index: dict[WatchlistType, dict[str, list[WatchlistItem]]] = {
            "assets": defaultdict(list),
            "persons": defaultdict(list),
            "topics": defaultdict(list),
            "sources": defaultdict(list),
        }

        for item_type, items in self._items_by_type.items():
            for item in items:
                for tag in item.tags:
                    tag_key = tag.lower()
                    has_identifier = any(
                        existing.identifier == item.identifier
                        for existing in mutable_index[item_type][tag_key]
                    )
                    if not has_identifier:
                        mutable_index[item_type][tag_key].append(item)

        for item_type, tags in mutable_index.items():
            index[item_type] = {
                tag: tuple(sorted(items, key=lambda item: item.identifier.lower()))
                for tag, items in tags.items()
            }
        return index

    def get_supported_types(self) -> tuple[WatchlistType, ...]:
        return SUPPORTED_WATCHLIST_TYPES

    def get_watchlist_items(
        self,
        tag: str,
        *,
        item_type: WatchlistType = "assets",
    ) -> list[WatchlistItem]:
        return list(self._tag_to_items[item_type].get(tag.lower(), ()))

    def get_watchlist(
        self,
        tag: str,
        *,
        item_type: WatchlistType = "assets",
    ) -> list[str]:
        return [item.identifier for item in self.get_watchlist_items(tag, item_type=item_type)]

    def get_all_watchlists(
        self,
        *,
        item_type: WatchlistType = "assets",
    ) -> Mapping[str, list[str]]:
        return {
            tag: [item.identifier for item in items]
            for tag, items in self._tag_to_items[item_type].items()
        }

    def get_items(self, *, item_type: WatchlistType) -> list[str]:
        return sorted({item.identifier for item in self._items_by_type[item_type]})

    def get_symbols_for_category(self, category: str) -> list[str]:
        cat_lower = category.lower()
        return sorted(
            [entry.symbol for entry in self._entries if entry.category.lower() == cat_lower]
        )

    def filter_documents(
        self,
        documents: Sequence[CanonicalDocument],
        tag: str,
        *,
        item_type: WatchlistType = "assets",
    ) -> list[CanonicalDocument]:
        watchlist_items = self.get_watchlist_items(tag, item_type=item_type)
        if not watchlist_items:
            return []

        matched_documents: list[CanonicalDocument] = []
        for document in documents:
            if self._document_matches(document, watchlist_items, item_type=item_type):
                matched_documents.append(document)
        return matched_documents

    def _document_matches(
        self,
        document: CanonicalDocument,
        items: Sequence[WatchlistItem],
        *,
        item_type: WatchlistType,
    ) -> bool:
        if item_type == "assets":
            document_values = {
                value.strip().upper()
                for value in (document.tickers + document.crypto_assets)
                if value.strip()
            }
            return any(item.identifier.upper() in document_values for item in items)

        if item_type == "persons":
            document_values = {
                _normalize_text(value)
                for value in (document.people + document.entities)
                if value.strip()
            }
            for mention in document.entity_mentions:
                if mention.entity_type.lower() == "person" and mention.name:
                    document_values.add(_normalize_text(mention.name))
                if mention.entity_type.lower() == "person" and mention.normalized_name:
                    document_values.add(_normalize_text(mention.normalized_name))
            return any(item.normalized_terms() & document_values for item in items)

        if item_type == "topics":
            document_values = {
                _normalize_text(value)
                for value in (document.tags + document.topics + document.categories)
                if value.strip()
            }
            return any(item.normalized_terms() & document_values for item in items)

        document_domain = _normalize_domain(document.url)
        if not document_domain:
            return False
        return any(_normalize_domain(item.identifier) == document_domain for item in items)
