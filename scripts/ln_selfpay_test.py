#!/usr/bin/env python
"""L402 self-pay learning harness (read-only, receive-only) — plan Stage 5.

Drives the full oracle loop for a minimal-risk 10-sat SELF-PAY test:
  1. GET the oracle endpoint → expect 402 → parse the L402 challenge (token+invoice).
  2. print the BOLT11 for you to pay from your phone; poll lnd until the invoice
     settles and read its preimage.
  3. retry with ``Authorization: L402 <token>:<preimage>`` → expect 200 + the fact.

Capital-free / receive-only: it NEVER spends (no ``pay_invoice``), it only READS the
node's own settled invoice. Run on the Pi with ``.venv/bin/python`` once the receive
path is flipped (see docs/runbooks/ln_g0_golive.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import httpx

from app.core.lightning_settings import LightningSettings
from app.core.settings import get_settings
from app.lightning.adapter import _build_client
from app.lightning.selfpay import (
    build_l402_authorization,
    find_settled_preimage,
    parse_l402_challenge,
)


async def _poll_preimage(
    cfg: LightningSettings, *, payment_request: str, timeout_s: int, interval_s: int
) -> str | None:
    """Poll the node's OWN invoices until ``payment_request`` settles (read-only)."""
    client = _build_client(cfg)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        invoices = await client.list_invoices(num_max_invoices=100)
        preimage = find_settled_preimage(invoices, payment_request=payment_request)
        if preimage:
            return preimage
        await asyncio.sleep(interval_s)
    return None


async def _run(args: argparse.Namespace) -> int:
    url = args.url.rstrip("/") + args.path
    async with httpx.AsyncClient(timeout=15.0) as http:
        first = await http.get(url)
        if first.status_code == 200:
            print("200 without a challenge (already paid / l402 off?):")
            print(json.dumps(first.json(), indent=2)[:800])
            return 0
        if first.status_code != 402:
            print(f"expected 402, got {first.status_code}: {first.text[:300]}")
            return 1

        token, invoice = parse_l402_challenge(first.headers.get("WWW-Authenticate", ""))
        print("== L402 challenge received ==")
        print(f"  scope path: {args.path}")
        print("  PAY this BOLT11 invoice (~10 sat) from your wallet:")
        print(f"  {invoice}")
        print(f"  waiting up to {args.timeout}s for settlement (polling lnd)…")

        preimage = await _poll_preimage(
            get_settings().lightning,
            payment_request=invoice,
            timeout_s=args.timeout,
            interval_s=args.interval,
        )
        if not preimage:
            print(f"not settled within {args.timeout}s — pay it and re-run (or raise --timeout)")
            return 2
        print(f"  settled! preimage={preimage[:16]}…")

        auth = build_l402_authorization(token, preimage)
        paid = await http.get(url, headers={"Authorization": auth})
        if paid.status_code != 200:
            print(f"retry expected 200, got {paid.status_code}: {paid.text[:300]}")
            return 3
        print("== 200 — oracle served the fact AFTER payment ==")
        print(json.dumps(paid.json(), indent=2)[:1200])
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="L402 self-pay learning harness (receive-only)")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--path", default="/oracle/onchain-facts")
    ap.add_argument("--timeout", type=int, default=300, help="max seconds to wait for settlement")
    ap.add_argument("--interval", type=int, default=3, help="poll interval seconds")
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
