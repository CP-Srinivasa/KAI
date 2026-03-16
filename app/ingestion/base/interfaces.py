"""
Source Adapter Interface
========================
Base contract for all source adapters.
Every ingestion adapter MUST extend BaseSourceAdapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import httpx
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from app.core.domain.document import CanonicalDocument
from app.core.enums import AuthMode, SourceStatus, SourceType
from app.core.errors import FetchError, RateLimitError
from app.core.logging import get_logger

logger = get_logger(__name__)


class SourceMetadata:
    def __init__(
        self,
        source_id: str,
        source_name: str,
        source_type: SourceType,
        provider: str,
        auth_mode: AuthMode = AuthMode.NONE,
        status: SourceStatus = SourceStatus.ACTIVE,
        url: str = "",
        language: str = "en",
        country: str = "",
        categories: list[str] | None = None,
        rate_limit_per_minute: int = 60,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.source_id = source_id
        self.source_name = source_name
        self.source_type = source_type
        self.provider = provider
        self.auth_mode = auth_mode
        self.status = status
        self.url = url
        self.language = language
        self.country = country
        self.categories = categories or []
        self.rate_limit_per_minute = rate_limit_per_minute
        self.metadata = metadata or {}
        self.last_fetched_at: datetime | None = None
        self.last_error: str | None = None
        self.consecutive_errors: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_type": self.source_type.value,
            "provider": self.provider,
            "auth_mode": self.auth_mode.value,
            "status": self.status.value,
            "url": self.url,
            "language": self.language,
            "country": self.country,
            "categories": self.categories,
            "last_fetched_at": self.last_fetched_at.isoformat() if self.last_fetched_at else None,
            "last_error": self.last_error,
            "consecutive_errors": self.consecutive_errors,
        }


class FetchResult:
    def __init__(
        self,
        source_id: str,
        documents: list[CanonicalDocument],
        fetched_at: datetime | None = None,
        error: str | None = None,
        items_fetched: int = 0,
        items_new: int = 0,
    ) -> None:
        self.source_id = source_id
        self.documents = documents
        self.fetched_at = fetched_at or datetime.utcnow()
        self.error = error
        self.items_fetched = items_fetched or len(documents)
        self.items_new = items_new
        self.success = error is None

    def __repr__(self) -> str:
        return (
            f"FetchResult(source={self.source_id}, "
            f"fetched={self.items_fetched}, success={self.success})"
        )


class BaseSourceAdapter(ABC):
    """
    Abstract base for all source adapters.
    Subclasses implement: metadata, _fetch_raw(), _normalize()
    """

    def __init__(
        self,
        max_retries: int = 3,
        timeout_seconds: float = 30.0,
        backoff_min: float = 1.0,
        backoff_max: float = 60.0,
    ) -> None:
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds
        self._backoff_min = backoff_min
        self._backoff_max = backoff_max
        self._http_client: httpx.AsyncClient | None = None

    @property
    @abstractmethod
    def metadata(self) -> SourceMetadata: ...

    @abstractmethod
    async def _fetch_raw(self) -> Any: ...

    @abstractmethod
    def _normalize(self, raw_data: Any) -> list[CanonicalDocument]: ...

    async def fetch(self) -> FetchResult:
        """Public fetch API with retry, rate-limit handling, and observability."""
        meta = self.metadata

        if meta.status not in (SourceStatus.ACTIVE,):
            return FetchResult(
                source_id=meta.source_id,
                documents=[],
                error=f"Source status is {meta.status.value}, skipping",
            )

        logger.info("fetch_start", source_id=meta.source_id, source_type=meta.source_type.value)

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries),
                wait=wait_exponential(min=self._backoff_min, max=self._backoff_max),
                reraise=True,
            ):
                with attempt:
                    raw_data = await self._fetch_raw()

            documents = self._normalize(raw_data)
            meta.last_fetched_at = datetime.utcnow()
            meta.last_error = None
            meta.consecutive_errors = 0

            logger.info("fetch_complete", source_id=meta.source_id, items=len(documents))
            return FetchResult(source_id=meta.source_id, documents=documents)

        except RateLimitError as e:
            meta.last_error = str(e)
            meta.consecutive_errors += 1
            logger.warning("fetch_rate_limited", source_id=meta.source_id)
            return FetchResult(source_id=meta.source_id, documents=[], error=str(e))

        except Exception as e:
            meta.last_error = str(e)
            meta.consecutive_errors += 1
            logger.exception("fetch_error", source_id=meta.source_id, error=str(e))
            return FetchResult(source_id=meta.source_id, documents=[], error=str(e))

    async def healthcheck(self) -> dict[str, Any]:
        meta = self.metadata
        return {
            "healthy": meta.status == SourceStatus.ACTIVE,
            "source_id": meta.source_id,
            "status": meta.status.value,
            "last_fetched_at": meta.last_fetched_at.isoformat() if meta.last_fetched_at else None,
            "consecutive_errors": meta.consecutive_errors,
            "last_error": meta.last_error,
        }

    def _build_http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_seconds),
            follow_redirects=True,
            headers={"User-Agent": "AI-Analyst-Bot/0.1"},
        )

    async def __aenter__(self) -> BaseSourceAdapter:
        self._http_client = self._build_http_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
