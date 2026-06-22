"""Lightning (RaspiBlitz/lnd) integration settings.

Extracted from ``app.core.settings`` (god-file ratchet, D-234): the read-only
Lightning client configuration lives here; ``settings.py`` re-exports
``LightningSettings`` so existing imports keep working.

See KAI-mirror/kai_lightning_integration_plan_20260614.md for the full phased
plan, macaroon-permission matrix and threat model.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LightningSettings(BaseSettings):
    """RaspiBlitz/lnd Lightning-node integration (KAI as read-only client first).

    Default-off, shadow-first, fail-closed — the trading loop is never blocked by
    Lightning availability. KAI is a *client* of the RaspiBlitz node; no KAI code
    runs on the node and only scope-minimal macaroons ever leave it (NEVER admin).

      - ``enabled=False`` (default): no Lightning surface is consulted anywhere.
      - ``enabled=True`` (Phase 1): read-only access via ``readonly.macaroon`` over
        the lnd REST API (getinfo/channelbalance/feereport). Pure observation.

    Phases 3+ (invoice/pay) live behind their OWN flags + the capital gate — this
    settings object stays read-only on purpose. ``pay_enabled`` is a placeholder
    kill-switch that defaults False and is NOT wired to any send path yet.
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_LN_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    # lnd REST endpoint on the RaspiBlitz node (LAN, later WireGuard overlay IP).
    host: str = Field(default="192.168.178.51")
    rest_port: int = Field(default=8080, ge=1, le=65535)
    # Hex-encoded macaroon OR a path to the macaroon file. Phase 1 = readonly.
    macaroon_path: str = Field(default="", repr=False)
    macaroon_hex: str = Field(default="", repr=False)
    # Path to lnd tls.cert (used to verify the node's self-signed TLS).
    tls_cert_path: str = Field(default="")
    timeout_seconds: float = Field(default=10.0, gt=0)
    # Placeholder kill-switch for the future send path (Phase 4). Not wired yet.
    pay_enabled: bool = Field(default=False)
    # L402 Truth-API (UC-3/UC-4): pay-per-call paywall over KAI's sovereign truth.
    # Default OFF; ``l402_secret`` signs the access tokens (HMAC) and MUST be set
    # before enabling. Env ``APP_LN_L402_ENABLED`` / ``APP_LN_L402_SECRET``.
    l402_enabled: bool = Field(default=False)
    l402_secret: str = Field(default="", repr=False)
    l402_default_price_sat: int = Field(default=10, ge=1)

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.rest_port}"
