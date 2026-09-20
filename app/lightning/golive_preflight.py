"""U5 — G0 go-live preflight: a hard GO/NO-GO gate before flipping the receive path.

Aggregates the readiness facts for enabling the L402 demand probe:
  * config facts (from ``LightningSettings``): the flags that must/ must-not be set;
  * node-side facts (INJECTED — the CLI probes the real node): reachability + the
    scope-minimal macaroon probe (satoshi auflage 4).

Fail-closed: an un-probed node fact (``None``) counts as NOT ok → NO-GO.

Two regimes (auto-detected from ``cfg.pay_enabled``):
  * **receive-only** (``pay_enabled=false``, the original G0 probe): ``pay_enabled_off``
    is a NEGATIVE invariant — the spend kill-switch must stay off — and the receive-side
    credentials must be scope-minimal (a read-only permission check MUST deny send).
  * **armed** (``pay_enabled=true``, operator has deliberately armed the value layer):
    those two receive-only invariants no longer apply — arming spend and using a
    send-capable PAYMENT macaroon is the INTENDED state, so the preflight checks the
    armed-appropriate facts instead (value layer armed + a dedicated payment credential
    that carries send scope) rather than emitting misleading blocking failures.

This is also the operator's **bake gate** for the capability split (W0/PR-A): the
read and invoice credentials are checked SEPARATELY, so "one macaroon does
everything" can no longer report GO.

Pure + side-effect-free → fully testable; the CLI supplies the live node facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.lightning_settings import LightningSettings
from app.core.payment_settings import PaymentSettings


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    detail: str


#: routerrpc ``/v2/router/send`` (SendPaymentV2) ist seit lnd 0.11 im REST-Gateway;
#: SendPaymentSync faellt in 0.21 weg. Der Sendepfad spricht seit D-277 nur noch v2.
ROUTER_SEND_MIN_LND_VERSION = (0, 11, 0)
#: Muss mit ``app.messaging.pay_telegram_commands.PAY_PURPOSE`` uebereinstimmen (Test).
PAY_PURPOSE = "operator_pay_invoice"


def parse_lnd_version(raw: str | None) -> tuple[int, int, int] | None:
    """``"0.18.3-beta commit=v0.18.3-beta"`` -> ``(0, 18, 3)``; unparsebar -> ``None``."""
    if not raw:
        return None
    head = raw.strip().split()[0].lstrip("v").split("-")[0]
    parts = head.split(".")
    if len(parts) < 2:
        return None
    try:
        nums = [int(x) for x in parts[:3]]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def _armed_send_checks(
    cfg: LightningSettings,
    *,
    node_version: str | None,
    scb_age_seconds: float | None,
    payments: PaymentSettings | None,
) -> list[PreflightCheck]:
    """Fakten, die nur im armierten Betrieb zaehlen (D-277). Alle fail-closed."""
    version = parse_lnd_version(node_version)
    minimum = ".".join(str(x) for x in ROUTER_SEND_MIN_LND_VERSION)
    checks = [
        PreflightCheck(
            "router_send_supported",
            version is not None and version >= ROUTER_SEND_MIN_LND_VERSION,
            f"lnd getinfo.version must parse and be >= {minimum} (the send path speaks "
            f"/v2/router/send only); observed {node_version!r}",
        ),
        PreflightCheck(
            "scb_backup_fresh",
            scb_age_seconds is not None and scb_age_seconds <= cfg.scb_max_age_seconds,
            f"the Static Channel Backup copy must exist and be <= {cfg.scb_max_age_seconds}s "
            f"old before money moves; observed age {scb_age_seconds!r}",
        ),
    ]
    if payments is None:
        checks.append(
            PreflightCheck(
                "payment_settings_probed", False, "PaymentSettings must be supplied when armed"
            )
        )
        return checks
    checks.extend(
        [
            PreflightCheck(
                "payment_mode_live",
                payments.mode == "live",
                "APP_PAYMENT_MODE must be 'live' for a send to leave the process; "
                f"is {payments.mode!r}",
            ),
            PreflightCheck(
                "fee_cap_configured",
                payments.fee_limit_default_ppm > 0 and payments.fee_limit_max_sat > 0,
                "APP_PAYMENT_FEE_LIMIT_DEFAULT_PPM and _MAX_SAT must both be > 0 (a send "
                "without a fee bound is refused by the client)",
            ),
            PreflightCheck(
                "pay_purpose_allowed",
                PAY_PURPOSE in payments.purposes_allowed_set,
                f"APP_PAYMENT_PURPOSES_ALLOWED must contain {PAY_PURPOSE!r} "
                "or /pay is policy-denied",
            ),
            PreflightCheck(
                "pay_destination_allowlisted",
                bool(payments.destination_allowlist_hashes)
                and all(
                    re.fullmatch(r"[0-9a-f]{64}", payee_hash)
                    for payee_hash in payments.destination_allowlist_hashes
                ),
                "APP_PAYMENT_DESTINATION_ALLOWLIST must contain valid SHA-256 payee "
                "hashes; an empty list makes every /pay invoice policy-denied. "
                "The actual invoice payee must match an entry before sending.",
            ),
        ]
    )
    return checks


def golive_preflight(
    cfg: LightningSettings,
    *,
    node_reachable: bool | None = None,
    macaroon_scope_minimal: bool | None = None,
    macaroon_can_mint: bool | None = None,
    inbound_liquidity_sat: int | None = None,
    booking_unit_present: bool | None = None,
    telemetry_writable: bool | None = None,
    node_version: str | None = None,
    scb_age_seconds: float | None = None,
    payments: PaymentSettings | None = None,
) -> dict[str, Any]:
    """Return ``{"verdict": "GO"|"NO-GO", "go": bool, "checks": [...], "blocking": [...]}``.

    The regime is auto-detected from ``cfg.pay_enabled``: when the value layer is armed
    the two receive-only invariants (``pay_enabled_off`` / ``macaroon_scope_minimal``)
    are replaced by armed-appropriate checks, so a deliberately-armed cockpit is not
    reported as a stack of blocking failures.
    """
    armed = cfg.pay_enabled
    invoice_credential_configured = bool(cfg.invoice_macaroon_hex or cfg.invoice_macaroon_path)
    payment_credential_configured = bool(cfg.payment_macaroon_hex or cfg.payment_macaroon_path)
    spend_scope_checks: list[PreflightCheck]
    if armed:
        spend_scope_checks = [
            PreflightCheck(
                "value_layer_armed",
                True,
                "APP_LN_PAY_ENABLED=true — the send path is intentionally armed; the "
                "receive-only spend-off invariant is N/A. Spend safety now rests on the "
                "payment rule chain (per-payment + daily cap, fee limit, destination "
                "allowlist, reserve_floor, HOTP above the approval threshold), not on the "
                "kill-switch. Since ADR 0018 §12 that chain lives in app/payments/policy.py; "
                "the check name is kept so the preflight report stays comparable.",
            ),
            PreflightCheck(
                # In armed mode the PAYMENT macaroon SHOULD carry spend scope, so the
                # Read-only permission check must show send rights (scope_minimal=False).
                # A True here would mean the macaroon cannot spend — a broken armed setup.
                "macaroon_send_capable",
                payment_credential_configured and macaroon_scope_minimal is False,
                "armed mode: the dedicated APP_LN_PAYMENT_MACAROON_* credential MUST be "
                "configured and carry offchain:write (CheckMacaroonPermissions must "
                "confirm send rights). The read/invoice credential is never promoted to "
                "send scope.",
            ),
            *_armed_send_checks(
                cfg, node_version=node_version, scb_age_seconds=scb_age_seconds, payments=payments
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
                "CheckMacaroonPermissions MUST deny send (macaroon carries NO spend scope)",
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
            "read_macaroon_configured",
            bool(cfg.macaroon_hex or cfg.macaroon_path),
            "APP_LN_MACAROON_* must contain the node read credential",
        ),
        PreflightCheck(
            "invoice_macaroon_configured",
            invoice_credential_configured,
            "APP_LN_INVOICE_MACAROON_* must contain a SEPARATE invoices:read/write "
            "credential — this is the bake gate for the capability split (W0/PR-A)",
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


__all__ = [
    "PAY_PURPOSE",
    "ROUTER_SEND_MIN_LND_VERSION",
    "PreflightCheck",
    "golive_preflight",
    "parse_lnd_version",
]
