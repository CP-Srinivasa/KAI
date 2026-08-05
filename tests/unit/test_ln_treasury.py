"""Sprint 7 — Self-Funding treasury accounting (shadow-only, B-004).

Aggregates the earnings ledger + node balances into earnings/operating/tradable
accounts. SATS ONLY here (USD/BTC-beta is a SEPARATE dimension, never co-mingled —
B-004: a self-funding claim must not silently measure beta). Pure, read-only, no
allocation/spend (that is gated at G2).
"""

from __future__ import annotations

from app.lightning.treasury import (
    PendingChannelsSnapshot,
    PendingForceClose,
    compute_treasury_snapshot,
    get_pending_channels_snapshot,
    parse_pending_channels,
)

_FORCE_CLOSE_FIXTURE = {
    "total_limbo_balance": "25815",
    "pending_open_channels": [],
    "pending_closing_channels": [],
    "waiting_close_channels": [],
    "pending_force_closing_channels": [
        {
            "channel": {
                "remote_node_pub": "03abc",
                "channel_point": "deadbeef:1",
                "capacity": "400000",
                "local_balance": "25815",
            },
            "closing_txid": "feedface",
            "limbo_balance": "25815",
            "maturity_height": 840000,
            "blocks_til_maturity": -98226,
            "recovered_balance": "0",
        }
    ],
}


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


def test_force_close_limbo_is_separate_from_available_capital() -> None:
    pending = parse_pending_channels(_FORCE_CLOSE_FIXTURE)
    assert pending.state == "ok"
    assert pending.total_limbo_sat == 25_815
    assert pending.pending_force_closing_count == 1
    assert pending.force_closes[0].channel_point == "deadbeef:1"
    assert pending.force_closes[0].recovered_balance_sat == 0
    assert pending.force_closes[0].blocks_til_maturity == -98_226

    snap = compute_treasury_snapshot(
        [],
        onchain_sat=100_000,
        channel_local_sat=50_000,
        operating_reserve_sat=20_000,
        total_limbo_sat=pending.total_limbo_sat,
    )
    assert snap["node_total_sat"] == 150_000
    assert snap["tradable_sat"] == 130_000
    assert snap["total_limbo_sat"] == 25_815


def test_pending_snapshot_without_force_close_has_no_limbo() -> None:
    pending = parse_pending_channels(
        {
            "pending_open_channels": [],
            "pending_closing_channels": [],
            "pending_force_closing_channels": [],
            "waiting_close_channels": [],
            "total_limbo_balance": "0",
        }
    )
    assert pending.total_limbo_sat == 0
    assert pending.pending_force_closing_count == 0
    assert pending.force_closes == []


async def test_pending_snapshot_fetches_pendingchannels(monkeypatch) -> None:
    async def _pending(self):  # noqa: ANN001, ANN202
        return _FORCE_CLOSE_FIXTURE

    monkeypatch.setattr("app.lightning.client.LndRestClient.pending_channels", _pending)
    from app.core.lightning_settings import LightningSettings

    result = await get_pending_channels_snapshot(
        LightningSettings(
            enabled=True,
            tls_cert_path="test-tls.pem",
            macaroon_hex="ab",
        )
    )
    assert result.state == "ok"
    assert result.total_limbo_sat == 25_815


async def test_pending_snapshot_missing_read_credential_is_unavailable() -> None:
    from app.core.lightning_settings import LightningSettings

    # macaroon_hex/macaroon_path MUESSEN explizit leer gesetzt werden: unbelegte
    # Felder zieht pydantic-settings aus der realen .env des Hosts. Auf dem Pi ist
    # APP_LN_MACAROON_PATH gesetzt -> das Credential gilt als vorhanden, der Code
    # laeuft am Macaroon-Check vorbei bis zum TLS-Boot-Validator und scheitert dort
    # an der nicht existierenden test-tls.pem ("unexpected: [Errno 2] ..."). In CI
    # (leere Umgebung) faellt das nicht auf. Gleiches Muster wie test_lightning.py.
    result = await get_pending_channels_snapshot(
        LightningSettings(
            enabled=True,
            macaroon_hex="",
            macaroon_path="",
            tls_cert_path="test-tls.pem",
        )
    )
    assert result.state == "unavailable"
    assert result.total_limbo_sat == 0
    assert "macaroon" in result.reason


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

    async def _pending():  # noqa: ANN202
        return PendingChannelsSnapshot(
            state="ok",
            total_limbo_sat=25_815,
            pending_force_closing_count=1,
            force_closes=[
                PendingForceClose(
                    channel_point="deadbeef:1",
                    remote_pubkey="03abc",
                    closing_txid="feedface",
                    capacity_sat=400_000,
                    limbo_balance_sat=25_815,
                    recovered_balance_sat=0,
                    maturity_height=840_000,
                    blocks_til_maturity=-98_226,
                )
            ],
        )

    monkeypatch.setattr("app.lightning.cache.get_cached_node_status", _node)
    monkeypatch.setattr("app.lightning.treasury.get_pending_channels_snapshot", _pending)
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
    assert b["total_limbo_sat"] == 25_815
    assert b["limbo_state"] == "ok"
    assert b["pending_force_closing_count"] == 1
    assert b["force_closes"][0]["channel_point"] == "deadbeef:1"
    assert b["usd_value"] is None


def test_channels_endpoint_surfaces_force_close_limbo(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routers.dashboard import router
    from app.lightning.adapter import LightningChannels

    async def _channels():  # noqa: ANN202
        return LightningChannels(state="ok", reachable=True)

    async def _pending():  # noqa: ANN202
        return parse_pending_channels(_FORCE_CLOSE_FIXTURE)

    monkeypatch.setattr("app.lightning.adapter.get_channels", _channels)
    monkeypatch.setattr("app.lightning.treasury.get_pending_channels_snapshot", _pending)

    app = FastAPI()
    app.include_router(router)
    body = TestClient(app).get("/dashboard/api/ln/channels").json()
    assert body["total_limbo_sat"] == 25_815
    assert body["pending_force_closing_count"] == 1
    assert body["force_closes"][0]["blocks_til_maturity"] == -98_226
