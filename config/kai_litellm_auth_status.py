"""LiteLLM-Callback: ein Auth-Fehler bleibt ein Auth-Fehler (Issue #1000).

KAI betreibt beide Proxies (``litellm.yaml``, ``litellm_dev.yaml``) bewusst
OHNE Datenbank: es gibt nur den Master-Key, keine virtuellen Schluessel. In
LiteLLM 1.99.0 laeuft ein Auth-Fehler dann in eine Fehlerklassifikation, die
``prisma`` bedingungslos importiert:

* ohne Key: ``Exception("No api key passed in.")`` ->
  ``is_database_service_unavailable_error`` -> ``import prisma`` ->
  ``ModuleNotFoundError`` -> **HTTP 500** statt 401;
* falscher Key: Suche in der (nicht vorhandenen) Schluesseltabelle ->
  ``ProxyException(type=no_db_connection)`` -> **HTTP 400 "No connected db."**.

Beides laesst einen fehlenden oder falschen Bearer wie einen Serverfehler
aussehen. LiteLLM bietet genau dafuer einen Erweiterungspunkt: Callbacks duerfen
im ``async_post_call_failure_hook`` eine ``HTTPException`` zurueckgeben, die den
Fehler ersetzt, BEVOR die Klassifikation laeuft
(``UserAPIKeyAuthExceptionHandler._handle_authentication_error``).

Die Abbildung ist eng begrenzt:

* nur ohne Datenbank (``proxy_server.prisma_client is None``) -- mit einer
  Datenbank bleiben deren 503-/Fallback-Semantiken unangetastet;
* nur fuer Fehler, die im Auth-Modul entstanden sind (Traceback), nie fuer
  Fehler eines Modellaufrufs, die denselben Hook durchlaufen;
* bereits korrekte HTTP-/Proxy-Fehler (401, 403, 429 ...) bleiben, wie sie sind.

Der Zugriff bleibt in jedem Fall verweigert; geaendert wird nur, WIE die
Verweigerung gemeldet wird. Keine Politik, kein Zustand -- Transport-Detail
unterhalb von app/ai (ADR 0017).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger

#: Modul, in dem LiteLLM die Anfrage-Authentisierung ausfuehrt.
AUTH_MODULES = frozenset(
    {
        "litellm.proxy.auth.user_api_key_auth",
        "litellm.proxy.auth.auth_checks",
    }
)
#: ``ProxyErrorTypes.no_db_connection`` als Wert -- ein falscher Key ohne
#: Datenbank endet so, obwohl keine Datenbank ausgefallen ist.
NO_DB_CONNECTION = "no_db_connection"

MESSAGE = (
    "Authentication Error: API-Key fehlt oder ist ungueltig "
    "(Bearer-Header pruefen; dieser Proxy kennt nur seinen Master-Key)."
)


def _raised_in_auth(exc: BaseException) -> bool:
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_globals.get("__name__") in AUTH_MODULES:
            return True
        tb = tb.tb_next
    return False


def _no_database() -> bool:
    try:
        from litellm.proxy import proxy_server
    except ImportError:
        return False
    return getattr(proxy_server, "prisma_client", None) is None


def auth_error_status(exc: BaseException, *, database_configured: bool) -> HTTPException | None:
    """Die 401-Ersetzung fuer ``exc`` oder ``None`` (Fehler bleibt unveraendert)."""
    if database_configured or isinstance(exc, HTTPException):
        return None
    # ``ProxyErrorTypes`` is a str-Enum; str() of a member is "Cls.member"
    # under Python 3.12, so compare its value.
    raw_type = getattr(exc, "type", None)
    error_type = str(getattr(raw_type, "value", raw_type) or "")
    is_proxy_exception = type(exc).__name__ == "ProxyException"
    if is_proxy_exception and error_type != NO_DB_CONNECTION:
        return None
    if not _raised_in_auth(exc):
        return None
    return HTTPException(status_code=401, detail=MESSAGE)


class AuthStatusHandler(CustomLogger):  # type: ignore[misc]
    async def async_post_call_failure_hook(
        self,
        request_data: dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: Any,
        traceback_str: str | None = None,
    ) -> HTTPException | None:
        return auth_error_status(original_exception, database_configured=not _no_database())


auth_status_handler = AuthStatusHandler()
