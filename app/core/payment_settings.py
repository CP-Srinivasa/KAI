"""Konfiguration des Payment Control Plane (ADR 0018 §11).

Eigene ``BaseSettings``-Klasse, Muster ``lightning_settings.py`` — und
ausdruecklich **keine Zeile** in ``app/core/settings.py`` (God-File-Ratchet,
ADR §2). Der Geldpfad soll seine Vorbedingungen an einem Ort tragen, an dem
man sie ganz lesen kann.

**Was hier fail-closed heisst.** Der Default ist ``SIMULATION``: ohne bewusste
Env-Aenderung beruehrt dieses Paket keinen Node. Die Allowlist ist leer, und
eine leere Allowlist ist eine geschlossene Tuer, keine offene (die Policy
verweigert, wenn ein Ziel nicht drinsteht). Caps sind klein genug, dass eine
vergessene Anpassung Geld kostet, das man verschmerzt.

**Die Scope-Kollision ist der unauffaelligste Totalschaden.** Zeigen
``APP_LN_MACAROON_PATH`` (read) und ``APP_LN_INVOICE_MACAROON_PATH`` auf
dieselbe Datei, traegt jeder Lesepfad die Rechte des Invoice-Scopes; die
Aufteilung in Capabilities ist dann rueckgaengig gemacht, ohne dass sich eine
Codezeile geaendert haette. :func:`validate_payment_boot` bricht deshalb in
JEDEM Modus ab — die Kollision beschreibt die Rechte auf der Platte, nicht das,
was dieser Prozess gerade vorhat.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ConfigurationError
from app.core.lightning_settings import LightningSettings

PaymentMode = Literal["simulation", "shadow", "live"]

#: Repo-Wurzel — ``app/core/payment_settings.py`` liegt zwei Ebenen darunter.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Der Schluessel, mit dem SIMULATION den Intent-Vault versiegelt. Er ist
#: ABGELEITET und nicht eingetippt: ein Literal in dieser Datei saehe aus wie
#: ein Geheimnis, das versehentlich eingecheckt wurde, und jeder
#: Secret-Scanner haette recht damit.
#:
#: Er schuetzt nichts, und das ist der Punkt. SIMULATION bewegt kein Geld; was
#: er leistet, ist, dass Entwicklungsrechner und Tests DENSELBEN Codepfad
#: nehmen wie der Pi. Ein Vault, den nur die Produktion hat, ist ein Vault, den
#: niemand testet. :func:`validate_payment_boot` verweigert ihn in jedem Modus,
#: der einen Node beruehrt.
SIMULATION_VAULT_KEY = hashlib.sha256(b"kai:payment-intent-vault:simulation-only").digest()


def _csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


class PaymentSettings(BaseSettings):
    """Grenzen und Betriebsmodus jeder Wertbewegung."""

    model_config = SettingsConfigDict(
        env_prefix="APP_PAYMENT_",
        env_file=".env",
        extra="ignore",
    )

    #: ``simulation`` beruehrt keinen Node, ``shadow`` liest nur, ``live`` sendet.
    mode: PaymentMode = "simulation"

    #: Das eine Journal (ADR §5). Nie rotiert, in den Backup-REQUIRED_SOURCES.
    journal_path: str = "artifacts/payments/payment_journal.jsonl"

    #: Obergrenze je Zahlung. Ein Betrag darueber wird abgelehnt, nicht bestaetigt.
    per_payment_max_sat: int = Field(default=10_000, ge=0)
    #: Harter Tagesdeckel (ADR §6): DENY, ausdruecklich kein ``needs_confirm``.
    daily_hard_cap_sat: int = Field(default=25_000, ge=0)
    #: Voreingestellte Routing-Gebuehrgrenze in ppm des Betrags.
    fee_limit_default_ppm: int = Field(default=3_000, ge=0)
    #: Absolute Kappe fuer die daraus errechnete Gebuehr.
    fee_limit_max_sat: int = Field(default=200, ge=0)
    #: CSV der erlaubten Payee-Hashes (SHA-256 des Ziels, nie das Ziel selbst).
    destination_allowlist: str = ""
    #: CSV der erlaubten Verwendungszwecke.
    purposes_allowed: str = "data_subscription,api_credit,self_test"
    #: JSON-Tabelle der Agenten-Limits (ADR §1 Agent-Flow).
    agent_limits_path: str = "config/payment_agent_limits.json"
    #: Ab diesem Betrag verlangt die Policy eine HOTP-Freigabe.
    approval_threshold_sat: int = Field(default=1_000, ge=0)
    #: Der verschluesselte Sidecar (ADR §5): die Rohfelder eines noch nicht
    #: gesendeten Vorgangs. Neben dem Journal, nicht darin — das Journal traegt
    #: nur Hashes, und das bleibt so.
    vault_path: str = "artifacts/payments/intent_vault.jsonl"
    #: Base64 von 32 Byte (AES-256). Leer ist nur in SIMULATION zulaessig.
    #: Der Wert steht bewusst NICHT in einer Datei wie die Macaroons: er wird
    #: bei jedem Start gebraucht, aber nie weitergereicht, und ein Pfad mehr
    #: waere ein Artefakt mehr, das ein Backup vergessen kann.
    vault_key: str = ""
    #: Wie lange ein Intent als "kann noch unterwegs sein" gilt (ADR §5).
    #: 86400s statt der 3600s des Bestands: ein steckengebliebener HTLC haengt
    #: bis zum CLTV-Delta, nicht bis zum Invoice-Ablauf.
    max_inflight_window_s: int = Field(default=86_400, gt=0)

    @property
    def destination_allowlist_hashes(self) -> tuple[str, ...]:
        return _csv(self.destination_allowlist)

    @property
    def purposes_allowed_set(self) -> frozenset[str]:
        return frozenset(_csv(self.purposes_allowed))

    def resolved_journal_path(self) -> Path:
        """Absoluter Journalpfad — relativ IMMER zur Repo-Wurzel.

        Nicht zum Arbeitsverzeichnis: der Server, der Reconcile-Timer und die
        CLI starten aus verschiedenen CWDs. Ein relativer Pfad wuerde je nach
        Aufrufer auf ein ANDERES Journal zeigen, und zwei Journale sind
        schlimmer als keins.
        """
        path = Path(self.journal_path)
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()

    def resolved_vault_path(self) -> Path:
        """Absoluter Vault-Pfad — relativ IMMER zur Repo-Wurzel, wie das Journal."""
        path = Path(self.vault_path)
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()

    def resolved_vault_key(self) -> bytes:
        """Das Schluesselmaterial fuer den Intent-Vault.

        Ohne konfigurierten Wert der abgeleitete SIMULATION-Schluessel. Das ist
        kein stiller Default fuer den Ernstfall: :func:`validate_payment_boot`
        laesst SHADOW und LIVE ohne echten Schluessel gar nicht erst starten.

        Raises:
            ValueError: der konfigurierte Wert ist kein Base64 von 32 Byte.
        """
        raw = self.vault_key.strip()
        if not raw:
            return SIMULATION_VAULT_KEY
        try:
            key = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"not valid base64: {type(exc).__name__}") from exc
        if len(key) != 32:
            raise ValueError(f"decodes to {len(key)} bytes, expected 32 (AES-256)")
        return key

    def resolved_agent_limits_path(self) -> Path:
        path = Path(self.agent_limits_path)
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()

    @model_validator(mode="after")
    def _cap_ordering(self) -> Self:
        if self.daily_hard_cap_sat < self.per_payment_max_sat:
            raise ValueError(
                f"daily_hard_cap_sat ({self.daily_hard_cap_sat}) is below "
                f"per_payment_max_sat ({self.per_payment_max_sat}) — the per-payment "
                "limit would be unreachable, which hides the real cap"
            )
        return self


def _check_vault_key(settings: PaymentSettings) -> None:
    """Der Intent-Vault braucht in jedem Node-Modus einen echten Schluessel.

    Warum SHADOW und nicht erst LIVE: SHADOW legt bereits Intents an, und ein
    Intent, der zwischen Anlage und Freigabe einen Neustart nicht ueberlebt,
    ist genau der Befund aus dem LIVE-Fenster 2026-09-04. Der Schluessel muss
    da sein, BEVOR der erste Vorgang entsteht — nicht erst, wenn Geld fliesst.

    Raises:
        ConfigurationError: Schluessel fehlt, ist unbrauchbar, oder es ist der
            abgeleitete SIMULATION-Schluessel.
    """
    if settings.mode == "simulation":
        return
    raw = settings.vault_key.strip()
    if not raw:
        raise ConfigurationError(
            f"payment mode {settings.mode!r} requires APP_PAYMENT_VAULT_KEY "
            "(base64 of 32 random bytes, e.g. `openssl rand -base64 32`) — without it "
            "an authorized intent does not survive a restart and has to be re-created "
            "by hand in the middle of a live window"
        )
    try:
        key = settings.resolved_vault_key()
    except ValueError as exc:
        raise ConfigurationError(f"APP_PAYMENT_VAULT_KEY is unusable: {exc}") from exc
    if key == SIMULATION_VAULT_KEY:
        raise ConfigurationError(
            f"payment mode {settings.mode!r} refuses the derived simulation vault key — "
            "it is computed from a constant in app/core/payment_settings.py and protects "
            "nothing. Generate a real one: `openssl rand -base64 32`"
        )


def _file_is_present(raw: str) -> bool:
    candidate = raw.strip()
    return bool(candidate) and Path(candidate).is_file()


def validate_payment_boot(
    settings: PaymentSettings,
    *,
    app_env: str,
    lightning: LightningSettings,
) -> None:
    """Fail-closed Startguard. Laeuft NACH ``validate_lightning_boot``.

    Reihenfolge mit Absicht: die Scope-Kollision zuerst, weil sie unabhaengig
    vom Modus gilt und die schwerste ist — eine Rechteausweitung, die man nur
    an zwei Env-Zeilen sieht. Danach die vier LIVE-Vorbedingungen.

    Raises:
        ConfigurationError: eine Vorbedingung fehlt. Der Prozess startet nicht;
            ein armierter, aber halb konfigurierter Geldpfad ist gefaehrlicher
            als ein Dienst, der gar nicht hochkommt.
    """
    read_path = lightning.macaroon_path.strip()
    invoice_path = lightning.invoice_macaroon_path.strip()
    if read_path and invoice_path and Path(read_path) == Path(invoice_path):
        raise ConfigurationError(
            "macaroon scope collision: APP_LN_MACAROON_PATH and "
            "APP_LN_INVOICE_MACAROON_PATH point at the same file "
            f"({read_path}) — every read path would carry invoice-write rights. "
            "Refusing to boot in any payment mode."
        )

    if settings.mode == "live":
        _check_live_preconditions(settings, app_env=app_env, lightning=lightning)
    _check_vault_key(settings)


def _check_live_preconditions(
    settings: PaymentSettings, *, app_env: str, lightning: LightningSettings
) -> None:
    """Die vier Vorbedingungen, ohne die LIVE nicht startet (ADR §11).

    Sie stehen vor der Vault-Pruefung, weil sie die schwereren sind: ein
    scharfer Geldpfad ohne Operator-Umgebung ist ein anderes Problem als ein
    Vorgang, der einen Neustart nicht ueberlebt.
    """

    if app_env != "production":
        raise ConfigurationError(
            f"payment mode 'live' requires APP_ENV=production, got {app_env!r} — "
            "a live money path outside production has no operator watching it"
        )
    if not lightning.pay_enabled:
        raise ConfigurationError(
            "payment mode 'live' requires APP_LN_PAY_ENABLED=true — the wired "
            "kill-switch stays the outermost gate, the payment mode never overrides it"
        )
    if not _file_is_present(lightning.payment_macaroon_path):
        raise ConfigurationError(
            "payment mode 'live' requires a readable payment macaroon FILE at "
            "APP_LN_PAYMENT_MACAROON_PATH. A hex credential in the environment is "
            "not accepted here: it cannot carry 0600 and it survives in every "
            "process listing and crash dump"
        )
    if not _file_is_present(lightning.hotp_seed_path):
        raise ConfigurationError(
            "payment mode 'live' requires the HOTP seed at APP_LN_HOTP_SEED_PATH — "
            "without it no approval can ever be granted and every payment above the "
            "threshold would be stuck, or worse, silently waved through"
        )
    if settings.fee_limit_default_ppm <= 0:
        raise ConfigurationError(
            "payment mode 'live' requires APP_PAYMENT_FEE_LIMIT_DEFAULT_PPM > 0 — "
            "lnd omits the fee limit entirely when it is 0, which means an unbounded "
            "routing fee (app/lightning/client.py: fee_limit is only sent when > 0)"
        )


@lru_cache(maxsize=1)
def get_payment_settings() -> PaymentSettings:
    """Prozessweit gecachte Payment-Konfiguration (Muster ``get_settings``).

    Der Cache ist hier mehr als Performance: er stellt sicher, dass der
    Startguard und der spaeter sendende Pfad dieselbe Konfiguration sehen.
    Eine Env-Aenderung zur Laufzeit darf einen Geldpfad nicht umschalten,
    an dem der Boot-Guard schon vorbei ist.
    """
    return PaymentSettings()


__all__ = [
    "SIMULATION_VAULT_KEY",
    "PaymentMode",
    "PaymentSettings",
    "get_payment_settings",
    "validate_payment_boot",
]
