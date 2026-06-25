"""U5 — G0 go-live preflight: a hard GO/NO-GO gate before flipping the receive path.

Aggregates the readiness facts for enabling the L402 demand probe:
  * config facts (from ``LightningSettings``): the flags that must/ must-not be set;
  * node-side facts (INJECTED — the CLI probes the real node): reachability + the
    scope-minimal macaroon probe (satoshi auflage 4).

Fail-closed: an un-probed node fact (``None``) counts as NOT ok → NO-GO. The
``pay_enabled_off`` check is a NEGATIVE invariant — the spend kill-switch must stay
off for the (receive-only) probe. Pure + side-effect-free → fully testable; the CLI
supplies the live node facts.
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
    booking_unit_present: bool | None = None,
    telemetry_writable: bool | None = None,
) -> dict[str, Any]:
    """Return ``{"verdict": "GO"|"NO-GO", "go": bool, "checks": [...], "blocking": [...]}``."""
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
        PreflightCheck(
            "pay_enabled_off",
            not cfg.pay_enabled,
            "APP_LN_PAY_ENABLED MUST stay false — the probe never enables spend",
        ),
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
            "macaroon_scope_minimal",
            macaroon_scope_minimal is True,
            "a pay_invoice probe MUST be permission-denied (macaroon carries NO spend scope)",
        ),
        PreflightCheck(
            "macaroon_can_mint",
            macaroon_can_mint is True,
            "the macaroon MUST be able to mint invoices (invoices:write) — a readonly "
            "macaroon passes the no-spend check but cannot RECEIVE (paid path would 503)",
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
