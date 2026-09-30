"""Geklaerter Force-Close-Altfall (D-287, Operator-Entscheid 2026-09-30).

lnd fuehrt den Kanal ``cf5fe058…:0`` weiter mit 25 815 sat Limbo, obwohl der Sweep
``2fd51c3d…`` das Geld laengst in die eigene Wallet geholt hat. KAI soll ihn als
geklaerten Altfall zeigen, ohne Dauerwarnung, ohne ihn zum Vermoegen zu addieren
oder abzuziehen. Neue oder veraenderte Force-Closes muessen sichtbar bleiben.
"""

from __future__ import annotations

import copy

from app.api.routers import node_blitz
from app.lightning import reconciled_closes
from app.lightning.treasury import (
    PendingChannelsSnapshot,
    compute_treasury_snapshot,
    parse_pending_channels,
)

_LEGACY = reconciled_closes.RECONCILED_FORCE_CLOSES[0]


def _force_close(channel_point: str, closing_txid: str, limbo: int) -> dict:
    return {
        "channel": {
            "remote_node_pub": "03legacy",
            "channel_point": channel_point,
            "capacity": "1000000",
            "local_balance": str(limbo),
        },
        "closing_txid": closing_txid,
        "limbo_balance": str(limbo),
        "maturity_height": 862481,
        "blocks_til_maturity": -106689,
        "recovered_balance": "0",
    }


def _pending(*entries: dict) -> dict:
    return {
        "total_limbo_balance": str(sum(int(e["limbo_balance"]) for e in entries)),
        "pending_open_channels": [],
        "pending_closing_channels": [],
        "waiting_close_channels": [],
        "pending_force_closing_channels": list(entries),
    }


_LIVE_ROW = _force_close(_LEGACY.channel_point, _LEGACY.closing_txid, _LEGACY.limbo_sat)


def test_the_documented_legacy_case_is_reconciled_and_not_a_warning() -> None:
    snap = parse_pending_channels(_pending(_LIVE_ROW))
    # lnd-Rohdaten bleiben erhalten ...
    assert snap.pending_force_closing_count == 1
    assert snap.total_limbo_sat == 25_815
    assert snap.force_closes[0].channel_point == _LEGACY.channel_point
    # ... aber es ist keine aktive Warnung und kein offener Anspruch mehr.
    assert snap.force_closes[0].reconciled is True
    assert snap.active_force_closing_count == 0
    assert snap.active_limbo_sat == 0


def test_payload_carries_display_text_and_evidence() -> None:
    body = parse_pending_channels(_pending(_LIVE_ROW)).payload()
    assert body["active_force_closing_count"] == 0
    [legacy] = body["reconciled_legacy"]
    assert legacy["title"] == "Historischer Force-Close"
    assert legacy["text"].startswith("Rückführung in die Wallet bestätigt.")
    assert legacy["decision"] == "D-287"
    assert legacy["recovery_txid"].startswith("2fd51c3d")
    assert legacy["recovery_height"] == 953_849
    assert legacy["verified_at"] == "2026-09-30"
    assert legacy["evidence"] == "docs/evidence/ln_node_forensics_20260926.md"
    assert body["force_closes"][0]["reconciled"] is True


def test_a_changed_amount_reactivates_the_warning() -> None:
    row = copy.deepcopy(_LIVE_ROW)
    row["limbo_balance"] = "25000"  # lnd meldet etwas anderes als belegt
    snap = parse_pending_channels(_pending(row))
    assert snap.force_closes[0].reconciled is False
    assert snap.active_force_closing_count == 1
    assert snap.active_limbo_sat == 25_000
    assert snap.payload()["reconciled_legacy"] == []


def test_a_different_closing_tx_is_not_the_legacy_case() -> None:
    row = _force_close(_LEGACY.channel_point, "ab" * 32, _LEGACY.limbo_sat)
    snap = parse_pending_channels(_pending(row))
    assert snap.active_force_closing_count == 1


def test_a_new_force_close_stays_visible_next_to_the_legacy_case() -> None:
    fresh = _force_close("feed" * 16 + ":1", "cd" * 32, 50_000)
    snap = parse_pending_channels(_pending(_LIVE_ROW, fresh))
    assert snap.pending_force_closing_count == 2
    assert snap.active_force_closing_count == 1
    assert snap.active_limbo_sat == 50_000
    assert len(snap.payload()["reconciled_legacy"]) == 1


def test_treasury_neither_adds_nor_subtracts_the_legacy_amount() -> None:
    snap = parse_pending_channels(_pending(_LIVE_ROW))
    before = compute_treasury_snapshot(
        [], onchain_sat=1_548_197, channel_local_sat=370_265, operating_reserve_sat=0
    )
    after = compute_treasury_snapshot(
        [],
        onchain_sat=1_548_197,
        channel_local_sat=370_265,
        operating_reserve_sat=0,
        total_limbo_sat=snap.active_limbo_sat,
        reconciled_legacy_sat=snap.total_limbo_sat - snap.active_limbo_sat,
    )
    assert after["node_total_sat"] == before["node_total_sat"] == 1_548_197 + 370_265
    assert after["total_limbo_sat"] == 0
    assert after["reconciled_legacy_limbo_sat"] == 25_815


def test_directly_built_snapshots_default_to_active() -> None:
    # Ohne Abgleich (z. B. Testdaten, alte Aufrufer) gilt alles als aktiv.
    snap = PendingChannelsSnapshot(state="ok", total_limbo_sat=10, pending_force_closing_count=1)
    assert snap.active_force_closing_count == 1
    assert snap.active_limbo_sat == 10


async def test_blitz_mirror_counts_reconciled_cases(monkeypatch) -> None:
    async def _snap():  # noqa: ANN202
        return parse_pending_channels(_pending(_LIVE_ROW))

    monkeypatch.setattr("app.lightning.treasury.get_pending_channels_snapshot", _snap)
    payload = {"available": True, "data": {"lnd": {"pending_channels": 1}}}
    await node_blitz._annotate_reconciled(payload)
    assert payload["data"]["lnd"]["pending_channels"] == 1  # Rohzahl bleibt
    assert payload["data"]["lnd"]["pending_channels_reconciled"] == 1


async def test_blitz_mirror_keeps_the_warning_when_lnd_is_unreachable(monkeypatch) -> None:
    async def _snap():  # noqa: ANN202
        return PendingChannelsSnapshot(state="unavailable", reason="down")

    monkeypatch.setattr("app.lightning.treasury.get_pending_channels_snapshot", _snap)
    payload = {"available": True, "data": {"lnd": {"pending_channels": 1}}}
    await node_blitz._annotate_reconciled(payload)
    assert payload["data"]["lnd"]["pending_channels_reconciled"] == 0
