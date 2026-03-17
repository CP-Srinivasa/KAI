"""CryptoPanic source adapter — implements BaseSourceAdapter."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.domain.document import CanonicalDocument
from app.core.enums import DocumentType, SourceType
from app.ingestion.base.interfaces import BaseSourceAdapter, FetchResult, SourceMetadata
from app.integrations.cryptopanic.client import CryptoPanicClient


class CryptoPanicAdapter(BaseSourceAdapter):
    """Fetches news posts from CryptoPanic API.

    SourceMetadata.metadata["auth_token"] must be set.
    SourceMetadata.metadata["filter"] is optional ("rising"|"hot"|"bullish"|...).
    SourceMetadata.metadata["currencies"] is optional list of ticker codes.
    """

    def __init__(self, metadata: SourceMetadata, timeout: int = 20) -> None:
        super().__init__(metadata)
        auth_token = metadata.metadata.get("auth_token", "")
        self._client = CryptoPanicClient(auth_token=auth_token, timeout=timeout)
        self._filter: str | None = metadata.metadata.get("filter")
        self._currencies: list[str] | None = metadata.metadata.get("currencies")

    async def fetch(self) -> FetchResult:
        fetched_at = datetime.now(UTC)
        try:
            posts = await self._client.fetch_posts(
                filter=self._filter,
                currencies=self._currencies,
            )
            documents = [self._post_to_doc(p, fetched_at) for p in posts]
            return FetchResult(
                source_id=self.source_id,
                documents=documents,
                fetched_at=fetched_at,
                success=True,
                metadata={"post_count": len(documents)},
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
            posts = await self._client.fetch_posts()
            return len(posts) > 0
        except Exception:
            return False

    def _post_to_doc(self, post, fetched_at: datetime) -> CanonicalDocument:
        from app.integrations.cryptopanic.client import CryptoPanicPost

        p: CryptoPanicPost = post
        return CanonicalDocument(
            external_id=str(p.id),
            source_id=self.source_id,
            source_name=self.metadata.source_name,
            source_type=SourceType.NEWS_API,
            document_type=DocumentType.ARTICLE,
            provider="cryptopanic",
            url=p.url,
            title=p.title,
            published_at=p.published_at,
            fetched_at=fetched_at,
            tickers=p.currencies,
            metadata={
                "source_domain": p.source_domain,
                "source_title": p.source_title,
                "kind": p.kind,
            },
        )
