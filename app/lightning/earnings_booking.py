"""U3 — earnings-booking job (read-only treasury source for the G0 probe).

Lists the node's OWN invoices, filters SETTLED ones whose memo carries the oracle
prefix, and books them idempotently into the earnings ledger via
``record_settled_invoices``. Listing one's own invoices is read-only against the
node → capital-free, and it runs on the INVOICE credential (never the read
macaroon, never a spend scope).

**M-11 — degradation is LOUD.** This job used to catch a node error, log a warning
and ``return 0``. On a timer that means: unit green, treasury numbers silently
frozen, and "0 sat booked today" indistinguishable from "0 sat earned today". For a
platform whose product is auditable truth that is the worst available failure mode.
A node/credential failure now raises :class:`EarningsBookingError` — the timer goes
red and the post-deploy smoke sees it. Only a DISABLED Lightning client returns 0,
because that is a deliberate configuration, not a degradation.

Run periodically (systemd timer / scheduler) once ``APP_LN_ENABLED`` is set and the
node is reachable. Until then it is inert.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.lightning_settings import LightningSettings
from app.lightning.adapter import _build_client
from app.lightning.client import LightningUnavailableError
from app.lightning.earnings_ledger import record_settled_invoices

logger = logging.getLogger(__name__)

_MEMO_PREFIX = "kai-oracle:"
_SOURCE = "oracle-l402"


class EarningsBookingError(RuntimeError):
    """The earnings ledger could not be brought up to date — the number is UNKNOWN.

    Deliberately distinct from "nothing was booked": a caller must never be able to
    read a degraded run as a truthful zero.
    """


def _ln_settings(cfg: LightningSettings | None) -> LightningSettings:
    if cfg is not None:
        return cfg
    from app.core.settings import get_settings

    return get_settings().lightning


async def book_oracle_earnings(
    *,
    memo_prefix: str = _MEMO_PREFIX,
    source: str = _SOURCE,
    path: Path | None = None,
    cfg: LightningSettings | None = None,
) -> int:
    """Book settled oracle invoices into the earnings ledger; returns the count newly
    booked.

    Returns 0 ONLY for the two honest zeros: Lightning is disabled (inert by
    configuration), or the node had nothing new to book. Anything else — node
    unreachable, invoice credential missing/unreadable, lnd error — raises
    :class:`EarningsBookingError` (M-11).
    """
    cfg = _ln_settings(cfg)
    if not cfg.enabled:
        return 0
    try:
        invoices = await _build_client(cfg, credential_scope="invoice").list_invoices()
    except LightningUnavailableError as exc:
        logger.error(
            "[ln-earnings-booking] treasury NOT updated for %r — node/credential "
            "unavailable: %s (booking count is UNKNOWN, not zero)",
            source,
            exc,
        )
        raise EarningsBookingError(
            f"earnings booking failed for {source!r}: {exc}; the booked count is "
            "UNKNOWN — do not read this run as 'no earnings'"
        ) from exc
    relevant = [
        inv
        for inv in invoices
        if isinstance(inv, dict) and str(inv.get("memo", "")).startswith(memo_prefix)
    ]
    return record_settled_invoices(relevant, source=source, path=path)


# Alle KAI-Inbound-Memo-Präfixe. "kai-pay:" = LNbits-Pay-Link-/Lightning-Address-
# Zahlungen (G2, ADR 0013): LNbits fundet über UNSER lnd, seine Invoices erscheinen
# in list_invoices — Buchung braucht daher weder LNbits-API noch Webhook, nur den
# Prefix in der Pay-Link-Description.
_BOOKING_SOURCES: tuple[tuple[str, str], ...] = (
    ("kai-oracle:", "oracle-l402"),
    ("kai-pay:", "lnurlp"),
)


async def book_all_earnings(
    *,
    path: Path | None = None,
    cfg: LightningSettings | None = None,
) -> dict[str, int]:
    """Book every known KAI inbound memo-prefix; returns newly-booked count per source.

    Propagates :class:`EarningsBookingError` (M-11): a partially-booked run must not
    be reported as a complete one. The booking itself is idempotent, so the next run
    picks up whatever this one could not.
    """
    counts: dict[str, int] = {}
    for memo_prefix, source in _BOOKING_SOURCES:
        counts[source] = await book_oracle_earnings(
            memo_prefix=memo_prefix, source=source, path=path, cfg=cfg
        )
    return counts


__all__ = ["EarningsBookingError", "book_all_earnings", "book_oracle_earnings"]
