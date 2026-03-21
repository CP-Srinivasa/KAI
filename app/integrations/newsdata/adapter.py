"""Newsdata.io source adapter — implements BaseSourceAdapter."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.domain.document import CanonicalDocument
from app.core.enums import DocumentType, SourceType
from app.ingestion.base.interfaces import BaseSourceAdapter, FetchResult, SourceMetadata
from app.integrations.newsdata.client import NewsdataArticle, NewsdataClient


class NewsdataAdapter(BaseSourceAdapter):
    """Fetches news articles from the Newsdata.io /api/1/latest endpoint.

    SourceMetadata.metadata keys (all optional):
        api_key   (str)  — Newsdata.io API key; falls back to constructor arg.
        q         (str)  — Search query.
        language  (str)  — Comma-separated language codes (default: "en").
        country   (str)  — Comma-separated country codes.
        category  (str)  — Comma-separated category names.
        size      (int)  — Number of results per request (default: 10).
    """

    def __init__(self, metadata: SourceMetadata, timeout: int = 20) -> None:
        super().__init__(metadata)
        api_key: str = metadata.metadata.get("api_key", "")
        self._client = NewsdataClient(api_key=api_key, timeout=timeout)
        self._q: str | None = metadata.metadata.get("q")
        self._language: str | None = metadata.metadata.get("language", "en")
        self._country: str | None = metadata.metadata.get("country")
        self._category: str | None = metadata.metadata.get("category")
        self._size: int = int(metadata.metadata.get("size", 10))

    async def fetch(self) -> FetchResult:
        fetched_at = datetime.now(UTC)
        try:
            articles = await self._client.fetch_latest(
                q=self._q,
                language=self._language,
                country=self._country,
                category=self._category,
                size=self._size,
            )
            documents = [self._article_to_doc(a, fetched_at) for a in articles]
            return FetchResult(
                source_id=self.source_id,
                documents=documents,
                fetched_at=fetched_at,
                success=True,
                metadata={"article_count": len(documents)},
            )
        except Exception as exc:
            return FetchResult(
                source_id=self.source_id,
                documents=[],
                fetched_at=fetched_at,
                success=False,
                error=str(exc),
            )

    async def validate(self) -> bool:
        try:
            articles = await self._client.fetch_latest(size=1)
            return len(articles) > 0
        except Exception:
            return False

    def _article_to_doc(self, article: NewsdataArticle, fetched_at: datetime) -> CanonicalDocument:
        raw_text = article.content or article.description or None
        authors = ", ".join(article.creator) if article.creator else None
        tickers: list[str] = []

        return CanonicalDocument(
            external_id=article.article_id,
            source_id=self.source_id,
            source_name=self.metadata.source_name,
            source_type=SourceType.NEWS_API,
            document_type=DocumentType.ARTICLE,
            provider="newsdata",
            url=article.link,
            title=article.title,
            raw_text=raw_text,
            published_at=article.published_at,
            fetched_at=fetched_at,
            tickers=tickers,
            metadata={
                "source_id": article.source_id,
                "source_url": article.source_url,
                "source_priority": article.source_priority,
                "language": article.language,
                "categories": article.category,
                "countries": article.country,
                "keywords": article.keywords,
                "authors": authors,
            },
        )
