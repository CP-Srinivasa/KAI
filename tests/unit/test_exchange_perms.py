"""Phase-0 Exchange-Permission-Verifier — Smoke-Tests.

Spec: docs/security/kai_light_live_phase0_spec.md §3.

Diese Tests decken:
- Mapper Binance/Bybit-Payload → ``PermissionStatus``
- Drift-Detection (alle 6 Phase-0-Bedingungen einzeln + kombiniert)
- ``verify_all``-Toplevel mit Verifier-Stubs
- Fail-closed bei Network-/API-Errors

Real-Network-Calls gegen Testnet kommen mit Sprint N+2 (separate
Integration-Suite, nicht Teil dieses Smoke-Sets).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.security.exchange_perms import (
    BinanceHttpCaller,
    BinancePermissionVerifier,
    BybitHttpCaller,
    BybitPermissionVerifier,
    ExchangeApiError,
    ExchangePermissionVerifier,
    ExchangePermsError,
    IpResolver,
    PermissionDriftError,
    PermissionStatus,
    Phase0Expectations,
    _binance_payload_to_status,
    _bybit_payload_to_status,
    get_pi_wan_ip,
    perms_summary,
    verify_against_phase0_requirements,
    verify_all,
)


# ── Test-Fixtures ─────────────────────────────────────────────────────────────


PI_WAN_IP_OK = "93.207.183.42"


def _binance_happy_payload(
    *,
    spot: bool = True,
    withdraw: bool = False,
    margin: bool = False,
    futures: bool = False,
    ip_restrict: bool = True,
    ip_list: tuple[str, ...] = (PI_WAN_IP_OK,),
) -> dict[str, Any]:
    return {
        "ipRestrict": ip_restrict,
        "ipList": list(ip_list),
        "createTime": 1623840000000,
        "enableWithdrawals": withdraw,
        "enableInternalTransfer": False,
        "permitsUniversalTransfer": False,
        "enableVanillaOptions": False,
        "enableReading": True,
        "enableFutures": futures,
        "enableMargin": margin,
        "enableSpotAndMarginTrading": spot,
        "tradingAuthorityExpirationTime": None,
    }


def _bybit_happy_payload(
    *,
    spot_perm: list[str] | None = None,
    derivatives: list[str] | None = None,
    options: list[str] | None = None,
    margin: list[str] | None = None,
    wallet: list[str] | None = None,
    ips: tuple[str, ...] = (PI_WAN_IP_OK,),
) -> dict[str, Any]:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "id": "abc123",
            "note": "kai-phase0-pi5",
            "apiKey": "fake-key",
            "readOnly": 0,
            "permissions": {
                "Spot": spot_perm if spot_perm is not None else ["SpotTrade"],
                "Derivatives": derivatives or [],
                "Options": options or [],
                "Margin": margin or [],
                "Wallet": wallet if wallet is not None else ["AccountTransfer"],
            },
            "ips": list(ips),
            "type": 1,
            "deadlineDay": 90,
            "expiredAt": "",
            "createdAt": "",
        },
    }


def _expectations() -> Phase0Expectations:
    return Phase0Expectations(expected_pi_wan_ip=PI_WAN_IP_OK)


# ── Binance-Mapper ────────────────────────────────────────────────────────────


def test_binance_mapper_happy_path() -> None:
    status = _binance_payload_to_status(_binance_happy_payload())
    assert status.exchange == "binance"
    assert status.spot_trading_enabled is True
    assert status.withdrawals_enabled is False
    assert status.margin_enabled is False
    assert status.futures_enabled is False
    assert status.derivatives_enabled is False
    assert status.ip_restrict_enforced is True
    assert status.ip_allowlist == (PI_WAN_IP_OK,)


def test_binance_mapper_withdraw_drift() -> None:
    status = _binance_payload_to_status(_binance_happy_payload(withdraw=True))
    assert status.withdrawals_enabled is True


def test_binance_mapper_no_ip_restrict_means_no_enforcement() -> None:
    status = _binance_payload_to_status(_binance_happy_payload(ip_restrict=False))
    assert status.ip_restrict_enforced is False


def test_binance_mapper_missing_field_defaults_to_false() -> None:
    """Defensive: fehlende Felder im API-Response dürfen den Mapper nicht crashen."""
    minimal: dict[str, Any] = {"enableSpotAndMarginTrading": True}
    status = _binance_payload_to_status(minimal)
    assert status.spot_trading_enabled is True
    assert status.withdrawals_enabled is False
    assert status.ip_restrict_enforced is False


# ── Bybit-Mapper ──────────────────────────────────────────────────────────────


def test_bybit_mapper_happy_path() -> None:
    status = _bybit_payload_to_status(_bybit_happy_payload())
    assert status.exchange == "bybit"
    assert status.api_key_label == "kai-phase0-pi5"
    assert status.spot_trading_enabled is True
    assert status.withdrawals_enabled is False
    assert status.derivatives_enabled is False
    assert status.ip_restrict_enforced is True
    assert PI_WAN_IP_OK in status.ip_allowlist


def test_bybit_mapper_retcode_nonzero_raises() -> None:
    payload = _bybit_happy_payload()
    payload["retCode"] = 10003
    payload["retMsg"] = "API key expired"
    with pytest.raises(ExchangeApiError) as exc_info:
        _bybit_payload_to_status(payload)
    assert "retCode" in str(exc_info.value)
    assert "10003" in str(exc_info.value)


def test_bybit_mapper_withdraw_permission_detected() -> None:
    """Bybit Withdraw-Permission via ``Wallet`` permission list."""
    status = _bybit_payload_to_status(
        _bybit_happy_payload(wallet=["AccountTransfer", "WithdrawWithinWhiteList"])
    )
    assert status.withdrawals_enabled is True


def test_bybit_mapper_derivatives_detected() -> None:
    status = _bybit_payload_to_status(
        _bybit_happy_payload(derivatives=["ContractTrade"])
    )
    assert status.derivatives_enabled is True
    assert status.futures_enabled is True  # Bybit-Sammelbegriff


def test_bybit_mapper_wildcard_ip_means_no_enforcement() -> None:
    status = _bybit_payload_to_status(_bybit_happy_payload(ips=("*",)))
    assert status.ip_restrict_enforced is False


def test_bybit_mapper_empty_ip_list_means_no_enforcement() -> None:
    status = _bybit_payload_to_status(_bybit_happy_payload(ips=()))
    assert status.ip_restrict_enforced is False


# ── Drift-Detection ──────────────────────────────────────────────────────────


def test_drift_clean_status_passes() -> None:
    status = _binance_payload_to_status(_binance_happy_payload())
    # Sollte ohne Exception durchgehen
    verify_against_phase0_requirements(status, _expectations())


def test_drift_withdrawals_enabled_rejected() -> None:
    status = _binance_payload_to_status(_binance_happy_payload(withdraw=True))
    with pytest.raises(PermissionDriftError) as exc_info:
        verify_against_phase0_requirements(status, _expectations())
    assert "withdrawals_enabled" in str(exc_info.value)


def test_drift_spot_disabled_rejected() -> None:
    status = _binance_payload_to_status(_binance_happy_payload(spot=False))
    with pytest.raises(PermissionDriftError) as exc_info:
        verify_against_phase0_requirements(status, _expectations())
    assert "spot_trading_disabled" in str(exc_info.value)


def test_drift_futures_enabled_rejected() -> None:
    status = _binance_payload_to_status(_binance_happy_payload(futures=True))
    with pytest.raises(PermissionDriftError) as exc_info:
        verify_against_phase0_requirements(status, _expectations())
    msg = str(exc_info.value)
    assert "futures_enabled" in msg or "derivatives_enabled" in msg


def test_drift_margin_enabled_rejected() -> None:
    status = _binance_payload_to_status(_binance_happy_payload(margin=True))
    with pytest.raises(PermissionDriftError) as exc_info:
        verify_against_phase0_requirements(status, _expectations())
    assert "margin_enabled" in str(exc_info.value)


def test_drift_ip_not_in_allowlist_rejected() -> None:
    status = _binance_payload_to_status(
        _binance_happy_payload(ip_list=("8.8.8.8",))
    )
    with pytest.raises(PermissionDriftError) as exc_info:
        verify_against_phase0_requirements(status, _expectations())
    assert "pi_wan_ip_missing_from_allowlist" in str(exc_info.value)
    assert PI_WAN_IP_OK in str(exc_info.value)


def test_drift_ip_restrict_not_enforced_rejected() -> None:
    status = _binance_payload_to_status(
        _binance_happy_payload(ip_restrict=False, ip_list=())
    )
    with pytest.raises(PermissionDriftError) as exc_info:
        verify_against_phase0_requirements(status, _expectations())
    assert "ip_restrict_not_enforced" in str(exc_info.value)


def test_drift_multiple_reasons_concatenated() -> None:
    status = _binance_payload_to_status(
        _binance_happy_payload(
            spot=False, withdraw=True, futures=True, ip_list=("1.2.3.4",)
        )
    )
    with pytest.raises(PermissionDriftError) as exc_info:
        verify_against_phase0_requirements(status, _expectations())
    msg = str(exc_info.value)
    assert "spot_trading_disabled" in msg
    assert "withdrawals_enabled" in msg
    assert "pi_wan_ip_missing_from_allowlist" in msg


# ── Verifier-Init guards ─────────────────────────────────────────────────────


def test_binance_verifier_rejects_empty_credentials() -> None:
    with pytest.raises(ExchangePermsError):
        BinancePermissionVerifier(api_key="", api_secret="")
    with pytest.raises(ExchangePermsError):
        BinancePermissionVerifier(api_key="abc", api_secret="")


def test_bybit_verifier_rejects_empty_credentials() -> None:
    with pytest.raises(ExchangePermsError):
        BybitPermissionVerifier(api_key="", api_secret="")
    with pytest.raises(ExchangePermsError):
        BybitPermissionVerifier(api_key="abc", api_secret="")


def test_binance_verifier_no_http_caller_raises() -> None:
    """Production-default ohne HTTP-Caller-Wiring muss explizit failen.

    Verhindert, dass ein vergessener Wiring-Schritt zu einem stillen
    no-op wird (würde das ganze Schutzkonzept aushebeln).
    """
    v = BinancePermissionVerifier(api_key="k", api_secret="s")
    with pytest.raises(ExchangeApiError) as exc_info:
        v.fetch_status()
    assert "http_caller not injected" in str(exc_info.value)


def test_bybit_verifier_no_http_caller_raises() -> None:
    v = BybitPermissionVerifier(api_key="k", api_secret="s")
    with pytest.raises(ExchangeApiError) as exc_info:
        v.fetch_status()
    assert "http_caller not injected" in str(exc_info.value)


# ── Verifier mit Stub-HTTP-Caller ────────────────────────────────────────────


class _BinanceStubCaller(BinanceHttpCaller):
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[tuple[str, str]] = []

    def call_api_restrictions(self, *, api_key: str, api_secret: str) -> dict[str, Any]:
        self.calls.append((api_key, api_secret))
        return self._payload


class _BybitStubCaller(BybitHttpCaller):
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[tuple[str, str]] = []

    def call_query_api(self, *, api_key: str, api_secret: str) -> dict[str, Any]:
        self.calls.append((api_key, api_secret))
        return self._payload


class _RaisingBinanceCaller(BinanceHttpCaller):
    def call_api_restrictions(self, *, api_key: str, api_secret: str) -> dict[str, Any]:
        raise TimeoutError("network down")


def test_binance_verifier_with_stub_caller_returns_status() -> None:
    caller = _BinanceStubCaller(_binance_happy_payload())
    v = BinancePermissionVerifier(api_key="k", api_secret="s", http_caller=caller)
    status = v.fetch_status()
    assert status.exchange == "binance"
    assert status.spot_trading_enabled is True
    assert caller.calls == [("k", "s")]


def test_binance_verifier_wraps_transport_error_as_api_error() -> None:
    v = BinancePermissionVerifier(
        api_key="k", api_secret="s", http_caller=_RaisingBinanceCaller()
    )
    with pytest.raises(ExchangeApiError) as exc_info:
        v.fetch_status()
    assert "binance api_restrictions call failed" in str(exc_info.value)
    assert "network down" in str(exc_info.value)


def test_bybit_verifier_with_stub_caller_returns_status() -> None:
    caller = _BybitStubCaller(_bybit_happy_payload())
    v = BybitPermissionVerifier(api_key="k", api_secret="s", http_caller=caller)
    status = v.fetch_status()
    assert status.exchange == "bybit"
    assert status.api_key_label == "kai-phase0-pi5"


# ── verify_all Top-Level ─────────────────────────────────────────────────────


def test_verify_all_both_exchanges_clean() -> None:
    binance = BinancePermissionVerifier(
        api_key="bk", api_secret="bs",
        http_caller=_BinanceStubCaller(_binance_happy_payload()),
    )
    bybit = BybitPermissionVerifier(
        api_key="yk", api_secret="ys",
        http_caller=_BybitStubCaller(_bybit_happy_payload()),
    )
    statuses = verify_all(
        binance_verifier=binance,
        bybit_verifier=bybit,
        expectations=_expectations(),
    )
    assert set(statuses.keys()) == {"binance", "bybit"}
    assert statuses["binance"].spot_trading_enabled
    assert statuses["bybit"].spot_trading_enabled


def test_verify_all_binance_drift_blocks_bybit_check() -> None:
    """Erste Drift-Detection wirft sofort — Bybit-Verifier wird nicht mehr aufgerufen."""
    binance = BinancePermissionVerifier(
        api_key="bk", api_secret="bs",
        http_caller=_BinanceStubCaller(_binance_happy_payload(withdraw=True)),
    )
    bybit_caller = _BybitStubCaller(_bybit_happy_payload())
    bybit = BybitPermissionVerifier(
        api_key="yk", api_secret="ys", http_caller=bybit_caller,
    )
    with pytest.raises(PermissionDriftError):
        verify_all(
            binance_verifier=binance,
            bybit_verifier=bybit,
            expectations=_expectations(),
        )
    assert bybit_caller.calls == []  # bybit nie aufgerufen


def test_verify_all_only_bybit_works() -> None:
    """Operator-Setup auf nur einem Exchange ist erlaubt."""
    bybit = BybitPermissionVerifier(
        api_key="yk", api_secret="ys",
        http_caller=_BybitStubCaller(_bybit_happy_payload()),
    )
    statuses = verify_all(
        binance_verifier=None,
        bybit_verifier=bybit,
        expectations=_expectations(),
    )
    assert set(statuses.keys()) == {"bybit"}


def test_verify_all_no_verifiers_raises() -> None:
    with pytest.raises(ExchangePermsError):
        verify_all(
            binance_verifier=None,
            bybit_verifier=None,
            expectations=_expectations(),
        )


# ── perms_summary ────────────────────────────────────────────────────────────


def test_perms_summary_serializes_to_jsonable_dict() -> None:
    import json

    binance = BinancePermissionVerifier(
        api_key="bk", api_secret="bs",
        http_caller=_BinanceStubCaller(_binance_happy_payload()),
    )
    statuses = verify_all(
        binance_verifier=binance,
        bybit_verifier=None,
        expectations=_expectations(),
    )
    summary = perms_summary(statuses)
    # JSON-Serialisierbarkeit ist Pflicht für /live status + audit-header
    rendered = json.dumps(summary)
    assert "binance" in rendered
    assert "spot_trading" in rendered


# ── IP-Resolver ──────────────────────────────────────────────────────────────


class _StaticIpResolver(IpResolver):
    def __init__(self, ip: str) -> None:
        self._ip = ip

    def resolve(self) -> str:
        return self._ip


class _RaisingIpResolver(IpResolver):
    def resolve(self) -> str:
        raise OSError("dns down")


def test_get_pi_wan_ip_no_resolver_raises() -> None:
    with pytest.raises(ExchangeApiError):
        get_pi_wan_ip()


def test_get_pi_wan_ip_with_stub_returns_value() -> None:
    assert get_pi_wan_ip(ip_resolver=_StaticIpResolver("1.2.3.4")) == "1.2.3.4"


def test_get_pi_wan_ip_wraps_resolver_errors() -> None:
    with pytest.raises(ExchangeApiError) as exc_info:
        get_pi_wan_ip(ip_resolver=_RaisingIpResolver())
    assert "pi_wan_ip resolve failed" in str(exc_info.value)


# ── ABC-Sanity ───────────────────────────────────────────────────────────────


def test_abstract_verifier_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        ExchangePermissionVerifier()  # type: ignore[abstract]
