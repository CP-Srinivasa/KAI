"""Der Callback am echten Uebergang: genau einmal, und nie zustandsrelevant."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.pay.status import PayStatus
from app.pay.store import EVENT_WEBHOOK_FAILED, EVENT_WEBHOOK_SENT
from tests.unit.pay.conftest import Harness, pay_settings

HOOK = "https://shop.example/hook"


def _with_secret(harness: Harness, secret: str) -> None:
    harness.service = type(harness.service)(
        payments=harness.payments,
        store=harness.store,
        settings=pay_settings(webhook_secret=secret),
        clock=lambda: harness.now,
    )


def _events(harness: Harness) -> list[str]:
    import json

    return [
        json.loads(line)["event"]
        for line in harness.store.path.read_text(encoding="utf-8").splitlines()
    ]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Eine Naht auf ``deliver`` — der Transport hat eigene Tests."""
    calls: list[dict[str, Any]] = []

    async def _capture(url: str, payload: dict[str, Any], *, secret: str, **_: Any) -> Any:
        calls.append({"url": url, "payload": payload, "secret": secret})
        from app.pay.webhook import WebhookResult

        return WebhookResult(ok=bool(secret), detail="http 200" if secret else "no_secret")

    monkeypatch.setattr("app.pay.service.deliver", _capture)
    return calls


async def test_der_callback_geht_genau_einmal_raus(harness: Harness, sent: list[Any]) -> None:
    _with_secret(harness, "s3cr3t")
    entry = await harness.service.create_request(
        amount_sat=1200, description="Beratung", reference="ORDER-1", webhook_url=HOOK
    )
    harness.rail.settle(entry.ref_hash)

    await harness.service.refresh(entry.payment_id)
    await harness.service.refresh(entry.payment_id)

    assert len(sent) == 1
    assert sent[0]["url"] == HOOK
    assert sent[0]["payload"]["status"] == PayStatus.SETTLED.value
    # Der Betrag im Callback stammt aus dem Journal-Record, nicht aus dem Store.
    assert sent[0]["payload"]["paid_amount_sat"] == 1200
    assert _events(harness).count(EVENT_WEBHOOK_SENT) == 1


async def test_ohne_geheimnis_wird_der_grund_festgehalten(
    harness: Harness, sent: list[Any]
) -> None:
    _with_secret(harness, "")
    entry = await harness.service.create_request(
        amount_sat=1200, description="Beratung", webhook_url=HOOK
    )
    harness.rail.settle(entry.ref_hash)
    updated = await harness.service.refresh(entry.payment_id)

    assert updated.status is PayStatus.SETTLED
    assert _events(harness).count(EVENT_WEBHOOK_FAILED) == 1


async def test_ohne_ziel_wird_gar_nichts_versucht(harness: Harness, sent: list[Any]) -> None:
    _with_secret(harness, "s3cr3t")
    entry = await harness.service.create_request(amount_sat=1200, description="Beratung")
    harness.rail.settle(entry.ref_hash)
    await harness.service.refresh(entry.payment_id)

    assert sent == []
    assert EVENT_WEBHOOK_SENT not in _events(harness)


async def test_ein_gescheiterter_callback_kippt_den_zustand_nicht(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bezahlt ist, was das Journal sagt — nicht, was ein HTTP-Aufruf sagt."""
    _with_secret(harness, "s3cr3t")

    async def _fail(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectError("connection refused")

    entry = await harness.service.create_request(
        amount_sat=1200, description="Beratung", webhook_url=HOOK
    )
    harness.rail.settle(entry.ref_hash)
    monkeypatch.setattr("app.pay.service.deliver", _fail)

    with pytest.raises(httpx.ConnectError):
        await harness.service.refresh(entry.payment_id)

    # Der Store traegt den Zustand bereits: er wird VOR dem Callback gesetzt,
    # damit ein unerreichbarer Empfaenger keinen Eingang verschwinden laesst.
    stored = harness.store.get(entry.payment_id)
    assert stored is not None and stored.status is PayStatus.SETTLED
    assert len(harness.settled_records(entry.ref_hash)) == 1
