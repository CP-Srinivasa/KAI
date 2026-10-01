"""Settings der KAI-Pay-Vorschlaege (D-297).

Eigene Klasse - keine Zeile in ``app/core/settings.py`` (God-File-Ratchet). Ohne Schluesseldatei
ist die Funktion aus: ``/vorschlag`` meldet dann, wie die Quelle angelegt wird. Der Schluessel
liegt als Datei (0600) unter ``~/kai-secrets/kai-pay/`` - nie als Env-Wert (wie
``app/core/payment_settings.py``: eine Datei traegt Rechte, ein Env-Wert landet in
Prozesslisten und Crash-Dumps).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_KEY_PATH = str(Path.home() / "kai-secrets" / "kai-pay" / "proposal-source.pem")


class KaiPayProposalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_KAIPAY_PROPOSAL_", env_file=".env", extra="ignore"
    )

    #: Privater Signierschluessel (PEM, PKCS#8, 0600). Wird nie ausgegeben oder geloggt.
    key_path: str = Field(default=DEFAULT_KEY_PATH, repr=False)
    #: Name der Quelle, wie ihn die Wallet beim Einrichten anzeigt.
    source_name: str = "KAI"
    #: Adresse der Wallet fuer Links (Fragment: #kaiprop= / #kaisrc= geht nie an einen Server).
    app_url: str = "https://app.kai-pay.net"
    #: Laufzeit eines Vorschlags in Stunden (die Wallet akzeptiert hoechstens 7 Tage).
    ttl_hours: int = Field(default=24, ge=1, le=168)

    @field_validator("app_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("APP_KAIPAY_PROPOSAL_APP_URL muss mit https:// beginnen")
        return value
