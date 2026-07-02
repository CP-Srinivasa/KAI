"""U3 — earnings-booking job: lnd.ListInvoices → settled kai-oracle:* → ledger.

Read-only against the node's OWN invoices (capital-free). Books only SETTLED
invoices whose memo carries the oracle prefix, idempotently, and is a no-op when
Lightning is disabled.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.lightning_settings import LightningSettings
from app.lightning.client import LndRestClient
from app.lightning.earnings_booking import book_all_earnings, book_oracle_earnings
from app.lightning.earnings_ledger import read_recent_ln_earnings


async def test_client_list_invoices_wire() -> None:
    """U3 client method: GET /v1/invoices, returns the node's own invoice array."""

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET" and req.url.path == "/v1/invoices"
        return httpx.Response(
            200, json={"invoices": [{"memo": "kai-oracle:x", "settled": True, "r_hash": "aa"}]}
        )

    c = LndRestClient(
        base_url="https://x:8080", macaroon_hex="ab", transport=httpx.MockTransport(handler)
    )
    inv = await c.list_invoices()
    assert isinstance(inv, list) and inv[0]["memo"] == "kai-oracle:x"


def _ph(seed: str) -> tuple[str, str]:
    """Return (r_hash_b64, payment_hash_hex) for a deterministic fake invoice."""
    h = hashlib.sha256(seed.encode()).hexdigest()
    return base64.b64encode(bytes.fromhex(h)).decode(), h


def _fake_client(invoices: list[dict]) -> MagicMock:
    c = MagicMock()
    c.list_invoices = AsyncMock(return_value=invoices)
    return c


@pytest.mark.asyncio
async def test_books_only_settled_oracle_invoices(tmp_path: Path) -> None:
    rh1, ph1 = _ph("paid-oracle")
    rh2, _ = _ph("unsettled-oracle")
    rh3, _ = _ph("settled-non-oracle")
    invoices = [
        {"memo": "kai-oracle:fee-series", "settled": True, "r_hash": rh1, "amt_paid_sat": 100},
        {"memo": "kai-oracle:fee-series", "settled": False, "r_hash": rh2, "amt_paid_sat": 0},
        {"memo": "donation", "settled": True, "r_hash": rh3, "amt_paid_sat": 50},
    ]
    p = tmp_path / "earn.jsonl"
    with patch("app.lightning.earnings_booking._build_client", return_value=_fake_client(invoices)):
        booked = await book_oracle_earnings(
            path=p, cfg=LightningSettings(enabled=True, tls_cert_path="test-tls.pem")
        )
    assert booked == 1
    rows = read_recent_ln_earnings(p, limit=0)
    assert len(rows) == 1
    assert rows[0]["payment_hash"] == ph1 and rows[0]["amount_sat"] == 100
    assert rows[0]["source"] == "oracle-l402"


@pytest.mark.asyncio
async def test_second_run_is_idempotent(tmp_path: Path) -> None:
    rh, _ = _ph("paid-oracle")
    invoices = [{"memo": "kai-oracle:x", "settled": True, "r_hash": rh, "amt_paid_sat": 100}]
    p = tmp_path / "earn.jsonl"
    with patch("app.lightning.earnings_booking._build_client", return_value=_fake_client(invoices)):
        first = await book_oracle_earnings(
            path=p, cfg=LightningSettings(enabled=True, tls_cert_path="test-tls.pem")
        )
        second = await book_oracle_earnings(
            path=p, cfg=LightningSettings(enabled=True, tls_cert_path="test-tls.pem")
        )
    assert first == 1 and second == 0  # same settled invoice never double-booked


@pytest.mark.asyncio
async def test_noop_when_lightning_disabled(tmp_path: Path) -> None:
    with patch("app.lightning.earnings_booking._build_client") as build:
        booked = await book_oracle_earnings(
            path=tmp_path / "e.jsonl", cfg=LightningSettings(enabled=False)
        )
    assert booked == 0
    build.assert_not_called()  # disabled → node never touched


@pytest.mark.asyncio
async def test_book_all_earnings_covers_paylink_prefix(tmp_path: Path) -> None:
    """G2 (ADR 0013): LNbits-Pay-Link-Invoices laufen durch UNSER lnd — der
    Memo-Prefix ``kai-pay:`` bucht Empfangs-Identitäts-Zahlungen ohne
    LNbits-API/Webhook über dieselbe idempotente Maschinerie."""
    rh1, ph1 = _ph("oracle-hit")
    rh2, ph2 = _ph("paylink-hit")
    invoices = [
        {"memo": "kai-oracle:verdicts", "settled": True, "r_hash": rh1, "amt_paid_sat": 10},
        {"memo": "kai-pay: kai@node", "settled": True, "r_hash": rh2, "amt_paid_sat": 2100},
        {"memo": "fremd", "settled": True, "r_hash": _ph("other")[0], "amt_paid_sat": 5},
    ]
    p = tmp_path / "earn.jsonl"
    with patch("app.lightning.earnings_booking._build_client", return_value=_fake_client(invoices)):
        counts = await book_all_earnings(
            path=p, cfg=LightningSettings(enabled=True, tls_cert_path="test-tls.pem")
        )
    assert counts == {"oracle-l402": 1, "lnurlp": 1}
    by_hash = {r["payment_hash"]: r for r in read_recent_ln_earnings(p)}
    assert by_hash[ph2]["source"] == "lnurlp"
    assert by_hash[ph2]["amount_sat"] == 2100
    assert by_hash[ph1]["source"] == "oracle-l402"
