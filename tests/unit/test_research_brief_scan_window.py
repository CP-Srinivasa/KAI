"""Der Kandidaten-Scan muss dasselbe Fenster sehen wie der Bericht.

Review-Befunde zum Commit "clarify AI health and bound research brief freshness":

1. Der Agenten-Pfad ``app.agents.tools.canonical_read.get_research_brief``
   kappt weiterhin ``docs[:limit]`` VOR dem Bau. Seit der Bau zusaetzlich nach
   Aktualitaet filtert, verbrauchen veraltete Dokumente das Limit-Budget und
   der Agent bekommt ein leeres Briefing, obwohl aktuelle Dokumente vorlagen.
   API und CLI wurden umgestellt, dieser dritte Aufrufer nicht.

2. Der Scan holt ``limit * 5`` Dokumente OHNE Fensterbedingung. Die
   Watchlist-Filterung laeuft danach auf einem Stapel, der ueberwiegend aus
   Dokumenten ausserhalb des Fensters bestehen kann -- genau die Verdraengung,
   die der Commit im Kommentar zu verhindern beansprucht, nur eine Stufe
   frueher. ``DocumentRepository.list`` kennt ``published_after`` bereits.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.core.domain.document import CanonicalDocument
from app.core.enums import SentimentLabel


class _RecordingRepo:
    """Repo-Doppel, das die Abfrage-Argumente festhaelt."""

    def __init__(self, documents: list[CanonicalDocument]) -> None:
        self._documents = documents
        self.calls: list[dict[str, Any]] = []

    async def list(self, **kwargs: Any) -> list[CanonicalDocument]:
        self.calls.append(kwargs)
        return self._documents


def _doc(url: str, *, age_hours: float, priority: int) -> CanonicalDocument:
    return CanonicalDocument(
        url=url,
        title=f"Gensler item {url}",
        is_analyzed=True,
        published_at=datetime.now(UTC) - timedelta(hours=age_hours),
        priority_score=priority,
        impact_score=0.5,
        summary="Regulatory pressure.",
        people=["Gary Gensler"],
        entities=["Gary Gensler"],
        sentiment_label=SentimentLabel.BEARISH,
    )


def _watchlists(tmp_path: Path) -> Path:
    path = tmp_path / "watchlists.yml"
    path.write_text(
        """
persons:
  - name: Gary Gensler
    aliases: [gensler]
    tags: [regulation]
""".strip(),
        encoding="utf-8",
    )
    return path


class _FakeSessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeSessionFactory:
    def begin(self) -> _FakeSessionContext:
        return _FakeSessionContext()


def test_agent_brief_keeps_current_document_when_stale_ones_fill_the_limit(
    tmp_path: Path,
) -> None:
    """Der Agenten-Pfad darf das Limit nicht an veraltete Dokumente verschenken."""
    import app.agents.tools.canonical_read as canonical_read
    from app.core.settings import AppSettings

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)
    _watchlists(tmp_path)

    stale = _doc("https://example.com/stale", age_hours=200, priority=9)
    current = _doc("https://example.com/current", age_hours=1, priority=3)
    repo = _RecordingRepo([stale, current])

    with (
        patch.object(canonical_read, "get_settings", return_value=settings),
        patch.object(canonical_read, "build_session_factory", return_value=_FakeSessionFactory()),
        patch.object(canonical_read, "DocumentRepository", lambda _session: repo),
    ):
        import asyncio

        markdown = asyncio.run(
            canonical_read.get_research_brief(
                watchlist="regulation", watchlist_type="persons", limit=1
            )
        )

    assert "current" in markdown or "Gensler item https://example.com/current" in markdown
    assert "**Data state:** current" in markdown


def test_agent_brief_scan_is_bounded_by_the_report_window(tmp_path: Path) -> None:
    """Der Scan fragt mit Fenstergrenze ab, nicht blind nach den neuesten N."""
    import app.agents.tools.canonical_read as canonical_read
    from app.core.settings import AppSettings

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)
    _watchlists(tmp_path)
    repo = _RecordingRepo([_doc("https://example.com/current", age_hours=1, priority=5)])

    with (
        patch.object(canonical_read, "get_settings", return_value=settings),
        patch.object(canonical_read, "build_session_factory", return_value=_FakeSessionFactory()),
        patch.object(canonical_read, "DocumentRepository", lambda _session: repo),
    ):
        import asyncio

        asyncio.run(
            canonical_read.get_research_brief(
                watchlist="regulation", watchlist_type="persons", limit=10
            )
        )

    assert len(repo.calls) == 1
    published_after = repo.calls[0].get("published_after")
    assert isinstance(published_after, datetime)
    assert published_after.tzinfo is not None
    # 24-Stunden-Default, grosszuegige Toleranz fuer die Laufzeit des Tests.
    delta = datetime.now(UTC) - published_after
    assert timedelta(hours=23, minutes=55) < delta < timedelta(hours=24, minutes=5)


@pytest.mark.parametrize("window_hours", [1, 48, 720])
def test_cli_brief_scan_window_matches_requested_window(tmp_path: Path, window_hours: int) -> None:
    """Auch der CLI-Pfad darf nicht ausserhalb seines eigenen Fensters scannen."""
    import app.cli.commands.research_core as research_core
    from app.core.settings import AppSettings

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)
    _watchlists(tmp_path)
    repo = _RecordingRepo([_doc("https://example.com/current", age_hours=0.5, priority=5)])

    with (
        patch.object(research_core, "get_settings", return_value=settings),
        patch.object(research_core, "build_session_factory", return_value=_FakeSessionFactory()),
        patch.object(research_core, "DocumentRepository", lambda _session: repo),
    ):
        research_core.research_brief(
            watchlist="regulation", watchlist_type="persons", limit=5, window_hours=window_hours
        )

    published_after = repo.calls[0].get("published_after")
    assert isinstance(published_after, datetime)
    delta = datetime.now(UTC) - published_after
    assert (
        timedelta(hours=window_hours) - timedelta(minutes=5)
        < delta
        < timedelta(hours=window_hours) + timedelta(minutes=5)
    )


def test_ai_health_survives_a_chain_provider_without_a_credential_entry() -> None:
    """Ein Ketten-Provider ausserhalb der vier Schluessel darf keinen 500 ausloesen.

    ``by_provider`` wird nur noch aus der Credential-Tabelle vorbelegt, die
    Reihenfolge kommt weiterhin aus der Kette. Ein Kettenname ohne eigenen
    Schluessel und ohne Verkehr traf damit auf einen fehlenden Schluessel.
    """
    import app.ai.health as ai_health
    import app.analysis.factory as factory

    with (
        patch.object(factory, "describe_primary_chain", return_value=["openai", "litellm"]),
        patch.object(factory, "describe_shadow_chain", return_value=[]),
    ):
        snapshot = ai_health.ai_health_snapshot(
            settings=_settings_with_openai_key(), path=Path("does-not-exist.jsonl")
        )

    names = [block["name"] for block in snapshot["ai"]["providers"]]
    assert "litellm" in names
    litellm_block = next(b for b in snapshot["ai"]["providers"] if b["name"] == "litellm")
    assert litellm_block["calls"] == 0
    assert litellm_block["state"] in {"not_configured", "unavailable", "disabled"}


def _settings_with_openai_key() -> Any:
    from app.core.settings import AppSettings

    settings = AppSettings()
    settings.providers.openai_api_key = "sk-test"
    return settings
