"""
Facebook Connector
===================
Read-only access to public Facebook Page posts via Graph API.
[REQUIRES: FACEBOOK_PAGE_ACCESS_TOKEN in .env]

Status: PLANNED — Facebook Graph API access is heavily restricted.
  - Requires app review for most permissions
  - Page Access Token only works for pages you admin
  - Public page posts accessible only via extended access
  - Not suitable for general news monitoring without approved app

This connector is a placeholder. DISABLED by default.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.ingestion.social.connectors.base import (
    BaseSocialConnector,
    ConnectorStatus,
    FetchParams,
    SocialPost,
)

logger = get_logger(__name__)


class FacebookConnector(BaseSocialConnector):
    """
    Facebook Graph API connector — PLANNED/DISABLED.
    [REQUIRES: FACEBOOK_PAGE_ACCESS_TOKEN + approved Meta app]

    Not recommended for general monitoring due to API restrictions.
    Prefer RSS feeds of public news outlets instead.
    """

    def __init__(
        self,
        page_access_token: str = "",
        page_id: str = "",
        enabled: bool = False,    # Disabled by default
    ) -> None:
        self._token = page_access_token
        self._page_id = page_id
        self._enabled = enabled

    @property
    def connector_id(self) -> str:
        return "facebook"

    @property
    def status(self) -> ConnectorStatus:
        if not self._enabled:
            return ConnectorStatus.PLANNED
        if not self._token or not self._page_id:
            return ConnectorStatus.REQUIRES_API
        return ConnectorStatus.ACTIVE

    @property
    def requires_action(self) -> str:
        return (
            "Facebook Graph API requires an approved Meta app and Page Access Token. "
            "Set FACEBOOK_PAGE_ACCESS_TOKEN and FACEBOOK_PAGE_ID in .env. "
            "See: https://developers.facebook.com/docs/graph-api/get-started"
        )

    async def fetch(self, params: FetchParams) -> list[SocialPost]:
        if self.status != ConnectorStatus.ACTIVE:
            logger.debug("facebook_connector_not_active", status=self.status.value)
            return []

        # Full implementation requires approved Meta app + Graph API v18+
        logger.warning(
            "facebook_fetch_not_implemented",
            note="Facebook connector is a placeholder. Implement after Meta app approval.",
        )
        return []
