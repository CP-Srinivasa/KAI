"""D-277 — Sendepfad auf RouterRPC (``POST /v2/router/send``, SendPaymentV2).

lnd hat ``POST /v1/channels/transactions`` (SendPaymentSync) in 0.20 als veraltet
markiert und entfernt es in 0.21. Der Client spricht deshalb nur noch den
Router-Endpunkt. Der antwortet als NDJSON-Strom (``{"result": {...}}`` je
Zustand, ``{"error": ...}`` bei Fehlern); mit ``no_inflight_updates`` kommt nur
der Endzustand. Der Client gibt EINE normalisierte Payment-Sicht zurueck —
``status``/``failure_reason`` sind lnd-Enums, kein Freitext.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.lightning import client as client_module
from app.lightning.client import (
    ROUTER_SEND_PATH,
    SEND_PAYMENT_TIMEOUT_SECONDS,
    LightningUnavailableError,
    LndRestClient,
)

BOLT11 = "lnbc10n1pexample"
PREIMAGE = "ab" * 32
PAYMENT_HASH = "cd" * 32


def _ndjson(*messages: dict[str, Any]) -> bytes:
    return "".join(json.dumps(m) + "\n" for m in messages).encode("utf-8")


def _client(handler) -> LndRestClient:
    return LndRestClient(
        base_url="https://node:8080",
        macaroon_hex="deadbeef",
        transport=httpx.MockTransport(handler),
    )


def _capture(status_code: int, content: bytes):
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(status_code, content=content)

    return handler, seen


# --------------------------------------------------------------------------- #
# pay_invoice — Wire
# --------------------------------------------------------------------------- #


async def test_pay_invoice_uses_the_router_endpoint_with_a_bounded_fee() -> None:
    handler, seen = _capture(
        200,
        _ndjson(
            {"result": {"status": "IN_FLIGHT", "payment_hash": PAYMENT_HASH}},
            {
                "result": {
                    "status": "SUCCEEDED",
                    "payment_hash": PAYMENT_HASH,
                    "payment_preimage": PREIMAGE,
                    "fee_sat": "3",
                    "value_sat": "1000",
                    "failure_reason": "FAILURE_REASON_NONE",
                }
            },
        ),
    )
    result = await _client(handler).pay_invoice(payment_request=BOLT11, fee_limit_sat=17)

    assert seen["path"] == ROUTER_SEND_PATH == "/v2/router/send"
    body = seen["body"]
    assert body["payment_request"] == BOLT11
    assert body["fee_limit_sat"] == "17"
    assert int(body["timeout_seconds"]) == SEND_PAYMENT_TIMEOUT_SECONDS > 0
    assert body["no_inflight_updates"] is True

    assert result == {
        "status": "SUCCEEDED",
        "payment_hash": PAYMENT_HASH,
        "payment_preimage": PREIMAGE,
        "fee_sat": 3,
        "value_sat": 1000,
        "failure_reason": "",
    }


async def test_pay_invoice_refuses_an_unbounded_fee_before_touching_the_node() -> None:
    handler, seen = _capture(200, b"")
    with pytest.raises(ValueError, match="fee_limit_sat"):
        await _client(handler).pay_invoice(payment_request=BOLT11, fee_limit_sat=0)
    assert seen == {}


async def test_a_failed_payment_keeps_the_lnd_failure_reason() -> None:
    handler, _ = _capture(
        200,
        _ndjson(
            {
                "result": {
                    "status": "FAILED",
                    "payment_hash": PAYMENT_HASH,
                    "failure_reason": "FAILURE_REASON_NO_ROUTE",
                    "fee_msat": "0",
                }
            }
        ),
    )
    result = await _client(handler).pay_invoice(payment_request=BOLT11, fee_limit_sat=5)
    assert result["status"] == "FAILED"
    assert result["failure_reason"] == "FAILURE_REASON_NO_ROUTE"
    assert result["payment_preimage"] == ""


async def test_fee_falls_back_to_msat_when_sat_is_absent() -> None:
    handler, _ = _capture(
        200,
        _ndjson(
            {
                "result": {
                    "status": "SUCCEEDED",
                    "payment_preimage": PREIMAGE,
                    "fee_msat": "2500",
                }
            }
        ),
    )
    result = await _client(handler).pay_invoice(payment_request=BOLT11, fee_limit_sat=5)
    assert result["fee_sat"] == 2


async def test_the_last_streamed_state_wins() -> None:
    handler, _ = _capture(
        200,
        _ndjson(
            {"result": {"status": "IN_FLIGHT"}},
            {"result": {"status": "IN_FLIGHT"}},
            {"result": {"status": "FAILED", "failure_reason": "FAILURE_REASON_TIMEOUT"}},
        ),
    )
    result = await _client(handler).pay_invoice(payment_request=BOLT11, fee_limit_sat=5)
    assert result["status"] == "FAILED"
    assert result["failure_reason"] == "FAILURE_REASON_TIMEOUT"


# --------------------------------------------------------------------------- #
# pay_invoice — Fehlerformen (jede wird zur Exception, nie zu einer Aussage)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "error_line",
    [
        {"error": "permission denied", "code": 7, "message": "permission denied"},
        {"error": {"grpc_code": 7, "http_code": 403, "message": "permission denied"}},
    ],
)
async def test_a_streamed_error_raises_with_its_message(error_line: dict[str, Any]) -> None:
    handler, _ = _capture(200, _ndjson(error_line))
    with pytest.raises(LightningUnavailableError, match="permission denied"):
        await _client(handler).pay_invoice(payment_request=BOLT11, fee_limit_sat=5)


async def test_a_non_200_raises_with_the_status() -> None:
    handler, _ = _capture(403, b'{"message": "permission denied"}')
    with pytest.raises(LightningUnavailableError, match="403"):
        await _client(handler).pay_invoice(payment_request=BOLT11, fee_limit_sat=5)


async def test_an_empty_stream_is_no_statement() -> None:
    handler, _ = _capture(200, b"")
    with pytest.raises(LightningUnavailableError, match="no payment state"):
        await _client(handler).pay_invoice(payment_request=BOLT11, fee_limit_sat=5)


async def test_a_transport_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout")

    with pytest.raises(LightningUnavailableError, match="ReadTimeout"):
        await _client(handler).pay_invoice(payment_request=BOLT11, fee_limit_sat=5)


async def test_garbage_lines_in_the_stream_are_ignored_not_trusted() -> None:
    content = b'not json\n{"result": {"status": "SUCCEEDED", "payment_preimage": "' + (
        PREIMAGE.encode() + b'"}}\n'
    )
    handler, _ = _capture(200, content)
    result = await _client(handler).pay_invoice(payment_request=BOLT11, fee_limit_sat=5)
    assert result["status"] == "SUCCEEDED"


# --------------------------------------------------------------------------- #
# keysend — derselbe Endpunkt, dieselbe Grenze
# --------------------------------------------------------------------------- #


async def test_keysend_uses_the_router_endpoint() -> None:
    handler, seen = _capture(
        200, _ndjson({"result": {"status": "SUCCEEDED", "payment_preimage": PREIMAGE}})
    )
    dest = "02" + "ab" * 32
    result = await _client(handler).keysend(dest_pubkey_hex=dest, amt_sat=100, fee_limit_sat=2)

    assert seen["path"] == ROUTER_SEND_PATH
    body = seen["body"]
    assert body["amt"] == "100"
    assert body["fee_limit_sat"] == "2"
    assert "5482373484" in body["dest_custom_records"]
    assert body["dest"] and body["payment_hash"]
    assert result["status"] == "SUCCEEDED"


async def test_keysend_refuses_an_unbounded_fee() -> None:
    handler, seen = _capture(200, b"")
    with pytest.raises(ValueError, match="fee_limit_sat"):
        await _client(handler).keysend(dest_pubkey_hex="02" + "ab" * 32, amt_sat=1, fee_limit_sat=0)
    assert seen == {}


# --------------------------------------------------------------------------- #
# Der alte Endpunkt ist weg — nicht abgeschaltet, sondern geloescht.
# --------------------------------------------------------------------------- #


def test_the_legacy_send_endpoint_is_gone_from_the_client() -> None:
    source = Path(client_module.__file__).read_text(encoding="utf-8")
    assert "/v1/channels/transactions" not in source
