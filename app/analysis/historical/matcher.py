"""Historical event analog matcher.

Given a set of HistoricalEvents and a document's extracted signals
(tags, assets, keywords), find the closest historical parallels.

Algorithm (deterministic, no LLM):
1. Asset overlap   → 0.50 weight
2. Tag overlap     → 0.30 weight
3. Category match  → 0.20 weight (category derived from event_type/tags)

Only events with total similarity >= min_score are returned.
Results are sorted by similarity descending.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.core.domain.events import EventAnalog, HistoricalEvent

_DEFAULT_MIN_SCORE = 0.20
_DEFAULT_TOP_N = 3


class EventAnalogMatcher:
    """Finds historical analogs for a document based on assets and tags."""

    def __init__(self, events: list[HistoricalEvent]) -> None:
        self._events = events

    # ── Public API ────────────────────────────────────────────────────────────

    def find_analogs(
        self,
        assets: list[str],
        tags: list[str],
        event_type: str | None = None,
        *,
        min_score: float = _DEFAULT_MIN_SCORE,
        top_n: int = _DEFAULT_TOP_N,
    ) -> list[EventAnalog]:
        """Return up to top_n historical analogs sorted by similarity."""
        asset_set = {a.lower() for a in assets}
        tag_set = {t.lower() for t in tags}

        results: list[EventAnalog] = []
        for event in self._events:
            score, shared_assets, shared_tags = _score_event(
                event, asset_set, tag_set, event_type
            )
            if score >= min_score:
                results.append(
                    EventAnalog(
                        event_id=event.id,
                        event_title=event.title,
                        similarity_score=round(score, 4),
                        matching_reason=_build_reason(event, shared_assets, shared_tags, score),
                        shared_assets=shared_assets,
                        shared_tags=shared_tags,
                    )
                )

        results.sort(key=lambda a: a.similarity_score, reverse=True)
        return results[:top_n]

    # ── Factory methods ───────────────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: str | Path) -> EventAnalogMatcher:
        """Load events from a YAML file and return a matcher instance."""
        events = _load_yaml(Path(path))
        return cls(events)

    @classmethod
    def from_monitor_dir(cls, monitor_dir: str | Path) -> EventAnalogMatcher:
        """Load from monitor/historical_events.yml (default location)."""
        path = Path(monitor_dir) / "historical_events.yml"
        if not path.exists():
            return cls([])
        return cls.from_yaml(path)


# ── Scoring helpers ───────────────────────────────────────────────────────────


def _score_event(
    event: HistoricalEvent,
    asset_set: set[str],
    tag_set: set[str],
    event_type: str | None,
) -> tuple[float, list[str], list[str]]:
    event_assets = {a.lower() for a in event.affected_assets}
    event_tags = {t.lower() for t in event.tags}

    shared_assets = sorted(asset_set & event_assets)
    shared_tags = sorted(tag_set & event_tags)

    asset_score = 0.0
    if event_assets:
        asset_score = len(shared_assets) / max(len(event_assets), len(asset_set), 1)

    tag_score = 0.0
    if event_tags:
        tag_score = len(shared_tags) / max(len(event_tags), len(tag_set), 1)

    category_bonus = 0.0
    if event_type and event_type.lower() == event.category.lower():
        category_bonus = 1.0

    total = asset_score * 0.50 + tag_score * 0.30 + category_bonus * 0.20
    return min(total, 1.0), shared_assets, shared_tags


def _build_reason(
    event: HistoricalEvent,
    shared_assets: list[str],
    shared_tags: list[str],
    score: float,
) -> str:
    parts: list[str] = []
    if shared_assets:
        parts.append(f"shared assets: {', '.join(shared_assets[:3])}")
    if shared_tags:
        parts.append(f"shared tags: {', '.join(shared_tags[:3])}")
    base = f"Analog to '{event.title}' ({event.event_date}, score={score:.2f})"
    if parts:
        return f"{base} — {'; '.join(parts)}"
    return base


# ── YAML loader ───────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> list[HistoricalEvent]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    events_raw = data.get("events", [])
    return [HistoricalEvent.model_validate(e) for e in events_raw]
