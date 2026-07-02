"""Messari metrics-based news ingestion adapter — implements BaseSourceAdapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.domain.document import CanonicalDocument
from app.core.enums import DocumentType, SourceType
from app.ingestion.base.interfaces import BaseSourceAdapter, FetchResult, SourceMetadata
from app.security.ssrf import ssrf_redirect_hook, validate_url

_DEFAULT_HEADERS = {
    "User-Agent": "ai-analyst-bot/0.1 (messari reader)",
    "Accept": "application/json",
}


class MessariAdapter(BaseSourceAdapter):
    """Fetches asset metrics from the Messari API and maps assets with news/research to docs.

    SourceMetadata.metadata keys (all optional):
        api_key   (str)  — Messari API key; falls back to empty.
        limit     (int)  — Number of assets to fetch (default: 100, max: 500).
    """

    def __init__(self, metadata: SourceMetadata, timeout: int = 20) -> None:
        super().__init__(metadata)
        self._timeout = timeout
        self._url = metadata.url or "https://api.messari.io/metrics/v2/assets"
        self._api_key = metadata.metadata.get("api_key") or ""
        self._limit = int(metadata.metadata.get("limit", 100))

    async def fetch(self) -> FetchResult:
        fetched_at = datetime.now(UTC)
        try:
            validate_url(self._url)  # SSRF guard before any network call
            payload = await self._fetch_raw()
            assets = payload.get("data") or []
            documents = []
            for a in assets:
                if not isinstance(a, dict) or not a.get("symbol"):
                    continue
                # Only include if hasNews or hasResearch is true (Proposal 1 constraint)
                if not (a.get("hasNews") or a.get("hasResearch")):
                    continue

                documents.append(self._asset_to_doc(a, fetched_at))

            return FetchResult(
                source_id=self.source_id,
                documents=documents,
                fetched_at=fetched_at,
                success=True,
                metadata={"asset_count": len(documents)},
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
            payload = await self._fetch_raw()
            return isinstance(payload, dict) and "data" in payload
        except Exception:
            return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _fetch_raw(self) -> dict[str, Any]:
        headers = dict(_DEFAULT_HEADERS)
        if self._api_key:
            headers["x-messari-api-key"] = self._api_key

        async with httpx.AsyncClient(
            timeout=self._timeout,
            headers=headers,
            follow_redirects=True,
            event_hooks={"response": [ssrf_redirect_hook]},
        ) as client:
            response = await client.get(self._url, params={"limit": self._limit})
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Messari assets payload is not a JSON object")
            return data

    def _asset_to_doc(self, asset: dict[str, Any], fetched_at: datetime) -> CanonicalDocument:
        symbol = str(asset.get("symbol")).upper()
        name = str(asset.get("name") or symbol)
        slug = str(asset.get("slug") or symbol.lower())
        tags = asset.get("tags") or []
        sector = asset.get("sector") or ""
        category = asset.get("category") or ""

        tags_str = ", ".join(tags) if isinstance(tags, list) else ""
        content = (
            f"Messari Asset Alert: {name} ({symbol}) has active news "
            f"and/or research coverage on Messari.\n"
            f"Asset details:\n"
            f"- Sector: {sector}\n"
            f"- Category: {category}\n"
            f"- Rank: {asset.get('rank')}\n"
            f"- Tags: {tags_str}\n"
            f"- Flags: hasNews={asset.get('hasNews')}, "
            f"hasResearch={asset.get('hasResearch')}, "
            f"hasIntel={asset.get('hasIntel')}\n"
        )

        tickers = [symbol]
        crypto_assets = [symbol]

        return CanonicalDocument(
            external_id=f"messari-{asset.get('id') or slug}",
            source_id=self.source_id,
            source_name=self.metadata.source_name,
            source_type=SourceType.NEWS_API,
            document_type=DocumentType.ARTICLE,
            provider="messari",
            url=f"https://messari.io/asset/{slug}",
            title=f"Messari Asset Coverage Update: {name} ({symbol})",
            raw_text=content,
            published_at=fetched_at,
            fetched_at=fetched_at,
            tickers=tickers,
            crypto_assets=crypto_assets,
            categories=[category] if category else [],
            tags=tags,
            metadata={
                "asset_id": asset.get("id"),
                "slug": slug,
                "sector": sector,
                "category": category,
                "rank": asset.get("rank"),
                "has_news": asset.get("hasNews"),
                "has_research": asset.get("hasResearch"),
            },
        )
