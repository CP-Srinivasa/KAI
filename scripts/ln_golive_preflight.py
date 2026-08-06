#!/usr/bin/env python
"""U5 — G0 go-live preflight CLI. Probes the live node + prints the GO/NO-GO report.

Run on the node host: ``python scripts/ln_golive_preflight.py`` (exit 0 = GO).
It NEVER flips a flag — it only REPORTS readiness. The actual flip stays an operator
action (see docs/runbooks/ln_g0_golive.md).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.core.lightning_settings import LightningSettings
from app.core.settings import get_settings
from app.lightning.adapter import CredentialScope, _build_client
from app.lightning.client import LightningUnavailableError
from app.lightning.golive_preflight import golive_preflight

_BOOKING_UNIT = Path("deploy/systemd/kai-oracle-earnings-booking.timer")
_DEMAND_DIR = Path("artifacts")


async def _spend_probe_denied(cfg: LightningSettings, scope: CredentialScope) -> bool | None:
    """Raw ``pay_invoice`` probe on one capability credential.

    Returns True when the node PERMISSION-DENIED the attempt (credential carries no
    spend scope), False when it got past the permission layer (credential CAN spend),
    and ``None`` when the credential is not provisioned at all — an un-probed fact,
    never silently folded into a pass. The payment request is deliberately garbage, so
    even a spend-capable macaroon moves no capital.

    This helper deliberately does not call a value-layer operation, but it does not
    bypass the central client choke point: ``scope="payment"`` cannot be built while
    ``APP_LN_PAY_ENABLED=false``.
    """
    try:
        client = _build_client(cfg, credential_scope=scope)
    except LightningUnavailableError:
        return None  # capability not provisioned → nothing probed (fail-closed upstream)
    try:
        await client.pay_invoice(payment_request="probe-not-a-real-invoice", fee_limit_sat=0)
        return False  # node ACCEPTED a spend attempt → macaroon too broad
    except LightningUnavailableError as exc:
        text = str(exc).lower()
        return "permission" in text or "403" in text


async def _probe_node(cfg: LightningSettings) -> tuple[bool, bool | None, bool, int]:
    """Return (node_reachable, macaroon_scope_minimal, macaroon_can_mint, inbound_sat).

    Every probe runs with the credential that would carry it in production, so the
    report proves the CAPABILITY SPLIT and not merely "some macaroon works":

    - reachability + inbound liquidity: the READ credential (``APP_LN_MACAROON_*``);
    - ``add_invoice`` MUST succeed on the INVOICE credential → proves it can receive.
      A readonly macaroon passes the no-spend check but cannot mint, which would 503
      the paid path — this catches that trap. The probe invoice is 1 sat, 60s expiry,
      capital-free, and expires unpaid;
    - ``pay_invoice`` MUST be permission-denied on BOTH receive-side credentials
      (read + invoice) while the layer is unarmed (satoshi auflage 4) — the read
      credential is still the one every live path uses until PR-C, so dropping it
      from the probe would silently retire the invariant. Once armed, the probe
      instead targets the dedicated PAYMENT credential, which MUST be accepted.

    A missing capability credential is reported as an un-probed/failed capability —
    never as "node unreachable", so the operator sees the real cause next to
    ``invoice_macaroon_configured``.
    """
    try:
        read_client = _build_client(cfg)
        await read_client.get_info()
    except LightningUnavailableError:
        return False, None, False, 0

    scope_minimal: bool | None
    if cfg.pay_enabled:
        scope_minimal = await _spend_probe_denied(cfg, "payment")
    else:
        read_denied = await _spend_probe_denied(cfg, "read")
        invoice_denied = await _spend_probe_denied(cfg, "invoice")
        # Fail-closed AND: an un-probed (None) or spend-capable (False) receive-side
        # credential must never be reported as scope-minimal.
        scope_minimal = read_denied is True and invoice_denied is True

    try:
        invoice_client = _build_client(cfg, credential_scope="invoice")
        await invoice_client.add_invoice(
            value_sat=1, memo="kai-preflight-mint-probe", expiry_seconds=60
        )
        can_mint = True
    except LightningUnavailableError:
        can_mint = False  # no invoices:write (e.g. a readonly macaroon) → cannot receive
    # inbound liquidity (read-only): remote_balance = what others can send us. lnd returns
    # it flat (older) or nested {sat,msat} (newer) — handle both. 0 inbound = nobody can pay.
    try:
        rb = (await read_client.channel_balance()).get("remote_balance", 0)
        inbound_sat = int(rb.get("sat", 0) if isinstance(rb, dict) else rb)
    except (LightningUnavailableError, TypeError, ValueError):
        inbound_sat = 0
    return True, scope_minimal, can_mint, inbound_sat


def _telemetry_writable() -> bool:
    try:
        _DEMAND_DIR.mkdir(parents=True, exist_ok=True)
        probe = _DEMAND_DIR / ".preflight_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


async def _main() -> int:
    cfg = get_settings().lightning
    reachable: bool | None
    scope_minimal: bool | None
    can_mint: bool | None
    inbound_sat: int | None
    if cfg.enabled:
        reachable, scope_minimal, can_mint, inbound_sat = await _probe_node(cfg)
    else:
        reachable, scope_minimal, can_mint, inbound_sat = None, None, None, None  # node inert
    report = golive_preflight(
        cfg,
        node_reachable=reachable,
        macaroon_scope_minimal=scope_minimal,
        macaroon_can_mint=can_mint,
        inbound_liquidity_sat=inbound_sat,
        booking_unit_present=_BOOKING_UNIT.exists(),
        telemetry_writable=_telemetry_writable(),
    )
    print(json.dumps(report, indent=2))
    return 0 if report["go"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
