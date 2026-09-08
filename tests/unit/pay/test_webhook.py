"""Der Rueckruf: Signatur, Wiederholung, und wann gar nicht gesendet wird.

Die Signatur ist der ganze Punkt. Ein Callback ohne sie ist eine Nachricht, die
jeder faelschen kann, der die URL kennt — und der Empfaenger schaltet darauf
eine Leistung frei. Deshalb gilt hier: kein Geheimnis, kein Versand.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest

from app.pay.webhook import (
    MAX_ATTEMPTS,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    build_body,
    deliver,
    sign,
)

PAYLOAD: dict[str, Any] = {
    "payment_id": "pay_000000000001",
    "status": "SETTLED",
    "amount_sat": 1200,
    "paid_amount_sat": 1200,
    "paid_at": "2026-09-08T12:00:00+00:00",
    "reference": "ORDER-1",
    "description": "Beratung 30 Minuten",
    "ts": "2026-09-08T12:00:01+00:00",
}


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Pausen sind Verhalten, nicht Gegenstand dieses Tests."""

    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.pay.webhook.asyncio.sleep", _instant)


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_der_empfaenger_kann_die_signatur_nachrechnen() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["signature"] = request.headers[SIGNATURE_HEADER]
        seen["timestamp"] = request.headers[TIMESTAMP_HEADER]
        return httpx.Response(200)

    async with _client(handler) as client:
        result = await deliver("https://shop.example/hook", PAYLOAD, secret="s3cr3t", client=client)

    assert result.ok
    expected = hmac.new(b"s3cr3t", seen["body"], hashlib.sha256).hexdigest()
    assert seen["signature"] == f"sha256={expected}"
    assert seen["timestamp"] == PAYLOAD["ts"]
    assert json.loads(seen["body"])["payment_id"] == "pay_000000000001"


def test_signatur_und_body_stammen_aus_derselben_serialisierung() -> None:
    """Zwei Serialisierungen waeren die klassische Signaturluecke."""
    body = build_body(PAYLOAD)
    assert sign("s3cr3t", body) == sign("s3cr3t", build_body(PAYLOAD))
    assert sign("s3cr3t", body) != sign("other", body)


async def test_ohne_geheimnis_wird_nichts_gesendet() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - darf nie laufen
        raise AssertionError("an unsigned webhook must never leave the process")

    async with _client(handler) as client:
        result = await deliver("https://shop.example/hook", PAYLOAD, secret="", client=client)

    assert not result.ok
    assert result.detail == "no_secret"


async def test_ohne_ziel_wird_nichts_gesendet() -> None:
    result = await deliver("", PAYLOAD, secret="s3cr3t")
    assert (result.ok, result.detail) == (False, "no_url")


async def test_ein_fehlschlag_wird_dreimal_versucht() -> None:
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503)

    async with _client(handler) as client:
        result = await deliver("https://shop.example/hook", PAYLOAD, secret="s3cr3t", client=client)

    assert len(attempts) == MAX_ATTEMPTS
    assert not result.ok
    assert result.detail == "http 503"


async def test_ein_spaeterer_versuch_gelingt_noch() -> None:
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(500) if len(attempts) < 2 else httpx.Response(204)

    async with _client(handler) as client:
        result = await deliver("https://shop.example/hook", PAYLOAD, secret="s3cr3t", client=client)

    assert result.ok and len(attempts) == 2


async def test_ein_transportfehler_wirft_nicht_nach_aussen() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with _client(handler) as client:
        result = await deliver("https://shop.example/hook", PAYLOAD, secret="s3cr3t", client=client)

    assert not result.ok
    assert "ConnectError" in result.detail
