"""CryptoPanic API v1 client.

API docs: https://cryptopanic.com/developers/api/
Endpoint: GET /api/v1/posts/
Auth:     auth_token query param
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

_BASE_URL = "https://cryptopanic.com/api/v1"
_DEFAULT_TIMEOUT = 20


@dataclass(frozen=True)
class CryptoPanicPost:
    id: int
    kind: str               # "news" | "media"
    title: str
    url: str
    published_at: datetime
    source_domain: str
    source_title: str
    currencies: list[str]   # e.g. ["BTC", "ETH"]
    extra: dict[str, Any] = field(default_factory=dict)


class CryptoPanicClient:
    """Minimal async client for CryptoPanic /api/v1/posts/.

    Args:
        auth_token: CryptoPanic API token (free tier supported).
        timeout:    HTTP timeout in seconds.
    """

    def __init__(self, auth_token: str, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._auth_token = auth_token
        self._timeout = timeout

    async def fetch_posts(
        self,
        *,
        kind: str = "news",
        filter: str | None = None,      # "rising" | "hot" | "bullish" | "bearish" | "important"
        currencies: list[str] | None = None,
        regions: str = "en",
        public: bool = True,
        page: int | None = None,
    ) -> list[CryptoPanicPost]:
        """Fetch posts from CryptoPanic.

        Returns an empty list on HTTP errors (non-2xx) to keep the caller resilient.
        """
        params: dict[str, Any] = {
            "auth_token": self._auth_token,
            "kind": kind,
            "regions": regions,
        }
        if public:
            params["public"] = "true"
        if filter:
            params["filter"] = filter
        if currencies:
            params["currencies"] = ",".join(currencies)
        if page:
            params["page"] = page

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{_BASE_URL}/posts/", params=params)
            response.raise_for_status()
            data = response.json()

        return [self._parse_post(p) for p in data.get("results", [])]

    def _parse_post(self, raw: dict[str, Any]) -> CryptoPanicPost:
        source = raw.get("source") or {}
        currencies = [c["code"] for c in raw.get("currencies") or []]
        published_at = datetime.fromisoformat(
            raw["published_at"].replace("Z", "+00:00")
        )
        return CryptoPanicPost(
            id=raw["id"],
            kind=raw.get("kind", "news"),
            title=raw.get("title", ""),
            url=raw.get("url", ""),
            published_at=published_at,
            source_domain=source.get("domain", ""),
            source_title=source.get("title", ""),
            currencies=currencies,
        )
