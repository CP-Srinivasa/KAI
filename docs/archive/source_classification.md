# Source Classification Pipeline

**Leitmotiv: Klassifizieren → Resolver → Normalisieren → Deduplizieren.**
Kein Dokument wird analysiert, bevor es diese Kette vollständig durchlaufen hat.

---

## Inhaltsverzeichnis

1. [Source Registry](#1-source-registry)
2. [URL Classifier](#2-url-classifier)
3. [Podcast Resolver](#3-podcast-resolver)
4. [YouTube URL Normalizer](#4-youtube-url-normalizer)
5. [RSS Adapter](#5-rss-adapter)
6. [RSS Resolver (HTTP-Validator)](#6-rss-resolver-http-validator)
7. [Canonical Document](#7-canonical-document)
8. [Deduplication](#8-deduplication)
9. [Tests](#9-tests)
10. [Vollständige Pipeline-Übersicht](#10-vollständige-pipeline-übersicht)

---

## 1. Source Registry

**Modul:** `app/storage/repositories/source_repo.py`
**Schema:** `app/storage/schemas/source.py`
**DB-Modell:** `app/storage/models/source.py`
**Migration:** `app/storage/migrations/versions/0001_create_sources_table.py`

Die Source Registry ist die persistente Quelle der Wahrheit für alle bekannten Quellen. Jede URL, die das System verarbeitet, muss zuerst als Source registriert sein.

### Felder

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `source_id` | `str` (UUID) | auto | Primärschlüssel |
| `source_type` | `SourceType` | ja | Klassifizierung (s.u.) |
| `status` | `SourceStatus` | ja | Lifecycle-Status (s.u.) |
| `auth_mode` | `AuthMode` | ja | Authentifizierungsart |
| `original_url` | `str` | ja | Original-URL wie eingegeben |
| `normalized_url` | `str` | nein | Normalisierte URL nach Resolver |
| `provider` | `str` | nein | Provider-Name (z.B. `"podigee"`) |
| `notes` | `str` | nein | Freitext, z.B. Auflösungsstatus |
| `created_at` | `datetime` | auto | Erstellungszeitpunkt |
| `updated_at` | `datetime` | auto | Letztes Update |

### SourceType — vollständige Taxonomie

| Wert | Bedeutung |
|---|---|
| `rss_feed` | URL liefert valides RSS/Atom-Feed |
| `website` | Generische Webseite |
| `news_domain` | Bekannte News-Domain (aus `monitor/news_domains.txt`) |
| `news_api` | Zugriff über News-API (z.B. NewsAPI.org) |
| `youtube_channel` | YouTube-Kanal (alle youtube.com / youtu.be URLs) |
| `podcast_feed` | Direkter Podcast-Feed (RSS, Podigee-Pattern) |
| `podcast_page` | Podcast-Landingpage (Apple, Spotify, generisch) |
| `reference_page` | Bildungs-/Referenzseite (a16z, Coinbase Learn, etc.) |
| `social_api` | Social-Media-API (Twitter, etc.) |
| `manual_source` | Manuell gepflegter Eintrag |
| `unresolved_source` | Keine Auflösungsstrategie verfügbar |

### SourceStatus — Lifecycle

| Wert | Bedeutung |
|---|---|
| `active` | Bereit für Ingestion |
| `planned` | Vorgesehen, noch nicht aktiviert |
| `disabled` | Manuell deaktiviert |
| `requires_api` | Benötigt Platform-API (Apple Podcasts, Spotify) |
| `manual_resolution` | Manuelle Überprüfung erforderlich |
| `unresolved` | Keine Auflösungsstrategie gefunden |

### AuthMode

| Wert | Bedeutung |
|---|---|
| `none` | Kein Auth nötig |
| `api_key` | API-Key-Authentifizierung |
| `oauth` | OAuth 2.0 |
| `basic` | HTTP Basic Auth |
| `manual` | Manueller Login |

### Repository-API

```python
repo = SourceRepository(session)

# Erstellen
source = await repo.create(SourceCreate(
    source_type=SourceType.RSS_FEED,
    status=SourceStatus.ACTIVE,
    original_url="https://cointelegraph.com/rss",
))

# Abrufen
source = await repo.get_by_id(source_id)
source = await repo.get_by_url("https://cointelegraph.com/rss")

# Filtern
active_feeds = await repo.list(source_type=SourceType.RSS_FEED, status=SourceStatus.ACTIVE)

# Aktualisieren
await repo.update(source_id, SourceUpdate(status=SourceStatus.DISABLED))

# Löschen
await repo.delete(source_id)
```

### REST API

```
POST   /sources                         — Neue Source anlegen
GET    /sources                         — Liste (filter: source_type, status, provider)
GET    /sources/{source_id}             — Einzelne Source
PATCH  /sources/{source_id}            — Felder aktualisieren
DELETE /sources/{source_id}            — Löschen (204)
GET    /sources/classify?url=<url>      — URL ohne DB klassifizieren
```

---

## 2. URL Classifier

**Modul:** `app/ingestion/classifier.py`
**Klasse:** `SourceClassifier` (domain-list-aware), `classify_url()` (stateless fallback)

Der Classifier trifft Entscheidungen **ausschliesslich anhand von URL-Patterns** — kein HTTP-Request, kein DNS-Lookup.

### Klassifizierungs-Priorität (first match wins)

| Priorität | Bedingung | Ergebnis |
|---|---|---|
| 1 | `youtube.com` oder `youtu.be` im Host | `youtube_channel / active` |
| 2 | `open.spotify.com/show/` im Pfad | `podcast_page / requires_api` |
| 2 | `podcasters.spotify.com` mit `/show/` im Pfad | `podcast_page / requires_api` |
| 3 | `podcasts.apple.com` im Host | `podcast_page / requires_api` |
| 4 | `*.podigee.io` Subdomain | `podcast_feed / active` |
| 5 | Pfad matcht RSS-Pattern | `rss_feed / active` |
| 6 | Pfad matcht Podcast-Landing-Pattern | `podcast_page / unresolved` |
| 7 | Host ist bekannte Referenz-Domain | `reference_page / active` |
| 8 | Pfad matcht Referenz-Pattern | `reference_page / active` |
| 9 | Host ist bekannte News-Domain (aus Monitor) | `news_domain / active` |
| 10 | Alles andere | `website / active` |

### RSS-Pfad-Patterns (Regel 5)

```
/feed, /feed/, /feed.xml, /feed.atom
/rss, /rss/, /rss.xml
/atom.xml
/feed/rss/, /feed/podcast/
/feed/mp3
/podcast.xml
*.rss, *.atom
```

### Podcast-Landing-Patterns (Regel 6)

```
/podcast/, /podcasts/, /podcast, /podcasts
/episode/, /episodes/, /episode, /episodes
/show/, /shows/, /show, /shows
/hoeren/, /zuhoeren/
```

### Referenz-Domains (hartkodiert, Regel 7)

```
a16zcrypto.com, a16z.com, coinbase.com, coinledger.io,
coin.dance, river.com, unchainedcrypto.com
```

### Referenz-Pfad-Patterns (Regel 8)

```
/learn/, /research/, /resources/, /guides/, /education/,
/knowledge/, /posts/article/, /explainer, /glossary, /wiki
```

### News-Domains (Regel 9)

Geladen aus `monitor/news_domains.txt`. Format: `domain|credibility|scope|lang`.
Nur bei `SourceClassifier.from_monitor_dir()` aktiv — `classify_url()` erkennt keine News-Domains.

### Verwendung

```python
# Stateless (ohne Monitor-Dateien)
from app.ingestion.classifier import classify_url
result = classify_url("https://cointelegraph.com/rss")
# → ClassificationResult(source_type=RSS_FEED, status=ACTIVE)

# Domain-list-aware (mit news_domains.txt)
from app.ingestion.classifier import SourceClassifier
classifier = SourceClassifier.from_monitor_dir(Path("monitor"))
result = classifier.classify("https://coindesk.com")
# → ClassificationResult(source_type=NEWS_DOMAIN, status=ACTIVE)
```

### Edge Cases

| URL | Ergebnis | Grund |
|---|---|---|
| `https://www.btc-echo.de/podcasts/` | `podcast_page / unresolved` | Landing-Page-Pattern |
| `https://cointelegraph.com/podcasts/hashing-it-out` | `podcast_page / unresolved` | Kein Feed-Pfad |
| `https://epicenter.tv/feed/podcast/` | `rss_feed / active` | RSS-Pattern |
| `https://saschahuber.podigee.io` | `podcast_feed / active` | Podigee-Subdomain |
| `https://open.spotify.com/episode/abc` | `podcast_page / unresolved` | Kein `/show/`-Pfad |
| `https://podcasters.spotify.com/pod/show/xyz` | `podcast_page / requires_api` | Spotify-Creator |
| `https://youtu.be/dQw4w9WgXcQ` | `youtube_channel / active` | youtu.be-Domain |

---

## 3. Podcast Resolver

**Modul:** `app/ingestion/resolvers/podcast.py`
**Datenquelle:** `monitor/podcast_feeds_raw.txt`

Lädt rohe Podcast-URLs, klassifiziert jede und versucht eine Feed-URL zu bestimmen.

### Auflösungs-Logik

```
URL
 │
 ├─ Classifier → RSS_FEED         → PodcastSource(type=PODCAST_FEED, status=ACTIVE, resolved_url=url)
 │
 ├─ Classifier → PODCAST_FEED     → PodcastSource(type=PODCAST_FEED, status=ACTIVE,
 │   (= Podigee)                      resolved_url="{base}/feed/mp3")
 │
 ├─ Classifier → PODCAST_PAGE     ┬─ status=REQUIRES_API → PodcastSource(type=PODCAST_PAGE, status=REQUIRES_API)
 │                                └─ status=UNRESOLVED   → PodcastSource(type=UNRESOLVED_SOURCE, status=UNRESOLVED)
 │
 └─ Alles andere                  → PodcastSource(type=UNRESOLVED_SOURCE, status=UNRESOLVED)
```

**Wichtig:** Apple Podcasts und Spotify landen in `requires_api`, nicht in `unresolved`.
Generische Landing-Pages landen in `unresolved_source`, nicht in `podcast_page`.

### Podigee-Pattern

```
https://saschahuber.podigee.io          → https://saschahuber.podigee.io/feed/mp3
https://saschahuber.podigee.io/feed/mp3 → https://saschahuber.podigee.io/feed/mp3  (idempotent)
```

### Verwendung

```python
from app.ingestion.resolvers.podcast import resolve_podcast_url, load_and_resolve_podcasts
from pathlib import Path

# Einzelne URL
source = resolve_podcast_url("https://saschahuber.podigee.io")
# → PodcastSource(source_type=PODCAST_FEED, status=ACTIVE,
#                 resolved_url="https://saschahuber.podigee.io/feed/mp3")

# Aus Monitor-Datei
resolved, unresolved = load_and_resolve_podcasts(Path("monitor"))
# resolved   → Liste aktiver Feeds
# unresolved → Apple, Spotify, Landing-Pages
```

---

## 4. YouTube URL Normalizer

**Modul:** `app/ingestion/resolvers/youtube.py`
**Datenquelle:** `monitor/youtube_channels.txt`

Normalisiert YouTube-Channel-URLs auf kanonische Form und dedupliziert nach normalisierter URL.

### Unterstützte URL-Formate

| Eingabe | Normalisiert | `channel_type` |
|---|---|---|
| `https://www.youtube.com/@Bankless` | `https://www.youtube.com/@Bankless` | `handle` |
| `https://www.youtube.com/channel/UCxxx` | `https://www.youtube.com/channel/UCxxx` | `channel_id` |
| `https://www.youtube.com/user/oldstyle` | `https://www.youtube.com/user/oldstyle` | `user` |
| `https://www.youtube.com/c/JacobCryptoBury` | `https://www.youtube.com/c/JacobCryptoBury` | `custom` |
| `https://youtu.be/dQw4w9WgXcQ` | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` | `video_link` |
| `https://example.com/not-youtube` | _(unveraendert)_ | `unknown` |

**Hinweis:** `youtu.be`-Links sind Video-Links, keine Kanal-Links. Sie werden als `video_link` markiert und sollten nicht in der Kanal-Registry erscheinen.

### Verwendung

```python
from app.ingestion.resolvers.youtube import normalize_youtube_url, load_youtube_channels
from pathlib import Path

# Einzelne URL
ch = normalize_youtube_url("https://www.youtube.com/@CoinBureau")
# → YouTubeChannel(normalized_url="https://www.youtube.com/@CoinBureau",
#                  handle="CoinBureau", channel_type="handle")

# Aus Monitor-Datei (automatisch dedupliziert)
channels = load_youtube_channels(Path("monitor"))
```

---

## 5. RSS Adapter

**Modul:** `app/ingestion/rss/adapter.py`
**Klasse:** `RSSFeedAdapter(BaseSourceAdapter)`

Fetcht einen RSS/Atom-Feed via `httpx`, parsed ihn mit `feedparser` und konvertiert Einträge in `CanonicalDocument`.

### Verhalten

- **Retry:** 3 Versuche mit exponentiellem Backoff (1s → 10s max) via `tenacity`
- **User-Agent:** `ai-analyst-bot/0.1 (feed reader)`
- **Accept-Header:** RSS, Atom, XML, generic
- **Fehlerbehandlung:** Netzwerkfehler → `FetchResult(success=False, error=...)`
- **Text-Extraktion:** bevorzugt `content[0].value` vor `summary`
- **Datum:** `published_parsed` → UTC-aware `datetime`, Fehler → `None`

### CanonicalDocument aus RSS-Eintrag

| RSS-Feld | CanonicalDocument-Feld |
|---|---|
| `entry.id` oder `entry.link` | `external_id` |
| `entry.link` | `url` |
| `entry.title` | `title` |
| `entry.author` | `author` |
| `entry.published_parsed` | `published_at` |
| `entry.content[0].value` / `summary` | `raw_text` (HTML-bereinigt) |
| `entry.summary` | `summary` (HTML-bereinigt) |
| Fetch-Zeitpunkt | `fetched_at` |

### Verwendung

```python
from app.ingestion.rss.adapter import RSSFeedAdapter
from app.ingestion.base.interfaces import SourceMetadata
from app.core.enums import SourceType, SourceStatus

metadata = SourceMetadata(
    source_id="src-001",
    source_name="CoinTelegraph",
    source_type=SourceType.RSS_FEED,
    url="https://cointelegraph.com/rss",
)
adapter = RSSFeedAdapter(metadata)
result = await adapter.fetch()
# result.documents → list[CanonicalDocument]
# result.success   → True/False
# result.error     → str | None
```

---

## 6. RSS Resolver (HTTP-Validator)

**Modul:** `app/ingestion/resolvers/rss.py`
**Funktion:** `resolve_rss_feed(url, timeout=10) → RSSResolveResult`

Validiert per HTTP ob eine URL wirklich einen RSS/Atom-Feed liefert.

**Prinzip: Kein Fake-URL-Bau.** Wenn der HTTP-Aufruf kein valides Feed liefert, ist `is_valid=False`. Es werden keine alternativen URLs (`/feed`, `/rss`) konstruiert oder geraten.

### RSSResolveResult

| Feld | Typ | Bedeutung |
|---|---|---|
| `url` | `str` | Eingabe-URL |
| `is_valid` | `bool` | True = feedparser konnte Feed lesen |
| `resolved_url` | `str \| None` | Finale URL nach Redirects |
| `feed_title` | `str \| None` | Titel des Feeds (wenn gültig) |
| `entry_count` | `int` | Anzahl Einträge (0 wenn invalid) |
| `error` | `str \| None` | Fehlerbeschreibung bei Misserfolg |

### Retry-Verhalten

3 Versuche, exponentieller Backoff: 1s → 10s. `reraise=True`.

### Verwendung

```python
from app.ingestion.resolvers.rss import resolve_rss_feed

result = await resolve_rss_feed("https://cointelegraph.com/rss")
if result.is_valid:
    print(f"Feed: {result.feed_title}, {result.entry_count} Einträge")
    print(f"Finale URL: {result.resolved_url}")
else:
    print(f"Kein gültiger Feed: {result.error}")
```

---

## 7. Canonical Document

**Modul:** `app/core/domain/document.py`
**Klasse:** `CanonicalDocument`

Einheitliches Dokumentmodell für alle Quell-Typen: News-Artikel, Podcast-Episoden, YouTube-Videos, Referenzseiten.

### Kern-Felder

| Feld | Typ | Beschreibung |
|---|---|---|
| `id` | `UUID` | Automatisch generiert |
| `content_hash` | `str` | SHA-256 automatisch berechnet aus `url + title + raw_text` |
| `document_type` | `DocumentType` | `article`, `podcast_episode`, `youtube_video`, `social_post`, `research_report`, `reference`, `unknown` |
| `source_type` | `SourceType` | Woher das Dokument stammt |
| `url` | `str` | Pflichtfeld |
| `title` | `str` | Pflichtfeld |
| `published_at` | `datetime \| None` | Veröffentlichungszeitpunkt |
| `fetched_at` | `datetime` | Abruf-Zeitpunkt (auto) |
| `raw_text` | `str \| None` | Originaltext (ggf. HTML-bereinigt) |
| `cleaned_text` | `str \| None` | Vollständig normalisierter Text |
| `word_count` | `int` | Computed property (aus cleaned_text oder raw_text) |

### Media-spezifische Metadaten

Nur eines der beiden Sub-Modelle wird pro Dokument befüllt:

**`YouTubeVideoMeta`:**
```
video_id, channel_id, channel_name,
duration_seconds, view_count, like_count, thumbnail_url
```

**`PodcastEpisodeMeta`:**
```
podcast_title, episode_number, season,
audio_url, duration_seconds, feed_url
```

### Analyse-Felder (befüllt nach Pipeline)

```
sentiment_label, sentiment_score     (−1.0 … +1.0)
relevance_score, impact_score        (0.0 … 1.0)
credibility_score, novelty_score     (0.0 … 1.0)
historical_similarity_score          (0.0 … 1.0)
entity_mentions                      list[EntityMention]
is_duplicate, is_analyzed            bool
```

### Content Hash

`content_hash` wird automatisch beim Erstellen des Objekts berechnet:
```python
sha256(f"{url}|{title}|{raw_text or ''}".encode())
```
Manuell gesetztes `content_hash` wird beibehalten (keine Überschreibung).

### Verwendung

```python
from app.core.domain.document import CanonicalDocument, YouTubeVideoMeta
from app.core.enums import SourceType, DocumentType

doc = CanonicalDocument(
    url="https://cointelegraph.com/article/bitcoin-ath",
    title="Bitcoin hits new all-time high",
    document_type=DocumentType.ARTICLE,
    source_type=SourceType.RSS_FEED,
    raw_text="Bitcoin reached...",
)
# doc.content_hash → SHA-256 hex string (auto)
# doc.word_count   → int (computed)
```

---

## 8. Deduplication

**Modul:** `app/enrichment/deduplication/deduplicator.py`
**Klasse:** `Deduplicator`
**Normalisierung:** `app/normalization/cleaner.py`

### URL-Normalisierung (`normalize_url`)

Vor jedem URL-Vergleich werden folgende Transformationen angewendet:

| Transformation | Beispiel |
|---|---|
| Lowercase Scheme + Host | `HTTPS://Example.COM` → `https://example.com` |
| `www.` entfernen | `www.coindesk.com` → `coindesk.com` |
| Trailing Slash entfernen | `/article/` → `/article` |
| Fragment entfernen | `#section` → _(weg)_ |
| Tracking-Params entfernen | `utm_source`, `utm_medium`, `fbclid`, `gclid`, `mc_cid`, `_ga` u.v.m. |
| Query-Params sortieren | `?b=2&a=1` → `?a=1&b=2` |

**Ergebnis:** Gleicher Artikel mit unterschiedlichen UTM-Links → gleiche normalisierte URL → Duplikat erkannt.

### Title-Normalisierung (`normalize_title`)

| Transformation | Beispiel |
|---|---|
| NFKD Unicode-Decomposition | `Überblick` → `Uberblick` |
| ASCII-only | Akzente, Umlaute → Basiszeichen |
| Lowercase | `BITCOIN` → `bitcoin` |
| Satzzeichen entfernen | `$100K!` → `100K` |
| Whitespace kollabieren | `a  b` → `a b` |

### DuplicateScore — Scoring-Signale

| Signal | Score | Auslöser |
|---|---|---|
| `url_match` | **1.0** | Normalisierte URL bereits gesehen |
| `content_hash` | **1.0** | SHA-256(normalize_url + normalize_title + raw_text) bereits gesehen |
| `title_hash` | **0.85** | SHA-256(normalize_title) bereits gesehen (gleicher Titel, andere URL) |

### Threshold — Konservative Defaults

| Threshold | Verhalten |
|---|---|
| `1.0` _(default)_ | Nur exakte URL- oder Content-Hash-Matches → Duplikat |
| `0.85` | Zusätzlich: gleicher Titel auf anderer Domain → Duplikat |

**Prinzip: Lieber ein Duplikat durchlassen als ein echtes Dokument fälschlicherweise zu blocken.**

### API

```python
from app.enrichment.deduplication.deduplicator import Deduplicator

dedup = Deduplicator(threshold=1.0)  # konservativ

# Binäre Prüfung
is_dup = dedup.is_duplicate(doc)

# Mit Begründung
score = dedup.score(doc)
# DuplicateScore(score=1.0, is_duplicate=True, reasons=['url_match', 'content_hash'])

# Registrieren
dedup.register(doc)

# Batch-Filter (gibt nur Nicht-Duplikate zurück)
unique = dedup.filter(documents)

# Audit-Batch (gibt alle mit Score — Duplikate werden NICHT registriert)
pairs = dedup.filter_scored(documents)
# [(doc, DuplicateScore), ...]

# Zustand löschen
dedup.reset()

# Info
print(dedup.seen_count)   # Anzahl registrierter URLs
print(dedup.threshold)    # Konfigurieter Schwellwert
```

---

## 9. Tests

### Test-Abdeckung

| Modul | Test-Datei | Tests |
|---|---|---|
| Source Registry | `test_source_registry.py` | 11 |
| URL Classifier | `test_classifier.py` | 24 |
| Podcast Resolver | `test_podcast_resolver.py` | 12 |
| YouTube Normalizer | `test_youtube_resolver.py` | 10 |
| RSS Adapter | `test_rss_adapter.py` | 6 |
| RSS Resolver | `test_rss_resolver.py` | 6 |
| Canonical Document | `test_canonical_document.py`, `test_models.py` | 16 + 16 |
| Deduplication | `test_deduplicator.py` | 25 |
| Normalisierung | `test_cleaner.py` | 33 |

**Gesamte Suite: >300 Tests, alle ohne echte Netzwerkverbindung.**

### Test-Ausführung

```bash
# Alle Tests
pytest tests/ -v

# Nur Classification-Pipeline
pytest tests/unit/test_classifier.py tests/unit/test_podcast_resolver.py \
       tests/unit/test_youtube_resolver.py tests/unit/test_rss_resolver.py -v

# Mit Coverage
pytest tests/ --cov=app --cov-report=term-missing
```

### Mock-Strategie

- **RSS Adapter / RSS Resolver:** `httpx.AsyncClient` via `unittest.mock.patch` gemockt — kein echter HTTP
- **Source Registry:** In-Memory SQLite via pytest-Fixtures — kein Postgres nötig
- **Classifier / Resolver:** Rein unit-testbar — keine externen Abhängigkeiten

---

## 10. Vollständige Pipeline-Übersicht

```
monitor/podcast_feeds_raw.txt
monitor/youtube_channels.txt
monitor/news_domains.txt
         │
         ▼
┌─────────────────────┐
│   URL Classifier    │  ← classify_url(url) → ClassificationResult
│   (URL-patterns     │    Kein HTTP, kein DNS
│    only, no HTTP)   │
└────────┬────────────┘
         │
    source_type?
         │
    ┌────┴─────────────────────────────────────┐
    │              │              │             │
    ▼              ▼              ▼             ▼
RSS_FEED      PODCAST_*     YOUTUBE_*       Sonstige
    │              │              │
    ▼              ▼              ▼
RSS Resolver  Podcast       YouTube
(HTTP-valid.) Resolver      Normalizer
    │              │              │
    ▼              ▼              ▼
RSSResolve-  PodcastSource  YouTubeChannel
Result        (resolved_url)  (normalized_url)
    │
    ▼
Source Registry (PostgreSQL)
source_type + status + normalized_url
         │
         ▼
┌─────────────────────┐
│    RSS Adapter      │  ← Ingestion: httpx + feedparser + tenacity
│    (active feeds)   │    3 Retries, exponentieller Backoff
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ CanonicalDocument   │  ← Einheitliches Modell für alle Quell-Typen
│  - content_hash     │    News, Podcast, YouTube, Web → gleiche Struktur
│  - document_type    │    YouTubeVideoMeta / PodcastEpisodeMeta als Sub-Modelle
│  - youtube_meta     │
│  - podcast_meta     │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   Deduplicator      │  ← normalize_url + title_hash + content_hash
│  threshold=1.0      │    Scoring: url_match(1.0), content_hash(1.0), title_hash(0.85)
│  (konservativ)      │    filter_scored() für Audit, filter() für Produktion
└────────┬────────────┘
         │
         ▼
    Analysis Pipeline
    (rule-based → LLM)
         │
         ▼
    Scoring / Ranking
    (priority 1–10)
         │
         ▼
    Alert / Research Pack
    (erst hier, nicht früher)
```

### Invarianten

1. **Kein Dokument ohne klassifizierte Source.**
2. **Kein HTTP beim Klassifizieren** — nur URL-Pattern-Matching.
3. **Keine fake RSS-URL-Konstruktion** — RSS Resolver baut keine URLs, validiert nur.
4. **Dedup vor Analyse** — kein LLM-Aufruf für Duplikate.
5. **Alert nach Analyse** — kein Alert ohne `is_alert_worthy()`.
6. **Konservative Dedup** — Threshold=1.0 default, lieber falsch negativ als falsch positiv.
