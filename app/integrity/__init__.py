"""Audit-integrity / on-chain anchoring (KAI L3).

Default-off, dependency-safe. Computes a deterministic digest of KAI's audit SSOT
and (optionally) anchors it on-chain via OpenTimestamps — proof that KAI's records
are unaltered. See KAI-mirror/kai_btc_ln_future_integration_20260616.md (Layer 3).
"""

from app.integrity.anchor import (
    AnchorResult,
    AnchorUnavailableError,
    NullStamper,
    OpenTimestampsStamper,
    anchor_audit_digest,
)
from app.integrity.digest import AuditDigest, compute_audit_digest
from app.integrity.status import IntegrityStatus, get_integrity_status

__all__ = [
    "AnchorResult",
    "AnchorUnavailableError",
    "AuditDigest",
    "IntegrityStatus",
    "NullStamper",
    "OpenTimestampsStamper",
    "anchor_audit_digest",
    "compute_audit_digest",
    "get_integrity_status",
]
