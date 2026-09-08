"""Der Takt: eine Runde, eine Zeitgrenze, ein sauberes Ende.

Der Poller entscheidet nichts — er ruft ``refresh``. Was hier geprueft wird,
ist deshalb sein VERHALTEN als Dauerschleife, und zwar an genau den Stellen,
die auf ``kai-pi5`` schon zweimal teuer waren: ein ``await`` ohne Zeitgrenze
(46 h und 65 h Stille) und ein Task, dessen Cancellation niemand abwartet
(20-s-Stop-Timeout).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.pay import poller
from app.pay.status import PayStatus
from app.payments.rail import RailError
from tests.unit.pay.conftest import NOW, Harness


async def _create(harness: Harness, **overrides: object) -> object:
    base: dict[str, object] = {"amount_sat": 1200, "description": "Beratung"}
    base.update(overrides)
    return await harness.service.create_request(**base)  # type: ignore[arg-type]


async def test_eine_runde_bestaetigt_eine_bezahlte_forderung(harness: Harness) -> None:
    entry = await _create(harness)
    harness.rail.settle(entry.ref_hash)  # type: ignore[attr-defined]

    assert await poller.tick(harness.service) == 1

    updated = harness.store.get(entry.payment_id)  # type: ignore[attr-defined]
    assert updated is not None and updated.status is PayStatus.SETTLED
    # Und genau EIN Record im Kern — der Poller ist kein zweiter Reconciler.
    assert len(harness.settled_records(entry.ref_hash)) == 1  # type: ignore[attr-defined]


async def test_erledigte_forderungen_werden_nicht_mehr_gefragt(harness: Harness) -> None:
    entry = await _create(harness)
    harness.rail.settle(entry.ref_hash)  # type: ignore[attr-defined]
    await poller.tick(harness.service)

    assert await poller.tick(harness.service) == 0


async def test_ein_fehler_nimmt_die_uebrigen_forderungen_nicht_mit(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await _create(harness, reference="ORDER-1")
    second = await _create(harness, reference="ORDER-2")
    harness.rail.settle(second.ref_hash)  # type: ignore[attr-defined]

    original = harness.rail.invoice_status

    async def _selective(ref_hash: str) -> object:
        if ref_hash == first.ref_hash:  # type: ignore[attr-defined]
            raise RailError("node unreachable")
        return await original(ref_hash)

    monkeypatch.setattr(harness.rail, "invoice_status", _selective)
    assert await poller.tick(harness.service) == 2

    still_open = harness.store.get(first.payment_id)  # type: ignore[attr-defined]
    settled = harness.store.get(second.payment_id)  # type: ignore[attr-defined]
    assert still_open is not None and still_open.status is PayStatus.WAITING
    assert settled is not None and settled.status is PayStatus.SETTLED


async def test_eine_haengende_nachfrage_laeuft_in_die_zeitgrenze(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Zeitgrenze haelt EIN toter Node die ganze Runde fuer immer an."""
    await _create(harness)

    async def _hang(_ref_hash: str) -> object:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")  # pragma: no cover

    monkeypatch.setattr(harness.rail, "invoice_status", _hang)
    monkeypatch.setattr(poller, "REFRESH_TIMEOUT_SECONDS", 0.05)

    assert await asyncio.wait_for(poller.tick(harness.service), timeout=5) == 1


async def test_die_runde_deckelt_die_offenen_forderungen(harness: Harness) -> None:
    from tests.unit.pay.conftest import pay_settings

    harness.service = type(harness.service)(
        payments=harness.payments,
        store=harness.store,
        settings=pay_settings(max_open_requests=2),
        clock=lambda: harness.now,
    )
    for index in range(4):
        await _create(harness, reference=f"ORDER-{index}")

    assert await poller.tick(harness.service) == 2


async def test_der_poller_endet_sauber_und_ohne_ausnahme(harness: Harness) -> None:
    task = poller.start(harness.service)
    await asyncio.sleep(0)
    await poller.stop(task)

    assert task.cancelled() or task.done()
    assert await poller.stop(None) is None


async def test_eine_kaputte_runde_toetet_den_poller_nicht(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine tote Runde ist kein toter Poller — sonst faellt der Takt still aus."""
    rounds: list[int] = []

    async def _boom(_service: object) -> int:
        rounds.append(1)
        raise RuntimeError("store gone")

    monkeypatch.setattr(poller, "tick", _boom)
    harness.settings.poll_interval_seconds = 5  # type: ignore[misc]

    task = poller.start(harness.service)
    for _ in range(20):
        await asyncio.sleep(0)
        if rounds:
            break
    await poller.stop(task)

    assert rounds
    assert not task.exception() if task.done() and not task.cancelled() else True


async def test_der_takt_wartet_zwischen_zwei_runden(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr("app.pay.poller.asyncio.sleep", _record)
    with pytest.raises(asyncio.CancelledError):
        await poller.run(harness.service)

    assert slept == [float(harness.settings.poll_interval_seconds)]


async def test_eine_abgelaufene_forderung_wird_in_der_runde_geschlossen(
    harness: Harness,
) -> None:
    entry = await _create(harness, expiry_seconds=60)
    harness.now = NOW + timedelta(minutes=5)

    await poller.tick(harness.service)

    updated = harness.store.get(entry.payment_id)  # type: ignore[attr-defined]
    assert updated is not None and updated.status is PayStatus.EXPIRED


async def test_eine_haengende_runde_wird_abgeschnitten(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zweite Verteidigungslinie: auch die RUNDE hat eine Zeitgrenze.

    Die Grenze je Nachfrage genuegt nicht — 200 offene Forderungen mal 30 s
    waeren 100 Minuten, in denen der Poller aussieht, als liefe er.
    """
    cycles: list[int] = []

    async def _hang(_service: object) -> int:
        cycles.append(1)
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _stop(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(poller, "tick", _hang)
    monkeypatch.setattr(poller, "ROUND_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("app.pay.poller.asyncio.sleep", _stop)

    with pytest.raises(asyncio.CancelledError):
        await poller.run(harness.service)
    assert cycles == [1]
