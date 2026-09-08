"""Der Rueckruf an den Auftraggeber (KAI PAY v0.1).

Wer eine Zahlung anfordert, will nicht pollen. Dieser Callback sagt genau
einmal: *diese Forderung ist beglichen*. Er ist bewusst duenn — er traegt
keine Wahrheit, sondern einen Hinweis, ihn abzuholen; die belegbare Antwort
steht in ``GET /pay/requests/{payment_id}`` und im Geld-Journal.

**Ohne Geheimnis kein Versand.** Ein unsignierter Callback ist eine Nachricht,
die jeder faelschen kann, der die URL kennt — und der Empfaenger wuerde darauf
eine Leistung freischalten. Fehlt ``APP_PAY_WEBHOOK_SECRET``, wird deshalb
nichts gesendet und der Grund festgehalten (``no_secret``), statt "erstmal
ohne" zu liefern.

**Die Signatur deckt den BODY, nicht die Felder.** ``sha256=<hmac(secret,
body)>`` ueber genau die Bytes, die auf der Leitung liegen — der Empfaenger
verifiziert, was er gelesen hat, nicht, was er daraus geparst hat. Der
Zeitstempel steht in einem eigenen Header, damit ein abgefangener Callback
nicht beliebig lange wiederverwendbar bleibt.

**Er blockiert nie einen Zustand.** Drei Versuche, dann Schluss; jedes
Ergebnis landet als Ereignis im Strom. Eine Forderung ist bezahlt, weil das
Journal es sagt — nicht, weil ein HTTP-Aufruf gelungen ist.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-KAI-Pay-Signature"
TIMESTAMP_HEADER = "X-KAI-Pay-Timestamp"

#: Zehn Sekunden je Versuch. Ein Callback, der laenger braucht, ist kein
#: Callback mehr, sondern eine Kopplung an die Verfuegbarkeit eines Fremden.
TIMEOUT_SECONDS = 10.0

#: Drei Versuche mit 1s/2s Pause. Mehr waere ein Wiederholungs-Sturm auf einen
#: Empfaenger, der ohnehin gerade nicht kann.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 2.0)


@dataclass(frozen=True)
class WebhookResult:
    """Ausgang eines Zustellversuchs — nie ein Zustand, immer nur ein Befund."""

    ok: bool
    detail: str


def sign(secret: str, body: bytes) -> str:
    """``sha256=<hex>`` ueber den Body. Der Empfaenger rechnet dasselbe nach."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_body(payload: dict[str, Any]) -> bytes:
    """Die Bytes, die gesendet UND signiert werden — genau einmal erzeugt.

    Zwei Serialisierungen (eine zum Senden, eine zum Signieren) waeren die
    klassische Signaturluecke: sobald sie sich um ein Leerzeichen
    unterscheiden, ist jede Signatur ungueltig oder, schlimmer, unabhaengig vom
    Inhalt gueltig.
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


async def deliver(
    url: str,
    payload: dict[str, Any],
    *,
    secret: str,
    client: httpx.AsyncClient | None = None,
) -> WebhookResult:
    """Stelle den Callback zu. Wirft nie — jedes Ergebnis ist ein Befund.

    Args:
        url: Ziel des Empfaengers. Leer heisst: er will keinen Callback.
        payload: Die Felder der Meldung.
        secret: HMAC-Schluessel. Leer heisst: kein Versand (``no_secret``).
        client: Test-Naht. ``None`` baut einen eigenen Client mit Zeitgrenze.
    """
    if not url.strip():
        return WebhookResult(ok=False, detail="no_url")
    if not secret.strip():
        return WebhookResult(ok=False, detail="no_secret")

    body = build_body(payload)
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: sign(secret, body),
        TIMESTAMP_HEADER: str(payload.get("ts", "")),
    }
    if client is not None:
        return await _attempts(client, url, body, headers)
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as owned:
        return await _attempts(owned, url, body, headers)


async def _attempts(
    client: httpx.AsyncClient, url: str, body: bytes, headers: dict[str, str]
) -> WebhookResult:
    detail = "no attempt"
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = await client.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            detail = f"{type(exc).__name__}: {exc}"
        else:
            if 200 <= response.status_code < 300:
                return WebhookResult(ok=True, detail=f"http {response.status_code}")
            detail = f"http {response.status_code}"
        if attempt < len(BACKOFF_SECONDS):
            await asyncio.sleep(BACKOFF_SECONDS[attempt])
    logger.warning("[kai-pay] webhook delivery failed after %d attempts: %s", MAX_ATTEMPTS, detail)
    return WebhookResult(ok=False, detail=detail)


__all__ = [
    "BACKOFF_SECONDS",
    "MAX_ATTEMPTS",
    "SIGNATURE_HEADER",
    "TIMEOUT_SECONDS",
    "TIMESTAMP_HEADER",
    "WebhookResult",
    "build_body",
    "deliver",
    "sign",
]
