from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.execution.close_evidence import VenueCandle
from app.execution.close_evidence_shadow import build_shadow_report, main

CLOSE_TS = "2026-08-21T09:00:30+00:00"
CLOSE_MS = int(datetime.fromisoformat(CLOSE_TS).timestamp() * 1000)
BUCKET_MS = CLOSE_MS - 30_000
NOW = datetime(2026, 8, 21, 9, 5, tzinfo=UTC)


def _close(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_type": "position_closed",
        "fill_id": "fill-1",
        "order_id": "order-1",
        "symbol": "BTC/USDT",
        "timestamp_utc": CLOSE_TS,
        "exit_price": 99.95,
        "price_source": "binance",
        "price_observed_at_utc": CLOSE_TS,
        "observed_market_price": 100.0,
        "execution_reference_price": 100.0,
        "monitor_tick_id": "tick-1",
        "market_data_is_stale": False,
        "market_data_age_ms": 10.0,
        "position_side": "long",
    }
    row.update(changes)
    return row


def _fetch(low: float, high: float):
    def fetch(**_: object) -> list[VenueCandle]:
        return [VenueCandle(BUCKET_MS, low, high, low, high)]

    return fetch


def test_report_zerlegt_verdikte_gruende_und_divergenz() -> None:
    report = build_shadow_report(
        [_close(), {"event_type": "order_filled"}],
        fetchers={"binance": _fetch(99.0, 101.0), "bybit": _fetch(100.0, 102.0)},
        now_utc=NOW,
    )

    assert report["mode"] == "shadow_read_only"
    assert report["input_rows"] == 2
    assert report["eligible_closes"] == 1
    assert report["venues"]["binance"]["verdict_counts"] == {"verified_execution_provenance": 1}
    assert report["venues"]["bybit"]["verdict_counts"] == {"unverified": 1}
    assert report["venues"]["bybit"]["reason_counts"] == {"venue_source_mismatch": 1}

    divergence = report["divergence"]
    assert divergence["comparable_n"] == 1
    assert divergence["unavailable_n"] == 0
    assert divergence["band_gap_pct"]["max"] == 0.0
    assert divergence["midpoint_pct"]["p50"] == pytest.approx(100 / 100.5)
    assert divergence["samples"][0]["fill_id"] == "fill-1"


def test_sammelfehler_ist_unverified_mit_explizitem_grund() -> None:
    def broken(**_: object) -> list[VenueCandle]:
        raise RuntimeError("provider down")

    report = build_shadow_report(
        [_close()],
        fetchers={"binance": broken, "bybit": _fetch(99.0, 101.0)},
        now_utc=NOW,
    )

    binance = report["venues"]["binance"]
    assert binance["collection_status_counts"] == {"fetch_failed": 1}
    assert binance["verdict_counts"] == {"unverified": 1}
    assert binance["reason_counts"] == {"collection:fetch_failed": 1}
    assert report["divergence"]["unavailable_n"] == 1


def test_cli_verlangt_shadow_flag_und_schreibt_kanonischen_report(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    output = tmp_path / "report.json"
    audit.write_text(json.dumps({"event_type": "order_filled"}) + "\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        main(["--audit", str(audit)])

    assert main(["--shadow", "--audit", str(audit), "--output", str(output)]) == 0
    assert json.loads(output.read_bytes())["eligible_closes"] == 0
    assert output.read_bytes().endswith(b"\n")


def test_cli_bricht_bei_kaputter_jsonl_zeile_laut_ab(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text("{kaputt}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="line 1"):
        main(["--shadow", "--audit", str(audit)])


def test_shadow_modul_hat_keinen_publish_oder_classification_pfad() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "app" / "execution" / "close_evidence_shadow.py"
    ).read_text(encoding="utf-8")
    assert "publish_evidence" not in source
    assert "collect_and_publish" not in source
    assert "close_classification" not in source
