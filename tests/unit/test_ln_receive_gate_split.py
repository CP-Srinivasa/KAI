"""U1 — receive/send-Gate-Split, jetzt an seinem eigenen Modul.

Kapitalfreies Invoice-Minting ist vom Spend-Kill-Switch (``pay_enabled``) auf
einen eigenen Schalter (``receive_enabled``) entkoppelt. Die Kern-Invariante:
Empfang einzuschalten darf NIE einen Sendepfad oeffnen, und NUR
``create_invoice`` darf je als ``receive`` klassifiziert werden (fail-closed
Allowlist).

**Was sich mit ADR 0018 §12 (PR 1) geaendert hat und was nicht.** Der
Empfangspfad ist unveraendert — er ist aus ``value_layer.py`` nach
``app/lightning/receive_gate.py`` gezogen, weil das umgebende Modul mit dem
alten Sendeweg gefallen ist. Die negative Kern-Invariante ("Empfang an, kein
Spend offen") wird nicht mehr an fuenf Sendemethoden gemessen, sondern
struktureller: **es gibt in diesem Paket keine Sendemethode mehr.** Der
Backstop im Gate bleibt trotzdem scharf, damit die naechste hinzugefuegte
Methode nicht versehentlich zum Empfang erklaert wird.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.lightning.receive_gate as rg
from app.core.lightning_settings import LightningSettings
from app.lightning.client import LightningUnavailableError
from app.lightning.receive_gate import RECEIVE_ACTIONS, _assert_send_allowed, create_invoice


def _cfg(*, pay_enabled: bool = False, receive_enabled: bool = False) -> LightningSettings:
    return LightningSettings(
        enabled=True,
        pay_enabled=pay_enabled,
        receive_enabled=receive_enabled,
        tls_cert_path="test-tls.pem",
    )


def _fake_client() -> MagicMock:
    c = MagicMock()
    c.add_invoice = AsyncMock(return_value={"payment_request": "lnbc1...", "r_hash": "aa"})
    return c


# --- receive path is gated by receive_enabled, NOT pay_enabled -------------------


@pytest.mark.asyncio
async def test_invoice_mints_with_receive_enabled_even_when_pay_disabled() -> None:
    """The core capital-free unlock: receive_enabled=True + pay_enabled=False must let
    create_invoice reach the node — minting is receive-side, no spend."""
    client = _fake_client()
    with patch("app.lightning.receive_gate._build_client", return_value=client):
        r = await create_invoice(
            value_sat=100,
            memo="kai-oracle:fee-series",
            dry_run=False,
            cfg=_cfg(pay_enabled=False, receive_enabled=True),
        )
    assert r.state == "executed"
    client.add_invoice.assert_awaited_once()


@pytest.mark.parametrize("node_fails", [False, True])
async def test_every_node_touched_invoice_outcome_gets_one_receive_audit_line(
    node_fails: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-7/PR-C: success and node-error are audited without the spend journal."""
    client = _fake_client()
    if node_fails:
        client.add_invoice = AsyncMock(
            side_effect=LightningUnavailableError("lnd request failed: test failure")
        )
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        rg,
        "append_receive_event",
        lambda action, state, **_: events.append((action, state)),
    )

    with patch("app.lightning.receive_gate._build_client", return_value=client):
        result = await create_invoice(
            value_sat=100,
            dry_run=False,
            cfg=_cfg(pay_enabled=False, receive_enabled=True),
        )

    expected_state = "error" if node_fails else "executed"
    assert result.state == expected_state
    assert events == [("create_invoice", expected_state)]


@pytest.mark.asyncio
async def test_invoice_disabled_when_receive_flag_off() -> None:
    with patch("app.lightning.receive_gate._build_client") as build:
        r = await create_invoice(
            value_sat=100, dry_run=False, cfg=_cfg(pay_enabled=True, receive_enabled=False)
        )
    assert r.state == "disabled" and "receive_enabled" in r.detail
    build.assert_not_called()


@pytest.mark.asyncio
async def test_the_memo_reaches_the_node_verbatim() -> None:
    """``kai-oracle:{scope}`` ist Vertrag, nicht Kosmetik.

    ``earnings_ledger`` ordnet eine Einnahme ueber genau dieses Feld ihrer
    Quelle zu. Ein Praefix-Wechsel (etwa auf ``MEMO_PREFIX = "kai-pay: "`` des
    Control Plane) waere still — und die Einnahmen waeren danach keiner
    Leistung mehr zuzuordnen. Genau deshalb ist der Mint NICHT migriert worden.
    """
    client = _fake_client()
    with patch("app.lightning.receive_gate._build_client", return_value=client):
        await create_invoice(
            value_sat=100,
            memo="kai-oracle:fee-series",
            dry_run=False,
            cfg=_cfg(receive_enabled=True),
        )
    assert client.add_invoice.await_args.kwargs["memo"] == "kai-oracle:fee-series"


# --- NEGATIVE CORE INVARIANT: receive ON must not open ANY spend -----------------


def test_no_spend_path_exists_in_this_package_anymore() -> None:
    """Permanenter Regressionswaechter, strukturell statt aufzaehlend.

    Der Bestand rief hier fuenf Sendemethoden auf und pruefte, dass sie
    ``disabled`` bleiben. Seit PR 1 gibt es sie nicht mehr: kein Modul in
    ``app/lightning`` ruft noch eine schreibende Node-Methode ausser
    ``add_invoice``. Das ist die staerkere Aussage — sie verwaessert nicht
    dadurch, dass jemand eine sechste Methode hinzufuegt und den Test vergisst.
    """
    spend_methods = {"pay_invoice", "keysend", "send_coins", "open_channel", "close_channel"}
    offenders: list[str] = []
    for path in sorted(Path("app/lightning").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in spend_methods:
                    offenders.append(f"{path.as_posix()}:{node.lineno} -> {node.func.attr}")
    assert not offenders, (
        "app/lightning darf keinen Sendepfad mehr fahren — der einzige Weg ist "
        f"PaymentService.execute (ADR 0018 §12): {offenders}"
    )


# --- backstop: a spend may NEVER be classified receive ---------------------------


def test_backstop_spend_action_declaring_receive_raises() -> None:
    with pytest.raises(ValueError):
        _assert_send_allowed(
            "pay_invoice",
            cfg=_cfg(receive_enabled=True),
            dry_run=False,
            confirm=True,
            irreversible=True,
            plan={},
            direction="receive",
        )


def test_receive_actions_allowlist_is_minimal() -> None:
    assert RECEIVE_ACTIONS == frozenset({"create_invoice"})


def test_unknown_direction_falls_back_to_send_gate() -> None:
    """fail-closed: an unrecognised direction must use the stricter send gate."""
    r = _assert_send_allowed(
        "x",
        cfg=_cfg(pay_enabled=False, receive_enabled=True),
        dry_run=False,
        confirm=True,
        irreversible=False,
        plan={},
        direction="bogus",
    )
    assert r is not None and r.state == "disabled" and "pay_enabled" in r.detail


# --- reflection: each write method declares the correct direction ----------------


def test_reflection_direction_declared_correctly_per_method() -> None:
    """Struktur-Invariante: nur ``RECEIVE_ACTIONS`` duerfen ``direction='receive'``
    deklarieren. Heute ist das genau eine Methode — und der Test bleibt stehen,
    damit eine kuenftige zweite nicht stillschweigend dazukommt."""
    pat = re.compile(r"direction\s*=\s*[\"'](\w+)[\"']")
    checked = 0
    for name, fn in inspect.getmembers(rg, inspect.iscoroutinefunction):
        if name.startswith("_") or getattr(fn, "__module__", "") != rg.__name__:
            continue
        src = inspect.getsource(fn)
        if "_assert_send_allowed" not in src:
            continue
        m = pat.search(src)
        assert m is not None, f"{name} does not declare an explicit direction="
        checked += 1
        if name in RECEIVE_ACTIONS:
            assert m.group(1) == "receive", f"{name} must declare direction='receive'"
        else:
            assert m.group(1) == "send", (
                f"{name} (spend) must declare direction='send', got {m.group(1)!r}"
            )
    assert checked == 1  # create_invoice — der einzige Schreibzugriff, der bleibt
