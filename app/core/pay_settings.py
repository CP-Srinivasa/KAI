"""Konfiguration der Zahlungs-Produktschicht (KAI PAY v0.1, D-CORE-006).

Eigene ``BaseSettings``-Klasse neben :mod:`app.core.payment_settings`, nicht
darin. Der Unterschied ist nicht Kosmetik: ``PaymentSettings`` beschreibt, was
mit GELD passieren darf (Modus, Caps, Fee-Limits, Vault) — diese Datei
beschreibt, wie eine Zahlung ANGEFORDERT und ihr Eingang gemeldet wird. Zwei
Fragen, zwei Dateien, und der Geldpfad behaelt seine Vorbedingungen an einem
Ort, an dem man sie ganz lesen kann (ADR 0018 §11).

**Fail-closed heisst hier zweierlei.** ``enabled`` ist per Default ``False``:
ohne bewusste Env-Aenderung existiert die Produktschicht als Router, der 404
antwortet — nicht als offener Endpunkt mit leeren Grenzen. Und wenn sie
eingeschaltet ist, muss ihr ``purpose`` in der Allowlist des Kerns stehen:
sonst wuerde jede Forderung erst am Rail scheitern, und zwar nach dem
Journal-Record. Ein Startguard beantwortet diese Frage einmal, statt sie in
jeden Request zu verschieben.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ConfigurationError
from app.core.payment_settings import REPO_ROOT, PaymentSettings


class PaySettings(BaseSettings):
    """Grenzen der Produktschicht ueber der versiegelten Payment Fabric."""

    model_config = SettingsConfigDict(
        env_prefix="APP_PAY_",
        env_file=".env",
        extra="ignore",
    )

    #: Aus heisst: ``/pay/*`` antwortet 404 und kein Poller laeuft.
    enabled: bool = False

    #: 15 Minuten. Ein Mensch oeffnet die Wallet, scannt und bestaetigt; eine
    #: laengere Frist belegt nur eine Zeile am Node, eine kuerzere erzeugt
    #: abgelaufene QR-Codes (Rueckweg-Befund 2026-09-04).
    default_expiry_seconds: int = Field(default=900, ge=60, le=86_400)

    #: Obergrenze je Forderung. Sie ist KEINE Policy ueber Geld — der Kern
    #: entscheidet, was ausgestellt werden darf. Sie ist die Grenze, ab der die
    #: Produktschicht eine Anfrage gar nicht erst weiterreicht.
    max_amount_sat: int = Field(default=1_000_000, gt=0)

    #: Der Verwendungszweck, unter dem jede Forderung dieser Schicht laeuft.
    #: Er muss in ``PaymentSettings.purposes_allowed`` stehen — siehe
    #: :func:`validate_pay_boot`.
    purpose: str = Field(default="kai_pay", min_length=1, max_length=64)

    #: HMAC-Schluessel der Webhook-Signatur. LEER heisst: kein Versand. Ein
    #: unsignierter Callback waere eine Nachricht, die jeder faelschen kann —
    #: und der Empfaenger wuerde darauf eine Leistung freischalten.
    webhook_secret: str = ""

    #: Abstand zwischen zwei Poller-Runden.
    poll_interval_seconds: int = Field(default=20, ge=5, le=3_600)

    #: Wie viele offene Forderungen eine Runde hoechstens prueft. Die Grenze
    #: schuetzt den Rail vor einer Runde, die laenger dauert als ihr Intervall.
    max_open_requests: int = Field(default=200, gt=0, le=10_000)

    #: Der Strom der Produktschicht. Er traegt KEINE Geldwahrheit (die steht im
    #: Journal des Kerns), sondern Praesentations- und Verknuepfungsdaten.
    store_path: str = "artifacts/pay/requests.jsonl"

    def resolved_store_path(self) -> Path:
        """Absoluter Pfad — relativ IMMER zur Repo-Wurzel, wie das Geld-Journal.

        Nicht zum Arbeitsverzeichnis: Server und Werkzeuge starten aus
        verschiedenen CWDs, und zwei Stroeme sind schlimmer als keiner.
        """
        path = Path(self.store_path)
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def validate_pay_boot(settings: PaySettings, *, payments: PaymentSettings) -> None:
    """Fail-closed Startguard. Laeuft NACH ``validate_payment_boot``.

    Die eine Frage, die sich nicht in den Request verschieben laesst: kennt der
    Kern den Verwendungszweck dieser Schicht? Steht er nicht in der Allowlist,
    lehnt die Policy jede Forderung ab — und zwar erst, nachdem der Aufrufer
    eine Zahlung angefordert hat. Ein Dienst, der strukturell nichts leisten
    kann, soll gar nicht erst hochkommen.

    Raises:
        ConfigurationError: ``enabled`` ist gesetzt, aber ``purpose`` fehlt in
            ``APP_PAYMENT_PURPOSES_ALLOWED``.
    """
    if not settings.enabled:
        return
    purpose = settings.purpose.strip().lower()
    if purpose in payments.purposes_allowed_set:
        return
    allowed = ", ".join(sorted(payments.purposes_allowed_set)) or "(leer)"
    raise ConfigurationError(
        f"APP_PAY_ENABLED=true, but the purpose {settings.purpose!r} is not in "
        f"APP_PAYMENT_PURPOSES_ALLOWED ({allowed}) — every payment request would be "
        "denied by the control plane's policy AFTER the journal record was written. "
        f"Add it: APP_PAYMENT_PURPOSES_ALLOWED=...,{purpose}"
    )


def get_pay_settings() -> PaySettings:
    """Die Konfiguration der Produktschicht.

    Bewusst OHNE ``lru_cache``, anders als ``get_payment_settings``: dort
    schuetzt der Cache die Zusage, dass Startguard und Sendepfad dieselbe
    Konfiguration sehen — hier gibt es keinen Sendepfad, dafuer aber Tests, die
    den Strom per ``APP_PAY_STORE_PATH`` umbiegen. Ein prozessweiter Cache
    waere dort eine Falle, die man erst nach dem zweiten Test findet.
    """
    return PaySettings()


__all__ = ["PaySettings", "get_pay_settings", "validate_pay_boot"]
