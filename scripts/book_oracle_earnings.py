#!/usr/bin/env python
"""Periodic earnings-booking job (U3): book settled ``kai-oracle:*`` invoices.

Lists the node's own settled invoices and books the oracle ones into the earnings
ledger (idempotent). Inert until ``APP_LN_ENABLED`` + a reachable node; fail-soft and
safe to run on a timer.

Run: ``python scripts/book_oracle_earnings.py``
"""

from __future__ import annotations

import asyncio
import logging

from app.lightning.earnings_booking import book_all_earnings

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("kai.earnings-booking")


async def _main() -> int:
    # Bucht ALLE KAI-Inbound-Präfixe: kai-oracle:* (L402) + kai-pay:* (LNbits
    # Pay-Link/Lightning-Address, G2 ADR 0013) — idempotent über dieselbe Maschinerie.
    counts = await book_all_earnings()
    _log.info("[ln-earnings-booking] booked=%s", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
