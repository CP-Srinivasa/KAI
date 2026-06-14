"""OKX announcements adapter — implements BaseSourceAdapter.

Fetches OKX's official public announcement feed (v5 `/api/v5/support/announcements`,
no auth) and maps each announcement to a CanonicalDocument. Listings/delistings
are discrete, dated, named-entity events → they clear the eligibility/priority
bar organically (no gate loosening). Non-directional notices (maintenance,
"front-end optimisation") still flow through but are filtered downstream by the
existing eligibility/analysis stages, exactly like generic news items.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.domain.document import CanonicalDocument
from app.core.enums import DocumentType, SourceType
from app.core.logging import get_logger
from app.ingestion.base.interfaces import BaseSourceAdapter, FetchResult, SourceMetadata
from app.security.ssrf import validate_url

_log = get_logger(__name__)

OKX_ANNOUNCEMENTS_URL = "https://www.okx.com/api/v5/support/announcements"
_HEADERS = {"User-Agent": "ai-analyst-bot/0.1 (announcement reader)", "Accept": "application/json"}


def _parse_ptime(ptime: Any) -> datetime | None:
    """OKX pTime is epoch milliseconds as a string. Return tz-aware UTC or None."""
    if ptime in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(ptime) / 1000, tz=UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def announcements_to_documents(
    payload: dict[str, Any],
    *,
    source_id: str,
    source_name: str,
    fetched_at: datetime,
) -> list[CanonicalDocument]:
    """Pure mapper: OKX announcements JSON → CanonicalDocuments.

    Tolerant of OKX's nested shape ``{"data": [{"details": [ {..}, .. ]}]}`` and
    skips entries without a title or url (nothing to analyse / dedupe on).
    """
    docs: list[CanonicalDocument] = []
    for page in payload.get("data") or []:
        for ann in page.get("details") or []:
            title = (ann.get("title") or "").strip()
            url = (ann.get("url") or "").strip()
            if not title or not url:
                continue
            docs.append(
                CanonicalDocument(
                    external_id=url,  # unique per announcement → dedupe anchor
                    source_id=source_id,
                    source_name=source_name,
                    source_type=SourceType.NEWS_API,
                    document_type=DocumentType.ARTICLE,
                    provider="okx_announcements",
                    url=url,
                    title=title,
                    raw_text=title,  # OKX feed carries only the headline
                    published_at=_parse_ptime(ann.get("pTime")),
                    fetched_at=fetched_at,
                    tickers=[],  # asset extraction left to the analysis stage
                    metadata={"ann_type": ann.get("annType")},
                )
            )
    return docs


class OKXAnnouncementsAdapter(BaseSourceAdapter):
    def __init__(self, metadata: SourceMetadata, timeout: int = 15) -> None:
        super().__init__(metadata)
        self._timeout = timeout
        self._url = metadata.url or OKX_ANNOUNCEMENTS_URL

    async def fetch(self) -> FetchResult:
        fetched_at = datetime.now(UTC)
        try:
            validate_url(self._url)  # SSRF guard before any network call
            payload = await self._fetch_raw()
            documents = announcements_to_documents(
                payload,
                source_id=self.source_id,
                source_name=self.metadata.source_name,
                fetched_at=fetched_at,
            )
            return FetchResult(
                source_id=self.source_id,
                documents=documents,
                fetched_at=fetched_at,
                success=True,
                metadata={"announcement_count": len(documents)},
            )
        except Exception as exc:  # noqa: BLE001 — adapter boundary, never raise
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
            return str(payload.get("code")) == "0"
        except Exception:  # noqa: BLE001
            return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _fetch_raw(self) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self._timeout, headers=_HEADERS, follow_redirects=True
        ) as client:
            response = await client.get(self._url)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("OKX announcements payload is not a JSON object")
            return data
