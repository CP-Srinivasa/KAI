"""KAI Truth Oracle router — L402 gating end-to-end (mocked node, no capital).

Asserts: disabled → 503; unpaid → 402 with an L402 invoice challenge; valid
paid token → 200 with the sovereign fact. No network, no funds.
"""

from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import truth_oracle
from app.lightning.l402 import mint_token
from app.lightning.value_layer import ValueLayerResult

_SECRET = "oracle-test-secret"
_PREIMAGE = "33" * 32
_PH_HEX = hashlib.sha256(bytes.fromhex(_PREIMAGE)).hexdigest()


def _settings(*, enabled: bool, secret: str = _SECRET) -> SimpleNamespace:
    return SimpleNamespace(
        lightning=SimpleNamespace(
            l402_enabled=enabled,
            l402_secret=secret,
            l402_default_price_sat=10,
            # S-002 mint caps (generous here so single-request tests never hit them).
            l402_mint_per_min=100,
            l402_mint_budget_per_min=100,
        )
    )


@pytest.fixture
def client() -> TestClient:
    truth_oracle.reset_mint_limiter()  # fresh per-test limiter (module-level state)
    app = FastAPI()
    app.include_router(truth_oracle.router)
    return TestClient(app, raise_server_exceptions=False)


def test_disabled_returns_503(client: TestClient) -> None:
    with patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=False)):
        r = client.get("/oracle/onchain-facts")
    assert r.status_code == 503


def test_unpaid_returns_402_with_invoice_challenge(client: TestClient) -> None:
    inv = ValueLayerResult(
        "create_invoice",
        "executed",
        "",
        response={
            "r_hash": base64.b64encode(bytes.fromhex(_PH_HEX)).decode(),
            "payment_request": "lnbc10n1...",
        },
    )
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch.object(truth_oracle, "create_invoice", AsyncMock(return_value=inv)),
    ):
        r = client.get("/oracle/onchain-facts")
    assert r.status_code == 402
    wa = r.headers.get("WWW-Authenticate", "")
    assert wa.startswith("L402 ") and 'invoice="lnbc10n1..."' in wa and "token=" in wa


def test_paid_token_returns_facts(client: TestClient) -> None:
    token = mint_token(_PH_HEX, secret=_SECRET, scope="onchain-facts")
    chain = SimpleNamespace(chain="main", blocks=954871, synced=True, fee_sat_vb=1.2, mempool_tx=42)
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch("app.chain.cache.get_cached_chain_status", AsyncMock(return_value=(chain, 5.0))),
    ):
        r = client.get(
            "/oracle/onchain-facts",
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["block_height"] == 954871 and body["fee_sat_vb"] == 1.2
    assert body["source"] == "kai_sovereign_bitcoind"


def test_paid_wrong_scope_is_rechallenged(client: TestClient) -> None:
    # A token minted for a different scope must NOT unlock onchain-facts.
    token = mint_token(_PH_HEX, secret=_SECRET, scope="timestamp")
    inv = ValueLayerResult(
        "create_invoice",
        "executed",
        "",
        response={
            "r_hash": base64.b64encode(bytes.fromhex(_PH_HEX)).decode(),
            "payment_request": "lnbc10n1...",
        },
    )
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch.object(truth_oracle, "create_invoice", AsyncMock(return_value=inv)),
    ):
        r = client.get(
            "/oracle/onchain-facts",
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert r.status_code == 402  # scope mismatch → re-challenge
