"""``recent_alert_rows`` — Vertrag der Uebersichtskarte "Letzte Directional Alerts"."""

from __future__ import annotations

from app.api.routers.dashboard_recent_alerts import recent_alert_rows

CONTRACT_KEYS = {
    "doc_id",
    "sentiment",
    "priority",
    "assets",
    "source_name",
    "dispatched_at",
    "outcome",
}


def test_rows_are_newest_first_and_carry_every_contract_key() -> None:
    records = [
        {
            "document_id": "abc12345-dea-older-record",
            "sentiment_label": "bullish",
            "priority": 9,
            "affected_assets": ["BTC", "ETH"],
            "source_name": "CoinDesk",
            "dispatched_at": "2026-09-15T10:00:00+00:00",
        },
        {
            "document_id": "ffff00001111-newer",
            "sentiment_label": "bearish",
            "priority": 7,
            "affected_assets": ["SOL"],
            "dispatched_at": "2026-09-15T11:30:00+00:00",
        },
    ]
    rows = recent_alert_rows(records, {"abc12345-dea-older-record": "hit"})

    assert [r["doc_id"] for r in rows] == ["ffff00001111", "abc12345-dea"]
    assert all(set(r) == CONTRACT_KEYS for r in rows)
    assert rows[1]["source_name"] == "CoinDesk"
    assert rows[1]["outcome"] == "hit"
    assert rows[1]["dispatched_at"] == "2026-09-15T10:00"


def test_missing_source_name_is_none_not_absent() -> None:
    rows = recent_alert_rows([{"document_id": "x" * 20}], {})
    assert "source_name" in rows[0]
    assert rows[0]["source_name"] is None
    assert rows[0]["outcome"] == ""


def test_empty_input_yields_empty_list() -> None:
    assert recent_alert_rows([], {}) == []
