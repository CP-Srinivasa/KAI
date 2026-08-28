"""Uploads nach Kosten holen — und die Falle, die erst der Live-Lauf zeigte.

``search.list`` kostet 100 API-Einheiten pro Kanal und Zyklus. 22 Kanaele x 12
Zyklen = 26.400 gegen ein Tagesbudget von 10.000; am 2026-08-28 lief die
Ingestion deshalb 17 Stunden gegen ``429``. Die Leiter lautet jetzt: Atom-Feed
(0) -> ``playlistItems.list`` (1) -> ``search.list`` (100).

Was diese Tests festhalten:

1. der Feed wird benutzt und die teure Suche **nicht**,
2. ein *leerer* Feed loest keinen Rueckfall aus (sonst zahlte jeder stille Kanal
   weiter), ein *kaputter* schon — und dann auf die 1-Einheiten-Stufe,
3. die Kanal-Aufloesung geht nach Kosten vor,
4. die Kanalseite wird an ihren **autoritativen** Feldern gelesen. Der erste
   ``"channelId"`` im echten HTML gehoert einem empfohlenen Kanal; genau daran
   lief der erste Entwurf in den falschen Feed und fiel auf die teure Suche
   zurueck.

Der Parser laeuft gegen einen **echten**, am 2026-08-28 gesicherten Feed
(``tests/fixtures/youtube_channel_feed.xml``) — nicht gegen eine erfundene Form.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.ingestion.youtube import adapter
from app.ingestion.youtube.feed import channel_feed_url, parse_channel_feed

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "youtube_channel_feed.xml"
CHANNEL_ID = "UCAl9Ld79qaZxp9JzEOwd3aA"


# ── Der Parser gegen den echten Feed ─────────────────────────────────────


def test_parses_the_real_captured_feed() -> None:
    videos = parse_channel_feed(FIXTURE.read_bytes())

    assert len(videos) == 15  # YouTube liefert die letzten 15 Uploads
    first = videos[0]
    assert first.video_id
    assert first.channel_id == CHANNEL_ID
    assert first.channel_title
    assert first.title
    assert first.published_at.startswith("20")


def test_feed_carries_the_full_description_not_a_snippet() -> None:
    """Der Grund, warum ``text_source`` noetig wurde.

    Das API-Snippet war auf ~143 Zeichen gekuerzt; der Feed liefert den ganzen
    Text. Eine Laengenheuristik kann Beschreibung und Transkript danach nicht
    mehr unterscheiden.
    """
    videos = parse_channel_feed(FIXTURE.read_bytes())

    assert max(len(v.description) for v in videos) > 500


def test_limit_stops_early() -> None:
    assert len(parse_channel_feed(FIXTURE.read_bytes(), limit=3)) == 3


def test_entries_without_a_video_id_are_skipped_not_fatal() -> None:
    payload = (
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
        "<entry><title>kaputt</title></entry>"
        "<entry><yt:videoId>abc</yt:videoId><title>heil</title></entry>"
        "</feed>"
    )

    videos = parse_channel_feed(payload)

    assert [v.video_id for v in videos] == ["abc"]


def test_unparsable_feed_raises_instead_of_looking_empty() -> None:
    """Ein leerer Kanal und ein kaputter Feed duerfen sich nicht gleich anfuehlen."""
    import xml.etree.ElementTree as ET

    with pytest.raises(ET.ParseError):
        parse_channel_feed(b"<feed><unclosed>")


def test_feed_url_is_the_free_endpoint() -> None:
    url = channel_feed_url(CHANNEL_ID)

    assert url == f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
    assert "googleapis.com" not in url  # das ist der kostenpflichtige Weg


# ── Der Adapter: welcher Weg wird tatsaechlich gegangen? ─────────────────


def _client_factory(monkeypatch: pytest.MonkeyPatch, handler):  # type: ignore[no-untyped-def]
    """Ersetzt den httpx-Client des Adapters und protokolliert jede URL."""
    seen: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    class _Client(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:
            kwargs.pop("transport", None)
            super().__init__(transport=httpx.MockTransport(_handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(adapter.httpx, "AsyncClient", _Client)
    return seen


@pytest.mark.asyncio
async def test_uses_the_feed_and_never_calls_the_expensive_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = FIXTURE.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if "feeds/videos.xml" in str(request.url):
            return httpx.Response(200, content=body)
        return httpx.Response(500, json={})

    seen = _client_factory(monkeypatch, handler)

    videos = await adapter.fetch_channel_videos("KEY", CHANNEL_ID, max_results=3)

    assert len(videos) == 3
    assert any("feeds/videos.xml" in u for u in seen)
    assert not any("/youtube/v3/search" in u for u in seen), seen


@pytest.mark.asyncio
async def test_uc_handle_costs_no_resolution_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Steht die Kanal-ID schon da, darf kein API-Aufruf passieren."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=FIXTURE.read_bytes())

    seen = _client_factory(monkeypatch, handler)

    await adapter.fetch_channel_videos("KEY", CHANNEL_ID, max_results=1)

    assert not any("googleapis.com" in u for u in seen), seen


@pytest.mark.asyncio
async def test_empty_feed_does_not_trigger_the_expensive_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein Kanal ohne frische Uploads ist kein Feed-Fehler — und muss gratis bleiben."""
    empty = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'

    def handler(request: httpx.Request) -> httpx.Response:
        if "feeds/videos.xml" in str(request.url):
            return httpx.Response(200, content=empty)
        return httpx.Response(200, json={"items": []})

    seen = _client_factory(monkeypatch, handler)

    videos = await adapter.fetch_channel_videos("KEY", CHANNEL_ID)

    assert videos == []
    assert not any("/youtube/v3/search" in u for u in seen), seen


@pytest.mark.asyncio
async def test_broken_feed_falls_to_the_one_unit_playlist_not_the_100_unit_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der eigentliche Hebel: faellt der Feed aus, kostet es EINE Einheit, nicht 100.

    Am 2026-08-28 gemessen: bei erschoepftem Tagesbudget lieferte ``search.list``
    ``429``, ``playlistItems.list`` auf derselben Uploads-Playlist ``200``.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "feeds/videos.xml" in url:
            return httpx.Response(404)
        if "/youtube/v3/playlistItems" in url:
            assert "playlistId=UU" in url.replace("%3D", "="), url
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "snippet": {
                                "resourceId": {"videoId": "vidA"},
                                "title": "T",
                                "description": "D",
                                "videoOwnerChannelId": CHANNEL_ID,
                                "videoOwnerChannelTitle": "Bankless",
                                "publishedAt": "2026-08-28T00:00:00Z",
                                "thumbnails": {},
                            }
                        }
                    ]
                },
            )
        return httpx.Response(500, json={})

    seen = _client_factory(monkeypatch, handler)

    videos = await adapter.fetch_channel_videos("KEY", CHANNEL_ID)

    assert [v.video_id for v in videos] == ["vidA"]
    assert videos[0].channel_title == "Bankless"
    assert any("/youtube/v3/playlistItems" in u for u in seen), seen
    assert not any("/youtube/v3/search" in u for u in seen), seen


def test_uploads_playlist_id_follows_the_api_convention() -> None:
    assert adapter.uploads_playlist_id("UCAl9Ld79qaZxp9JzEOwd3aA") == "UUAl9Ld79qaZxp9JzEOwd3aA"
    assert adapter.uploads_playlist_id("etwas-anderes") == "etwas-anderes"


@pytest.mark.asyncio
async def test_expensive_search_is_the_last_resort_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Erst wenn Feed UND Playlist ausfallen, darf die 100-Einheiten-Suche laufen."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "feeds/videos.xml" in url:
            return httpx.Response(404)
        if "/youtube/v3/playlistItems" in url:
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": {"videoId": "vid1"},
                        "snippet": {
                            "title": "T",
                            "description": "D",
                            "channelId": CHANNEL_ID,
                            "channelTitle": "C",
                            "publishedAt": "2026-08-28T00:00:00Z",
                            "thumbnails": {},
                        },
                    }
                ]
            },
        )

    seen = _client_factory(monkeypatch, handler)

    videos = await adapter.fetch_channel_videos("KEY", CHANNEL_ID)

    assert [v.video_id for v in videos] == ["vid1"]
    assert any("/youtube/v3/search" in u for u in seen), seen


FOREIGN_CHANNEL_ID = "UCCRxYlYOmLE2l5wxs3ckJtg"


def _channel_page(own_channel_id: str) -> str:
    """Eine Kanalseite in der Form, die am 2026-08-28 gemessen wurde.

    Entscheidend ist die Reihenfolge: der erste ``"channelId"`` im echten
    2,3-MB-HTML gehoert einem **empfohlenen** Kanal, nicht dem eigenen.
    """
    return (
        "<html><head>"
        f'<script>{{"channelId":"{FOREIGN_CHANNEL_ID}","reason":"empfohlener Kanal"}}</script>'
        f'<link rel="canonical" href="https://www.youtube.com/channel/{own_channel_id}">'
        f'<meta itemprop="identifier" content="{own_channel_id}">'
        "</head><body></body></html>"
    )


def test_page_extraction_ignores_the_recommended_channel_trap() -> None:
    """Die Falle, die erst der Live-Lauf zeigte — und die den Feed ins Leere schickte."""
    page = _channel_page(CHANNEL_ID)

    hits = [p.search(page) for p in adapter._CHANNEL_ID_PATTERNS]
    found = next((m.group(1) for m in hits if m), None)

    assert found == CHANNEL_ID
    assert found != FOREIGN_CHANNEL_ID
    assert FOREIGN_CHANNEL_ID in page  # die Falle steht wirklich drin


@pytest.mark.asyncio
async def test_handle_resolution_prefers_the_free_page_over_the_100_unit_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """forHandle (1 Einheit) scheitert -> Kanalseite (0), NICHT search (100)."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/youtube/v3/channels" in url:
            return httpx.Response(200, json={"items": []})  # forHandle findet nichts
        if url.startswith("https://www.youtube.com/@"):
            return httpx.Response(200, text=_channel_page(CHANNEL_ID))
        if "feeds/videos.xml" in url:
            return httpx.Response(200, content=FIXTURE.read_bytes())
        return httpx.Response(500, json={})

    seen = _client_factory(monkeypatch, handler)

    videos = await adapter.fetch_channel_videos("KEY", "Bankless", max_results=2)

    assert len(videos) == 2
    assert any(u.startswith("https://www.youtube.com/@") for u in seen), seen
    assert not any("/youtube/v3/search" in u for u in seen), seen


# ── Herkunft des Textes ──────────────────────────────────────────────────


def _video(description: str) -> adapter.YouTubeVideo:
    return adapter.YouTubeVideo(
        video_id="v1",
        title="T",
        description=description,
        channel_id=CHANNEL_ID,
        channel_title="Bankless",
        published_at="2026-08-28T00:00:00+00:00",
    )


def test_transcript_is_marked_as_transcript() -> None:
    doc = adapter._video_to_document(_video("kurz"), "ein echtes Transkript", "youtube", "YouTube")

    assert doc.youtube_meta is not None
    assert doc.youtube_meta.text_source == "transcript"
    assert doc.raw_text == "ein echtes Transkript"


def test_long_description_is_marked_as_description_not_transcript() -> None:
    """Die Regression, die dieser Umbau sonst in die Abdeckungswache gerissen haette.

    Der Feed liefert volle Beschreibungen (~1400 Zeichen). An der Laenge waeren
    sie von einem Transkript nicht zu unterscheiden.
    """
    doc = adapter._video_to_document(_video("x" * 1400), None, "youtube", "YouTube")

    assert doc.youtube_meta is not None
    assert doc.youtube_meta.text_source == "description"
    assert len(doc.raw_text or "") == 1400


def test_description_fallback_is_capped_like_a_transcript() -> None:
    doc = adapter._video_to_document(_video("x" * 50_000), None, "youtube", "YouTube")

    assert len(doc.raw_text or "") == adapter._MAX_TRANSCRIPT_CHARS


def test_no_text_at_all_marks_no_source() -> None:
    doc = adapter._video_to_document(_video(""), None, "youtube", "YouTube")

    assert doc.youtube_meta is not None
    assert doc.youtube_meta.text_source is None
