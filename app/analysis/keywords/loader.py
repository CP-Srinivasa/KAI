"""Loads keywords and aliases from monitor files."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class AliasGroup(BaseModel):
    """Represents one entity group from entity_aliases.yml."""

    canonical: str
    aliases: list[str]
    handles: dict[str, str] = {}
    category: str = "unknown"


class KeywordLoader:
    """Loads keywords and builds alias mappings from the monitor/ directory."""

    def __init__(self, monitor_dir: Path):
        self.monitor_dir = monitor_dir

    def load_keywords(self) -> set[str]:
        """Loads simple lowercased keywords from keywords.txt."""
        keywords: set[str] = set()
        path = self.monitor_dir / "keywords.txt"

        if not path.exists():
            logger.warning(f"Keyword file not found: {path}")
            return keywords

        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Some lines might have trailing comments like "Bitcoin # Top asset"
                word = line.split("#")[0].strip().lower()
                if word:
                    keywords.add(word)
        except Exception as e:
            logger.error(f"Failed to load keywords from {path}: {e}")

        return keywords

    def load_aliases(self) -> dict[str, str]:
        """Loads entity aliases and returns a mapping from lowercase alias -> canonical name."""
        alias_map: dict[str, str] = {}
        path = self.monitor_dir / "entity_aliases.yml"

        if not path.exists():
            logger.warning(f"Alias file not found: {path}")
            return alias_map

        try:
            content = path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)

            if not isinstance(data, dict) or "entity_aliases" not in data:
                logger.warning(f"Invalid format in {path}: missing 'entity_aliases' root key")
                return alias_map

            for group_data in data["entity_aliases"]:
                # Pydantic validation
                group = AliasGroup.model_validate(group_data)

                # Map canonical itself
                alias_map[group.canonical.lower()] = group.canonical

                # Map all explicit aliases
                for alias in group.aliases:
                    alias_map[alias.lower()] = group.canonical

                # Map social handles (e.g., "@cz_binance")
                for handle in group.handles.values():
                    alias_map[handle.lower()] = group.canonical

        except Exception as e:
            logger.error(f"Failed to load aliases from {path}: {e}")

        return alias_map
