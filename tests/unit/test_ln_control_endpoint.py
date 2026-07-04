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
    # Isolate the daily-cap input from the shared ops ledger (other tests append
    # executed spends to the default path) so the policy decision is deterministic.
    monkeypatch.setattr(lc, "spent_today_sat", lambda: 0)


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


# --- pay_invoice amount is parsed from the BOLT11 (Audit-P0 completion) ------------
# A 25 000-sat invoice: HRP "lnbc250u1" → 250 * 100_000 msat = 25 000 sat.
_INV_25K = "lnbc250u1pjfaketestinvoicexxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
_INV_AMOUNTLESS = "lnbc1pjfaketestinvoicexxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


def test_pay_invoice_amount_over_cap_needs_confirm(monkeypatch) -> None:
    # Without amount-parsing the policy saw 0 → auto_execute (covert spend hole).
    # Now the 25k invoice exceeds the 10k per-action cap → needs_confirm (HOTP).
    env = PolicyEnvelope(
        allowed_actions=frozenset({"pay_invoice"}),
        per_action_cap_sat=10_000,
        daily_cap_sat=1_000_000,
    )
    _patch(monkeypatch, env)
    r = TestClient(_app()).post(
        _URL, json={"action": "pay_invoice", "params": {"payment_request": _INV_25K}}
    )
    assert r.status_code == 200
    assert r.json()["policy"]["decision"] == "needs_confirm"


def test_pay_invoice_breaches_reserve_floor_is_denied(monkeypatch) -> None:
    # The reserve-floor backstop now applies to pay_invoice: balance 1_000_000 −
    # 25_000 = 975_000 < 990_000 floor → hard denied (was silently bypassed at amount=0).
    env = PolicyEnvelope(
        allowed_actions=frozenset({"pay_invoice"}),
        per_action_cap_sat=1_000_000,
        daily_cap_sat=1_000_000,
        reserve_floor_sat=990_000,
    )
    _patch(monkeypatch, env)
    r = TestClient(_app()).post(
        _URL, json={"action": "pay_invoice", "params": {"payment_request": _INV_25K}}
    )
    assert r.status_code == 200
    assert r.json()["policy"]["decision"] == "denied"


def test_pay_invoice_amountless_forces_confirm(monkeypatch) -> None:
    # Amountless invoice → amount unknown → fail-closed to needs_confirm even under
    # generous caps (never auto-execute an unbounded spend).
    env = PolicyEnvelope(
        allowed_actions=frozenset({"pay_invoice"}),
        per_action_cap_sat=1_000_000,
        daily_cap_sat=1_000_000,
    )
    _patch(monkeypatch, env)
    r = TestClient(_app()).post(
        _URL, json={"action": "pay_invoice", "params": {"payment_request": _INV_AMOUNTLESS}}
    )
    assert r.status_code == 200
    b = r.json()
    assert b["policy"]["decision"] == "needs_confirm"
    assert "amount unknown" in b["policy"]["reason"]


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
