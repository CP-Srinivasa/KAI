"""Kanal-Uploads aus dem oeffentlichen YouTube-Atom-Feed — ohne API-Kontingent.

Warum es diese Datei gibt: ``search.list`` der YouTube Data API kostet **100
Einheiten** pro Aufruf. Bei 22 Kanaelen und 12 Zyklen pro Tag sind das 26.400
Einheiten gegen ein Tagesbudget von 10.000 — 2,6-fach darueber. Gemessen am
2026-08-28 auf dem Pi: die Ingestion lief bis zur Erschoepfung (5 Zyklen,
07:36-15:32) und lief danach 17 Stunden ins Leere, jeder Abruf ein
``429 Too Many Requests``. Der Zyklus meldete dabei treu
``youtube done: 22 channels processed`` — er zaehlt Versuche, nicht Erfolge.

``https://www.youtube.com/feeds/videos.xml?channel_id=UC...`` liefert dieselben
Uploads (die letzten ~15) **fuer 0 Einheiten**. Der Feed traegt alles, was
``YouTubeVideo`` braucht, und die Beschreibung sogar vollstaendig statt als
143-Zeichen-Schnipsel wie im API-Snippet.

Bewusst ein eigener Parser statt ``feedparser``: die gebrauchten Felder liegen in
YouTube-eigenen Namensraeumen (``yt:videoId``, ``media:description``) und werden
hier explizit adressiert. Gelesen wird ueber ``defusedxml`` — der Feed kommt aus
dem offenen Netz, und ``xml.etree`` ist gegen XML-Bomben und externe Entitaeten
nicht gehaertet.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from app.ingestion.youtube.models import YouTubeVideo

#: Was beim Lesen eines fremden Feeds schiefgehen darf, ohne ein Programmfehler
#: zu sein. ``defusedxml`` wirft fuer XML-Bomben und externe Entitaeten eigene
#: Ausnahmen, die KEINE ``ParseError`` sind — wer nur die faengt, laesst genau
#: den Angriffsfall durch.
FEED_PARSE_ERRORS: tuple[type[Exception], ...] = (ParseError, DefusedXmlException)

CHANNEL_FEED_URL = "https://www.youtube.com/feeds/videos.xml"

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def channel_feed_url(channel_id: str) -> str:
    """Die Feed-Adresse eines Kanals. Nur ``channel_id`` (UC…) wird akzeptiert."""
    return f"{CHANNEL_FEED_URL}?channel_id={channel_id}"


def _text(element: Element, path: str) -> str:
    found = element.find(path, _NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_channel_feed(payload: bytes | str, *, limit: int | None = None) -> list[YouTubeVideo]:
    """Atom-Feed eines Kanals in Videos uebersetzen.

    Ein unbrauchbarer Feed wirft eine der ``FEED_PARSE_ERRORS`` — der Aufrufer soll das sehen und
    nicht auf eine leere Liste hereinfallen, die wie ein ruhiger Kanal aussaehe.
    Einzelne Eintraege **ohne** ``yt:videoId`` werden dagegen uebersprungen: ein
    kaputter Eintrag darf nicht den ganzen Kanal kosten.
    """
    root = fromstring(payload)
    feed_channel_id = _text(root, "yt:channelId")
    feed_author = _text(root, "atom:author/atom:name")

    videos: list[YouTubeVideo] = []
    for entry in root.findall("atom:entry", _NS):
        video_id = _text(entry, "yt:videoId")
        if not video_id:
            continue
        thumbnail = entry.find("media:group/media:thumbnail", _NS)
        videos.append(
            YouTubeVideo(
                video_id=video_id,
                title=_text(entry, "atom:title"),
                description=_text(entry, "media:group/media:description"),
                channel_id=_text(entry, "yt:channelId") or feed_channel_id,
                channel_title=_text(entry, "atom:author/atom:name") or feed_author,
                published_at=_text(entry, "atom:published"),
                thumbnail_url=thumbnail.get("url") if thumbnail is not None else None,
            )
        )
        if limit is not None and len(videos) >= limit:
            break
    return videos
