"""TL-002: der dokumentierte Mock-Vorfall vom 11./12.08.2026 ist quittiert, nichts sonst.

Vier Monitor-Closes liefen gegen die Mock-Kurve, bevor #728/#729 (18.08.) den
Mock-Adapter aus dem Monitor-Pfad nahmen; der Scheingewinn (+2255 USD) ist mit
#723 auf der Lese-Seite herausgerechnet. Weil sie NACH dem Gate-Stichtag
(11.07.) liegen, hielten sie den Truth-Status seither dauerhaft auf DEGRADED.

Operator-Entscheid 02.10.2026: genau diese Order-IDs quittieren. Sie bleiben als
INFO-Zeile sichtbar; jeder andere Treffer — auch zum selben Preis im selben
Fenster — bleibt eine Verletzung.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.truth.lint import run_lint

# Echte Zeilen aus artifacts/paper_execution_audit.jsonl (kai-pi5, gelesen 02.10.2026).
_INCIDENT = [
    ("ord_3bde9b249140", "ETH/USDT", 3225.6863500000004, "2026-08-11T23:09:58.551468+00:00"),
    ("ord_9cea46bd3cac", "BTC/USDT", 65454.246505, "2026-08-11T23:09:58.565788+00:00"),
    ("ord_a09f2540ea27", "ETH/USDT", 3225.6863500000004, "2026-08-12T23:06:34.502323+00:00"),
    ("ord_a9931db11647", "SOL/USDT", 152.14603499999998, "2026-08-12T23:06:34.497868+00:00"),
]


def _fill(order_id: str, symbol: str, price: float, ts: str) -> dict[str, Any]:
    return {
        "event_type": "order_filled",
        "order_id": order_id,
        "symbol": symbol,
        "fill_price": price,
        "timestamp_utc": ts,
    }


def _lint(tmp_path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    art = tmp_path / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    with (art / "paper_execution_audit.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return run_lint(art)


def _tl002(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [v for v in report["violations"] if v["invariant_id"] == "TL-002"]


def test_dokumentierter_vorfall_degradiert_nicht_mehr(tmp_path: Path) -> None:
    report = _lint(tmp_path, [_fill(*row) for row in _INCIDENT])

    tl002 = _tl002(report)
    assert [v["severity"] for v in tl002] == ["INFO"]
    evidence = tl002[0]["evidence"]
    assert evidence["classification"] == "DOCUMENTED_INCIDENT"
    assert evidence["count"] == 4
    assert sorted(evidence["order_ids"]) == sorted(row[0] for row in _INCIDENT)
    assert report["max_severity"] == "INFO"  # Truth-Status nicht mehr degraded


def test_gleicher_preis_andere_order_bleibt_verletzung(tmp_path: Path) -> None:
    # Quittiert ist die Order, nicht der Preis: derselbe Mock-Kurs auf einer
    # neuen Order ist wieder MOCK_SYNTHETIC.
    rows = [_fill(*row) for row in _INCIDENT]
    rows.append(_fill("ord_neu_eth", "ETH/USDT", 3225.6863500000004, "2026-10-02T08:00:00+00:00"))
    report = _lint(tmp_path, rows)

    by_class = {v["evidence"]["classification"]: v for v in _tl002(report)}
    assert by_class["MOCK_SYNTHETIC"]["severity"] == "WARNING"
    assert [r["order_id"] for r in by_class["MOCK_SYNTHETIC"]["evidence"]["rows"]] == [
        "ord_neu_eth"
    ]
    assert by_class["DOCUMENTED_INCIDENT"]["evidence"]["count"] == 4
    assert report["max_severity"] == "WARNING"
