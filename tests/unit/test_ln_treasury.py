"""Sprint 7 — Self-Funding treasury accounting (shadow-only, B-004).

Aggregates the earnings ledger + node balances into earnings/operating/tradable
accounts. SATS ONLY here (USD/BTC-beta is a SEPARATE dimension, never co-mingled —
B-004: a self-funding claim must not silently measure beta). Pure, read-only, no
allocation/spend (that is gated at G2).
"""

from __future__ import annotations

from app.lightning.treasury import compute_treasury_snapshot


def test_aggregates_earnings_by_source_and_total() -> None:
    earnings = [
        {"amount_sat": 500, "source": "l402"},
        {"amount_sat": 700, "source": "l402"},
        {"amount_sat": 300, "source": "bolt12"},
    ]
    snap = compute_treasury_snapshot(
        earnings, onchain_sat=1_000_000, channel_local_sat=0, operating_reserve_sat=200_000
    )
    assert snap["currency"] == "sat"
    assert snap["earnings_total_sat"] == 1500
    assert snap["earnings_by_source"] == {"l402": 1200, "bolt12": 300}


def test_operating_and_tradable_split() -> None:
    snap = compute_treasury_snapshot(
        [], onchain_sat=800_000, channel_local_sat=200_000, operating_reserve_sat=300_000
    )
    assert snap["node_total_sat"] == 1_000_000
    assert snap["operating_sat"] == 300_000  # reserve held for node operation
    assert snap["tradable_sat"] == 700_000  # node_total - operating


def test_tradable_never_negative_when_reserve_exceeds_balance() -> None:
    snap = compute_treasury_snapshot(
        [], onchain_sat=100_000, channel_local_sat=0, operating_reserve_sat=300_000
    )
    assert snap["operating_sat"] == 100_000  # capped at available
    assert snap["tradable_sat"] == 0  # never negative


def test_empty_is_zero_and_flags_usd_separate() -> None:
    snap = compute_treasury_snapshot(
        [], onchain_sat=0, channel_local_sat=0, operating_reserve_sat=0
    )
    assert snap["earnings_total_sat"] == 0 and snap["tradable_sat"] == 0
    # B-004: no fabricated USD/self-funding number — usd dimension explicitly absent.
    assert snap.get("usd_value") is None
    assert "btc_beta" in snap["caveat"].lower() or "usd" in snap["caveat"].lower()


def test_treasury_endpoint_aggregates_live(monkeypatch) -> None:
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routers.dashboard import router
    from app.lightning.policy import PolicyEnvelope

    monkeypatch.setattr(
        "app.lightning.earnings_ledger.read_recent_ln_earnings",
        lambda: [{"amount_sat": 1000, "source": "l402"}],
    )

    async def _node():  # noqa: ANN202
        return SimpleNamespace(wallet_total_sat=800_000, channel_local_sat=200_000), 5.0

    monkeypatch.setattr("app.lightning.cache.get_cached_node_status", _node)
    monkeypatch.setattr(
        "app.lightning.policy.PolicyStore.load",
        lambda self: PolicyEnvelope(reserve_floor_sat=300_000),
    )

    app = FastAPI()
    app.include_router(router)
    b = TestClient(app).get("/dashboard/api/ln/treasury").json()
    assert b["earnings_total_sat"] == 1000
    assert b["node_total_sat"] == 1_000_000
    assert b["operating_sat"] == 300_000
    assert b["tradable_sat"] == 700_000
    assert b["usd_value"] is None
