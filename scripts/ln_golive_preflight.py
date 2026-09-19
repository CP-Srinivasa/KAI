#!/usr/bin/env python
"""U5 — G0 go-live preflight CLI. Probes the live node + prints the GO/NO-GO report.

Run on the node host: ``python scripts/ln_golive_preflight.py`` (exit 0 = GO).
It NEVER flips a flag — it only REPORTS readiness. The actual flip stays an operator
action (see docs/runbooks/ln_g0_golive.md).
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path

from app.core.lightning_settings import LightningSettings
from app.core.payment_settings import get_payment_settings
from app.core.settings import get_settings
from app.lightning.adapter import CredentialScope, _build_client
from app.lightning.backup_monitor import read_scb_status
from app.lightning.client import LightningUnavailableError
from app.lightning.golive_preflight import golive_preflight

_BOOKING_UNIT = Path("deploy/systemd/kai-oracle-earnings-booking.timer")
_DEMAND_DIR = Path("artifacts")


async def _spend_probe_denied(cfg: LightningSettings, scope: CredentialScope) -> bool | None:
    """Check the exact SendPaymentV2 permissions without making a payment request.

    True means this credential cannot send, False means it can, None means unknown.
    The payment client is built first so the central disarmed guard remains effective.
    Both LND RPCs below are read-only; malformed or missing facts fail closed.
    """
    try:
        candidate = _build_client(cfg, credential_scope=scope)
        observer = _build_client(cfg, credential_scope="read")
        methods = (await observer._get("/v1/macaroon/permissions")).get("method_permissions")
        if not isinstance(methods, dict):
            return None
        method = methods.get("/routerrpc.Router/SendPaymentV2")
        required = method.get("permissions") if isinstance(method, dict) else None
        if not isinstance(required, list) or not required:
            return None
        if not all(
            isinstance(p, dict)
            and isinstance(p.get("entity"), str)
            and isinstance(p.get("action"), str)
            for p in required
        ):
            return None
        if {"entity": "offchain", "action": "write"} not in required:
            return None
        # Positive control: lnd 0.19 returns HTTP 400/code 3 for a candidate
        # WITHOUT the permission rather than {"valid": false}. Verify that the
        # observer itself can use this RPC before interpreting that denial.
        observer_macaroon = base64.b64encode(bytes.fromhex(observer._macaroon_hex)).decode("ascii")
        self_check = await observer._post(
            "/v1/macaroon/checkpermissions",
            {
                "macaroon": observer_macaroon,
                "permissions": [{"entity": "macaroon", "action": "read"}],
            },
        )
        if self_check.get("valid") is not True:
            return None
        macaroon = base64.b64encode(bytes.fromhex(candidate._macaroon_hex)).decode("ascii")
        try:
            result = await observer._post(
                "/v1/macaroon/checkpermissions",
                {"macaroon": macaroon, "permissions": required},
            )
        except LightningUnavailableError as exc:
            prefix = "lnd returned 400 for /v1/macaroon/checkpermissions: "
            if not str(exc).startswith(prefix):
                return None
            try:
                error = json.loads(str(exc)[len(prefix) :])
            except ValueError:
                return None
            return (
                True
                if isinstance(error, dict)
                and error.get("code") == 3
                and error.get("message") == "permission denied"
                else None
            )
        valid = result.get("valid")
        return not valid if isinstance(valid, bool) else None
    except (LightningUnavailableError, ValueError):
        return None


async def _probe_node(cfg: LightningSettings) -> tuple[bool, bool | None, bool, int]:
    """Return (node_reachable, macaroon_scope_minimal, macaroon_can_mint, inbound_sat).

    Every probe runs with the credential that would carry it in production, so the
    report proves the CAPABILITY SPLIT and not merely "some macaroon works":

    - reachability + inbound liquidity: the READ credential (``APP_LN_MACAROON_*``);
    - ``add_invoice`` MUST succeed on the INVOICE credential → proves it can receive.
      A readonly macaroon passes the no-spend check but cannot mint, which would 503
      the paid path — this catches that trap. The probe invoice is 1 sat, 60s expiry,
      capital-free, and expires unpaid;
    - ``SendPaymentV2`` MUST be permission-denied on BOTH receive-side credentials
      (read + invoice) while the layer is unarmed (satoshi auflage 4) — the read
      credential remains the read-only production credential after PR-C, so dropping
      it from the probe would silently retire the invariant. Once armed, the probe
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


async def _probe_armed_facts(cfg: LightningSettings) -> tuple[str | None, float | None]:
    """D-277: (lnd version, SCB copy age in seconds) -- ``None`` = unproven (NO-GO)."""
    version: str | None = None
    try:
        info = await _build_client(cfg).get_info()
        version = str(getattr(info, "version", "") or "") or None
    except LightningUnavailableError:
        version = None
    age: float | None = None
    if cfg.scb_path:
        status = read_scb_status(cfg.scb_path)
        if status.present and status.mtime_epoch is not None:
            age = max(0.0, time.time() - float(status.mtime_epoch))
    return version, age


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
    node_version: str | None = None
    scb_age: float | None = None
    if cfg.enabled and cfg.pay_enabled:
        node_version, scb_age = await _probe_armed_facts(cfg)
    report = golive_preflight(
        cfg,
        node_reachable=reachable,
        macaroon_scope_minimal=scope_minimal,
        macaroon_can_mint=can_mint,
        inbound_liquidity_sat=inbound_sat,
        booking_unit_present=_BOOKING_UNIT.exists(),
        telemetry_writable=_telemetry_writable(),
        node_version=node_version,
        scb_age_seconds=scb_age,
        payments=get_payment_settings() if cfg.pay_enabled else None,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["go"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
