"""Ingestion layer contracts.

These dataclasses and the BaseSourceAdapter ABC are the ONLY interfaces
between the ingestion layer and everything downstream (storage, analysis).

Rules enforced here:
- FetchResult.documents is always a list, never None.
- FetchResult.success=False + error=<message> on any failure — adapters must not raise.
- Adapters must set url, title, source_id, source_name, source_type on every document.
- content_hash is auto-computed by CanonicalDocument — adapters must not set it.
- source_type on each CanonicalDocument must match SourceMetadata.source_type.

FetchItem is the canonical raw-source type:
- Adapters should produce FetchItem first, then call normalize_fetch_item().
- FetchItem contains NO analysis, NO persistence state, NO lifecycle fields.
- It is as close to the source as possible.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.domain.document import CanonicalDocument
from app.core.enums import SourceStatus, SourceType


@dataclass
class FetchItem:
    """Raw item as returned directly by a source adapter.

    This is the canonical ingestion-layer type — as close to the source as possible.

    Contract:
    - NO analysis fields (no scores, no sentiment, no priority, no tickers)
    - NO persistence state (no status, no is_analyzed, no is_duplicate, no content_hash)
    - NO source metadata (source_id, source_name, source_type are added by normalize_fetch_item)
    - Adapters produce FetchItem; normalize_fetch_item() converts to CanonicalDocument

    Fields mirror what a typical source (RSS, API, scraper) provides directly:
    - url:          Canonical URL of the item (required).
    - external_id:  Source-specific ID (RSS guid, API id, etc.) — None if not available.
    - title:        Raw title from source — None if not available.
    - content:      Raw body text or excerpt — None if not available.
    - published_at: Publication timestamp from source — None if not provided.
    - metadata:     Source-specific extras (image_url, tags, author, …) — no schema.
    """

    url: str
    external_id: str | None = None
    title: str | None = None
    content: str | None = None
    published_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_fetch_item(
    item: FetchItem,
    *,
    source_id: str,
    source_name: str,
    source_type: SourceType,
) -> CanonicalDocument:
    """Convert a FetchItem into a CanonicalDocument with source metadata applied.

    This is the ONLY sanctioned conversion path from raw adapter output to the
    domain model. All source metadata is injected here — never inside the adapter.

    content_hash is auto-computed by CanonicalDocument.model_validator (do not set it).
    """
    return CanonicalDocument(
        url=item.url,
        external_id=item.external_id,
        title=item.title or "",
        raw_text=item.content,
        published_at=item.published_at,
        source_id=source_id,
        source_name=source_name,
        source_type=source_type,
        metadata=item.metadata,
    )


@dataclass
class SourceMetadata:
    """Descriptor for a configured source — passed to every adapter at construction time.

    Fields:
        source_id:   Stable UUID string from the source registry.
        source_name: Human-readable name (used in document.source_name).
        source_type: Classification (RSS_FEED, PODCAST_FEED, NEWS_API, …).
        url:         Resolved, validated fetch URL. Must pass SSRF validation.
        status:      ACTIVE (default) — adapters are only instantiated for active sources.
        provider:    Optional free-text provider tag (e.g. "cryptopanic", "coindesk").
        notes:       Optional classification notes from the classifier.
        metadata:    Arbitrary source-specific extras (timeouts, auth hints, etc.).
    """

    source_id: str
    source_name: str
    source_type: SourceType
    url: str
    status: SourceStatus = SourceStatus.ACTIVE
    provider: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    """Output of one adapter fetch cycle.

    Contract (adapters MUST respect this):
    - On success: success=True, documents=[...], error=None
    - On failure: success=False, documents=[], error=<non-empty message>
    - documents is NEVER None — use empty list on failure
    - Adapters MUST NOT raise — all exceptions must be caught and reflected here
    - Every document must have: url, title, source_id, source_name, source_type
    - source_id on each document must equal FetchResult.source_id

    Downstream contract (storage/ingest MUST respect this):
    - FetchResult is read-only after creation — no mutation
    - Only persist_fetch_result() in app/storage/document_ingest.py consumes this
    - Do not mix FetchResult with analysis or scoring logic
    """

    source_id: str
    documents: list[CanonicalDocument]
    fetched_at: datetime
    success: bool
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def document_count(self) -> int:
        return len(self.documents)


class BaseSourceAdapter(ABC):
    """Abstract base for all source adapters.

    Subclasses implement fetch() and validate() for one SourceType.
    The SSRF guard (app/security/ssrf.validate_url) must be called
    before any outbound HTTP request inside fetch() or validate().
    """

    def __init__(self, metadata: SourceMetadata) -> None:
        self.metadata = metadata

    @property
    def source_id(self) -> str:
        return self.metadata.source_id

    @property
    def source_type(self) -> SourceType:
        return self.metadata.source_type

    @abstractmethod
    async def fetch(self) -> FetchResult:
        """Fetch documents from the source. Must not raise — catch all exceptions."""

    @abstractmethod
    async def validate(self) -> bool:
        """Return True if the source is reachable and correctly classified."""
