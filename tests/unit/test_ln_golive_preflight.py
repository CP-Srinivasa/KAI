"""U5 — G0 go-live preflight: a hard GO/NO-GO gate before flipping the receive path.

Config facts come from settings; node-side facts (reachability, macaroon scope) are
injected (the CLI probes the real node). Fail-closed: an unprobed node fact is NOT ok
→ NO-GO. The pay_enabled-off check is a NEGATIVE invariant: the spend kill-switch must
stay off for the probe.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.lightning_settings import LightningSettings
from app.core.payment_settings import PaymentSettings
from app.lightning.client import LightningUnavailableError, LndRestClient
from app.lightning.golive_preflight import golive_preflight


def _ready_cfg() -> LightningSettings:
    return LightningSettings(
        _env_file=None,
        enabled=True,
        l402_enabled=True,
        receive_enabled=True,
        pay_enabled=False,
        l402_secret="a" * 32,
        macaroon_hex="deadbeef",
        invoice_macaroon_hex="invoice",
        payment_macaroon_hex="payment",
        tls_cert_path="test-tls.pem",
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


def _armed_send_facts() -> dict:
    """D-277: im armierten Regime muessen die Sende-Fakten bewiesen sein (fail-closed)."""
    return {
        "node_version": "0.18.3-beta commit=v0.18.3-beta",
        "scb_age_seconds": 600.0,
        "payments": PaymentSettings(
            _env_file=None,
            mode="live",
            fee_limit_default_ppm=3000,
            fee_limit_max_sat=200,
            purposes_allowed="data_subscription,operator_pay_invoice",
        ),
    }


def test_go_when_everything_ready() -> None:
    out = golive_preflight(_ready_cfg(), **_all_node_ok())
    assert out["verdict"] == "GO" and out["go"] is True and out["blocking"] == []


def test_armed_mode_drops_receive_only_invariants() -> None:
    """When the value layer is armed (pay_enabled=true) the receive-only invariants
    (pay_enabled_off / macaroon_scope_minimal) are REPLACED, not reported as failures.
    A send-capable macaroon (scope_minimal=False) is the correct armed state → GO."""
    cfg = _ready_cfg().model_copy(update={"pay_enabled": True})
    out = golive_preflight(
        cfg, **{**_all_node_ok(), "macaroon_scope_minimal": False}, **_armed_send_facts()
    )
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
    """A receive-side macaroon with send permission is a hard NO-GO."""
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


def test_no_go_when_only_one_macaroon_is_configured() -> None:
    """W0/PR-A bake gate: the Bestands-Pi config (ONE macaroon in APP_LN_MACAROON_*)
    must NOT report GO any more — the separate invoice credential is the whole point
    of the split and is checked independently of the read credential."""
    cfg = _ready_cfg().model_copy(update={"invoice_macaroon_hex": ""})
    out = golive_preflight(cfg, **_all_node_ok())
    assert out["verdict"] == "NO-GO"
    assert "invoice_macaroon_configured" in out["blocking"]
    assert "read_macaroon_configured" not in out["blocking"]


def test_armed_mode_no_go_without_a_dedicated_payment_credential() -> None:
    """Armed + a send-capable probe is NOT enough: without APP_LN_PAYMENT_MACAROON_*
    the send scope could only come from promoting read/invoice → hard NO-GO."""
    cfg = _ready_cfg().model_copy(update={"pay_enabled": True, "payment_macaroon_hex": ""})
    out = golive_preflight(cfg, **{**_all_node_ok(), "macaroon_scope_minimal": False})
    assert out["verdict"] == "NO-GO" and "macaroon_send_capable" in out["blocking"]


def test_no_go_when_secret_missing() -> None:
    cfg = _ready_cfg().model_copy(update={"l402_secret": ""})
    out = golive_preflight(cfg, **_all_node_ok())
    assert out["verdict"] == "NO-GO" and "l402_secret_set" in out["blocking"]


def test_blocking_lists_every_failure_on_a_blank_config() -> None:
    # _env_file=None: "blank" muss Code-Default sein — die Pi-.env füllt sonst
    # Secret-/Macaroon-Felder und lässt erwartete Failures verschwinden.
    out = golive_preflight(LightningSettings(_env_file=None))  # no flags, no node probes
    assert out["verdict"] == "NO-GO"
    for name in (
        "ln_enabled",
        "l402_enabled",
        "receive_enabled",
        "l402_secret_set",
        "read_macaroon_configured",
        "invoice_macaroon_configured",
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


# --- CLI probe: every fact is measured with the credential that would carry it ----


class _FakeClient:
    """lnd stand-in for scoped credentials and read-only permission RPCs."""

    def __init__(self, scope: str, *, spends: bool, mints: bool = True) -> None:
        self.scope = scope
        self._spends = spends
        self._mints = mints
        self.calls: list[str] = []
        self._macaroon_hex = scope.encode("ascii").hex()
        self._spends_by_scope: dict[str, bool] = {}

    async def get_info(self) -> dict[str, Any]:
        self.calls.append("get_info")
        return {}

    async def _get(self, path: str) -> dict[str, Any]:
        self.calls.append(path)
        assert path == "/v1/macaroon/permissions"
        return {
            "method_permissions": {
                "/routerrpc.Router/SendPaymentV2": {
                    "permissions": [{"entity": "offchain", "action": "write"}]
                }
            }
        }

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        import base64

        self.calls.append(path)
        assert path == "/v1/macaroon/checkpermissions"
        if body["permissions"] == [{"entity": "macaroon", "action": "read"}]:
            return {"valid": True}
        scope = base64.b64decode(body["macaroon"]).decode("ascii")
        return {"valid": self._spends_by_scope[scope]}

    async def add_invoice(self, **_: Any) -> dict[str, Any]:
        self.calls.append("add_invoice")
        if not self._mints:
            raise LightningUnavailableError("lnd returned 403: permission denied")
        return {"payment_request": "lnbc1"}

    async def channel_balance(self) -> dict[str, Any]:
        self.calls.append("channel_balance")
        return {"remote_balance": {"sat": "5000"}}


def _patch_clients(
    monkeypatch: pytest.MonkeyPatch, clients: dict[str, _FakeClient]
) -> dict[str, _FakeClient]:
    """Wire ``_build_client`` in the CLI to the per-scope fakes; a scope that is not
    in the mapping models an UNPROVISIONED credential (the client fails closed)."""
    import scripts.ln_golive_preflight as cli

    def _build(cfg: LightningSettings, *, credential_scope: str = "read") -> _FakeClient:
        client = clients.get(credential_scope)
        if client is None:
            raise LightningUnavailableError("no macaroon configured (hex or path)")
        return client

    monkeypatch.setattr(cli, "_build_client", _build)
    clients.get("read", _FakeClient("read", spends=False))._spends_by_scope = {
        scope: client._spends for scope, client in clients.items()
    }
    return clients


async def test_probe_measures_read_and_invoice_credentials_separately(monkeypatch) -> None:
    """Receive-only: BOTH receive-side credentials must be spend-free, the mint probe
    runs on the invoice credential and liquidity on the read credential."""
    import scripts.ln_golive_preflight as cli

    clients = _patch_clients(
        monkeypatch,
        {
            "read": _FakeClient("read", spends=False),
            "invoice": _FakeClient("invoice", spends=False),
        },
    )
    reachable, scope_minimal, can_mint, inbound = await cli._probe_node(_ready_cfg())
    assert (reachable, scope_minimal, can_mint, inbound) == (True, True, True, 5000)
    assert clients["read"].calls.count("/v1/macaroon/checkpermissions") == 4
    assert "pay_invoice" not in clients["read"].calls
    assert "add_invoice" in clients["invoice"].calls
    assert "add_invoice" not in clients["read"].calls


async def test_probe_flags_a_spend_capable_read_credential(monkeypatch) -> None:
    """The credential every live path still uses (PR-A/PR-B) must keep proving that it
    cannot spend — a fat read macaroon is NOT covered by a clean invoice macaroon."""
    import scripts.ln_golive_preflight as cli

    _patch_clients(
        monkeypatch,
        {
            "read": _FakeClient("read", spends=True),
            "invoice": _FakeClient("invoice", spends=False),
        },
    )
    _, scope_minimal, _, _ = await cli._probe_node(_ready_cfg())
    assert scope_minimal is False


async def test_probe_reports_a_missing_invoice_credential_not_an_unreachable_node(
    monkeypatch,
) -> None:
    """Bestands-Pi after this PR: only APP_LN_MACAROON_* is set. The report must blame
    the missing capability (can_mint False), not fake a dead node."""
    import scripts.ln_golive_preflight as cli

    _patch_clients(monkeypatch, {"read": _FakeClient("read", spends=False)})
    reachable, scope_minimal, can_mint, inbound = await cli._probe_node(_ready_cfg())
    assert reachable is True and inbound == 5000
    assert can_mint is False
    assert scope_minimal is False  # un-probed invoice credential never passes


async def test_armed_probe_targets_the_payment_credential(monkeypatch) -> None:
    """Armed: the spend probe belongs on the dedicated payment credential; a denial
    there means the armed cockpit cannot actually pay."""
    import scripts.ln_golive_preflight as cli

    clients = _patch_clients(
        monkeypatch,
        {
            "read": _FakeClient("read", spends=False),
            "invoice": _FakeClient("invoice", spends=False),
            "payment": _FakeClient("payment", spends=True),
        },
    )
    cfg = _ready_cfg().model_copy(update={"pay_enabled": True})
    _, scope_minimal, _, _ = await cli._probe_node(cfg)
    assert scope_minimal is False  # payment credential CAN spend → macaroon_send_capable
    assert clients["read"].calls.count("/v1/macaroon/checkpermissions") == 2
    assert "pay_invoice" not in clients["payment"].calls


@pytest.mark.parametrize(
    "permissions",
    [
        {},
        {"method_permissions": {}},
        {"method_permissions": {"/routerrpc.Router/SendPaymentV2": {"permissions": []}}},
        {
            "method_permissions": {
                "/routerrpc.Router/SendPaymentV2": {
                    "permissions": [{"entity": "offchain", "action": "read"}]
                }
            }
        },
    ],
)
async def test_armed_probe_missing_or_malformed_permissions_are_unknown(
    monkeypatch, permissions: dict[str, Any]
) -> None:
    import scripts.ln_golive_preflight as cli

    clients = _patch_clients(
        monkeypatch,
        {
            "read": _FakeClient("read", spends=False),
            "payment": _FakeClient("payment", spends=True),
        },
    )
    clients["read"]._get = AsyncMock(return_value=permissions)
    cfg = _ready_cfg().model_copy(update={"pay_enabled": True})

    assert await cli._spend_probe_denied(cfg, "payment") is None
    assert "/v1/macaroon/checkpermissions" not in clients["read"].calls


@pytest.mark.parametrize("response", [{}, {"valid": "true"}, {"valid": None}])
async def test_armed_probe_requires_boolean_permission_verdict(monkeypatch, response) -> None:
    import scripts.ln_golive_preflight as cli

    clients = _patch_clients(
        monkeypatch,
        {
            "read": _FakeClient("read", spends=False),
            "payment": _FakeClient("payment", spends=True),
        },
    )
    clients["read"]._post = AsyncMock(side_effect=[{"valid": True}, response])
    cfg = _ready_cfg().model_copy(update={"pay_enabled": True})

    assert await cli._spend_probe_denied(cfg, "payment") is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            "lnd returned 400 for /v1/macaroon/checkpermissions: "
            '{"code":3,"message":"permission denied","details":[]}',
            True,
        ),
        (
            "lnd returned 400 for /v1/macaroon/checkpermissions: "
            '{"code":3,"message":"invalid macaroon","details":[]}',
            None,
        ),
        (
            "lnd returned 500 for /v1/macaroon/checkpermissions: "
            '{"code":2,"message":"permission denied","details":[]}',
            None,
        ),
    ],
)
async def test_credential_denial_only_for_exact_lnd_error(monkeypatch, error, expected) -> None:
    import scripts.ln_golive_preflight as cli

    clients = _patch_clients(
        monkeypatch,
        {
            "read": _FakeClient("read", spends=False),
            "payment": _FakeClient("payment", spends=True),
        },
    )
    clients["read"]._post = AsyncMock(
        side_effect=[{"valid": True}, LightningUnavailableError(error)]
    )
    cfg = _ready_cfg().model_copy(update={"pay_enabled": True})

    assert await cli._spend_probe_denied(cfg, "payment") is expected


@pytest.mark.parametrize(
    "probe_error",
    [
        "lnd request failed: ReadTimeout connecting to 10.0.0.4:4000",
        "lnd request failed: TLS certificate verify failed",
        "lnd returned 500 for /v1/channels/transactions: unavailable",
    ],
)
async def test_armed_probe_failure_is_unknown_and_blocks_go(monkeypatch, probe_error: str) -> None:
    """B-6: transport failure proves neither permission-denied nor send-capable."""
    import scripts.ln_golive_preflight as cli

    clients = _patch_clients(
        monkeypatch,
        {
            "read": _FakeClient("read", spends=False),
            "invoice": _FakeClient("invoice", spends=False),
            "payment": _FakeClient("payment", spends=True),
        },
    )
    clients["read"]._post = AsyncMock(side_effect=LightningUnavailableError(probe_error))
    cfg = _ready_cfg().model_copy(update={"pay_enabled": True})

    reachable, send_probe, can_mint, inbound = await cli._probe_node(cfg)

    assert (reachable, send_probe, can_mint, inbound) == (True, None, True, 5000)
    report = golive_preflight(
        cfg,
        node_reachable=reachable,
        macaroon_scope_minimal=send_probe,
        macaroon_can_mint=can_mint,
        inbound_liquidity_sat=inbound,
        booking_unit_present=True,
        telemetry_writable=True,
    )
    assert report["verdict"] == "NO-GO"
    assert "macaroon_send_capable" in report["blocking"]


async def test_disarmed_preflight_cannot_build_or_probe_the_payment_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read-only permission probe also inherits the payment-credential guard."""
    import scripts.ln_golive_preflight as cli

    permission_check = AsyncMock(return_value={"valid": True})
    monkeypatch.setattr(LndRestClient, "_post", permission_check)

    assert await cli._spend_probe_denied(_ready_cfg(), "payment") is None
    permission_check.assert_not_awaited()
