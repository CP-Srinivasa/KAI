"""KAI Truth Oracle router — L402 gating end-to-end (mocked node, no capital).

Asserts: disabled → 503; unpaid → 402 with an L402 invoice challenge; valid
paid token → 200 with the sovereign fact. No network, no funds.
"""

from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import truth_oracle
from app.lightning.demand_ledger import ACCESS_GRANTED, CHALLENGE_MINTED, requester_fingerprint
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


# --- U2 demand telemetry ---------------------------------------------------------


def test_unpaid_request_logs_challenge_minted_with_fingerprint(client: TestClient) -> None:
    """An unpaid request logs ``challenge_minted`` with the salted requester
    fingerprint (resolved from X-Forwarded-For behind the proxy), NOT a raw IP."""
    inv = ValueLayerResult(
        "create_invoice",
        "executed",
        "",
        response={
            "r_hash": base64.b64encode(bytes.fromhex(_PH_HEX)).decode(),
            "payment_request": "lnbc10n1...",
        },
    )
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kw: Any) -> bool:
        events.append((event, kw))
        return True

    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch.object(truth_oracle, "create_invoice", AsyncMock(return_value=inv)),
        patch.object(truth_oracle, "append_demand_event", _capture),
    ):
        r = client.get("/oracle/onchain-facts", headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 402
    minted = [kw for ev, kw in events if ev == CHALLENGE_MINTED]
    assert len(minted) == 1
    kw = minted[0]
    assert kw["scope"] == "onchain-facts" and kw["payment_hash"] == _PH_HEX
    assert kw["requester_fp"] and kw["requester_fp"] != "203.0.113.9"
    assert kw["requester_fp"] == requester_fingerprint("203.0.113.9", secret=_SECRET)


def test_paid_request_logs_access_granted(client: TestClient) -> None:
    token = mint_token(_PH_HEX, secret=_SECRET, scope="onchain-facts")
    chain = SimpleNamespace(chain="main", blocks=954871, synced=True, fee_sat_vb=1.2, mempool_tx=42)
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kw: Any) -> bool:
        events.append((event, kw))
        return True

    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch("app.chain.cache.get_cached_chain_status", AsyncMock(return_value=(chain, 5.0))),
        patch.object(truth_oracle, "append_demand_event", _capture),
    ):
        r = client.get(
            "/oracle/onchain-facts", headers={"Authorization": f"L402 {token}:{_PREIMAGE}"}
        )
    assert r.status_code == 200
    granted = [kw for ev, kw in events if ev == ACCESS_GRANTED]
    assert len(granted) == 1 and granted[0]["payment_hash"] == _PH_HEX


# --- /oracle/verdicts — the auditable falsification-verdict product (Stage 3) -----


def test_verdicts_unpaid_returns_402_challenge(client: TestClient) -> None:
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
        r = client.get("/oracle/verdicts")
    assert r.status_code == 402
    assert r.headers.get("WWW-Authenticate", "").startswith("L402 ")


def test_verdicts_paid_returns_attested_list(client: TestClient) -> None:
    token = mint_token(_PH_HEX, secret=_SECRET, scope="verdicts")
    rows = [
        {
            "file": "20260702_momentum.json",
            "hypothesis": "momentum_24h",
            "verdict": "FAILED",
            "prereg_id": "pid1",
            "generated_at_utc": "2026-07-02T00:00:00+00:00",
            "code_version": "deadbee",
            "attestation_hash": "a" * 64,
        }
    ]
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch("app.research.verdict_report.list_verdict_reports", return_value=rows),
        patch("app.truth.ledger.verify_ledger", return_value={"ok": True, "records": 5}),
    ):
        r = client.get(
            "/oracle/verdicts",
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "kai_falsification_platform"
    assert body["count"] == 1
    assert body["verdicts"][0]["attestation_hash"] == "a" * 64
    assert body["attestation_ledger"] == {"chain_ok": True, "records": 5}


def test_verdicts_paid_wrong_scope_is_rechallenged(client: TestClient) -> None:
    token = mint_token(_PH_HEX, secret=_SECRET, scope="onchain-facts")  # wrong scope
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
            "/oracle/verdicts",
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert r.status_code == 402  # scope mismatch → re-challenge
