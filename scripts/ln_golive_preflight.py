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


async def _probe_node(cfg: LightningSettings) -> tuple[bool, bool]:
    """Return (node_reachable, macaroon_scope_minimal).

    The macaroon probe attempts a RAW pay_invoice (bypassing the value-layer gate): it
    MUST be permission-denied by the NODE — that proves the configured macaroon carries
    no spend scope (defense-in-depth independent of the app gate, satoshi auflage 4)."""
    client = _build_client(cfg)
    try:
        await client.get_info()
    except LightningUnavailableError:
        return False, False
    try:
        await client.pay_invoice(payment_request="probe-not-a-real-invoice", fee_limit_sat=0)
        scope_minimal = False  # node ACCEPTED a spend attempt → macaroon too broad
    except LightningUnavailableError as exc:
        text = str(exc).lower()
        scope_minimal = "permission" in text or "403" in text
    return True, scope_minimal


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
    if cfg.enabled:
        reachable: bool | None
        scope_minimal: bool | None
        reachable, scope_minimal = await _probe_node(cfg)
    else:
        reachable, scope_minimal = None, None  # node client inert → cannot probe
    report = golive_preflight(
        cfg,
        node_reachable=reachable,
        macaroon_scope_minimal=scope_minimal,
        booking_unit_present=_BOOKING_UNIT.exists(),
        telemetry_writable=_telemetry_writable(),
    )
    print(json.dumps(report, indent=2))
    return 0 if report["go"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
