#!/usr/bin/env python
"""Run Lightning v2-journal reconciliation (read node, append outcomes only)."""

from __future__ import annotations

import asyncio
import json

from app.core.settings import get_settings
from app.lightning.adapter import _build_client
from app.lightning.client import LightningUnavailableError
from app.lightning.reconciliation import reconcile_ln_ops


async def _main() -> int:
    cfg = get_settings().lightning
    client = None
    client_error = ""
    if not cfg.enabled:
        client_error = "lightning_disabled"
    else:
        try:
            # Reconciliation needs ListPayments only: always the READ credential,
            # never the pay-gated payment credential and never a write endpoint.
            client = _build_client(cfg, credential_scope="read")
        except LightningUnavailableError as exc:
            client_error = f"read_client_unavailable:{type(exc).__name__}"

    report = await reconcile_ln_ops(client=client, client_error=client_error)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
