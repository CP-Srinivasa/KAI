"""Der Adapter und die installierte `youtube-transcript-api` müssen zusammenpassen.

Gefunden am 2026-08-27 beim Zurückbau der mypy-Ausnahmen: `adapter.py` rief
`YouTubeTranscriptApi.list_transcripts(video_id)` — eine Methode, die es in der
installierten Version **1.2.4 nicht mehr gibt**. Der Aufruf steckte in einem
weiten ``except Exception``, das den ``AttributeError`` schluckte und ``None``
zurückgab. Ergebnis: YouTube-Transkripte kamen seit dem Bibliotheks-Upgrade nie
an, ohne einen einzigen Fehler im Log. Ein stiller Eingangsausfall — dieselbe
Klasse wie der TradingView-Ingest, der zwei Wochen tot war, während alle
Ausgangs-Wächter grün meldeten.

Ein Test, der die Bibliothek mockt, hätte das nie bemerkt: er hätte die
gemockte API bestätigt, nicht die installierte. Deshalb prüfen die Tests hier
die **echte** Bibliothek — sie sind die Naht zwischen unserem Code und einer
Abhängigkeit, die sich unter uns bewegt.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator

import pytest

from app.ingestion.youtube import adapter


def test_installed_library_offers_the_api_the_adapter_uses() -> None:
    """Bricht laut, wenn ein Upgrade die API erneut verschiebt."""
    from youtube_transcript_api import YouTubeTranscriptApi

    assert hasattr(YouTubeTranscriptApi, "list"), (
        "youtube-transcript-api hat keine .list()-Methode mehr — die API hat sich "
        "wieder bewegt. adapter.fetch_transcript anpassen, nicht diesen Test."
    )
    sig = inspect.signature(YouTubeTranscriptApi.list)
    assert "video_id" in sig.parameters

    # Instanzmethode, nicht Klassenmethode: `YouTubeTranscriptApi.list(video_id)`
    # ohne Instanz wäre genau der Fehler, der uns das eingebrockt hat.
    assert not isinstance(
        inspect.getattr_static(YouTubeTranscriptApi, "list"), (classmethod, staticmethod)
    ), "list() ist keine Instanzmethode mehr — Aufrufform im Adapter prüfen"


def test_adapter_does_not_call_the_removed_classmethod() -> None:
    """Der konkrete Regressionsschutz gegen genau diesen Aufruf."""
    source = inspect.getsource(adapter)
    assert "list_transcripts(" not in source, (
        "adapter.py ruft wieder list_transcripts() — in 1.x entfernt"
    )


def test_snippets_are_objects_not_dicts() -> None:
    """Die zweite Bruchstelle, die der erste Fix allein nicht erwischt.

    In 0.6 lieferte ``transcript.fetch()`` eine Liste von Dicts (``entry["text"]``).
    In 1.x ist es ein ``FetchedTranscript`` aus ``FetchedTranscriptSnippet``-
    Objekten — ``entry.get("text")`` würde dort mit ``AttributeError`` scheitern,
    also erneut still im ``except`` verschwinden.
    """
    from youtube_transcript_api import FetchedTranscript, FetchedTranscriptSnippet

    assert hasattr(FetchedTranscript, "__iter__"), (
        "FetchedTranscript ist nicht mehr iterierbar — der Adapter laeuft direkt darueber"
    )
    assert not hasattr(FetchedTranscriptSnippet, "get"), (
        "Snippets sind wieder Dict-artig — dann kann der Adapter vereinfacht werden"
    )
    assert "text" in getattr(FetchedTranscriptSnippet, "__annotations__", {}), (
        "FetchedTranscriptSnippet hat kein .text-Feld mehr"
    )


class _Snippet:
    """Verhält sich wie ein FetchedTranscriptSnippet: Attribut, kein Dict."""

    def __init__(self, text: str) -> None:
        self.text = text


class _Fetched:
    """Verhält sich wie ein FetchedTranscript: iterierbar über Snippet-Objekte."""

    def __init__(self, texts: list[str]) -> None:
        self._snippets = [_Snippet(t) for t in texts]

    def __iter__(self) -> Iterator[_Snippet]:
        return iter(self._snippets)


class _Transcript:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def fetch(self) -> _Fetched:
        return _Fetched(self._texts)


class _TranscriptList:
    def __init__(self, texts: list[str], *, generated_only: bool = False) -> None:
        self._texts = texts
        self._generated_only = generated_only

    def find_transcript(self, languages: list[str]) -> _Transcript:
        from youtube_transcript_api import NoTranscriptFound

        if self._generated_only:
            raise NoTranscriptFound(
                video_id="v", requested_language_codes=languages, transcript_data={}
            )
        return _Transcript(self._texts)

    def find_generated_transcript(self, languages: list[str]) -> _Transcript:
        return _Transcript(self._texts)


def test_fetch_transcript_reads_text_from_snippet_objects(monkeypatch) -> None:
    """End-to-end über die neue Aufrufform — mit Objekt-Snippets."""

    class _Api:
        def __init__(self, *a: object, **k: object) -> None: ...

        def list(self, video_id: str) -> _TranscriptList:
            assert video_id == "vid123"
            return _TranscriptList(["hallo", "welt"])

    monkeypatch.setattr(adapter, "YouTubeTranscriptApi", _Api)
    assert adapter.fetch_transcript("vid123") == "hallo welt"


def test_fetch_transcript_falls_back_to_generated(monkeypatch) -> None:
    class _Api:
        def __init__(self, *a: object, **k: object) -> None: ...

        def list(self, video_id: str) -> _TranscriptList:
            return _TranscriptList(["auto"], generated_only=True)

    monkeypatch.setattr(adapter, "YouTubeTranscriptApi", _Api)
    assert adapter.fetch_transcript("v") == "auto"


def test_fetch_transcript_returns_none_on_api_error(monkeypatch) -> None:
    """Fehler bleiben fail-soft — aber sie werden geloggt, nicht verschluckt."""

    class _Api:
        def __init__(self, *a: object, **k: object) -> None: ...

        def list(self, video_id: str) -> _TranscriptList:
            raise RuntimeError("network down")

    logged: list[str] = []
    monkeypatch.setattr(adapter, "YouTubeTranscriptApi", _Api)
    monkeypatch.setattr(adapter.logger, "warning", lambda msg, **kw: logged.append(msg))
    assert adapter.fetch_transcript("v") is None
    assert logged, "ein API-Fehler muss sichtbar sein, sonst faellt der Eingang wieder still aus"


@pytest.mark.parametrize("attr", ["list", "fetch"])
def test_api_surface_is_stable(attr: str) -> None:
    from youtube_transcript_api import YouTubeTranscriptApi

    assert callable(getattr(YouTubeTranscriptApi, attr, None))
