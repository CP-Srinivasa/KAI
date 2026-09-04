"""Der Zusammenbau des Control Plane beim Start (ADR 0018 §1/§5).

Steht hier und nicht im ``lifespan``, weil zwei Prozesse ihn brauchen — der
Server und (in der Zukunft) jede CLI, die den Geldpfad anfasst — und weil
``app/api/main.py`` gross genug ist. Der ``lifespan`` behaelt genau die
Aussage, die er treffen muss: *dieser Prozess ist der sendende*.

**Der Modus waehlt den Rail, nicht der Aufrufer.** SIMULATION bekommt einen
Rail ohne Node; SHADOW und LIVE bekommen denselben Lightning-Adapter, denn der
Unterschied zwischen ihnen ist nicht der Rail, sondern dass ``execute`` ihn in
SHADOW nie erreicht (``PaymentService.execute`` verweigert, und
``LightningRail.pay`` verweigert ein zweites Mal).

**``recover()`` gehoert an den Start und nirgendwo sonst.** Ein ``submitted``
ohne Antwort im Journal heisst: dieser Prozess ist abgestuerzt, waehrend Geld
unterwegs war. Wer das erst beim naechsten Zugriff klaert, hat einen Intent im
Speicher, der nie wieder angefasst wird.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core.lightning_settings import LightningSettings
from app.core.payment_settings import PaymentSettings
from app.payments.agent_limits import load_actor_limits
from app.payments.intent_vault import IntentVault
from app.payments.journal import PaymentJournal
from app.payments.rail import PaymentRail
from app.payments.service import PaymentService

logger = logging.getLogger(__name__)


def build_rails(settings: PaymentSettings, lightning: LightningSettings) -> dict[str, PaymentRail]:
    """Der Rail zum Modus (ADR §1). Verzoegerte Importe: siehe ``rails/lightning``."""
    if settings.mode == "simulation":
        from app.payments.rails.simulation import SimulationRail

        return {"simulation": SimulationRail()}
    from app.payments.rails.lightning import LightningRail

    return {"lightning": LightningRail(payment_settings=settings, lightning_settings=lightning)}


def build_hotp_verifier(lightning: LightningSettings) -> Any:
    """Der HOTP-Verifier — oder ``None``, wenn kein Seed konfiguriert ist.

    ``None`` ist kein Freifahrtschein: :func:`app.payments.approval.grant`
    verweigert ohne Verifier JEDE Freigabe. Ein fehlendes Geheimnis wird hier
    also zu einem geschlossenen Tor, nicht zu einem offenen.
    """
    seed = lightning.hotp_seed_path.strip()
    if not seed:
        return None
    from app.security.hotp_auth import HotpVerifier

    return HotpVerifier(seed_path=Path(seed), journal_path=Path(lightning.hotp_journal_path))


def build_intent_vault(settings: PaymentSettings) -> IntentVault:
    """Der verschluesselte Sidecar (ADR §5).

    Er wird IMMER gebaut, auch in SIMULATION — dort mit dem abgeleiteten
    Simulationsschluessel. Ein Vault, den nur die Produktion hat, ist ein Vault,
    den niemand testet; und der Startguard sorgt dafuer, dass dieser Schluessel
    keinen Modus erreicht, der einen Node beruehrt.
    """
    return IntentVault(settings.resolved_vault_path(), key=settings.resolved_vault_key())


def build_payment_service(
    settings: PaymentSettings,
    lightning: LightningSettings,
    *,
    app_env: str,
) -> PaymentService:
    """Journal oeffnen (volle Kettenpruefung), Rail waehlen, Dienst bauen."""
    journal = PaymentJournal(settings.resolved_journal_path())
    journal.open()
    return PaymentService(
        journal=journal,
        rails=build_rails(settings, lightning),
        settings=settings,
        app_env=app_env,
        hotp_verifier=build_hotp_verifier(lightning),
        actor_limits=load_actor_limits(settings.resolved_agent_limits_path()),
        vault=build_intent_vault(settings),
    )


def recover_on_start(service: PaymentService) -> list[str]:
    """Offene Sends nach einem Neustart in die Klaerung heben — nie in FAILED."""
    recovered = service.recover()
    if recovered:
        logger.warning(
            "payment_intents_recovered",
            extra={"count": len(recovered), "status": "RECONCILIATION_REQUIRED"},
        )
    return recovered


__all__ = [
    "build_hotp_verifier",
    "build_intent_vault",
    "build_payment_service",
    "build_rails",
    "recover_on_start",
]
