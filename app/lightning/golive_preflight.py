"""U5 — G0 go-live preflight: a hard GO/NO-GO gate before flipping the receive path.

Aggregates the readiness facts for enabling the L402 demand probe:
  * config facts (from ``LightningSettings``): the flags that must/ must-not be set;
  * node-side facts (INJECTED — the CLI probes the real node): reachability + the
    scope-minimal macaroon probe (satoshi auflage 4).

Fail-closed: an un-probed node fact (``None``) counts as NOT ok → NO-GO.

Two regimes (auto-detected from ``cfg.pay_enabled``):
  * **receive-only** (``pay_enabled=false``, the original G0 probe): ``pay_enabled_off``
    is a NEGATIVE invariant — the spend kill-switch must stay off — and the macaroon
    must be scope-minimal (a ``pay_invoice`` probe MUST be permission-denied).
  * **armed** (``pay_enabled=true``, operator has deliberately armed the value layer):
    those two receive-only invariants no longer apply — arming spend and using a
    send-capable cockpit macaroon is the INTENDED state, so the preflight checks the
    armed-appropriate facts instead (value layer armed + macaroon carries send scope)
    rather than emitting misleading blocking failures.

Pure + side-effect-free → fully testable; the CLI supplies the live node facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.lightning_settings import LightningSettings


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    detail: str


def golive_preflight(
    cfg: LightningSettings,
    *,
    node_reachable: bool | None = None,
    macaroon_scope_minimal: bool | None = None,
    macaroon_can_mint: bool | None = None,
    inbound_liquidity_sat: int | None = None,
    booking_unit_present: bool | None = None,
    telemetry_writable: bool | None = None,
) -> dict[str, Any]:
    """Return ``{"verdict": "GO"|"NO-GO", "go": bool, "checks": [...], "blocking": [...]}``.

    The regime is auto-detected from ``cfg.pay_enabled``: when the value layer is armed
    the two receive-only invariants (``pay_enabled_off`` / ``macaroon_scope_minimal``)
    are replaced by armed-appropriate checks, so a deliberately-armed cockpit is not
    reported as a stack of blocking failures.
    """
    armed = cfg.pay_enabled
    spend_scope_checks: list[PreflightCheck]
    if armed:
        spend_scope_checks = [
            PreflightCheck(
                "value_layer_armed",
                True,
                "APP_LN_PAY_ENABLED=true — value layer intentionally armed; the receive-only "
                "spend-off invariant is N/A. Spend safety now rests on the policy envelope "
                "(caps + reserve floor + HOTP), not on the kill-switch.",
            ),
            PreflightCheck(
                # In armed mode the cockpit macaroon SHOULD carry spend scope, so the
                # pay_invoice probe must NOT be permission-denied (scope_minimal=False).
                # A True here would mean the macaroon cannot spend — a broken armed setup.
                "macaroon_send_capable",
                macaroon_scope_minimal is False,
                "armed mode: the cockpit macaroon MUST carry send scope (a pay_invoice probe "
                "must NOT be permission-denied). Permission-denied here = macaroon too narrow "
                "for the armed value layer.",
            ),
        ]
    else:
        spend_scope_checks = [
            PreflightCheck(
                "pay_enabled_off",
                not cfg.pay_enabled,
                "APP_LN_PAY_ENABLED MUST stay false — the probe never enables spend",
            ),
            PreflightCheck(
                "macaroon_scope_minimal",
                macaroon_scope_minimal is True,
                "a pay_invoice probe MUST be permission-denied (macaroon carries NO spend scope)",
            ),
        ]

    checks = [
        PreflightCheck(
            "ln_enabled", cfg.enabled, "APP_LN_ENABLED must be true (lnd client active)"
        ),
        PreflightCheck(
            "l402_enabled", cfg.l402_enabled, "APP_LN_L402_ENABLED must be true (serve 402)"
        ),
        PreflightCheck(
            "receive_enabled",
            cfg.receive_enabled,
            "APP_LN_RECEIVE_ENABLED must be true (mint invoices)",
        ),
        *spend_scope_checks,
        PreflightCheck("l402_secret_set", bool(cfg.l402_secret), "APP_LN_L402_SECRET must be set"),
        PreflightCheck(
            "macaroon_configured",
            bool(cfg.macaroon_hex or cfg.macaroon_path),
            "a scope-minimal invoice macaroon (invoices:write/read only) must be configured",
        ),
        PreflightCheck(
            "node_reachable", node_reachable is True, "lnd getinfo must succeed (node reachable)"
        ),
        PreflightCheck(
            "macaroon_can_mint",
            macaroon_can_mint is True,
            "the macaroon MUST be able to mint invoices (invoices:write) — a readonly "
            "macaroon passes the no-spend check but cannot RECEIVE (paid path would 503)",
        ),
        PreflightCheck(
            "inbound_liquidity",
            inbound_liquidity_sat is not None
            and inbound_liquidity_sat >= cfg.l402_default_price_sat,
            f"the node needs >= {cfg.l402_default_price_sat} sat INBOUND liquidity to receive "
            "a payment (0 inbound = nobody can pay); getinfo-green does NOT prove this",
        ),
        PreflightCheck(
            "booking_unit_present",
            booking_unit_present is True,
            "the earnings-booking systemd timer must be installed",
        ),
        PreflightCheck(
            "telemetry_writable",
            telemetry_writable is True,
            "the demand-ledger directory must be writable",
        ),
    ]
    blocking = [c.name for c in checks if not c.ok]
    go = not blocking
    return {
        "verdict": "GO" if go else "NO-GO",
        "go": go,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
        "blocking": blocking,
    }


__all__ = ["PreflightCheck", "golive_preflight"]
