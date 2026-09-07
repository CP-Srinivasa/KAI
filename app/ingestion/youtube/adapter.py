"""YouTube channel ingestion adapter.

Uploads werden nach **Kosten** geholt, nicht nach Bequemlichkeit:

1. oeffentlicher Atom-Feed (``feed.py``) — **0 Einheiten**,
2. ``playlistItems.list`` auf der Uploads-Playlist — **1 Einheit**,
3. ``search.list`` — **100 Einheiten**, nur noch letzte Instanz.

Der Grund steht in Zahlen: 22 Kanaele x 12 Zyklen x 100 Einheiten = 26.400 gegen
ein Tagesbudget von 10.000. Am 2026-08-28 lief die Ingestion deshalb 17 Stunden
gegen ``429``. Ueber Stufe 2 sind es 528 Einheiten am Tag — 5 % des Budgets.

Jede Stufe faellt nur bei einem **Fehler** weiter, nie bei einem legitim leeren
Ergebnis; sonst zahlte jeder stille Kanal weiter den vollen Preis.

Produces CanonicalDocuments compatible with the standard pipeline
(persist_fetch_result → AnalysisPipeline → AlertService).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

from app.core.domain.document import CanonicalDocument, YouTubeVideoMeta
from app.core.enums import DocumentType, SourceType
from app.ingestion.base.interfaces import FetchResult
from app.ingestion.youtube.feed import FEED_PARSE_ERRORS, channel_feed_url, parse_channel_feed
from app.ingestion.youtube.models import YouTubeVideo

logger = logging.getLogger(__name__)

_YT_API_BASE = "https://www.googleapis.com/youtube/v3"
_MAX_RESULTS_PER_CHANNEL = 10
_MAX_TRANSCRIPT_CHARS = 12_000
_PREFERRED_LANGUAGES = ["en", "de"]


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


#: Der Abruf hat genau vier Ausgaenge, und jeder bekommt einen Namen. Vorher
#: waren zwei davon nicht unterscheidbar — beide ``None``, beide ohne Log.
TRANSCRIPT_STATUS_OK = "ok"
TRANSCRIPT_STATUS_DISABLED = "transcripts_disabled"
TRANSCRIPT_STATUS_NONE_FOUND = "none_found"
#: Alles andere traegt seinen Ausnahmetyp mit: ``error:IpBlocked`` ist eine
#: voellig andere Handlung als ``none_found`` — die eine heisst warten, die
#: andere heisst, dass die Videos schlicht keine Untertitel haben.
TRANSCRIPT_STATUS_ERROR_PREFIX = "error:"
#: Wir haben selbst aufgehoert zu fragen. Kein Fehler von YouTube, sondern
#: unsere Entscheidung -- und deshalb ein eigener Name.
TRANSCRIPT_STATUS_BLOCK_COOLDOWN = "skipped:ip_block_cooldown"

#: Ausnahmetypen, die YouTube vergibt, wenn es die aufrufende IP sperrt.
_IP_BLOCK_EXC_NAMES = frozenset({"IpBlocked", "RequestBlocked", "YouTubeRequestFailed"})

#: Zustand ueberlebt Neustarts: der Block liegt bei YouTube, nicht im Prozess.
#: Ein prozesslokaler Merker waere nach jedem Restart wieder auf Anfang -- und
#: genau der Restart passiert hier mehrmals taeglich.
_BLOCK_STATE_PATH = Path("artifacts/youtube/ip_block.json")
_BLOCK_COOLDOWN_S = 6 * 3600.0


def _block_state_path() -> Path:
    return _BLOCK_STATE_PATH


def ip_block_active(*, now_s: float | None = None) -> bool:
    """Laeuft gerade eine selbst verhaengte Sperrpause?"""
    now = time.time() if now_s is None else now_s
    try:
        raw = json.loads(_block_state_path().read_text(encoding="utf-8"))
        until = float(raw["cooldown_until_s"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return now < until


def _note_ip_block(*, now_s: float | None = None) -> None:
    """Sperrpause setzen bzw. verlaengern."""
    now = time.time() if now_s is None else now_s
    path = _block_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.new")
        tmp.write_text(
            json.dumps(
                {
                    "blocked_at_s": now,
                    "cooldown_until_s": now + _BLOCK_COOLDOWN_S,
                    "cooldown_s": _BLOCK_COOLDOWN_S,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:  # pragma: no cover - Dateisystem-Ausfall
        logger.warning("youtube.block_state_unwritable", extra={"error": str(exc)})


__all__ = [
    "TRANSCRIPT_STATUS_BLOCK_COOLDOWN",
    "TRANSCRIPT_STATUS_DISABLED",
    "TRANSCRIPT_STATUS_ERROR_PREFIX",
    "TRANSCRIPT_STATUS_NONE_FOUND",
    "TRANSCRIPT_STATUS_OK",
    "YouTubeVideo",
    "fetch_transcript",
    "fetch_transcript_with_reason",
    "fetch_youtube_channel",
    "ip_block_active",
]


async def fetch_channel_videos(
    api_key: str,
    channel_handle: str,
    *,
    max_results: int = _MAX_RESULTS_PER_CHANNEL,
    timeout: int = 15,
) -> list[YouTubeVideo]:
    """Die letzten Uploads eines Kanals — ueber den Feed, nicht ueber ``search``.

    Akzeptiert @handle, Kanal-ID oder /c/-URL. Aufloesung zuerst, dann der Feed
    (0 Einheiten). Die 100-Einheiten-Suche laeuft nur, wenn der Feed **kaputt**
    ist; ein leerer Feed ist ein leerer Kanal und kostet nichts.
    """
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        channel_id = await _resolve_channel_id(client, api_key, channel_handle)
        if not channel_id:
            logger.warning("youtube.channel_not_found", extra={"handle": channel_handle})
            return []

        videos = await _fetch_via_feed(client, channel_id, max_results)
        if videos is not None:
            return videos

        # Der Feed ist nicht erreichbar. Nicht gleich zur 100-Einheiten-Suche:
        # die Uploads-Playlist liefert dasselbe fuer EINE Einheit.
        videos = await _fetch_via_uploads_playlist(client, api_key, channel_id, max_results)
        if videos is not None:
            return videos

        logger.warning(
            "youtube.falling_back_to_expensive_search",
            extra={"channel_id": channel_id, "cost_units": 100},
        )
        return await _fetch_via_data_api(client, api_key, channel_id, max_results)


async def _fetch_via_feed(
    client: httpx.AsyncClient, channel_id: str, limit: int
) -> list[YouTubeVideo] | None:
    """Der kontingentfreie Weg. ``None`` heisst kaputt, ``[]`` heisst leer.

    Die Unterscheidung ist der ganze Punkt: nur ``None`` rechtfertigt den teuren
    Rueckfall. Wuerde ein leerer Feed ihn ausloesen, zahlte jeder Kanal ohne
    frische Uploads weiter 100 Einheiten pro Zyklus.
    """
    try:
        resp = await client.get(channel_feed_url(channel_id))
    except httpx.HTTPError as exc:
        logger.warning(
            "youtube.feed_unreachable", extra={"channel_id": channel_id, "error": str(exc)}
        )
        return None
    if resp.status_code != 200:
        logger.warning(
            "youtube.feed_http_error",
            extra={"channel_id": channel_id, "status": resp.status_code},
        )
        return None
    try:
        return parse_channel_feed(resp.content, limit=limit)
    except FEED_PARSE_ERRORS as exc:
        logger.warning(
            "youtube.feed_unparsable", extra={"channel_id": channel_id, "error": str(exc)}
        )
        return None


def uploads_playlist_id(channel_id: str) -> str:
    """Die Uploads-Playlist eines Kanals: ``UC…`` -> ``UU…``.

    Eine Konvention der YouTube-API, kein Ratespiel: jede Kanal-ID hat eine
    gleichnamige Uploads-Playlist mit ``UU``-Praefix.
    """
    return "UU" + channel_id[2:] if channel_id.startswith("UC") else channel_id


async def _fetch_via_uploads_playlist(
    client: httpx.AsyncClient, api_key: str, channel_id: str, max_results: int
) -> list[YouTubeVideo] | None:
    """Uploads ueber ``playlistItems.list`` — **1 Einheit** statt 100.

    Der eigentliche Hebel gegen das Kontingent, und der einzige Weg, der am
    2026-08-28 nachweislich noch trug: bei erschoepftem Tagesbudget lieferte
    ``search.list`` ``429``, waehrend derselbe Kanal ueber diesen Aufruf ``200``
    und dieselben Videos zurueckgab.

    ``None`` heisst wieder: nicht benutzbar, der Aufrufer darf weiterfallen. Eine
    leere Liste ist dagegen eine Antwort — ein Kanal ohne Uploads.
    """
    try:
        resp = await client.get(
            f"{_YT_API_BASE}/playlistItems",
            params={
                "key": api_key,
                "playlistId": uploads_playlist_id(channel_id),
                "part": "snippet",
                "maxResults": max_results,
            },
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "youtube.uploads_playlist_unreachable",
            extra={"channel_id": channel_id, "error": str(exc)},
        )
        return None
    if resp.status_code != 200:
        logger.warning(
            "youtube.uploads_playlist_http_error",
            extra={"channel_id": channel_id, "status": resp.status_code},
        )
        return None

    videos: list[YouTubeVideo] = []
    for item in resp.json().get("items", []):
        snippet = item.get("snippet", {})
        vid_id = (snippet.get("resourceId") or {}).get("videoId")
        if not vid_id:
            continue
        thumbnails = snippet.get("thumbnails", {})
        thumb = (thumbnails.get("high") or thumbnails.get("default") or {}).get("url")
        videos.append(
            YouTubeVideo(
                video_id=vid_id,
                title=snippet.get("title", ""),
                description=snippet.get("description", ""),
                # Bei einem Playlist-Eintrag zeigt `channelId` auf den Besitzer der
                # PLAYLIST; `videoOwnerChannelId` auf den des Videos. Bei einer
                # Uploads-Playlist ist beides dasselbe — die genauere Angabe zuerst.
                channel_id=snippet.get("videoOwnerChannelId")
                or snippet.get("channelId")
                or channel_id,
                channel_title=snippet.get("videoOwnerChannelTitle")
                or snippet.get("channelTitle", ""),
                published_at=snippet.get("publishedAt", ""),
                thumbnail_url=thumb,
            )
        )
    return videos


async def _fetch_via_data_api(
    client: httpx.AsyncClient, api_key: str, channel_id: str, max_results: int
) -> list[YouTubeVideo]:
    """Rueckfall ueber ``search.list`` — **100 Einheiten pro Aufruf**.

    Bleibt bestehen, damit ein Feed-Ausfall die Ingestion nicht stilllegt; er
    soll nur nicht mehr der Normalweg sein.
    """
    resp = await client.get(
        f"{_YT_API_BASE}/search",
        params={
            "key": api_key,
            "channelId": channel_id,
            "part": "snippet",
            "order": "date",
            "type": "video",
            "maxResults": max_results,
        },
    )
    resp.raise_for_status()

    videos: list[YouTubeVideo] = []
    for item in resp.json().get("items", []):
        snippet = item.get("snippet", {})
        vid_id = item.get("id", {}).get("videoId")
        if not vid_id:
            continue
        thumbnails = snippet.get("thumbnails", {})
        thumb = (thumbnails.get("high") or thumbnails.get("default") or {}).get("url")
        videos.append(
            YouTubeVideo(
                video_id=vid_id,
                title=snippet.get("title", ""),
                description=snippet.get("description", ""),
                channel_id=snippet.get("channelId", channel_id),
                channel_title=snippet.get("channelTitle", ""),
                published_at=snippet.get("publishedAt", ""),
                thumbnail_url=thumb,
            )
        )
    return videos


async def _resolve_channel_id(
    client: httpx.AsyncClient,
    api_key: str,
    handle: str,
) -> str | None:
    """Handle oder Custom-URL zu einer Kanal-ID aufloesen — billigster Weg zuerst.

    Reihenfolge nach Kosten, nicht nach Bequemlichkeit:
    ``UC…`` direkt (0) → ``channels?forHandle`` (1) → Kanalseite (0) →
    ``search`` (**100**, letzte Instanz).
    """
    clean = handle.strip().lstrip("@")

    # Steht die ID schon da, kostet nichts — vorher lief hierfuer trotzdem erst
    # ein API-Aufruf.
    if clean.startswith("UC"):
        return clean

    # forHandle: 1 Einheit, autoritativ.
    resp = await client.get(
        f"{_YT_API_BASE}/channels",
        params={"key": api_key, "forHandle": clean, "part": "id"},
    )
    if resp.status_code == 200:
        items = resp.json().get("items", [])
        if items:
            return _string_or_none(items[0].get("id"))

    # Die Kanalseite nennt ihre eigene ID — 0 Einheiten und funktioniert auch,
    # wenn das Kontingent bereits erschoepft oder der Schluessel tot ist.
    from_page = await _channel_id_from_page(client, clean)
    if from_page:
        return from_page

    # Try search as fallback
    resp = await client.get(
        f"{_YT_API_BASE}/search",
        params={
            "key": api_key,
            "q": clean,
            "type": "channel",
            "part": "snippet",
            "maxResults": 1,
        },
    )
    if resp.status_code == 200:
        items = resp.json().get("items", [])
        if items:
            snippet = items[0].get("snippet", {})
            if isinstance(snippet, dict):
                return _string_or_none(snippet.get("channelId"))

    return None


#: Nur autoritative Felder — und ganz bewusst **nicht** ``"channelId"``.
#: Gemessen am 2026-08-28: in der 2,3-MB-Kanalseite steht der erste
#: ``"channelId"`` in einem *empfohlenen* Kanal, nicht im eigenen. Das Muster
#: lieferte fuer @Bankless prompt ``UCCRxYlYOmLE2l5wxs3ckJtg`` statt
#: ``UCAl9Ld79qaZxp9JzEOwd3aA`` — der Feed lief danach ins Leere und der teure
#: API-Rueckfall sprang an. Ein Beinahe-Fehlschlag, den erst der Live-Lauf zeigte.
_CHANNEL_ID_PATTERNS = (
    re.compile(
        r'<link\s+rel="canonical"\s+href="https://www\.youtube\.com/channel/(UC[\w-]{20,})"'
    ),
    re.compile(r'<meta\s+itemprop="identifier"\s+content="(UC[\w-]{20,})"'),
    re.compile(r'"externalId"\s*:\s*"(UC[\w-]{20,})"'),
)


async def _channel_id_from_page(client: httpx.AsyncClient, handle: str) -> str | None:
    """Kanal-ID aus der oeffentlichen Kanalseite lesen — 0 API-Einheiten.

    Bewusst hinter ``forHandle`` (das ist autoritativ) und bewusst **vor** der
    100-Einheiten-Suche. Schlaegt es fehl, faellt der Aufrufer weiter durch; ein
    Scrape darf ein Ergebnis liefern, aber keins verhindern.

    Nicht gratis im Sinne von billig: die Kanalseite ist ~2,3 MB. Sie ist der Weg,
    der auch dann noch traegt, wenn Schluessel oder Kontingent tot sind — genau
    der Zustand, in dem der Umbau entstanden ist.
    """
    try:
        # Ohne dieses Cookie landet der Abruf aus der EU auf consent.youtube.com,
        # und die 34 kB Zustimmungsseite enthaelt keine Kanal-ID (gemessen
        # 2026-08-28). Als Header gesetzt, damit es nicht an der httpx-Version
        # haengt, die `cookies=` je nach Fassung nicht mehr pro Request annimmt.
        resp = await client.get(
            f"https://www.youtube.com/@{handle}", headers={"Cookie": "SOCS=CAI"}
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    for pattern in _CHANNEL_ID_PATTERNS:
        match = pattern.search(resp.text)
        if match:
            return match.group(1)
    return None


def fetch_transcript(video_id: str) -> str | None:
    """Nur der Text — duenner Wrapper um :func:`fetch_transcript_with_reason`.

    Bleibt fuer bestehende Aufrufer erhalten; wer den Grund braucht, nimmt die
    Variante mit ``reason``.
    """
    return fetch_transcript_with_reason(video_id)[0]


def fetch_transcript_with_reason(video_id: str) -> tuple[str | None, str]:
    """Transkript UND der Grund, falls keines kommt.

    Der Grund ist der eigentliche Punkt. Vorher gab es drei Ausgaenge, von
    denen zwei still ``None`` lieferten (``TranscriptsDisabled`` /
    ``NoTranscriptFound`` und die erfolglose Suche nach einem generierten
    Transkript) — im Journal stand bei 0/12 Transkripten keine einzige
    ``transcript_error``-Zeile. „Kein Transkript" und „YouTube blockt uns" sahen
    identisch aus, und der einzige Weg, sie zu unterscheiden, war eine neue
    Anfrage an YouTube: genau die Handlung, die den IP-Block erzeugt hat.
    """
    # Kurzschluss VOR dem Netzaufruf. Ohne ihn fragt die Pipeline pro Zyklus
    # jedes Video erneut an, und jede dieser Anfragen erneuert genau den Block,
    # auf dessen Ablauf wir warten. Gemessen am 2026-09-04: 0/26 Transkripte,
    # davon 21x IpBlocked -- der Ausfall hielt sich seit Tagen selbst am Leben.
    if ip_block_active():
        return None, TRANSCRIPT_STATUS_BLOCK_COOLDOWN

    try:
        # youtube-transcript-api 1.x: `list_transcripts` (Klassenmethode) ist weg,
        # es gibt nur noch die Instanzmethode `.list()`. Der alte Aufruf warf einen
        # AttributeError, den das weite `except Exception` unten verschluckte —
        # Transkripte kamen seit dem Upgrade nie an, ohne eine Zeile im Log.
        transcript_list = YouTubeTranscriptApi().list(video_id)

        # Try preferred languages first
        transcript = None
        for lang in _PREFERRED_LANGUAGES:
            try:
                transcript = transcript_list.find_transcript([lang])
                break
            except NoTranscriptFound:
                continue

        # Fall back to auto-generated
        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(_PREFERRED_LANGUAGES)
            except NoTranscriptFound:
                return None, TRANSCRIPT_STATUS_NONE_FOUND

        # Zweite Bruchstelle derselben Umstellung: `fetch()` liefert in 1.x ein
        # `FetchedTranscript` aus Snippet-Objekten, keine Liste von Dicts mehr.
        # `entry.get("text")` waere hier erneut still gescheitert.
        parts = transcript.fetch()
        text = " ".join(part.text for part in parts)
        if not text:
            return None, TRANSCRIPT_STATUS_NONE_FOUND
        return text[:_MAX_TRANSCRIPT_CHARS], TRANSCRIPT_STATUS_OK

    except TranscriptsDisabled:
        return None, TRANSCRIPT_STATUS_DISABLED
    except NoTranscriptFound:
        return None, TRANSCRIPT_STATUS_NONE_FOUND
    except Exception as exc:
        # Der Typ gehoert IN den Status, nicht nur ins Log: ein Log ist weg,
        # sobald niemand hinschaut — der Status steht am Dokument.
        status = f"{TRANSCRIPT_STATUS_ERROR_PREFIX}{type(exc).__name__}"
        if type(exc).__name__ in _IP_BLOCK_EXC_NAMES:
            # Der Kommentar oben sagt seit jeher "die eine heisst warten". Ab
            # hier warten wir auch, statt es nur zu protokollieren.
            _note_ip_block()
        logger.warning(
            "youtube.transcript_error",
            extra={"video_id": video_id, "error": str(exc), "status": status},
        )
        return None, status


def _video_to_document(
    video: YouTubeVideo,
    transcript: str | None,
    source_id: str,
    source_name: str,
    transcript_status: str | None = None,
) -> CanonicalDocument:
    """Convert a YouTube video + transcript into a CanonicalDocument."""
    url = f"https://www.youtube.com/watch?v={video.video_id}"
    # Die Beschreibung ist der Rueckfall, wenn kein Transkript da ist. Sie wird
    # auf dieselbe Laenge gekappt wie ein Transkript — der Atom-Feed liefert sie
    # vollstaendig, nicht mehr als 143-Zeichen-Schnipsel.
    description = (video.description or "")[:_MAX_TRANSCRIPT_CHARS]
    text = transcript or description
    text_source = "transcript" if transcript else ("description" if text else None)
    published = None
    if video.published_at:
        try:
            published = datetime.fromisoformat(video.published_at.replace("Z", "+00:00"))
        except ValueError:
            pass

    return CanonicalDocument(
        url=url,
        title=video.title,
        raw_text=text,
        source_id=source_id,
        source_name=source_name,
        source_type=SourceType.YOUTUBE_CHANNEL,
        document_type=DocumentType.YOUTUBE_VIDEO,
        author=video.channel_title,
        published_at=published,
        youtube_meta=YouTubeVideoMeta(
            video_id=video.video_id,
            channel_id=video.channel_id,
            channel_name=video.channel_title,
            thumbnail_url=video.thumbnail_url,
            text_source=text_source,
            transcript_status=transcript_status,
        ),
    )


async def fetch_youtube_channel(
    api_key: str,
    channel_url: str,
    *,
    source_id: str = "youtube",
    source_name: str = "YouTube",
    max_results: int = _MAX_RESULTS_PER_CHANNEL,
    timeout: int = 15,
) -> FetchResult:
    """Fetch recent videos from a YouTube channel and return as FetchResult.

    Compatible with persist_fetch_result() and the standard pipeline.
    """
    try:
        # Extract handle from URL
        handle = _extract_handle(channel_url)
        videos = await fetch_channel_videos(
            api_key, handle, max_results=max_results, timeout=timeout
        )

        documents: list[CanonicalDocument] = []
        for video in videos:
            transcript, transcript_status = fetch_transcript_with_reason(video.video_id)
            doc = _video_to_document(
                video, transcript, source_id, source_name, transcript_status=transcript_status
            )
            documents.append(doc)

        logger.info(
            "youtube.channel_fetched",
            extra={
                "channel": channel_url,
                "videos": len(videos),
                # Vorher an der Textlaenge geraten (>200) — das zaehlte ab dem
                # Feed-Umbau volle Beschreibungen mit. Jetzt das explizite Signal.
                "with_transcript": sum(
                    1
                    for d in documents
                    if d.youtube_meta and d.youtube_meta.text_source == "transcript"
                ),
                # Zerlegung statt Summe: "0 mit Transkript" allein sagt nicht,
                # ob geblockt, ohne Untertitel oder kaputt.
                "transcript_status": Counter(
                    d.youtube_meta.transcript_status or "unknown"
                    for d in documents
                    if d.youtube_meta
                ),
            },
        )

        return FetchResult(
            source_id=source_id,
            documents=documents,
            fetched_at=datetime.now(UTC),
            success=True,
        )

    except Exception as exc:
        logger.error(
            "youtube.fetch_failed",
            extra={"channel": channel_url, "error": str(exc)},
        )
        return FetchResult(
            source_id=source_id,
            documents=[],
            fetched_at=datetime.now(UTC),
            success=False,
            error=str(exc),
        )


def _extract_handle(url: str) -> str:
    """Extract the channel handle from a YouTube URL."""
    url = url.strip()
    # https://www.youtube.com/@Bankless -> Bankless
    if "/@" in url:
        return url.split("/@")[-1].split("/")[0].split("?")[0]
    # https://www.youtube.com/c/JacobCryptoBury -> JacobCryptoBury
    if "/c/" in url:
        return url.split("/c/")[-1].split("/")[0].split("?")[0]
    # https://www.youtube.com/channel/UC... -> UC...
    if "/channel/" in url:
        return url.split("/channel/")[-1].split("/")[0].split("?")[0]
    # https://www.youtube.com/user/... -> ...
    if "/user/" in url:
        return url.split("/user/")[-1].split("/")[0].split("?")[0]
    # Bare handle
    return url.split("/")[-1].lstrip("@")
