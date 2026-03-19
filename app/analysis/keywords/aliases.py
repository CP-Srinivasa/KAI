"""Entity alias loader — reads monitor/entity_aliases.yml.

Format:
    entity_aliases:
      - canonical: "Anthony Pompliano"
        aliases: ["Pomp", "Anthony Pompliano"]
        handles:
          twitter: "@APompliano"
        category: crypto_influencer
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


@dataclass(frozen=True)
class EntityAlias:
    canonical: str
    aliases: frozenset[str]  # all lowercased
    handles: dict[str, str]
    category: str


def load_entity_aliases(path: str | Path) -> list[EntityAlias]:
    """Parse entity_aliases.yml → list[EntityAlias]."""
    path = Path(path)
    if not path.exists():
        return []

    with path.open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    entities: list[EntityAlias] = []
    for item in data.get("entity_aliases") or []:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical", "")).strip()
        if not canonical:
            continue
        raw_aliases: list[str] = [str(a) for a in (item.get("aliases") or [])]
        handles: dict[str, str] = item.get("handles") or {}
        category = str(item.get("category", "person")).strip()
        entities.append(
            EntityAlias(
                canonical=canonical,
                aliases=frozenset(a.lower() for a in raw_aliases),
                handles=handles,
                category=category,
            )
        )

    return entities
