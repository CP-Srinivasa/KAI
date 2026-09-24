"""KAI Truth Oracle router — L402 gating end-to-end (mocked node, no capital).

Asserts: disabled → 503; unpaid → 402 with an L402 invoice challenge; valid
paid token → 200 with the sovereign fact. No network, no funds.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.api.routers import truth_oracle
from app.lightning.demand_ledger import (
    ACCESS_GRANTED,
    CHALLENGE_MINTED,
    PAID_UNAVAILABLE,
    requester_fingerprint,
)
from app.lightning.l402 import mint_token
from app.lightning.receive_gate import ValueLayerResult

_SECRET = "oracle-test-secret"
_PREIMAGE = "33" * 32
_PH_HEX = hashlib.sha256(bytes.fromhex(_PREIMAGE)).hexdigest()


def _settings(
    *,
    enabled: bool,
    secret: str = _SECRET,
    mint_per_min: int = 100,
    mint_budget_per_min: int = 100,
) -> SimpleNamespace:
    return SimpleNamespace(
        integrity=SimpleNamespace(
            enabled=True,
            stamper="opentimestamps",
            proofs_dir="monitor/integrity",
            timestamp_jobs_dir="monitor/integrity/uc3_timestamp_jobs",
        ),
        lightning=SimpleNamespace(
            l402_enabled=enabled,
            l402_secret=secret,
            l402_default_price_sat=10,
            # S-002 mint caps (generous here so single-request tests never hit them).
            l402_mint_per_min=mint_per_min,
            l402_mint_budget_per_min=mint_budget_per_min,
        ),
    )


def _healthy_chain(*, blocks: int = 954871) -> SimpleNamespace:
    return SimpleNamespace(
        state="ok",
        reachable=True,
        chain="main",
        blocks=blocks,
        headers=blocks,
        best_block_hash="ab" * 32,
        synced=True,
        fee_sat_vb=1.2,
        mempool_tx=42,
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
    assert r.json()["detail"] == "truth oracle disabled"


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
        patch(
            "app.chain.cache.get_cached_chain_status",
            AsyncMock(return_value=(_healthy_chain(), 5.0)),
        ),
    ):
        r = client.get("/oracle/onchain-facts")
    assert r.status_code == 402
    wa = r.headers.get("WWW-Authenticate", "")
    assert wa.startswith("L402 ") and 'invoice="lnbc10n1..."' in wa and "token=" in wa


def test_paid_token_returns_facts(client: TestClient) -> None:
    token = mint_token(_PH_HEX, secret=_SECRET, scope="onchain-facts")
    chain = _healthy_chain()
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
    assert body["headers"] == 954871 and body["observed_at_utc"].endswith("+00:00")
    assert body["best_block_hash"] == "ab" * 32
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
        patch(
            "app.chain.cache.get_cached_chain_status",
            AsyncMock(return_value=(_healthy_chain(), 5.0)),
        ),
    ):
        r = client.get(
            "/oracle/onchain-facts",
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert r.status_code == 402  # scope mismatch → re-challenge


# --- /oracle/timestamp — digest-bound, durable UC-3 authorization ---------------


def test_timestamp_unpaid_challenge_is_bound_to_requested_digest(client: TestClient) -> None:
    digest = "ab" * 32
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
        patch("app.integrity.timestamp_jobs.TimestampJobStore") as store_cls,
    ):
        store_cls.return_value.has_capacity.return_value = True
        response = client.post("/oracle/timestamp", json={"sha256_hex": digest.upper()})
    assert response.status_code == 402
    header = response.headers["WWW-Authenticate"]
    token = header.split('token="', 1)[1].split('"', 1)[0]
    from app.lightning.l402 import verify

    verdict = verify(token, _PREIMAGE, secret=_SECRET)
    assert verdict.valid and verdict.scope == f"timestamp:{digest}"


def test_timestamp_disabled_configuration_never_mints(client: TestClient) -> None:
    settings = _settings(enabled=True)
    settings.integrity.enabled = False
    mint = AsyncMock()
    with (
        patch.object(truth_oracle, "get_settings", return_value=settings),
        patch.object(truth_oracle, "create_invoice", mint),
    ):
        response = client.post("/oracle/timestamp", json={"sha256_hex": "ab" * 32})
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "timestamp_service_disabled",
        "retriable": False,
    }
    mint.assert_not_awaited()


def test_timestamp_digest_stays_out_of_invoice_memo_and_demand_scope(
    client: TestClient,
) -> None:
    digest = "ab" * 32
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
    store = MagicMock()
    store.has_capacity.return_value = True
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch.object(truth_oracle, "create_invoice", AsyncMock(return_value=inv)) as mint,
        patch.object(
            truth_oracle,
            "append_demand_event",
            lambda event, **payload: events.append((event, payload)),
        ),
        patch("app.integrity.timestamp_jobs.TimestampJobStore", return_value=store),
    ):
        response = client.post("/oracle/timestamp", json={"sha256_hex": digest})
    assert response.status_code == 402
    assert mint.await_args.kwargs["memo"] == "kai-oracle:timestamp"
    assert events[0][1]["scope"] == "timestamp"
    assert digest not in str(events)


def test_timestamp_capacity_is_rejected_before_invoice_mint(client: TestClient) -> None:
    digest = "ab" * 32
    store = MagicMock()
    store.has_capacity.return_value = False
    mint = AsyncMock()
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch.object(truth_oracle, "create_invoice", mint),
        patch("app.integrity.timestamp_jobs.TimestampJobStore", return_value=store),
    ):
        response = client.post("/oracle/timestamp", json={"sha256_hex": digest})
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "timestamp_capacity_exhausted",
        "retriable": False,
    }
    mint.assert_not_awaited()


def test_paid_timestamp_capacity_race_is_non_retriable_and_not_granted(
    client: TestClient,
) -> None:
    from app.integrity.timestamp_jobs import TimestampJobCapacityError

    digest = "ab" * 32
    token = mint_token(_PH_HEX, secret=_SECRET, scope=f"timestamp:{digest}")
    store = MagicMock()
    store.submit.side_effect = TimestampJobCapacityError("full")
    events: list[str] = []
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch.object(
            truth_oracle,
            "append_demand_event",
            lambda event, **_payload: events.append(event),
        ),
        patch("app.integrity.timestamp_jobs.TimestampJobStore", return_value=store),
    ):
        response = client.post(
            "/oracle/timestamp",
            json={"sha256_hex": digest},
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert response.status_code == 503
    assert response.json()["detail"]["retriable"] is False
    assert ACCESS_GRANTED not in events


def test_timestamp_paid_response_is_pending_not_mined_finality(client: TestClient) -> None:
    digest = "ab" * 32
    token = mint_token(_PH_HEX, secret=_SECRET, scope=f"timestamp:{digest}")
    proof = b"ots-calendar-commitment"
    store = MagicMock()
    store.submit.return_value = (
        {"proof_sha256": hashlib.sha256(proof).hexdigest(), "state": "pending_bitcoin"},
        proof,
    )
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch("app.integrity.timestamp_jobs.TimestampJobStore", return_value=store) as store_cls,
    ):
        response = client.post(
            "/oracle/timestamp",
            json={"sha256_hex": digest},
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "pending_bitcoin"
    assert "calendar commitment only" in response.json()["note"]
    store_cls.assert_called_once_with(Path("monitor/integrity/uc3_timestamp_jobs"))
    store.submit.assert_called_once_with(payment_hash=_PH_HEX, digest=digest)


@pytest.mark.parametrize(
    ("failure", "status_code", "detail"),
    [
        (
            "conflict",
            409,
            "payment already bound to another digest",
        ),
        (
            "unavailable",
            503,
            {"code": "timestamp_pending_retry", "retriable": True},
        ),
    ],
)
def test_timestamp_store_failures_have_stable_http_mapping(
    client: TestClient, failure: str, status_code: int, detail: object
) -> None:
    from app.integrity.timestamp_jobs import (
        TimestampJobConflictError,
        TimestampJobUnavailableError,
    )

    digest = "ab" * 32
    token = mint_token(_PH_HEX, secret=_SECRET, scope=f"timestamp:{digest}")
    store = MagicMock()
    store.submit.side_effect = (
        TimestampJobConflictError("bound")
        if failure == "conflict"
        else TimestampJobUnavailableError("calendar")
    )
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch("app.integrity.timestamp_jobs.TimestampJobStore", return_value=store),
    ):
        response = client.post(
            "/oracle/timestamp",
            json={"sha256_hex": digest},
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert response.status_code == status_code
    assert response.json()["detail"] == detail


def test_timestamp_token_for_other_digest_cannot_submit(client: TestClient) -> None:
    paid_digest = "ab" * 32
    requested_digest = "cd" * 32
    token = mint_token(_PH_HEX, secret=_SECRET, scope=f"timestamp:{paid_digest}")
    inv = ValueLayerResult(
        "create_invoice",
        "executed",
        "",
        response={
            "r_hash": base64.b64encode(bytes.fromhex(_PH_HEX)).decode(),
            "payment_request": "lnbc10n1...",
        },
    )
    store = MagicMock()
    store.has_binding.return_value = False
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch.object(truth_oracle, "create_invoice", AsyncMock(return_value=inv)),
        patch("app.integrity.timestamp_jobs.TimestampJobStore", return_value=store),
    ):
        response = client.post(
            "/oracle/timestamp",
            json={"sha256_hex": requested_digest},
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert response.status_code == 402
    store.submit.assert_not_called()


def test_timestamp_expired_token_cannot_submit(client: TestClient) -> None:
    digest = "ab" * 32
    token = mint_token(_PH_HEX, secret=_SECRET, scope=f"timestamp:{digest}", ttl_s=-1)
    inv = ValueLayerResult(
        "create_invoice",
        "executed",
        "",
        response={
            "r_hash": base64.b64encode(bytes.fromhex(_PH_HEX)).decode(),
            "payment_request": "lnbc10n1...",
        },
    )
    store = MagicMock()
    store.has_binding.return_value = False
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch.object(truth_oracle, "create_invoice", AsyncMock(return_value=inv)),
        patch("app.integrity.timestamp_jobs.TimestampJobStore", return_value=store),
    ):
        response = client.post(
            "/oracle/timestamp",
            json={"sha256_hex": digest},
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert response.status_code == 402
    store.submit.assert_not_called()


def test_timestamp_expired_token_replays_existing_paid_job_without_new_invoice(
    client: TestClient,
) -> None:
    digest = "ab" * 32
    token = mint_token(_PH_HEX, secret=_SECRET, scope=f"timestamp:{digest}", ttl_s=-1)
    proof = b"persisted-proof"
    store = MagicMock()
    store.has_binding.return_value = True
    store.submit.return_value = (
        {"proof_sha256": hashlib.sha256(proof).hexdigest(), "state": "pending_bitcoin"},
        proof,
    )
    mint = AsyncMock()
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch.object(truth_oracle, "create_invoice", mint),
        patch("app.integrity.timestamp_jobs.TimestampJobStore", return_value=store),
    ):
        response = client.post(
            "/oracle/timestamp",
            json={"sha256_hex": digest},
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert response.status_code == 200
    mint.assert_not_awaited()
    store.has_binding.assert_called_once_with(payment_hash=_PH_HEX, digest=digest)


@pytest.mark.asyncio
async def test_timestamp_calendar_wait_does_not_block_api_event_loop() -> None:
    digest = "ab" * 32
    token = mint_token(_PH_HEX, secret=_SECRET, scope=f"timestamp:{digest}")
    started = threading.Event()
    proof = b"slow-calendar-proof"

    class SlowStore:
        def submit(self, **_kwargs):
            started.set()
            time.sleep(0.15)
            return {
                "proof_sha256": hashlib.sha256(proof).hexdigest(),
                "state": "pending_bitcoin",
            }, proof

    app = FastAPI()
    app.include_router(truth_oracle.router)
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch("app.integrity.timestamp_jobs.TimestampJobStore", return_value=SlowStore()),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as async_client:
            request_task = asyncio.create_task(
                async_client.post(
                    "/oracle/timestamp",
                    json={"sha256_hex": digest},
                    headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
                )
            )
            assert await asyncio.to_thread(started.wait, 1.0)
            before = asyncio.get_running_loop().time()
            await asyncio.sleep(0.01)
            assert asyncio.get_running_loop().time() - before < 0.08
            response = await request_task
    assert response.status_code == 200


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
        patch(
            "app.chain.cache.get_cached_chain_status",
            AsyncMock(return_value=(_healthy_chain(), 5.0)),
        ),
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
    chain = _healthy_chain()
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


@pytest.mark.parametrize("age", [0.0, 60.0, 120.0])
def test_onchain_facts_accepts_cache_age_through_refresh_grace(
    client: TestClient, age: float
) -> None:
    token = mint_token(_PH_HEX, secret=_SECRET, scope="onchain-facts")
    chain = _healthy_chain()
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch("app.chain.cache.get_cached_chain_status", AsyncMock(return_value=(chain, age))),
    ):
        response = client.get(
            "/oracle/onchain-facts",
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert response.status_code == 200


def test_paid_unavailable_is_logged_without_access_grant(client: TestClient) -> None:
    token = mint_token(_PH_HEX, secret=_SECRET, scope="onchain-facts")
    events: list[tuple[str, dict[str, Any]]] = []

    def capture(event: str, **payload: Any) -> bool:
        events.append((event, payload))
        return True

    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch.object(truth_oracle, "append_demand_event", capture),
        patch(
            "app.chain.cache.get_cached_chain_status",
            AsyncMock(return_value=(_healthy_chain(), 120.0001)),
        ),
    ):
        response = client.get(
            "/oracle/onchain-facts",
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )

    assert response.status_code == 503
    assert [event for event, _ in events] == [PAID_UNAVAILABLE]
    assert events[0][1]["payment_hash"] == _PH_HEX


@pytest.mark.parametrize(
    ("chain", "age", "expected_state"),
    [
        (
            SimpleNamespace(
                state="pending", reachable=False, synced=False, chain="", blocks=0, headers=0
            ),
            None,
            "pending",
        ),
        (
            SimpleNamespace(
                state="disabled", reachable=False, synced=False, chain="", blocks=0, headers=0
            ),
            None,
            "disabled",
        ),
        (
            SimpleNamespace(
                state="unavailable", reachable=False, synced=False, chain="", blocks=0, headers=0
            ),
            None,
            "unavailable",
        ),
        (
            SimpleNamespace(
                state="ok", reachable=True, synced=False, chain="main", blocks=100, headers=101
            ),
            1.0,
            "not_ready",
        ),
        (
            SimpleNamespace(
                state="ok", reachable=True, synced=True, chain="main", blocks=100, headers=101
            ),
            1.0,
            "not_ready",
        ),
        (
            SimpleNamespace(
                state="ok", reachable=True, synced=True, chain="main", blocks=0, headers=0
            ),
            1.0,
            "not_ready",
        ),
        (
            SimpleNamespace(
                state="ok", reachable=True, synced=True, chain="main", blocks=100, headers=100
            ),
            1.0,
            "not_ready",
        ),
        (
            SimpleNamespace(
                state="ok", reachable=True, synced=True, chain="main", blocks=100, headers=100
            ),
            121.0,
            "stale",
        ),
        (_healthy_chain(), float("nan"), "not_ready"),
        (_healthy_chain(), float("inf"), "not_ready"),
        (_healthy_chain(), -1.0, "not_ready"),
        (_healthy_chain(), True, "not_ready"),
        (_healthy_chain(), None, "not_ready"),
    ],
)
def test_unready_chain_never_mints_or_serves_paid_200(
    client: TestClient, chain: SimpleNamespace, age: float | None, expected_state: str
) -> None:
    mint = AsyncMock()
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch.object(truth_oracle, "create_invoice", mint),
        patch("app.chain.cache.get_cached_chain_status", AsyncMock(return_value=(chain, age))),
    ):
        unpaid = client.get("/oracle/onchain-facts")
        token = mint_token(_PH_HEX, secret=_SECRET, scope="onchain-facts")
        paid = client.get(
            "/oracle/onchain-facts",
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert unpaid.status_code == 503 and paid.status_code == 503
    assert paid.json()["detail"] == {
        "code": "onchain_facts_unavailable",
        "state": expected_state,
        "retriable": True,
    }
    assert paid.headers["Retry-After"] == "5"
    mint.assert_not_awaited()


def _healthy_except(**overrides: Any) -> SimpleNamespace:
    chain = _healthy_chain()
    for field, value in overrides.items():
        setattr(chain, field, value)
    return chain


@pytest.mark.parametrize(
    "overrides",
    [
        {"reachable": False},
        {"synced": False},
        {"chain": ""},
        {"blocks": 0, "headers": 0},
        {"headers": 954871 + 1},
        {"headers": 954871 - 1},
        {"best_block_hash": "zz" * 32},
        {"best_block_hash": "ab" * 31},
    ],
    ids=[
        "unreachable",
        "not_synced",
        "empty_chain",
        "zero_blocks",
        "header_ahead",
        "header_behind",
        "non_hex_hash",
        "short_hash",
    ],
)
def test_each_readiness_condition_alone_blocks_paid_200(
    client: TestClient, overrides: dict[str, Any]
) -> None:
    """Exactly one broken field on an otherwise healthy snapshot must fail closed.

    Earlier negative cases lacked ``best_block_hash`` and failed on the hash
    check alone, so six readiness conditions could be deleted without a red test.
    """
    chain = _healthy_except(**overrides)
    token = mint_token(_PH_HEX, secret=_SECRET, scope="onchain-facts")
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch("app.chain.cache.get_cached_chain_status", AsyncMock(return_value=(chain, 1.0))),
    ):
        paid = client.get(
            "/oracle/onchain-facts",
            headers={"Authorization": f"L402 {token}:{_PREIMAGE}"},
        )
    assert paid.status_code == 503
    assert paid.json()["detail"]["state"] == "not_ready"


def test_same_paid_token_retries_after_chain_recovers_without_new_invoice(
    client: TestClient,
) -> None:
    token = mint_token(_PH_HEX, secret=_SECRET, scope="onchain-facts")
    unavailable = SimpleNamespace(
        state="pending", reachable=False, synced=False, chain="", blocks=0, headers=0
    )
    healthy = SimpleNamespace(
        state="ok",
        reachable=True,
        synced=True,
        chain="main",
        blocks=101,
        headers=101,
        best_block_hash="cd" * 32,
        fee_sat_vb=None,
        mempool_tx=0,
    )
    cached = AsyncMock(side_effect=[(unavailable, None), (healthy, 2.0)])
    mint = AsyncMock()
    with (
        patch.object(truth_oracle, "get_settings", return_value=_settings(enabled=True)),
        patch.object(truth_oracle, "create_invoice", mint),
        patch("app.chain.cache.get_cached_chain_status", cached),
    ):
        first = client.get(
            "/oracle/onchain-facts", headers={"Authorization": f"L402 {token}:{_PREIMAGE}"}
        )
        retry = client.get(
            "/oracle/onchain-facts", headers={"Authorization": f"L402 {token}:{_PREIMAGE}"}
        )
    assert first.status_code == 503
    assert retry.status_code == 200 and retry.json()["block_height"] == 101
    mint.assert_not_awaited()


def test_unready_chain_does_not_consume_invoice_mint_budget(client: TestClient) -> None:
    """A stale response must leave the single available mint slot untouched."""
    stale = _healthy_chain()
    healthy = _healthy_chain(blocks=stale.blocks + 1)
    cached = AsyncMock(side_effect=[(stale, 121.0), (healthy, 1.0)])
    inv = ValueLayerResult(
        "create_invoice",
        "executed",
        "",
        response={
            "r_hash": base64.b64encode(bytes.fromhex(_PH_HEX)).decode(),
            "payment_request": "lnbc10n1...",
        },
    )
    mint = AsyncMock(return_value=inv)
    settings = _settings(enabled=True, mint_per_min=1, mint_budget_per_min=1)

    with (
        patch.object(truth_oracle, "get_settings", return_value=settings),
        patch.object(truth_oracle, "create_invoice", mint),
        patch("app.chain.cache.get_cached_chain_status", cached),
    ):
        unavailable = client.get("/oracle/onchain-facts")
        challenge = client.get("/oracle/onchain-facts")

    assert unavailable.status_code == 503
    assert challenge.status_code == 402
    assert challenge.headers["WWW-Authenticate"].startswith("L402 ")
    mint.assert_awaited_once()


def test_onchain_facts_mint_limit_applies_with_healthy_node(client: TestClient) -> None:
    inv = ValueLayerResult(
        "create_invoice",
        "executed",
        "",
        response={
            "r_hash": base64.b64encode(bytes.fromhex(_PH_HEX)).decode(),
            "payment_request": "lnbc10n1...",
        },
    )
    settings = _settings(enabled=True, mint_per_min=1, mint_budget_per_min=1)
    mint = AsyncMock(return_value=inv)
    with (
        patch.object(truth_oracle, "get_settings", return_value=settings),
        patch.object(truth_oracle, "create_invoice", mint),
        patch(
            "app.chain.cache.get_cached_chain_status",
            AsyncMock(return_value=(_healthy_chain(), 1.0)),
        ),
    ):
        first = client.get("/oracle/onchain-facts")
        second = client.get("/oracle/onchain-facts")

    assert first.status_code == 402
    assert second.status_code == 429
    mint.assert_awaited_once()


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
