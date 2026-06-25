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
from app.lightning.adapter import _build_client
from app.lightning.client import LightningUnavailableError
from app.lightning.golive_preflight import golive_preflight

_BOOKING_UNIT = Path("deploy/systemd/kai-oracle-earnings-booking.timer")
_DEMAND_DIR = Path("artifacts")


async def _probe_node(cfg: LightningSettings) -> tuple[bool, bool, bool, int]:
    """Return (node_reachable, macaroon_scope_minimal, macaroon_can_mint).

    Two RAW probes (bypassing the value-layer gate):
    - pay_invoice MUST be permission-denied → proves NO spend scope (satoshi auflage 4);
    - add_invoice MUST succeed → proves the macaroon CAN receive (invoices:write). A
      readonly macaroon passes the no-spend check but cannot mint, which would 503 the
      paid path — this catches that trap. The probe invoice is 1 sat, 60s expiry,
      capital-free, and expires unpaid."""
    client = _build_client(cfg)
    try:
        await client.get_info()
    except LightningUnavailableError:
        return False, False, False, 0
    try:
        await client.pay_invoice(payment_request="probe-not-a-real-invoice", fee_limit_sat=0)
        scope_minimal = False  # node ACCEPTED a spend attempt → macaroon too broad
    except LightningUnavailableError as exc:
        text = str(exc).lower()
        scope_minimal = "permission" in text or "403" in text
    try:
        await client.add_invoice(value_sat=1, memo="kai-preflight-mint-probe", expiry_seconds=60)
        can_mint = True
    except LightningUnavailableError:
        can_mint = False  # no invoices:write (e.g. a readonly macaroon) → cannot receive
    # inbound liquidity (read-only): remote_balance = what others can send us. lnd returns
    # it flat (older) or nested {sat,msat} (newer) — handle both. 0 inbound = nobody can pay.
    try:
        rb = (await client.channel_balance()).get("remote_balance", 0)
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
