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


# --------------------------------------------------------------------------- #
# Dauerhafte Zustellung (Lueckenregister 26.09., Befund 5)
# --------------------------------------------------------------------------- #


def _script(monkeypatch: pytest.MonkeyPatch, answers: list[bool]) -> list[dict[str, Any]]:
    """``deliver`` antwortet der Reihe nach mit ``answers`` (danach: ok)."""
    calls: list[dict[str, Any]] = []

    async def _deliver(url: str, payload: dict[str, Any], *, secret: str, **_: Any) -> Any:
        from app.pay.webhook import WebhookResult

        ok = answers.pop(0) if answers else True
        calls.append(dict(payload))
        return WebhookResult(ok=ok, detail="http 200" if ok else "http 503")

    monkeypatch.setattr("app.pay.service.deliver", _deliver)
    return calls


async def _settled_entry(harness: Harness) -> Any:
    entry = await harness.service.create_request(
        amount_sat=1200, description="Beratung", reference="ORDER-7", webhook_url=HOOK
    )
    harness.rail.settle(entry.ref_hash)
    return entry


async def test_absturz_zwischen_settled_und_callback_wird_nachgeholt(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_secret(harness, "s3cr3t")
    entry = await _settled_entry(harness)

    async def _crash(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("prozess stirbt nach dem Speichern von SETTLED")

    monkeypatch.setattr("app.pay.service.deliver", _crash)
    with pytest.raises(RuntimeError):
        await harness.service.refresh(entry.payment_id)

    # Neustart: Store frisch aus der Datei, Callback noch nie zugestellt.
    harness.store.load()
    calls = _script(monkeypatch, [])
    assert await harness.service.redeliver_webhooks() == 1
    assert await harness.service.redeliver_webhooks() == 0
    assert len(calls) == 1
    assert calls[0]["event_id"] == f"{entry.payment_id}:settled"
    assert calls[0]["paid_amount_sat"] == 1200
    assert _events(harness).count(EVENT_WEBHOOK_SENT) == 1


async def test_ein_fehlschlag_wird_mit_backoff_wiederholt_mit_derselben_event_id(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    _with_secret(harness, "s3cr3t")
    entry = await _settled_entry(harness)
    calls = _script(monkeypatch, [False, True])

    await harness.service.refresh(entry.payment_id)  # Versuch 1 scheitert
    assert await harness.service.redeliver_webhooks() == 0  # Backoff laeuft noch
    harness.now = harness.now + timedelta(minutes=2)
    assert await harness.service.redeliver_webhooks() == 1  # Versuch 2 gelingt
    harness.now = harness.now + timedelta(hours=2)
    assert await harness.service.redeliver_webhooks() == 0  # zugestellt: nie wieder

    assert len(calls) == 2
    assert calls[0]["event_id"] == calls[1]["event_id"]


async def test_nach_der_obergrenze_wird_aufgegeben_und_gemeldet(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    from app.pay.service import WEBHOOK_MAX_ROUNDS

    _with_secret(harness, "s3cr3t")
    entry = await _settled_entry(harness)
    calls = _script(monkeypatch, [False] * 50)

    await harness.service.refresh(entry.payment_id)
    for _ in range(WEBHOOK_MAX_ROUNDS + 5):
        harness.now = harness.now + timedelta(hours=2)
        await harness.service.redeliver_webhooks()

    assert len(calls) == WEBHOOK_MAX_ROUNDS
    health = harness.service.health()
    assert health["webhooks_pending"] == 0
    assert health["webhooks_given_up"] == 1


async def test_ein_zugestellter_callback_ueberlebt_den_neustart(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_secret(harness, "s3cr3t")
    entry = await _settled_entry(harness)
    calls = _script(monkeypatch, [])
    await harness.service.refresh(entry.payment_id)

    harness.store.load()  # Neustart
    assert await harness.service.redeliver_webhooks() == 0
    assert len(calls) == 1
    assert harness.service.health()["webhooks_pending"] == 0
