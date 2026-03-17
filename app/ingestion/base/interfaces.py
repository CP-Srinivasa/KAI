from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from app.core.domain.document import CanonicalDocument
from app.core.enums import SourceStatus, SourceType


@dataclass
class SourceMetadata:
    source_id: str
    source_name: str
    source_type: SourceType
    url: str
    status: SourceStatus = SourceStatus.ACTIVE
    provider: str | None = None
    notes: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class FetchResult:
    source_id: str
    documents: list[CanonicalDocument]
    fetched_at: datetime
    success: bool
    error: str | None = None
    metadata: dict = field(default_factory=dict)


class BaseSourceAdapter(ABC):
    """Base interface for all source adapters."""

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
        """Fetch documents from the source."""

    @abstractmethod
    async def validate(self) -> bool:
        """Validate that the source is reachable and correctly classified."""
