"""U3 — earnings-booking job (read-only treasury source for the G0 probe).

Lists the node's OWN invoices, filters SETTLED ones whose memo carries the oracle
prefix, and books them idempotently into the earnings ledger via
``record_settled_invoices``. Listing one's own invoices is read-only against the
node → capital-free. No-op when Lightning is disabled; fail-soft on node errors (logs
+ returns 0, never crashes the scheduler).

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
    booked. No-op (0) when Lightning is disabled or the node is unavailable."""
    cfg = _ln_settings(cfg)
    if not cfg.enabled:
        return 0
    try:
        invoices = await _build_client(cfg).list_invoices()
    except LightningUnavailableError as exc:
        logger.warning("[ln-earnings-booking] node unavailable: %s", exc)
        return 0
    relevant = [
        inv
        for inv in invoices
        if isinstance(inv, dict) and str(inv.get("memo", "")).startswith(memo_prefix)
    ]
    return record_settled_invoices(relevant, source=source, path=path)


__all__ = ["book_oracle_earnings"]
