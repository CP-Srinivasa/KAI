"""PR7 — in-process end-to-end harness for the L402 demand flow.

Proves that U1–U5 COMPOSE: a single round-trip drives the real oracle router, L402
token crypto, demand telemetry, earnings booking and the demand evaluator together,
with no real node (the only mock is the invoice MINT + the lnd ListInvoices, exactly
the two node touches). This is the CI-runnable proof; the real regtest/signet harness
(operator-run, needs lnd) is documented in docs/runbooks/ln_regtest_e2e.md.

Flow: GET /oracle/fee-series (unpaid) → 402 + challenge_minted → "pay" (we hold the
preimage) → retry with L402 token:preimage → 200 + fee-series + access_granted →
book the settled invoice → evaluate the demand verdict over the two ledgers.
"""

from __future__ import annotations

import base64
import hashlib
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import truth_oracle
from app.core.lightning_settings import LightningSettings
from app.lightning import demand_ledger
from app.lightning.demand_evaluator import evaluate_l402_demand
from app.lightning.earnings_booking import book_oracle_earnings
from app.lightning.value_layer import ValueLayerResult

_SECRET = "e2e-secret"
_PREIMAGE = "ab" * 32
_PH_HEX = hashlib.sha256(bytes.fromhex(_PREIMAGE)).hexdigest()
_R_HASH_B64 = base64.b64encode(bytes.fromhex(_PH_HEX)).decode()
_XFF = "203.0.113.50"

_FEE_RECORDS = [
    {"ts": 1750000000, "blocks": 900000, "fee_sat_vb": 1.5, "mempool_tx": 1200},
    {"ts": 1750000600, "blocks": 900001, "fee_sat_vb": 2.0, "mempool_tx": 1500},
]


def _oracle_settings() -> SimpleNamespace:
    return SimpleNamespace(
        lightning=SimpleNamespace(
            l402_enabled=True,
            l402_secret=_SECRET,
            l402_default_price_sat=100,
            l402_mint_per_min=100,
            l402_mint_budget_per_min=100,
        )
    )


def _token_from_challenge(www_authenticate: str) -> str:
    m = re.search(r'token="([^"]+)"', www_authenticate)
    assert m, f"no token in challenge: {www_authenticate!r}"
    return m.group(1)


@pytest.mark.asyncio
async def test_full_l402_demand_flow_in_process(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    demand_p = tmp_path / "demand.jsonl"
    earnings_p = tmp_path / "earnings.jsonl"
    # Redirect the demand ledger the oracle writes to (default path) into tmp.
    monkeypatch.setattr(demand_ledger, "_DEMAND_PATH", demand_p)

    truth_oracle.reset_mint_limiter()
    app = FastAPI()
    app.include_router(truth_oracle.router)
    client = TestClient(app, raise_server_exceptions=False)

    minted = ValueLayerResult(
        "create_invoice",
        "executed",
        "",
        response={"r_hash": _R_HASH_B64, "payment_request": "lnbc1u1pe2e..."},
    )

    with (
        patch.object(truth_oracle, "get_settings", return_value=_oracle_settings()),
        patch.object(truth_oracle, "create_invoice", AsyncMock(return_value=minted)),
        patch("app.signals.l2_features.read_onchain_fee_shadow", return_value=_FEE_RECORDS),
    ):
        # 1) unpaid → 402 with an L402 invoice challenge
        r1 = client.get("/oracle/fee-series", headers={"X-Forwarded-For": _XFF})
        assert r1.status_code == 402
        token = _token_from_challenge(r1.headers["WWW-Authenticate"])

        # 2) pay (we hold the preimage) → retry → 200 + real fee-series facts
        r2 = client.get(
            "/oracle/fee-series",
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}", "X-Forwarded-For": _XFF},
        )
        assert r2.status_code == 200
        body = r2.json()
        assert body["source"] == "kai_sovereign_bitcoind" and body["count"] == 2

    # 3) telemetry: both demand events recorded, fingerprint (not raw IP), bound to the hash
    events = demand_ledger.read_recent_demand_events(demand_p, limit=0)
    minted_ev = [e for e in events if e["event"] == demand_ledger.CHALLENGE_MINTED]
    granted_ev = [e for e in events if e["event"] == demand_ledger.ACCESS_GRANTED]
    assert len(minted_ev) == 1 and minted_ev[0]["payment_hash"] == _PH_HEX
    assert minted_ev[0]["requester_fp"] and minted_ev[0]["requester_fp"] != _XFF
    assert len(granted_ev) == 1 and granted_ev[0]["payment_hash"] == _PH_HEX

    # 4) the paid invoice settles on the node → earnings booking books it once
    settled = {
        "memo": "kai-oracle:fee-series",
        "settled": True,
        "r_hash": _R_HASH_B64,
        "amt_paid_sat": 100,
        "settle_date": "1750000000",
    }
    fake_client = MagicMock()
    fake_client.list_invoices = AsyncMock(return_value=[settled])
    with patch("app.lightning.earnings_booking._build_client", return_value=fake_client):
        booked = await book_oracle_earnings(path=earnings_p, cfg=LightningSettings(enabled=True))
    assert booked == 1

    # 5) the evaluator joins both ledgers — the round-trip is visible end-to-end
    out = evaluate_l402_demand(demand_path=demand_p, earnings_path=earnings_p)
    assert out["challenges"] == 1 and out["access_granted"] == 1
    assert out["settled_payments"] == 1 and out["distinct_payer_fps"] == 1
    # one payment is correctly NOT enough for G0 — but the full pipeline is proven
    assert out["verdict"] == "NO-PASS"
