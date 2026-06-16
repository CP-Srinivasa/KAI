"""Audit-integrity anchoring settings (KAI L3).

KAI's audit/decision streams (paper_execution_audit = replay SSOT, decisions,
signals) are KAI's truth backbone. L3 anchors a daily SHA256 digest of that state
on-chain via OpenTimestamps so KAI can *cryptographically prove* its records were
not altered after the fact — operationalising the truth/honesty identity.

Default-off. The ``opentimestamps`` library is an OPTIONAL dependency, imported
lazily only when ``stamper="opentimestamps"`` and ``enabled=True`` — so this code
does not force a lockfile change until the operator opts in. With ``stamper="null"``
the digest is computed + recorded but not anchored (dry inventory).

See KAI-mirror/kai_btc_ln_future_integration_20260616.md (Layer 3 / UC-3).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IntegritySettings(BaseSettings):
    """Audit-integrity / on-chain anchoring config.

    - ``enabled=False`` (default): nothing is computed or anchored.
    - ``enabled=True`` + ``stamper="null"``: compute + record the digest only.
    - ``enabled=True`` + ``stamper="opentimestamps"``: also create an OTS proof
      (needs the optional ``opentimestamps`` lib + network to calendar servers).
    """

    model_config = SettingsConfigDict(
        env_prefix="APP_INTEGRITY_",
        env_file=".env",
        extra="ignore",
    )

    enabled: bool = Field(default=False)
    # Files whose combined content forms the audit digest (replay-SSOT first).
    audit_paths: list[str] = Field(default_factory=list)
    # "null" (record only) | "opentimestamps" (anchor on-chain via OTS calendars).
    stamper: str = Field(default="null")
    # Where digest records + .ots proofs are written.
    proofs_dir: str = Field(default="monitor/integrity")
