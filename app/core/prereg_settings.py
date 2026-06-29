"""Pre-registration ledger settings (shadow-only, default-off).

The pre-registration ledger (:mod:`app.research.prereg_ledger`) is record-only and
never gates trading. This flag exists so the discipline can later be ENFORCED
(e.g. a promotion check that an edge claim must reference a prior ``prereg_id``)
without changing today's behaviour. Default off; instantiated directly at the CLI
call site (not via ``get_settings()``), mirroring
:class:`app.core.integrity_settings.IntegritySettings` — so adding it never
touches the ratcheted :mod:`app.core.settings`.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PreRegSettings(BaseSettings):
    """Pre-registration discipline config.

    - ``enabled=False`` (default): the ledger is purely informational; nothing is
      enforced anywhere.
    - ``enabled=True``: reserved for a future promotion/CI check that an edge
      claim references a prior pre-registration (NOT wired today).
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_PREREG_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    ledger_path: str = Field(default="artifacts/research/prereg_ledger.jsonl")
