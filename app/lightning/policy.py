"""Sprint 5 — Policy-Envelope-Engine (max-automation, manual only on exceptions).

The operator sets capital limits ONCE (the "envelope"); thereafter every
capital-effective action is classified by :func:`evaluate_policy`:

  * ``auto_execute`` — inside the envelope → KAI executes without asking;
  * ``needs_confirm`` — out-of-policy / over a threshold / a new counterparty →
    operator confirm (HOTP + plan-hash, wired in the POST layer, B-005);
  * ``denied`` — a disallowed action type or a reserve-floor breach (hard backstop,
    protects the sovereign reserve — never auto-overridable).

Safe default (``PolicyEnvelope.default``): deny EVERYTHING until the operator
configures policies. Caps are positive-to-allow: a 0/absent cap means "no auto
headroom on that dimension" → ``needs_confirm`` (never silent auto). This engine is
pure + side-effect-free; persistence is the small JSON-backed :class:`PolicyStore`.
No capital path of its own — it only classifies; execution stays behind the
value-layer send-gate (B-002) + ``pay_enabled``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_POLICY_PATH = Path("artifacts/ln_policy.json")
_SCHEMA = 1


@dataclass(frozen=True)
class PolicyEnvelope:
    """Operator-set capital limits. All sat amounts; a 0 cap = no auto headroom."""

    allowed_actions: frozenset[str] = field(default_factory=frozenset)
    per_action_cap_sat: int = 0
    daily_cap_sat: int = 0
    confirm_threshold_sat: int = 0  # >0: at/over this, always confirm (even within caps)
    recipient_allowlist: frozenset[str] = field(default_factory=frozenset)
    reserve_floor_sat: int = 0  # sovereign reserve that a spend may never dip below

    @classmethod
    def default(cls) -> PolicyEnvelope:
        """Deny-everything default — nothing auto-executes until the operator sets it."""
        return cls()


@dataclass(frozen=True)
class PolicyDecision:
    decision: str  # "auto_execute" | "needs_confirm" | "denied"
    reason: str = ""


def evaluate_policy(
    action: str,
    *,
    amount_sat: int,
    recipient: str | None,
    spent_today_sat: int,
    available_balance_sat: int,
    envelope: PolicyEnvelope,
) -> PolicyDecision:
    """Classify a capital-effective action against the operator's envelope (pure).

    ``amount_sat`` is the OUTGOING spend (0 for non-spend). Order: hard denies first
    (disallowed action, reserve-floor breach), then the out-of-policy cases that an
    operator may still confirm.
    """
    if action not in envelope.allowed_actions:
        return PolicyDecision("denied", f"action not allowed: {action}")
    # Hard backstop: a spend may never dip the balance below the sovereign reserve.
    if amount_sat > 0 and available_balance_sat - amount_sat < envelope.reserve_floor_sat:
        return PolicyDecision("denied", "would breach reserve floor")
    # New counterparty (allowlist set + recipient not on it) → confirm.
    if envelope.recipient_allowlist and recipient and recipient not in envelope.recipient_allowlist:
        return PolicyDecision("needs_confirm", "new counterparty (not in allowlist)")
    # Caps are positive-to-allow: amount above the per-action cap (0 → any spend).
    if amount_sat > envelope.per_action_cap_sat:
        return PolicyDecision("needs_confirm", "over per-action cap")
    if spent_today_sat + amount_sat > envelope.daily_cap_sat:
        return PolicyDecision("needs_confirm", "over daily cap")
    if envelope.confirm_threshold_sat > 0 and amount_sat >= envelope.confirm_threshold_sat:
        return PolicyDecision("needs_confirm", "at/over confirm threshold")
    return PolicyDecision("auto_execute", "within envelope")


class PolicyStore:
    """JSON-backed envelope persistence. Missing/corrupt → safe deny-everything."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else _POLICY_PATH

    def load(self) -> PolicyEnvelope:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return PolicyEnvelope.default()  # fail-safe: deny everything
        if not isinstance(data, dict):
            return PolicyEnvelope.default()
        try:
            return PolicyEnvelope(
                allowed_actions=frozenset(data.get("allowed_actions", []) or []),
                per_action_cap_sat=int(data.get("per_action_cap_sat", 0) or 0),
                daily_cap_sat=int(data.get("daily_cap_sat", 0) or 0),
                confirm_threshold_sat=int(data.get("confirm_threshold_sat", 0) or 0),
                recipient_allowlist=frozenset(data.get("recipient_allowlist", []) or []),
                reserve_floor_sat=int(data.get("reserve_floor_sat", 0) or 0),
            )
        except (TypeError, ValueError):
            return PolicyEnvelope.default()

    def save(self, envelope: PolicyEnvelope) -> None:
        payload: dict[str, Any] = {
            "schema": _SCHEMA,
            "allowed_actions": sorted(envelope.allowed_actions),
            "per_action_cap_sat": envelope.per_action_cap_sat,
            "daily_cap_sat": envelope.daily_cap_sat,
            "confirm_threshold_sat": envelope.confirm_threshold_sat,
            "recipient_allowlist": sorted(envelope.recipient_allowlist),
            "reserve_floor_sat": envelope.reserve_floor_sat,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


__all__ = ["PolicyDecision", "PolicyEnvelope", "PolicyStore", "evaluate_policy"]
