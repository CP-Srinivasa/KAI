#!/usr/bin/env python
"""Periodic earnings-booking job (U3): book settled ``kai-oracle:*`` invoices.

Lists the node's own settled invoices and books the oracle ones into the earnings
ledger (idempotent). Inert until ``APP_LN_ENABLED``.

M-11: a degraded run EXITS NON-ZERO so the systemd unit goes red. A green timer that
booked nothing because the node was unreachable is indistinguishable from a green
timer that booked nothing because nobody paid — and only one of those is true.

Run: ``python scripts/book_oracle_earnings.py``
"""

from __future__ import annotations

import asyncio
import logging

from app.lightning.earnings_booking import EarningsBookingError, book_all_earnings

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("kai.earnings-booking")


async def _main() -> int:
    # Bucht ALLE KAI-Inbound-Präfixe: kai-oracle:* (L402) + kai-pay:* (LNbits
    # Pay-Link/Lightning-Address, G2 ADR 0013) — idempotent über dieselbe Maschinerie.
    try:
        counts = await book_all_earnings()
    except EarningsBookingError as exc:
        _log.error("[ln-earnings-booking] FAILED — treasury not updated: %s", exc)
        return 1
    _log.info("[ln-earnings-booking] booked=%s", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
