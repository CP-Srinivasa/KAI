"""TrackPaymentV2 startup recovery closes only explicit terminal states."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from app.core.lightning_settings import LightningSettings
from app.lightning.client import LightningUnavailableError
from app.lightning.ops_ledger import prepare_ln_intent, verify_ln_ops_ledger
from app.lightning.payment_reconciliation import reconcile_open_payments, reconcile_spent_today


def _cfg() -> LightningSettings:
    return LightningSettings(
        enabled=True,
        pay_enabled=True,
        tls_cert_path="test-tls.pem",
        payment_macaroon_hex="ab",
    )


async def test_reconciliation_closes_succeeded_payment(monkeypatch, tmp_path) -> None:
    ledger = tmp_path / "ops.jsonl"
    prepare_ln_intent(
        "pay_invoice",
        plan={"amount_sat": 5000, "payment_hash": "11" * 32},
        intent_id="p1",
        path=ledger,
    )
    client = MagicMock()
    client.track_payment_v2 = AsyncMock(
        return_value={"status": "SUCCEEDED", "value_sat": "5000", "fee_sat": "2"}
    )
    monkeypatch.setattr(
        "app.lightning.payment_reconciliation._build_client", lambda cfg, **kwargs: client
    )

    report = await reconcile_open_payments(cfg=_cfg(), path=ledger)

    assert report["succeeded"] == 1
    assert report["open_intents"] == []
    assert verify_ln_ops_ledger(ledger)["ok"] is True


async def test_reconciliation_keeps_in_flight_payment_open(monkeypatch, tmp_path) -> None:
    ledger = tmp_path / "ops.jsonl"
    prepare_ln_intent(
        "pay_invoice",
        plan={"amount_sat": 5000, "payment_hash": "22" * 32},
        intent_id="p2",
        path=ledger,
    )
    client = MagicMock()
    client.track_payment_v2 = AsyncMock(return_value={"status": "IN_FLIGHT"})
    monkeypatch.setattr(
        "app.lightning.payment_reconciliation._build_client", lambda cfg, **kwargs: client
    )

    report = await reconcile_open_payments(cfg=_cfg(), path=ledger)

    assert report["unresolved"] == 1
    assert report["open_intents"] == ["p2"]


async def test_reconciliation_skips_without_payment_credential(tmp_path) -> None:
    report = await reconcile_open_payments(
        cfg=LightningSettings(enabled=True, tls_cert_path="test-tls.pem"),
        path=tmp_path / "missing.jsonl",
    )
    assert report["skipped"] == 1
    assert "payment macaroon unavailable" in report["errors"]


async def test_daily_spend_uses_larger_lnd_value(monkeypatch) -> None:
    client = MagicMock()
    client.list_payments = AsyncMock(
        return_value=[
            {"status": "SUCCEEDED", "value_sat": "2000", "fee_sat": "2"},
            {"status": "FAILED", "value_sat": "9999", "fee_sat": "0"},
            {"status": "IN_FLIGHT", "value_sat": "100", "fee_sat": "0"},
        ]
    )
    monkeypatch.setattr(
        "app.lightning.payment_reconciliation._build_client", lambda cfg, **kwargs: client
    )
    report = await reconcile_spent_today(
        cfg=_cfg(),
        ledger_spent_sat=1000,
        now=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
    )
    assert report["lnd_spent_sat"] == 2102
    assert report["effective_spent_sat"] == 2102
    assert report["gap_sat"] == 1102


async def test_daily_spend_falls_back_to_ledger_when_lnd_unavailable(monkeypatch) -> None:
    client = MagicMock()
    client.list_payments = AsyncMock(side_effect=LightningUnavailableError("offline"))
    monkeypatch.setattr(
        "app.lightning.payment_reconciliation._build_client", lambda cfg, **kwargs: client
    )
    report = await reconcile_spent_today(cfg=_cfg(), ledger_spent_sat=5000)
    assert report["available"] is False
    assert report["effective_spent_sat"] == 5000
