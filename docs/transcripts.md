# Transcript Pipeline Reference

The transcript pipeline ingests textual content from YouTube videos and podcast episodes. It supports multiple transcript sources with graceful fallbacks and no mandatory API keys for the core path.

---

## YouTube Transcript Pipeline

### Module: `app/ingestion/youtube/transcript.py`

Fetches transcripts from YouTube videos using `youtube-transcript-api` (no YouTube API key required for public videos with captions).

**[REQUIRES: `pip install youtube-transcript-api`]**

### TranscriptStatus Values

| Status | Meaning |
|--------|---------|
| `available` | Transcript fetched successfully |
| `unavailable` | No transcript found for this video |
| `disabled` | Video owner has disabled transcripts |
| `rate_limited` | Too many requests (HTTP 429) |
| `requires_metadata` | Need YouTube Data API to get video list |
| `error` | Unexpected error |

### TranscriptResult

```python
@dataclass
class TranscriptResult:
    video_id: str
    status: TranscriptStatus
    language: str = "en"
    is_generated: bool = True    # True = auto-generated captions
    segments: list[TranscriptSegment] = field(default_factory=list)
    error: str = ""

    @property
    def full_text(self) -> str:
        """All segments joined by space."""
```

### TranscriptSegment

```python
@dataclass
class TranscriptSegment:
    text: str
    start: float    # seconds from video start
    duration: float
```

### Usage

```python
from app.ingestion.youtube.transcript import YouTubeTranscriptPipeline

pipeline = YouTubeTranscriptPipeline(
    preferred_languages=["en", "de"],
)

# Single video
result = await pipeline.fetch("dQw4w9WgXcQ")
if result.status.value == "available":
    print(result.full_text[:500])

# Batch
results = await pipeline.fetch_batch(["id1", "id2", "id3"])
```

### Graceful Degradation

If `youtube-transcript-api` is not installed:
- `fetch()` returns `TranscriptResult(status=UNAVAILABLE)` immediately
- No exception is raised

---

## YouTube Video Metadata

### Module: `app/ingestion/youtube/video_metadata.py`

**[REQUIRES: `YOUTUBE_API_KEY` environment variable]**
**[REQUIRES: YouTube Data API v3 enabled in Google Cloud Console]**

Used for getting video metadata and channel video lists (e.g. to discover new episodes).

```python
from app.ingestion.youtube.video_metadata import YouTubeMetadataClient

client = YouTubeMetadataClient()  # reads YOUTUBE_API_KEY from env

# Single video
meta = await client.get_video_metadata("video_id")
print(meta.title, meta.channel_title, meta.duration_seconds)

# Channel video list (for discovery)
videos = await client.list_channel_videos("channel_id", max_results=10)
```

If `YOUTUBE_API_KEY` is not set, `get_video_metadata()` returns a stub with `status="requires_api"`.

---

## Podcast Transcript Pipeline

### Module: `app/ingestion/podcasts/transcript.py`

Parses podcast RSS feeds and detects transcript availability. Supports three discovery layers without requiring audio download.

### Transcript Discovery Layers

| Priority | Method | Detection |
|----------|--------|-----------|
| 1 | **Podcasting 2.0** `podcast:transcript` tag | `in_feed` |
| 2 | Long description (≥1500 chars) | `external_file` (uses description as proxy) |
| 3 | Whisper AI (if enabled + audio URL present) | `ai_required` |

### TranscriptAvailability Values

| Value | Meaning |
|-------|---------|
| `in_feed` | Transcript URL found in RSS feed (Podcasting 2.0) |
| `external_file` | Transcript linked externally or derived from long description |
| `ai_required` | No transcript in feed; Whisper needed for audio |
| `unavailable` | No transcript and no audio or insufficient description |
| `pending` | Transcript fetch queued but not yet done |

### PodcastEpisode

```python
@dataclass
class PodcastEpisode:
    episode_id: str
    title: str
    url: str                # Episode page / episode link
    description: str = ""
    audio_url: str = ""
    published_at: datetime | None = None
    duration_seconds: int | None = None

    # Transcript
    transcript_availability: TranscriptAvailability = TranscriptAvailability.UNAVAILABLE
    transcript_refs: list[TranscriptRef] = field(default_factory=list)
    transcript_text: str = ""   # Set if description used as proxy or transcript fetched

    # Podcast metadata
    podcast_name: str = ""
    episode_number: int | None = None
    season_number: int | None = None
    itunes_episode_type: str = ""
    language: str = "en"
    tags: list[str] = field(default_factory=list)
```

### TranscriptRef

```python
@dataclass
class TranscriptRef:
    url: str
    mime_type: str = ""     # text/vtt | application/json | text/plain | text/html
    language: str = "en"
    rel: str = ""           # captions | transcript
```

### Supported Transcript Formats (Podcasting 2.0)

| MIME Type | Format | Notes |
|-----------|--------|-------|
| `text/vtt` | WebVTT | Most common, includes timestamps |
| `application/json` | JSON chapters | Speaker-labeled |
| `text/plain` | Plain text | No timestamps |
| `text/html` | HTML | May need stripping |
| `application/srt` | SRT subtitles | Common legacy format |

### Usage

```python
from app.ingestion.podcasts.transcript import PodcastTranscriptParser

# Basic (no Whisper)
parser = PodcastTranscriptParser()
episodes = await parser.parse_feed("https://example.com/podcast.rss")

for ep in episodes:
    print(ep.title, ep.transcript_availability.value)
    if ep.transcript_refs:
        print("  Transcript URL:", ep.transcript_refs[0].url)
    elif ep.transcript_text:
        print("  Text length:", len(ep.transcript_text))

# With Whisper flag (marks episodes as ai_required if they have audio but no transcript)
parser = PodcastTranscriptParser(whisper_enabled=True)
```

### Duration Parsing

`PodcastTranscriptParser._parse_duration()` handles common podcast feed formats:
- `"3600"` → 3600 seconds
- `"1:30"` → 90 seconds (MM:SS)
- `"1:00:00"` → 3600 seconds (HH:MM:SS)
- Invalid/empty → `None`

---

## Integration with Signal Pipeline

Transcripts flow into the signal pipeline as text sources:

```
[YouTube / Podcast RSS]
         │
         ▼
  TranscriptResult / PodcastEpisode
         │  (full_text / transcript_text)
         ▼
  NLP / Entity Extraction     (future: Phase 7+)
         │
         ▼
  SignalCandidateGenerator
         │
         ▼
  NarrativeClusterEngine
```

Currently, transcripts provide raw text that can be passed to any text analysis pipeline. Structured entity extraction and signal generation from transcripts is planned for Phase 7.

---

## Configuration Reference

```env
# YouTube
YOUTUBE_API_KEY=your_key        # [REQUIRES] for metadata/channel listing
# youtube-transcript-api has no key requirement for public videos

# Podcast
# No env vars needed for RSS parsing
# WHISPER_ENABLED=true          # Not currently env-configured; set in code
```

---

## Limitations

- YouTube auto-generated captions may have transcription errors.
- Podcast `podcast:transcript` adoption is ~30-40% of modern shows (as of 2025).
- Whisper AI integration (for audio-only episodes) is not yet implemented — transcripts marked `ai_required` indicate the capability is planned.
- YouTube batch fetching is sequential (no parallel API — rate limit safety).
- No caching layer yet — re-fetching the same video ID makes repeated HTTP calls.
