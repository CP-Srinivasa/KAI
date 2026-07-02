"""U5 — G0 go-live preflight: a hard GO/NO-GO gate before flipping the receive path.

Config facts come from settings; node-side facts (reachability, macaroon scope) are
injected (the CLI probes the real node). Fail-closed: an unprobed node fact is NOT ok
→ NO-GO. The pay_enabled-off check is a NEGATIVE invariant: the spend kill-switch must
stay off for the probe.
"""

from __future__ import annotations

from app.core.lightning_settings import LightningSettings
from app.lightning.golive_preflight import golive_preflight


def _ready_cfg() -> LightningSettings:
    return LightningSettings(
        enabled=True,
        l402_enabled=True,
        receive_enabled=True,
        pay_enabled=False,
        l402_secret="a" * 32,
        macaroon_hex="deadbeef",
    )


def _all_node_ok() -> dict:
    return {
        "node_reachable": True,
        "macaroon_scope_minimal": True,
        "macaroon_can_mint": True,
        "inbound_liquidity_sat": 1000,
        "booking_unit_present": True,
        "telemetry_writable": True,
    }


def test_go_when_everything_ready() -> None:
    out = golive_preflight(_ready_cfg(), **_all_node_ok())
    assert out["verdict"] == "GO" and out["go"] is True and out["blocking"] == []


def test_armed_mode_drops_receive_only_invariants() -> None:
    """When the value layer is armed (pay_enabled=true) the receive-only invariants
    (pay_enabled_off / macaroon_scope_minimal) are REPLACED, not reported as failures.
    A send-capable macaroon (scope_minimal=False) is the correct armed state → GO."""
    cfg = _ready_cfg().model_copy(update={"pay_enabled": True})
    out = golive_preflight(cfg, **{**_all_node_ok(), "macaroon_scope_minimal": False})
    names = {c["name"] for c in out["checks"]}
    assert "pay_enabled_off" not in names
    assert "macaroon_scope_minimal" not in names
    assert "value_layer_armed" in names and "macaroon_send_capable" in names
    assert out["verdict"] == "GO" and out["blocking"] == []


def test_armed_mode_no_go_when_macaroon_cannot_send() -> None:
    """Armed but the macaroon is scope-minimal (pay probe permission-denied) → the
    cockpit cannot actually spend → NO-GO on macaroon_send_capable."""
    cfg = _ready_cfg().model_copy(update={"pay_enabled": True})
    out = golive_preflight(cfg, **{**_all_node_ok(), "macaroon_scope_minimal": True})
    assert out["verdict"] == "NO-GO" and "macaroon_send_capable" in out["blocking"]


def test_receive_only_no_go_when_pay_enabled_off_violated() -> None:
    """Receive-only regime (pay_enabled=false) still enforces the scope-minimal invariant."""
    out = golive_preflight(_ready_cfg(), **{**_all_node_ok(), "macaroon_scope_minimal": False})
    assert out["verdict"] == "NO-GO" and "macaroon_scope_minimal" in out["blocking"]


def test_no_go_when_macaroon_not_scope_minimal() -> None:
    """satoshi auflage 4: a pay_invoice probe that is NOT permission-denied means the
    macaroon carries spend scope → hard NO-GO."""
    out = golive_preflight(_ready_cfg(), **{**_all_node_ok(), "macaroon_scope_minimal": False})
    assert out["verdict"] == "NO-GO" and "macaroon_scope_minimal" in out["blocking"]


def test_no_go_when_macaroon_cannot_mint() -> None:
    """The readonly-macaroon trap: a macaroon can pass the no-spend check yet still be
    unable to MINT invoices (no invoices:write) — then the paid path 503s. Hard NO-GO."""
    out = golive_preflight(_ready_cfg(), **{**_all_node_ok(), "macaroon_can_mint": False})
    assert out["verdict"] == "NO-GO" and "macaroon_can_mint" in out["blocking"]


def test_no_go_when_no_inbound_liquidity() -> None:
    """0 inbound = the node physically cannot receive any payment → hard NO-GO."""
    out = golive_preflight(_ready_cfg(), **{**_all_node_ok(), "inbound_liquidity_sat": 0})
    assert out["verdict"] == "NO-GO" and "inbound_liquidity" in out["blocking"]


def test_no_go_when_node_unprobed_fail_closed() -> None:
    out = golive_preflight(_ready_cfg(), **{**_all_node_ok(), "node_reachable": None})
    assert out["verdict"] == "NO-GO" and "node_reachable" in out["blocking"]


def test_no_go_when_secret_missing() -> None:
    cfg = _ready_cfg().model_copy(update={"l402_secret": ""})
    out = golive_preflight(cfg, **_all_node_ok())
    assert out["verdict"] == "NO-GO" and "l402_secret_set" in out["blocking"]


def test_blocking_lists_every_failure_on_a_blank_config() -> None:
    out = golive_preflight(LightningSettings())  # all flags default/false, no node probes
    assert out["verdict"] == "NO-GO"
    for name in (
        "ln_enabled",
        "l402_enabled",
        "receive_enabled",
        "l402_secret_set",
        "macaroon_configured",
        "node_reachable",
        "macaroon_scope_minimal",
        "macaroon_can_mint",
        "inbound_liquidity",
        "booking_unit_present",
        "telemetry_writable",
    ):
        assert name in out["blocking"]
    # pay_enabled defaults false → the negative check PASSES even on a blank config
    assert "pay_enabled_off" not in out["blocking"]
