"""U4 — G0 demand evaluator: joins the demand ledger (who hit the paywall / paid)
with the earnings ledger (who actually settled), and renders the pre-registered G0
verdict.

Pre-registration (§5): G0-PASS = ≥3 settled kai-oracle:fee-series payments AND from
≥2 distinct requester fingerprints AND on ≥2 distinct calendar days. The
≥2-FP/≥2-day guard is what stops a single actor faking demand by self-paying 3×.
"""

from __future__ import annotations

from pathlib import Path

from app.lightning.demand_evaluator import evaluate_l402_demand
from app.lightning.demand_ledger import ACCESS_GRANTED, CHALLENGE_MINTED, append_demand_event
from app.lightning.earnings_ledger import append_ln_earning

# Two unix timestamps on different UTC days (2025-06-15 / 2025-06-16).
_DAY1 = 1750000000
_DAY2 = _DAY1 + 86_400


def _challenge(demand_p: Path, ph: str, fp: str) -> None:
    append_demand_event(
        CHALLENGE_MINTED,
        scope="fee-series",
        requester_fp=fp,
        price_sat=100,
        payment_hash=ph,
        path=demand_p,
    )


def _settled(earnings_p: Path, ph: str, settled_at: int) -> None:
    append_ln_earning(
        payment_hash=ph,
        amount_sat=100,
        source="oracle-l402",
        memo="kai-oracle:fee-series",
        settled_at=str(settled_at),
        path=earnings_p,
    )


def test_g0_pass_with_three_payments_two_fingerprints_two_days(tmp_path: Path) -> None:
    d, e = tmp_path / "demand.jsonl", tmp_path / "earn.jsonl"
    for ph, fp in [("a" * 64, "fpA"), ("b" * 64, "fpA"), ("c" * 64, "fpB")]:
        _challenge(d, ph, fp)
    _settled(e, "a" * 64, _DAY1)
    _settled(e, "b" * 64, _DAY2)
    _settled(e, "c" * 64, _DAY1)
    out = evaluate_l402_demand(demand_path=d, earnings_path=e)
    assert out["settled_payments"] == 3
    assert out["distinct_payer_fps"] == 2
    assert out["distinct_days"] == 2
    assert out["verdict"] == "G0-PASS"


def test_g0_no_pass_when_single_actor_self_pays(tmp_path: Path) -> None:
    """The fraud guard: 3 settled payments but all one fingerprint on one day → NO-PASS."""
    d, e = tmp_path / "demand.jsonl", tmp_path / "earn.jsonl"
    for ph in ("a" * 64, "b" * 64, "c" * 64):
        _challenge(d, ph, "fpA")
        _settled(e, ph, _DAY1)
    out = evaluate_l402_demand(demand_path=d, earnings_path=e)
    assert out["settled_payments"] == 3
    assert out["distinct_payer_fps"] == 1 and out["distinct_days"] == 1
    assert out["verdict"] == "NO-PASS"
    assert any("fingerprint" in r or "day" in r for r in out["reasons"])


def test_g0_no_pass_when_too_few_payments(tmp_path: Path) -> None:
    d, e = tmp_path / "demand.jsonl", tmp_path / "earn.jsonl"
    _challenge(d, "a" * 64, "fpA")
    _settled(e, "a" * 64, _DAY1)
    out = evaluate_l402_demand(demand_path=d, earnings_path=e)
    assert out["settled_payments"] == 1 and out["verdict"] == "NO-PASS"
    assert any("payment" in r for r in out["reasons"])


def test_window_excludes_payments_before_window_start(tmp_path: Path) -> None:
    d, e = tmp_path / "demand.jsonl", tmp_path / "earn.jsonl"
    _challenge(d, "a" * 64, "fpA")
    _settled(e, "a" * 64, _DAY1)  # 2025-06-15, before the window below
    out = evaluate_l402_demand(
        demand_path=d, earnings_path=e, window_start="2026-06-01", window_days=14
    )
    assert out["settled_payments"] == 0


def test_reports_interest_metrics(tmp_path: Path) -> None:
    d, e = tmp_path / "demand.jsonl", tmp_path / "empty.jsonl"
    _challenge(d, "a" * 64, "fpA")
    _challenge(d, "b" * 64, "fpB")
    append_demand_event(ACCESS_GRANTED, scope="fee-series", payment_hash="a" * 64, path=d)
    out = evaluate_l402_demand(demand_path=d, earnings_path=e)
    assert out["challenges"] == 2 and out["distinct_challenge_fps"] == 2
    assert out["access_granted"] == 1
    assert out["verdict"] == "NO-PASS"  # zero settled payments


def test_demand_endpoint_returns_verdict() -> None:
    """U4 surface: GET /dashboard/api/ln/demand returns the verdict + metrics."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routers import ln_control

    app = FastAPI()
    app.include_router(ln_control.router)
    r = TestClient(app).get("/dashboard/api/ln/demand")
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] in ("G0-PASS", "NO-PASS")
    assert "settled_payments" in body and "thresholds" in body
