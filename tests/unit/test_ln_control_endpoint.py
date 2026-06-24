"""Sprint 5 — value-action control endpoint (plan/execute, policy + B-005, inert).

Covers: plan mode returns the plan + policy verdict + plan_hash; execute is denied
for a disallowed action; an in-envelope auto_execute runs straight through but stays
INERT (pay_enabled off → disabled); a needs_confirm execute with a mismatched
plan-hash is rejected (B-005) WITHOUT touching the node.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import ln_control as lc
from app.lightning.policy import PolicyEnvelope

_URL = "/dashboard/api/ln/value-action"


def _app() -> FastAPI:
    a = FastAPI()
    a.include_router(lc.router)
    return a


async def _bal_million() -> int:
    return 1_000_000


def _patch(monkeypatch, envelope: PolicyEnvelope) -> None:
    lc.reset_control_state()
    monkeypatch.setattr(lc.PolicyStore, "load", lambda self: envelope)
    monkeypatch.setattr(lc, "_available_balance_sat", _bal_million)


def test_plan_mode_returns_plan_decision_and_hash(monkeypatch) -> None:
    _patch(monkeypatch, PolicyEnvelope.default())  # deny everything
    r = TestClient(_app()).post(
        _URL, json={"action": "send_coins", "params": {"addr": "bc1q", "amount_sat": 1000}}
    )
    assert r.status_code == 200
    b = r.json()
    assert b["mode"] == "plan"
    assert b["policy"]["decision"] == "denied"  # default envelope denies
    assert len(b["plan_hash"]) == 64
    assert b["plan"]["state"] == "disabled"  # inert: pay_enabled off → node never touched


def test_execute_denied_for_disallowed_action(monkeypatch) -> None:
    _patch(monkeypatch, PolicyEnvelope.default())
    r = TestClient(_app()).post(
        _URL,
        json={
            "action": "send_coins",
            "params": {"addr": "bc1q", "amount_sat": 1000},
            "confirm": {"hotp": "x", "plan_hash": "y", "idempotency_key": "k"},
        },
    )
    assert r.status_code == 403 and "policy denied" in r.json()["detail"]


def test_execute_auto_within_envelope_is_inert(monkeypatch) -> None:
    env = PolicyEnvelope(
        allowed_actions=frozenset({"send_coins"}), per_action_cap_sat=10_000, daily_cap_sat=50_000
    )
    _patch(monkeypatch, env)
    r = TestClient(_app()).post(
        _URL,
        json={
            "action": "send_coins",
            "params": {"addr": "bc1q", "amount_sat": 1000},
            "confirm": {"hotp": "x", "plan_hash": "y", "idempotency_key": "k"},
        },
    )
    assert r.status_code == 200
    b = r.json()
    # auto_execute needs NO HOTP (max automation), but stays INERT (pay_enabled off).
    assert b["mode"] == "execute" and b["result"]["state"] == "disabled"


def test_execute_needs_confirm_rejects_bad_plan_hash(monkeypatch) -> None:
    # cap below the amount → needs_confirm; a wrong plan_hash is rejected before HOTP.
    env = PolicyEnvelope(
        allowed_actions=frozenset({"send_coins"}), per_action_cap_sat=100, daily_cap_sat=50_000
    )
    _patch(monkeypatch, env)
    r = TestClient(_app()).post(
        _URL,
        json={
            "action": "send_coins",
            "params": {"addr": "bc1q", "amount_sat": 1000},
            "confirm": {"hotp": "x", "plan_hash": "WRONG", "idempotency_key": "k"},
        },
    )
    assert r.status_code == 403 and "confirm rejected" in r.json()["detail"]
