"""Der Takt, in dem KAI nachfragt (KAI PAY v0.1).

Eine Forderung wird von AUSSEN beglichen, und niemand ruft dabei an. Der
Reconcile-Timer merkt das nach spaetestens 15 Minuten — fuer eine Seite, die
"bezahlt" anzeigen soll, ist das eine Ewigkeit. Diese Schleife fragt haeufiger,
aber sie **entscheidet nichts**: sie ruft ``PayService.refresh``, und der ruft
denselben Kern-Durchgang wie der Timer. Zwei Takte, ein Schreiber.

**Was diese Datei aus zwei teuren Vorfaellen gelernt hat** (2026-05-31, 46 h
stiller Telethon-Stream; 2026-09-04, 65 h stiller Poll-Backstop): jeder externe
``await`` in einer Dauerschleife bekommt eine Zeitgrenze. Ohne sie wartet eine
Bibliothek auf ein Future, das nie kommt, der Prozess lebt weiter, systemd
sieht einen gesunden Dienst — und die eingebaute Selbstheilung greift nie, weil
sie Exceptions zaehlt und keine fliegt.

**Er wirft nie und blockiert den Lifespan nie.** Ein Fehler in einer Runde wird
geloggt und die naechste laeuft trotzdem; ``CancelledError`` wird
durchgereicht, nicht verschluckt, damit ein Shutdown nicht in ein Timeout
laeuft (Lehre: 20-s-Stop-Timeout).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.pay.service import PayService

logger = logging.getLogger(__name__)

#: Zeitgrenze fuer EINE Nachfrage. Der Rail-Aufruf dahinter spricht ueber Netz;
#: ohne Grenze haelt eine haengende Verbindung die ganze Runde an.
REFRESH_TIMEOUT_SECONDS = 30.0

#: Zeitgrenze fuer eine GANZE Runde. Sie ist die zweite Verteidigungslinie und
#: nicht die Summe der ersten: ``max_open_requests`` mal
#: ``REFRESH_TIMEOUT_SECONDS`` waeren im Grenzfall 100 Minuten, in denen der
#: Poller aussieht, als liefe er. Fuenf Minuten sind grosszuegig fuer 200
#: Nachfragen gegen einen gesunden Node und eng genug, dass eine haengende
#: Runde auffaellt statt still zu bleiben. Die uebrigen Forderungen holt die
#: naechste Runde nach — der Zustand liegt im Kern, nicht in dieser Schleife.
ROUND_TIMEOUT_SECONDS = 300.0


async def tick(service: PayService) -> int:
    """Eine Runde ueber die offenen Forderungen. Returns: geprueft.

    Wirft nur ``CancelledError`` weiter. Jeder andere Fehler gehoert einer
    einzelnen Forderung und darf die anderen nicht mitnehmen.
    """
    open_requests = service.store.open_requests(service.settings.max_open_requests)
    checked = 0
    for request in open_requests:
        try:
            await asyncio.wait_for(
                service.refresh(request.payment_id), timeout=REFRESH_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                "[kai-pay] refresh timed out after %.0fs for %s",
                REFRESH_TIMEOUT_SECONDS,
                request.payment_id,
            )
        except Exception:  # noqa: BLE001 - eine Forderung darf die Runde nicht toeten
            logger.warning("[kai-pay] refresh failed for %s", request.payment_id, exc_info=True)
        checked += 1
    return checked


async def run(service: PayService) -> None:
    """Die Dauerschleife. Endet ausschliesslich durch Cancellation."""
    interval = float(service.settings.poll_interval_seconds)
    logger.info("[kai-pay] poller started (interval=%.0fs)", interval)
    try:
        while True:
            try:
                await asyncio.wait_for(tick(service), timeout=ROUND_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                logger.warning(
                    "[kai-pay] poll cycle exceeded %.0fs and was cut short",
                    ROUND_TIMEOUT_SECONDS,
                )
            except Exception:  # noqa: BLE001 - eine tote Runde ist kein toter Poller
                logger.warning("[kai-pay] poll cycle failed", exc_info=True)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[kai-pay] poller stopped")
        raise


def start(service: PayService) -> asyncio.Task[None]:
    """Haenge den Poller in die laufende Event-Loop."""
    return asyncio.create_task(run(service), name="kai-pay-poller")


async def stop(task: asyncio.Task[None] | None) -> None:
    """Beende ihn sauber. ``None`` heisst: er lief nie — auch das ist ein Ende."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


__all__ = [
    "REFRESH_TIMEOUT_SECONDS",
    "ROUND_TIMEOUT_SECONDS",
    "run",
    "start",
    "stop",
    "tick",
]
