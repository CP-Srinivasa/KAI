"""Der Zusammenbau der Produktschicht beim Start (KAI PAY v0.1).

Steht hier und nicht im ``lifespan``, aus demselben Grund wie
``app/payments/wiring.py``: ``app/api/main.py`` ist gross genug, und der
``lifespan`` soll genau die eine Aussage behalten, die er treffen muss —
*dieser Prozess fragt nach*.

**Aus ist ein Zustand, kein Fehler.** Ohne ``APP_PAY_ENABLED=true`` gibt
:func:`build_pay_service` ``None`` zurueck, der Poller startet nie, und der
Router antwortet 404. Kein halb aufgebauter Dienst, der spaeter im ersten
Request auffaellt.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.pay_settings import PaySettings
from app.pay import poller
from app.pay.service import PayService
from app.pay.store import PayStore
from app.payments.service import PaymentService

logger = logging.getLogger(__name__)


def build_pay_service(settings: PaySettings, *, payments: PaymentService) -> PayService | None:
    """Store oeffnen, Index bauen, Dienst bauen — oder ``None``, wenn aus."""
    if not settings.enabled:
        logger.info("[kai-pay] disabled (APP_PAY_ENABLED is not true)")
        return None
    store = PayStore(settings.resolved_store_path())
    events = store.load()
    logger.info(
        "[kai-pay] store ready: %d events, %d open request(s) at %s",
        events,
        len(store.open_requests(settings.max_open_requests)),
        store.path,
    )
    return PayService(payments=payments, store=store, settings=settings)


def start_pay_poller(service: PayService | None) -> asyncio.Task[None] | None:
    """Den Takt starten. ``None`` heisst: die Schicht ist aus."""
    return poller.start(service) if service is not None else None


async def stop_pay_poller(task: asyncio.Task[None] | None) -> None:
    """Den Takt beenden und die Cancellation ABWARTEN (nicht nur ausloesen)."""
    await poller.stop(task)


__all__ = ["build_pay_service", "start_pay_poller", "stop_pay_poller"]
