"""Capital segmentation / reserve policy settings (ADR 0013, fail-closed, inert).

Mirrors the ``app.core.lightning_settings`` extraction pattern (kept OUT of the
``settings.py`` god-file). Defaults are FULLY inert and this object is deliberately
NOT wired into ``AppSettings`` yet — nothing consumes it, so there is zero behaviour
change. When a consumer is added, APPLY must remain gated at the call site (HOTP +
edge-validation-gate); ``apply_enabled`` alone never authorises a move.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CapitalSettings(BaseSettings):
    """Reserve/profit-split configuration. Default-off, shadow-first, fail-closed."""

    model_config = SettingsConfigDict(
        env_prefix="CAPITAL_",
        env_file=".env",
        extra="ignore",
    )

    # Master switch for the (future) segmentation snapshot surface. Inert by default.
    segmentation_enabled: bool = Field(default=False)
    # Second, stricter gate: even when segmentation is shown, APPLYING a recommended
    # move stays impossible without this AND a fresh HOTP AND the edge-validation-gate
    # green at the call site. Default False → shadow / recommendation only.
    apply_enabled: bool = Field(default=False)
    # Fraction of a realized gain taken out of the risk loop into reserve. 0.0 = inert.
    profit_split_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    reserve_target_usd: float = Field(default=0.0, ge=0.0)
    long_term_target_usd: float = Field(default=0.0, ge=0.0)


__all__ = ["CapitalSettings"]
