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


def _consume_idempotency_key(seen_keys: set[str], key: str) -> ConfirmVerdict:
    """Consume a key atomically when the persistent store supports it."""
    try:
        consume = getattr(seen_keys, "consume", None)
        if callable(consume):
            if not consume(key):
                return ConfirmVerdict(False, "idempotency key replay")
        else:
            if key in seen_keys:
                return ConfirmVerdict(False, "idempotency key replay")
            seen_keys.add(key)
    except Exception as exc:  # noqa: BLE001 - persistence uncertainty denies execution
        return ConfirmVerdict(False, f"idempotency persistence failed: {exc}")
    return ConfirmVerdict(True, "execution intent verified")


def _reject_plan_or_key(
    submitted_plan_hash: str,
    expected_plan_hash: str,
    idempotency_key: str,
    seen_keys: set[str],
) -> ConfirmVerdict | None:
    """Shared cheap checks (no side effects): plan binding + replay guard."""
    if not expected_plan_hash or submitted_plan_hash != expected_plan_hash:
        return ConfirmVerdict(False, "plan hash mismatch (plan changed since preview)")
    if not idempotency_key:
        return ConfirmVerdict(False, "idempotency key required")
    try:
        if idempotency_key in seen_keys:
            return ConfirmVerdict(False, "idempotency key replay")
    except Exception as exc:  # noqa: BLE001 - persistence uncertainty denies execution
        return ConfirmVerdict(False, f"idempotency persistence failed: {exc}")
    return None


def verify_execution_intent(
    *,
    submitted_plan_hash: str,
    expected_plan_hash: str,
    idempotency_key: str,
    seen_keys: set[str],
) -> ConfirmVerdict:
    """Verify plan binding + durable replay protection for every execute path."""
    rejected = _reject_plan_or_key(
        submitted_plan_hash=submitted_plan_hash,
        expected_plan_hash=expected_plan_hash,
        idempotency_key=idempotency_key,
        seen_keys=seen_keys,
    )
    if rejected is not None:
        return rejected
    return _consume_idempotency_key(seen_keys, idempotency_key)


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
    rejected = _reject_plan_or_key(
        submitted_plan_hash, expected_plan_hash, idempotency_key, seen_keys
    )
    if rejected is not None:
        return rejected
    try:
        hotp_verifier.verify(hotp_code)
    except Exception as exc:  # noqa: BLE001 — any HOTP failure → reject (honest reason)
        return ConfirmVerdict(False, f"hotp rejected: {exc}")
    consumed = _consume_idempotency_key(seen_keys, idempotency_key)
    if not consumed.ok:
        return consumed
    return ConfirmVerdict(True, "confirmed")


def verify_auto_execute_confirm(
    *,
    submitted_plan_hash: str,
    expected_plan_hash: str,
    idempotency_key: str,
    seen_keys: set[str],
) -> ConfirmVerdict:
    """Authorise an ``auto_execute`` (W0-P4) — plan binding + replay guard, no HOTP.

    Max automation keeps the 2nd factor out of in-envelope actions, but the auto
    path previously skipped EVERYTHING: a replayed request re-executed (unbounded
    ``create_invoice`` minting) and the executed params were never bound to the
    previewed plan. On success the idempotency key is consumed.
    """
    return verify_execution_intent(
        submitted_plan_hash=submitted_plan_hash,
        expected_plan_hash=expected_plan_hash,
        idempotency_key=idempotency_key,
        seen_keys=seen_keys,
    )


__all__ = [
    "ConfirmVerdict",
    "plan_hash",
    "verify_auto_execute_confirm",
    "verify_capital_confirm",
    "verify_execution_intent",
]
