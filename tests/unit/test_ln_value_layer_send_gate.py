"""B-002 — central send-gate for the Lightning value layer (security blocker).

Every capital/write path MUST pass ONE chokepoint (``_assert_send_allowed``) before
the node is ever touched, so no write method can silently forget the kill-switch /
dry-run / confirm gates. The reflection test makes that structurally enforced: a
new public write method that bypasses the gate fails the suite.
"""

from __future__ import annotations

import inspect

import app.lightning.value_layer as vl
from app.core.lightning_settings import LightningSettings
from app.lightning.value_layer import _assert_send_allowed


def _cfg(pay_enabled: bool) -> LightningSettings:
    return LightningSettings(enabled=True, pay_enabled=pay_enabled)


def test_gate_disabled_when_kill_switch_off() -> None:
    r = _assert_send_allowed(
        "x", cfg=_cfg(False), dry_run=False, confirm=True, irreversible=False, plan={}
    )
    assert r is not None and r.state == "disabled" and "pay_enabled" in r.detail


def test_gate_planned_on_dry_run() -> None:
    r = _assert_send_allowed(
        "x", cfg=_cfg(True), dry_run=True, confirm=True, irreversible=False, plan={}
    )
    assert r is not None and r.state == "planned" and r.detail == "dry_run"


def test_gate_irreversible_requires_confirm() -> None:
    r = _assert_send_allowed(
        "x", cfg=_cfg(True), dry_run=False, confirm=False, irreversible=True, plan={}
    )
    assert r is not None and r.state == "planned" and r.detail == "confirm=False"


def test_gate_clears_when_all_gates_pass() -> None:
    r = _assert_send_allowed(
        "x", cfg=_cfg(True), dry_run=False, confirm=True, irreversible=True, plan={}
    )
    assert r is None  # cleared to touch the node


def test_gate_reversible_action_ignores_confirm() -> None:
    # receive-side (irreversible=False) does not require confirm
    r = _assert_send_allowed(
        "x", cfg=_cfg(True), dry_run=False, confirm=False, irreversible=False, plan={}
    )
    assert r is None


def test_gate_preserves_plan_in_terminal_result() -> None:
    plan = {"value_sat": 1000}
    r = _assert_send_allowed(
        "create_invoice", cfg=_cfg(False), dry_run=True, confirm=True, irreversible=False, plan=plan
    )
    assert r is not None and r.plan == plan


def test_reflection_no_public_write_bypasses_send_gate() -> None:
    """B-002 structural invariant: EVERY value-layer-defined public async function
    must route through ``_assert_send_allowed``. Adding a write method that forgets
    the gate fails here."""
    offenders: list[str] = []
    for name, fn in inspect.getmembers(vl, inspect.iscoroutinefunction):
        if name.startswith("_") or getattr(fn, "__module__", "") != vl.__name__:
            continue
        if "_assert_send_allowed" not in inspect.getsource(fn):
            offenders.append(name)
    assert offenders == [], f"write methods bypassing the central send-gate: {offenders}"
