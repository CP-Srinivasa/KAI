# Source Classification

## Overview

Every source must be classified before ingestion. Classification is based on URL patterns only — no HTTP requests are made during classification.

## Source Types

| Type | When applied |
|------|-------------|
| `rss_feed` | URL path matches known RSS/Atom patterns (`/feed`, `/rss`, `.xml`, etc.) |
| `youtube_channel` | `youtube.com` or `youtu.be` domain |
| `podcast_feed` | Podigee subdomain (pattern-resolvable) |
| `podcast_page` | Apple Podcasts or Spotify show page (requires API) |
| `website` | Everything else — general news or data website |
| `reference_page` | Set manually in `monitor/website_sources.txt` |
| `unresolved_source` | URL could not be classified |

## Source Status

| Status | Meaning |
|--------|---------|
| `active` | Ready for ingestion |
| `requires_api` | Needs platform API access (Apple, Spotify) |
| `unresolved` | No resolution strategy available |
| `disabled` | Manually disabled |
| `planned` | Not yet implemented |

## Classifier Rules (in priority order)

1. YouTube domain → `youtube_channel / active`
2. `open.spotify.com/show/` → `podcast_page / requires_api`
3. `podcasts.apple.com` → `podcast_page / requires_api`
4. `*.podigee.io` → `podcast_feed / active` (feed: `{base}/feed/mp3`)
5. Path matches RSS pattern → `rss_feed / active`
6. Otherwise → `website / active`

## Podcast Resolution

`PodcastResolver` loads `monitor/podcast_feeds_raw.txt` and classifies each entry:
- RSS paths → confirmed `podcast_feed`
- Podigee → constructed feed URL via pattern
- Apple/Spotify → `podcast_page / requires_api`
- Other pages → `unresolved_source`

## YouTube Resolution

`YouTubeResolver` loads `monitor/youtube_channels.txt` and normalizes each URL:
- `@handle` → `https://www.youtube.com/@{handle}`
- `/channel/{id}` → `https://www.youtube.com/channel/{id}`
- `/c/{name}` → `https://www.youtube.com/c/{name}`
- `/user/{name}` → `https://www.youtube.com/user/{name}`

Duplicates (same normalized URL) are removed automatically.

## CLI Usage

```bash
# Classify a single URL
python -m app.cli.main sources classify https://epicenter.tv/feed/podcast/

# Resolve all podcast sources
python -m app.cli.main podcasts resolve

# Resolve all YouTube channels
python -m app.cli.main youtube resolve

# Ingest an RSS feed
python -m app.cli.main ingest rss https://cointelegraph.com/rss
```

## API Usage

```
GET /sources/classify?url=https://epicenter.tv/feed/podcast/
```
