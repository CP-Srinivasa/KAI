"""D-277 — Preflight-Fakten fuer den ARMIERTEN Betrieb (Sendepfad).

Im Empfangs-Regime (``pay_enabled=false``) aendert sich nichts. Armiert kommen
fuenf Fakten dazu, alle fail-closed: Node-Version traegt ``/v2/router/send``,
SCB-Kopie ist frisch, Payment-Modus ist ``live``, Fee-Cap ist gesetzt, der
``/pay``-Purpose ist erlaubt.
"""

from __future__ import annotations

import pytest

from app.core.lightning_settings import LightningSettings
from app.core.payment_settings import PaymentSettings
from app.lightning.golive_preflight import (
    PAY_PURPOSE,
    ROUTER_SEND_MIN_LND_VERSION,
    golive_preflight,
    parse_lnd_version,
)
from app.messaging.pay_telegram_commands import PAY_PURPOSE as TELEGRAM_PAY_PURPOSE

ARMED_NAMES = {
    "router_send_supported",
    "scb_backup_fresh",
    "payment_mode_live",
    "fee_cap_configured",
    "pay_purpose_allowed",
}


def _cfg(*, armed: bool) -> LightningSettings:
    return LightningSettings(
        _env_file=None,
        enabled=True,
        l402_enabled=True,
        receive_enabled=True,
        pay_enabled=armed,
        l402_secret="a" * 32,
        macaroon_hex="deadbeef",
        invoice_macaroon_hex="invoice",
        payment_macaroon_hex="payment",
        tls_cert_path="test-tls.pem",
        scb_max_age_seconds=7200,
    )


def _payments(**overrides) -> PaymentSettings:
    base = {
        "mode": "live",
        "fee_limit_default_ppm": 3000,
        "fee_limit_max_sat": 200,
        "purposes_allowed": f"data_subscription,{PAY_PURPOSE}",
    }
    return PaymentSettings(_env_file=None, **{**base, **overrides})


def _node_ok(*, armed: bool) -> dict:
    return {
        "node_reachable": True,
        "macaroon_scope_minimal": not armed,
        "macaroon_can_mint": True,
        "inbound_liquidity_sat": 1000,
        "booking_unit_present": True,
        "telemetry_writable": True,
    }


def _armed_ok() -> dict:
    return {"node_version": "0.18.3-beta commit=v0.18.3-beta", "scb_age_seconds": 600.0}


def _blocking(out: dict) -> set[str]:
    return set(out["blocking"])


# --------------------------------------------------------------------------- #
# Regime
# --------------------------------------------------------------------------- #


def test_receive_only_regime_ignores_the_armed_facts() -> None:
    out = golive_preflight(_cfg(armed=False), **_node_ok(armed=False))
    names = {c["name"] for c in out["checks"]}
    assert names.isdisjoint(ARMED_NAMES)
    assert out["verdict"] == "GO"


def test_armed_go_when_every_send_fact_is_proven() -> None:
    out = golive_preflight(
        _cfg(armed=True), **_node_ok(armed=True), **_armed_ok(), payments=_payments()
    )
    names = {c["name"] for c in out["checks"]}
    assert ARMED_NAMES <= names
    assert out["verdict"] == "GO", out["blocking"]


def test_armed_without_any_send_fact_blocks_on_all_of_them() -> None:
    """Fail-closed: nichts gemessen heisst nichts bewiesen."""
    out = golive_preflight(_cfg(armed=True), **_node_ok(armed=True))
    assert {"router_send_supported", "scb_backup_fresh", "payment_settings_probed"} <= _blocking(
        out
    )
    assert out["verdict"] == "NO-GO"


# --------------------------------------------------------------------------- #
# Einzelfakten
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.18.3-beta commit=v0.18.3-beta", (0, 18, 3)),
        ("v0.21.0-beta.rc1", (0, 21, 0)),
        ("0.11", (0, 11, 0)),
        ("", None),
        (None, None),
        ("garbage", None),
        ("x.y.z", None),
    ],
)
def test_parse_lnd_version(raw, expected) -> None:
    assert parse_lnd_version(raw) == expected


@pytest.mark.parametrize("raw", ["0.10.4-beta", "0.9.0", "", None])
def test_a_node_too_old_or_unknown_for_router_send_blocks(raw) -> None:
    out = golive_preflight(
        _cfg(armed=True),
        **_node_ok(armed=True),
        **{**_armed_ok(), "node_version": raw},
        payments=_payments(),
    )
    assert "router_send_supported" in _blocking(out)


def test_the_minimum_version_is_the_first_with_router_rest() -> None:
    assert ROUTER_SEND_MIN_LND_VERSION == (0, 11, 0)


@pytest.mark.parametrize("age", [None, 7200.1, 99999.0])
def test_a_missing_or_stale_scb_copy_blocks(age) -> None:
    out = golive_preflight(
        _cfg(armed=True),
        **_node_ok(armed=True),
        **{**_armed_ok(), "scb_age_seconds": age},
        payments=_payments(),
    )
    assert "scb_backup_fresh" in _blocking(out)


def test_an_scb_copy_exactly_at_the_limit_still_passes() -> None:
    out = golive_preflight(
        _cfg(armed=True),
        **_node_ok(armed=True),
        **{**_armed_ok(), "scb_age_seconds": 7200.0},
        payments=_payments(),
    )
    assert "scb_backup_fresh" not in _blocking(out)


@pytest.mark.parametrize("mode", ["shadow", "simulation"])
def test_a_non_live_payment_mode_blocks_the_armed_verdict(mode) -> None:
    out = golive_preflight(
        _cfg(armed=True), **_node_ok(armed=True), **_armed_ok(), payments=_payments(mode=mode)
    )
    assert "payment_mode_live" in _blocking(out)


@pytest.mark.parametrize("overrides", [{"fee_limit_default_ppm": 0}, {"fee_limit_max_sat": 0}])
def test_a_missing_fee_cap_blocks(overrides) -> None:
    out = golive_preflight(
        _cfg(armed=True),
        **_node_ok(armed=True),
        **_armed_ok(),
        payments=_payments(**overrides),
    )
    assert "fee_cap_configured" in _blocking(out)


def test_the_pay_purpose_must_be_allowed() -> None:
    out = golive_preflight(
        _cfg(armed=True),
        **_node_ok(armed=True),
        **_armed_ok(),
        payments=_payments(purposes_allowed="data_subscription"),
    )
    assert "pay_purpose_allowed" in _blocking(out)


def test_the_purpose_constant_is_the_one_telegram_uses() -> None:
    """Ein Katalog: Preflight prueft genau den Purpose, den /pay einreicht."""
    assert PAY_PURPOSE == TELEGRAM_PAY_PURPOSE == "operator_pay_invoice"
