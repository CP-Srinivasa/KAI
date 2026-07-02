#!/usr/bin/env python
"""Set a TIGHT LN policy envelope for the value-layer cockpit (capital-free config).

Writes ``artifacts/ln_policy.json`` (via :class:`app.lightning.policy.PolicyStore`)
with safe-by-default bounds, so the dashboard channel-open flow ALWAYS needs an HOTP
confirm and can never dip the sovereign reserve. Reuses the existing policy engine —
no new policy logic, config only. Nothing here spends.

Defaults: allow only ``create_invoice`` + ``open_channel``; reserve floor 1.9M sat
(≈ only ~50k spendable of a ~1.95M stack); per-action & daily cap 150k;
``confirm_threshold=1`` ⇒ EVERY spend ≥1 sat requires HOTP (nothing auto-executes).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.lightning.policy import PolicyEnvelope, PolicyStore

# Must mirror app/api/routers/ln_control.py _ACTIONS.
KNOWN_ACTIONS = frozenset(
    {"create_invoice", "pay_invoice", "keysend", "send_coins", "open_channel", "close_channel"}
)

DEFAULT_ACTIONS = ("create_invoice", "open_channel")
DEFAULT_RESERVE_FLOOR = 1_900_000
DEFAULT_PER_ACTION_CAP = 150_000
DEFAULT_DAILY_CAP = 150_000
DEFAULT_CONFIRM_THRESHOLD = 1  # >0 ⇒ every spend ≥1 sat needs an HOTP confirm


def build_recommended_envelope(
    *,
    actions: list[str],
    reserve_floor_sat: int,
    per_action_cap_sat: int,
    daily_cap_sat: int,
    confirm_threshold_sat: int,
    recipient_allowlist: list[str],
) -> PolicyEnvelope:
    """Validate + build the envelope (pure). Rejects unknown actions / negatives."""
    unknown = sorted(set(actions) - KNOWN_ACTIONS)
    if unknown:
        raise ValueError(f"unknown action(s): {unknown}; known: {sorted(KNOWN_ACTIONS)}")
    for name, val in (
        ("reserve_floor_sat", reserve_floor_sat),
        ("per_action_cap_sat", per_action_cap_sat),
        ("daily_cap_sat", daily_cap_sat),
        ("confirm_threshold_sat", confirm_threshold_sat),
    ):
        if val < 0:
            raise ValueError(f"{name} must be >= 0, got {val}")
    return PolicyEnvelope(
        allowed_actions=frozenset(actions),
        per_action_cap_sat=per_action_cap_sat,
        daily_cap_sat=daily_cap_sat,
        confirm_threshold_sat=confirm_threshold_sat,
        recipient_allowlist=frozenset(recipient_allowlist),
        reserve_floor_sat=reserve_floor_sat,
    )


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="Set a tight LN policy envelope (capital-free)")
    ap.add_argument("--actions", default=",".join(DEFAULT_ACTIONS), help="CSV of allowed actions")
    ap.add_argument("--reserve-floor", type=int, default=DEFAULT_RESERVE_FLOOR)
    ap.add_argument("--per-action-cap", type=int, default=DEFAULT_PER_ACTION_CAP)
    ap.add_argument("--daily-cap", type=int, default=DEFAULT_DAILY_CAP)
    ap.add_argument("--confirm-threshold", type=int, default=DEFAULT_CONFIRM_THRESHOLD)
    ap.add_argument("--recipient-allowlist", default="", help="CSV of allowed recipient pubkeys")
    ap.add_argument(
        "--policy-path", default=None, help="ln_policy.json path (default: PolicyStore)"
    )
    ap.add_argument("--dry-run", action="store_true", help="Print the envelope, do not write")
    args = ap.parse_args()

    try:
        envelope = build_recommended_envelope(
            actions=_csv(args.actions),
            reserve_floor_sat=args.reserve_floor,
            per_action_cap_sat=args.per_action_cap,
            daily_cap_sat=args.daily_cap,
            confirm_threshold_sat=args.confirm_threshold,
            recipient_allowlist=_csv(args.recipient_allowlist),
        )
    except ValueError as exc:
        print(f"policy refused: {exc}", file=sys.stderr)
        return 2

    target = args.policy_path or "artifacts/ln_policy.json"
    print("LN policy envelope:")
    print(f"  allowed_actions:       {sorted(envelope.allowed_actions)}")
    print(f"  reserve_floor_sat:     {envelope.reserve_floor_sat}")
    print(f"  per_action_cap_sat:    {envelope.per_action_cap_sat}")
    print(f"  daily_cap_sat:         {envelope.daily_cap_sat}")
    print(f"  confirm_threshold_sat: {envelope.confirm_threshold_sat} (>=1 ⇒ HOTP on every spend)")
    print(f"  recipient_allowlist:   {sorted(envelope.recipient_allowlist) or '(any)'}")
    if args.dry_run:
        print("dry-run: not written")
        return 0
    PolicyStore(Path(target)).save(envelope)
    print(f"written -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
