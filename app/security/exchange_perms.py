"""Exchange-API-Permission-Verifier für KAI Phase-0 Live-Trading.

Spec: docs/security/kai_light_live_phase0_spec.md §3.

Zweck
-----
Vor jedem Boot des kai-server (lifespan-event) UND alle 30 Min via
Position-Monitor-Hook prüft dieses Modul, dass der API-Key am Exchange
**genau** die Permissions trägt, die Phase 0 verlangt:

- ``Spot-Trade``           = ON
- ``Withdrawals``          = OFF
- ``Margin``               = OFF
- ``Futures / Derivatives``= OFF
- ``IP-Allowlist``         enthält die aktuelle Pi-WAN-IP

Drift-Detection
---------------
Wenn der Operator versehentlich ``enableWithdrawals`` aktiviert (oder ein
Angreifer den API-Key am Exchange manipuliert hat), wird dieser Check rot
und der Live-Mode wird **sofort** gesperrt. Phase 0 ist damit gegen
schleichende Permission-Eskalation auf Operator-Seite robust.

Status 2026-05-10
-----------------
Skeleton + Drift-Logic ist live + getestet. Die echten HTTP-Calls
(``_call_binance_api_restrictions`` / ``_call_bybit_query_api``) sind
als reine Hook-Methoden ausgeführt — Smoke-Tests injizieren ein
``ExchangeApiClient``-Stub. Real-Network-Integration kommt mit Sprint
N+2 (Task 4-7 aus Phase-0-Spec).

Coding-Regeln
-------------
1. Fail-closed: jeder unerwartete Drift / API-Error → ``PermissionDriftError``.
2. Keine Cache-Bypass-Flags. Cache-TTL ist hardcoded (siehe ``CHECK_TTL_SECONDS``).
3. Keine Auto-Heal-Pfade. Drift wird gemeldet, nicht automatisch korrigiert.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

logger = logging.getLogger(__name__)

# Re-Check-Intervall im Position-Monitor-Hook. 30 Min ist eng genug für
# eine reaktive Drift-Erkennung, aber nicht so eng dass Rate-Limits am
# Exchange ein Problem werden.
CHECK_TTL_SECONDS: Final[int] = 30 * 60


# ─── Errors ──────────────────────────────────────────────────────────────────


class ExchangePermsError(Exception):
    """Basis für alle Permission-Verifier-Fehler."""


class PermissionDriftError(ExchangePermsError):
    """Eine geforderte Permission-Bedingung ist verletzt.

    Wird von ``verify_against_phase0_requirements`` geworfen. Die Message
    enthält die einzelnen Drift-Reasons als ``;``-separierte Liste, damit
    sie 1:1 in den Live-Audit-Stream geschrieben werden können.
    """


class ExchangeApiError(ExchangePermsError):
    """HTTP-/API-Error auf Exchange-Seite (timeout, 5xx, json-malformed).

    Im Boot-Pfad führt dieser Error zu ``LIVE_MODE_PERMANENT_DISABLED``
    bis zum nächsten manuellen Operator-Restart — Phase 0 vertraut keinem
    intermittierenden Drift-Signal.
    """


# ─── Datentypen ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PermissionStatus:
    """Snapshot eines API-Key-Permission-Sets nach Live-Verifikation."""

    exchange: str  # "binance" | "bybit"
    api_key_label: str | None  # vom Exchange zurückgegeben (Binance: kein Label, Bybit: "note")
    spot_trading_enabled: bool
    withdrawals_enabled: bool
    margin_enabled: bool
    futures_enabled: bool
    derivatives_enabled: bool  # Bybit-Sammelbegriff (linear/inverse/options)
    ip_allowlist: tuple[str, ...]  # frozenset wäre semantisch sauberer, tuple ist json-serialisierbar
    ip_restrict_enforced: bool  # Binance hat ein separates Flag für "Allowlist aktiv"
    last_check_utc: str
    raw_payload: dict[str, Any] = field(default_factory=dict)  # nur für debugging / audit-trail


@dataclass(frozen=True)
class Phase0Expectations:
    """Erwartete Phase-0-Permissions (alles, was nicht passt = Drift)."""

    expected_pi_wan_ip: str
    require_spot: bool = True
    forbid_withdrawals: bool = True
    forbid_margin: bool = True
    forbid_futures: bool = True
    forbid_derivatives: bool = True
    require_ip_allowlist: bool = True


# ─── Drift-Logic (HTTP-frei, voll testbar) ───────────────────────────────────


def verify_against_phase0_requirements(
    status: PermissionStatus,
    expectations: Phase0Expectations,
) -> None:
    """Prüft einen ``PermissionStatus`` gegen die Phase-0-Anforderungen.

    Args:
        status: aktuelle, verbose-API-bestätigte Permissions.
        expectations: was Phase 0 verlangt (Defaults sind die strikten Werte).

    Raises:
        PermissionDriftError: bei jeder Verletzung. Die Nachricht listet
            alle Drift-Reasons in einer ``;``-separierten Sequenz, damit
            sie 1:1 in den ``live_execution_audit.jsonl`` Stream gelangt.
    """
    drift_reasons: list[str] = []

    if expectations.require_spot and not status.spot_trading_enabled:
        drift_reasons.append("spot_trading_disabled")
    if expectations.forbid_withdrawals and status.withdrawals_enabled:
        drift_reasons.append("withdrawals_enabled")
    if expectations.forbid_margin and status.margin_enabled:
        drift_reasons.append("margin_enabled")
    if expectations.forbid_futures and status.futures_enabled:
        drift_reasons.append("futures_enabled")
    if expectations.forbid_derivatives and status.derivatives_enabled:
        drift_reasons.append("derivatives_enabled")

    if expectations.require_ip_allowlist:
        if not status.ip_restrict_enforced:
            drift_reasons.append("ip_restrict_not_enforced")
        elif expectations.expected_pi_wan_ip not in status.ip_allowlist:
            drift_reasons.append(
                f"pi_wan_ip_missing_from_allowlist:{expectations.expected_pi_wan_ip}"
            )

    if drift_reasons:
        raise PermissionDriftError(
            f"{status.exchange}: " + "; ".join(drift_reasons)
        )


# ─── Verifier-Protocol + HTTP-Implementierungen ──────────────────────────────


class ExchangePermissionVerifier(ABC):
    """Abstract Verifier — eine Implementierung pro Exchange."""

    exchange_name: str = ""

    @abstractmethod
    def fetch_status(self) -> PermissionStatus:
        """Fragt den Exchange ab und mappt das Response auf ``PermissionStatus``.

        Implementierungen müssen ``ExchangeApiError`` werfen, wenn:
          - Network-Timeout / Connection-Reset
          - HTTP non-2xx
          - JSON-Schema-Verletzung
          - Authentication-Fehler (ungültiger Key/Signatur)

        Implementierungen DÜRFEN NICHT ``PermissionDriftError`` werfen —
        Drift-Detection ist Aufgabe von ``verify_against_phase0_requirements``.
        """


class BinancePermissionVerifier(ExchangePermissionVerifier):
    """Binance ``GET /sapi/v1/account/apiRestrictions`` Verifier.

    Doc: https://binance-docs.github.io/apidocs/spot/en/#account-api-trading-status-user_data
    """

    exchange_name = "binance"

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        http_caller: "BinanceHttpCaller | None" = None,
    ) -> None:
        if not api_key or not api_secret:
            raise ExchangePermsError("binance_api_key/secret missing in settings")
        self._api_key = api_key
        self._api_secret = api_secret
        self._http = http_caller  # injection point for tests; production-default in fetch_status

    def fetch_status(self) -> PermissionStatus:
        if self._http is None:
            # Production-default wird in Sprint N+2 mit echtem httpx-Client verdrahtet.
            # Heute: skeleton ohne Network-Call, damit der Smoke-Test ohne API-Keys läuft.
            raise ExchangeApiError(
                "binance: http_caller not injected — wire BinanceHttpCallerLive in Sprint N+2"
            )
        try:
            payload = self._http.call_api_restrictions(
                api_key=self._api_key, api_secret=self._api_secret
            )
        except Exception as exc:  # noqa: BLE001 — domain wraps any transport error
            raise ExchangeApiError(f"binance api_restrictions call failed: {exc}") from exc
        return _binance_payload_to_status(payload, raw=payload)


class BybitPermissionVerifier(ExchangePermissionVerifier):
    """Bybit ``GET /v5/user/query-api`` Verifier.

    Doc: https://bybit-exchange.github.io/docs/v5/user/apikey-info
    """

    exchange_name = "bybit"

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        http_caller: "BybitHttpCaller | None" = None,
    ) -> None:
        if not api_key or not api_secret:
            raise ExchangePermsError("bybit_api_key/secret missing in settings")
        self._api_key = api_key
        self._api_secret = api_secret
        self._http = http_caller

    def fetch_status(self) -> PermissionStatus:
        if self._http is None:
            raise ExchangeApiError(
                "bybit: http_caller not injected — wire BybitHttpCallerLive in Sprint N+2"
            )
        try:
            payload = self._http.call_query_api(
                api_key=self._api_key, api_secret=self._api_secret
            )
        except Exception as exc:  # noqa: BLE001
            raise ExchangeApiError(f"bybit query_api call failed: {exc}") from exc
        return _bybit_payload_to_status(payload, raw=payload)


# ─── HTTP-Caller-Protocols (von Tests gemockt, später real implementiert) ────


class BinanceHttpCaller(ABC):
    @abstractmethod
    def call_api_restrictions(self, *, api_key: str, api_secret: str) -> dict[str, Any]:
        """``GET /sapi/v1/account/apiRestrictions`` — signed, timestamp-windowed."""


class BybitHttpCaller(ABC):
    @abstractmethod
    def call_query_api(self, *, api_key: str, api_secret: str) -> dict[str, Any]:
        """``GET /v5/user/query-api`` — auth-headers, no body-signature."""


# ─── Payload-Mapper (HTTP-frei, testbar mit Mock-Dicts) ──────────────────────


def _binance_payload_to_status(
    payload: dict[str, Any],
    *,
    raw: dict[str, Any] | None = None,
) -> PermissionStatus:
    """Mappt Binance ``apiRestrictions`` response auf ``PermissionStatus``."""
    return PermissionStatus(
        exchange="binance",
        api_key_label=None,  # Binance gibt kein Label im apiRestrictions-Endpoint zurück
        spot_trading_enabled=bool(payload.get("enableSpotAndMarginTrading", False)),
        withdrawals_enabled=bool(payload.get("enableWithdrawals", False)),
        margin_enabled=bool(payload.get("enableMargin", False)),
        futures_enabled=bool(payload.get("enableFutures", False)),
        derivatives_enabled=bool(payload.get("enableVanillaOptions", False))
        or bool(payload.get("enableFutures", False)),
        ip_allowlist=tuple(payload.get("ipList", ()) or ()),
        ip_restrict_enforced=bool(payload.get("ipRestrict", False)),
        last_check_utc=_now_utc(),
        raw_payload=dict(raw or {}),
    )


def _bybit_payload_to_status(
    payload: dict[str, Any],
    *,
    raw: dict[str, Any] | None = None,
) -> PermissionStatus:
    """Mappt Bybit ``query-api`` response auf ``PermissionStatus``."""
    if int(payload.get("retCode", -1)) != 0:
        raise ExchangeApiError(
            f"bybit query_api retCode != 0: "
            f"retCode={payload.get('retCode')} retMsg={payload.get('retMsg')}"
        )
    result = payload.get("result") or {}
    permissions = result.get("permissions") or {}

    spot_perms = permissions.get("Spot") or []
    derivative_perms = permissions.get("Derivatives") or permissions.get("ContractTrade") or []
    options_perms = permissions.get("Options") or []
    wallet_perms = permissions.get("Wallet") or []
    margin_perms = permissions.get("Margin") or []

    spot_enabled = any("trade" in p.lower() for p in spot_perms)
    derivatives_enabled = bool(derivative_perms) or bool(options_perms)
    margin_enabled = bool(margin_perms)
    # Bybit unterscheidet Wallet:AccountTransfer (intern) vs Wallet:SubMemberTransfer
    # vs explizites Withdrawal-Permission. ``WithdrawWithinWhiteList`` ist die
    # Withdraw-Berechtigung, die in Phase 0 zwingend OFF sein muss.
    withdrawals_enabled = any(
        "withdraw" in p.lower() for p in wallet_perms
    )

    ips_raw = result.get("ips") or []
    ip_allowlist: tuple[str, ...] = tuple(ips_raw)
    # Bybit liefert "*" wenn kein IP-Restrict aktiv ist
    ip_restrict_enforced = bool(ip_allowlist) and ip_allowlist != ("*",)

    return PermissionStatus(
        exchange="bybit",
        api_key_label=result.get("note"),
        spot_trading_enabled=spot_enabled,
        withdrawals_enabled=withdrawals_enabled,
        margin_enabled=margin_enabled,
        futures_enabled=derivatives_enabled,
        derivatives_enabled=derivatives_enabled,
        ip_allowlist=ip_allowlist,
        ip_restrict_enforced=ip_restrict_enforced,
        last_check_utc=_now_utc(),
        raw_payload=dict(raw or {}),
    )


# ─── Pi-WAN-IP-Detection ─────────────────────────────────────────────────────


def get_pi_wan_ip(*, ip_resolver: "IpResolver | None" = None) -> str:
    """Liefert die aktuelle Pi-WAN-IP für die Allowlist-Verifikation.

    Production-Default: api.ipify.org. Für Tests: ``ip_resolver`` Stub.

    Wirft ``ExchangeApiError`` wenn der Resolver nicht antwortet — Phase 0
    verlangt eine bekannte Pi-WAN-IP, sonst ist der ganze Verifier-Pfad
    sinnlos.
    """
    if ip_resolver is None:
        raise ExchangeApiError(
            "ip_resolver not injected — wire IpifyResolver in Sprint N+2"
        )
    try:
        return ip_resolver.resolve()
    except Exception as exc:  # noqa: BLE001
        raise ExchangeApiError(f"pi_wan_ip resolve failed: {exc}") from exc


class IpResolver(ABC):
    @abstractmethod
    def resolve(self) -> str:
        """Returns the current public WAN-IP. May call api.ipify.org or read
        from a cached file. Implementations decide; the protocol just
        promises a sync string return."""


# ─── Verify-All Top-Level (CLI + Boot-Hook + Periodic-Hook) ──────────────────


def verify_all(
    *,
    binance_verifier: ExchangePermissionVerifier | None = None,
    bybit_verifier: ExchangePermissionVerifier | None = None,
    expectations: Phase0Expectations,
) -> dict[str, PermissionStatus]:
    """Top-Level Verify — wirft ``PermissionDriftError`` bei erstem Drift.

    Phase 0 verifiziert beide Exchanges parallel ehe Live-Mode überhaupt
    unlock-fähig ist. Gibt einen Map ``exchange_name → status`` zurück
    wenn beide grün sind.

    Args:
        binance_verifier: optional. Wenn ``None`` wird Binance übersprungen.
            (Sinnvoll für Operatoren die Phase 0 nur auf Bybit fahren.)
        bybit_verifier: optional. Analog.
        expectations: Phase-0-Anforderungen. ``expected_pi_wan_ip`` ist
            Pflicht.

    Raises:
        PermissionDriftError: bei erster Drift-Detection. Die Message
            nennt den Exchange + die Drift-Reasons.
        ExchangeApiError: bei Network-/API-Layer-Fehler. Im Boot-Pfad
            ist das ein ``LIVE_MODE_PERMANENT_DISABLED``-Trigger.
    """
    if binance_verifier is None and bybit_verifier is None:
        raise ExchangePermsError("verify_all: at least one verifier must be configured")

    statuses: dict[str, PermissionStatus] = {}

    if binance_verifier is not None:
        b_status = binance_verifier.fetch_status()
        verify_against_phase0_requirements(b_status, expectations)
        statuses["binance"] = b_status
        logger.info("[exchange-perms] binance verified ✓ (last_check=%s)", b_status.last_check_utc)

    if bybit_verifier is not None:
        y_status = bybit_verifier.fetch_status()
        verify_against_phase0_requirements(y_status, expectations)
        statuses["bybit"] = y_status
        logger.info("[exchange-perms] bybit verified ✓ (last_check=%s)", y_status.last_check_utc)

    return statuses


def perms_summary(statuses: dict[str, PermissionStatus]) -> dict[str, Any]:
    """Read-only Summary für Telegram ``/live status`` und Audit-Header."""
    return {
        ex: {
            "spot_trading": s.spot_trading_enabled,
            "withdrawals": s.withdrawals_enabled,
            "margin": s.margin_enabled,
            "futures": s.futures_enabled,
            "derivatives": s.derivatives_enabled,
            "ip_allowlist": list(s.ip_allowlist),
            "ip_restrict_enforced": s.ip_restrict_enforced,
            "last_check_utc": s.last_check_utc,
            "api_key_label": s.api_key_label,
        }
        for ex, s in statuses.items()
    }


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "BinanceHttpCaller",
    "BinancePermissionVerifier",
    "BybitHttpCaller",
    "BybitPermissionVerifier",
    "CHECK_TTL_SECONDS",
    "ExchangeApiError",
    "ExchangePermissionVerifier",
    "ExchangePermsError",
    "IpResolver",
    "PermissionDriftError",
    "PermissionStatus",
    "Phase0Expectations",
    "_binance_payload_to_status",
    "_bybit_payload_to_status",
    "get_pi_wan_ip",
    "perms_summary",
    "verify_against_phase0_requirements",
    "verify_all",
]
