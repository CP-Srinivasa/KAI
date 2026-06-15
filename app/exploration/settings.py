"""Standalone settings for the exploration sandbox.

Deliberately NOT wired into ``app.core.settings.AppSettings`` — that would make a
production module import the sandbox and break the isolation guarantee. The
sandbox owns its own config surface, all flags default-off.

Env prefix: ``EXPLORATION_`` (e.g. EXPLORATION_ENABLED=true,
EXPLORATION_COINGLASS_ENABLED=true, EXPLORATION_COINGLASS_API_KEY=...).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BOM = "\ufeff"


def _strip_secret(value: object) -> object:
    """Strip trailing whitespace / BOM from pasted keys (mirrors core settings)."""
    if isinstance(value, str):
        return value.strip().lstrip(_BOM)
    return value


class ExplorationSettings(BaseSettings):
    """Sandbox configuration — default-off, per-source opt-in.

    A probe runs only when BOTH the global ``enabled`` flag and the source's own
    ``<source>_enabled`` flag are true (and, for scrapers, ``<source>_scrape_enabled``).
    Probes that ``requires_key`` are skipped (status ``disabled``) when no key is set.
    """

    model_config = SettingsConfigDict(
        env_prefix="EXPLORATION_",
        env_file=".env",
        extra="ignore",
    )

    # -- global ---------------------------------------------------------------
    enabled: bool = Field(default=False)
    timeout_seconds: int = Field(default=20, ge=1, le=120)
    max_records_per_probe: int = Field(default=50, ge=1, le=2000)
    # Politeness throttle between probe runs in the same runner pass.
    min_request_interval_seconds: float = Field(default=2.0, ge=0.0, le=60.0)
    artifacts_dir: str = Field(default="artifacts/exploration")
    # A descriptive UA so the grey-area scrapers identify themselves honestly.
    user_agent: str = Field(
        default="KAI-Exploration/0.1 (+research; contact: operator)",
    )

    # -- per-source enable flags (all default-off) ----------------------------
    coinglass_enabled: bool = Field(default=False)
    coinglass_scrape_enabled: bool = Field(default=False)
    messari_enabled: bool = Field(default=False)
    messari_scrape_enabled: bool = Field(default=False)
    dune_enabled: bool = Field(default=False)
    coingecko_enabled: bool = Field(default=False)
    coingecko_scrape_enabled: bool = Field(default=False)
    glassnode_enabled: bool = Field(default=False)
    glassnode_scrape_enabled: bool = Field(default=False)
    coinmarketcap_enabled: bool = Field(default=False)
    coinmarketcap_scrape_enabled: bool = Field(default=False)
    nansen_enabled: bool = Field(default=False)

    # Always-available demonstration probe used for the durchstich + tests.
    dummy_enabled: bool = Field(default=True)

    # -- per-source API keys (secret) -----------------------------------------
    coinglass_api_key: str = Field(default="", repr=False)
    messari_api_key: str = Field(default="", repr=False)
    dune_api_key: str = Field(default="", repr=False)
    coingecko_api_key: str = Field(default="", repr=False)
    glassnode_api_key: str = Field(default="", repr=False)
    coinmarketcap_api_key: str = Field(default="", repr=False)
    nansen_api_key: str = Field(default="", repr=False)

    # -- probe-specific knobs --------------------------------------------------
    # Default sample symbol(s) for price/metric probes.
    sample_symbol: str = Field(default="BTC")
    # Dune query id to execute when dune_enabled (operator-curated; no default run).
    dune_query_id: int | None = Field(default=None)

    _strip_secrets = field_validator(
        "coinglass_api_key",
        "messari_api_key",
        "dune_api_key",
        "coingecko_api_key",
        "glassnode_api_key",
        "coinmarketcap_api_key",
        "nansen_api_key",
        mode="before",
    )(_strip_secret)


@lru_cache(maxsize=1)
def get_exploration_settings() -> ExplorationSettings:
    """Cached accessor (mirrors app.core.settings.get_settings)."""
    return ExplorationSettings()
