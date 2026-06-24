"""B-005 — capital-confirm gate for irreversible value-layer actions (security core).

The POST layer (Sprint 5) lets the operator preview a value-layer action (dry-run
plan + policy verdict) and then EXECUTE it. For an irreversible execute the plan
mandates a hardened confirm — this module is the pure, testable verifier:

  * **plan-hash match** — the operator confirms the EXACT plan; a hash mismatch
    means the params changed between preview and execute → reject (no substitution);
  * **idempotency key** — fresh per execute → a replayed request cannot double-spend;
  * **fresh HOTP** — out-of-band, replay-safe 2nd factor (``app.security.hotp_auth``).

Order matters: the cheap checks (hash, idempotency) run BEFORE the HOTP so a bad
plan never advances the operator's HOTP counter. No node/capital path here — this
only authorises; execution stays behind the value-layer send-gate (B-002) +
``pay_enabled``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol


def plan_hash(action: str, params: dict[str, Any]) -> str:
    """Canonical SHA-256 over ``(action, params)`` — stable across key order."""
    canonical = json.dumps(
        {"action": action, "params": params}, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ConfirmVerdict:
    ok: bool
    reason: str = ""


class _HotpLike(Protocol):
    def verify(self, code: str) -> Any: ...


def verify_capital_confirm(
    *,
    hotp_verifier: _HotpLike,
    hotp_code: str,
    submitted_plan_hash: str,
    expected_plan_hash: str,
    idempotency_key: str,
    seen_keys: set[str],
) -> ConfirmVerdict:
    """Authorise an irreversible execute (B-005). Returns an honest verdict; on
    success the idempotency key is consumed (added to ``seen_keys``).

    Cheap, side-effect-free checks first; the HOTP (which advances the counter and
    must be treated as a brute-force-sensitive resource) is verified LAST, and only
    a fully valid confirm consumes the idempotency key.
    """
    if not expected_plan_hash or submitted_plan_hash != expected_plan_hash:
        return ConfirmVerdict(False, "plan hash mismatch (plan changed since preview)")
    if not idempotency_key:
        return ConfirmVerdict(False, "idempotency key required")
    if idempotency_key in seen_keys:
        return ConfirmVerdict(False, "idempotency key replay")
    try:
        hotp_verifier.verify(hotp_code)
    except Exception as exc:  # noqa: BLE001 — any HOTP failure → reject (honest reason)
        return ConfirmVerdict(False, f"hotp rejected: {exc}")
    seen_keys.add(idempotency_key)
    return ConfirmVerdict(True, "confirmed")


__all__ = ["ConfirmVerdict", "plan_hash", "verify_capital_confirm"]
