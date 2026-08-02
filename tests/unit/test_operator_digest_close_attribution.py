"""Close-Attribution im Operator-Digest (Paper 24h).

Befund 2026-08-02: ``position_closed`` traegt eine ANDERE ``order_id`` als der
Entry-Fill (0/468 Label-Treffer im Live-Audit), weshalb saemtliche Closes samt
PnL in einem ``unlabeled``-Eimer landeten — der Digest konnte keinen Euro einer
Route zuordnen. Die Kette ``paper_trade_label.order_id -> order_filled.document_id
-> position_closed.document_id`` traegt, ``signal_source`` auf der Close-Zeile ist
der Fallback (275/275 Uebereinstimmung im Live-Audit, 0 Widersprueche).

Gleiche Fehlerklasse wie das Join-Artefakt vom 2026-07-28 (order_id statt
document_id) — hier fuer die Reporting-Schicht geschlossen.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import operator_digest as od  # noqa: E402

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
TS = "2026-08-02T10:00:00+00:00"


def _write(tmp_path: Path, monkeypatch, rows: list[dict]) -> None:
    (tmp_path / "paper_execution_audit.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(od, "_ARTIFACTS", tmp_path)


def _label(order_id: str, source: str) -> dict:
    return {
        "event_type": "paper_trade_label",
        "timestamp_utc": TS,
        "order_id": order_id,
        "source_name": source,
        "feed_source": source,
    }


def _fill(order_id: str, document_id: str) -> dict:
    return {
        "event_type": "order_filled",
        "timestamp_utc": TS,
        "order_id": order_id,
        "document_id": document_id,
        "side": "buy",
        "position_side": "long",
    }


def _close(order_id: str, pnl: float, **extra) -> dict:
    return {
        "event_type": "position_closed",
        "timestamp_utc": TS,
        "order_id": order_id,
        "trade_pnl_usd": pnl,
        **extra,
    }


def test_close_attributed_via_document_id_despite_different_order_id(tmp_path, monkeypatch):
    """Der Close traegt eine andere order_id — die document_id-Kette traegt trotzdem."""
    _write(
        tmp_path,
        monkeypatch,
        [
            _label("ord_entry", "technical_paper"),
            _fill("ord_entry", "doc_1"),
            _close("ord_exit_DIFFERENT", 42.0, document_id="doc_1"),
        ],
    )
    out = od.collect_paper_fills_24h(now=NOW)
    assert out["technical_paper"]["closes"] == 1
    assert out["technical_paper"]["pnl_usd"] == 42.0
    assert "unlabeled" not in out
    assert "unknown" not in out


def test_close_falls_back_to_signal_source(tmp_path, monkeypatch):
    """Ohne Label-Kette traegt ``signal_source`` der Close-Zeile."""
    _write(
        tmp_path,
        monkeypatch,
        [_close("ord_exit", -7.5, document_id="doc_orphan", signal_source="real_analysis")],
    )
    out = od.collect_paper_fills_24h(now=NOW)
    assert out["real_analysis"]["closes"] == 1
    assert out["real_analysis"]["pnl_usd"] == -7.5


def test_label_chain_wins_over_signal_source(tmp_path, monkeypatch):
    """Das bei Entry vergebene Label ist die harte Attribution und schlaegt den Fallback."""
    _write(
        tmp_path,
        monkeypatch,
        [
            _label("ord_entry", "technical_paper"),
            _fill("ord_entry", "doc_2"),
            _close("ord_exit", 1.0, document_id="doc_2", signal_source="autonomous_generator"),
        ],
    )
    out = od.collect_paper_fills_24h(now=NOW)
    assert out["technical_paper"]["closes"] == 1
    assert "autonomous_generator" not in out


def test_unattributable_close_is_visible_as_unknown(tmp_path, monkeypatch):
    """Nicht zuordenbar wird sichtbar gemacht, nicht geraten und nicht verschluckt."""
    _write(tmp_path, monkeypatch, [_close("ord_exit", 3.0)])
    out = od.collect_paper_fills_24h(now=NOW)
    assert out["unknown"]["closes"] == 1
    assert out["unknown"]["pnl_usd"] == 3.0


def test_opening_fill_attribution_unchanged(tmp_path, monkeypatch):
    """Regression: der Fill-Pfad joint weiterhin ueber order_id."""
    _write(
        tmp_path,
        monkeypatch,
        [_label("ord_entry", "real_analysis"), _fill("ord_entry", "doc_3")],
    )
    out = od.collect_paper_fills_24h(now=NOW)
    assert out["real_analysis"]["fills"] == 1
    assert out["real_analysis"]["closes"] == 0


def test_label_chain_survives_outside_the_24h_window(tmp_path, monkeypatch):
    """Entry vor dem Fenster, Close darin: die Kette muss ueber die Fenstergrenze tragen."""
    old = "2026-07-20T08:00:00+00:00"
    _write(
        tmp_path,
        monkeypatch,
        [
            {**_label("ord_entry", "technical_paper"), "timestamp_utc": old},
            {**_fill("ord_entry", "doc_4"), "timestamp_utc": old},
            _close("ord_exit", 10.0, document_id="doc_4"),
        ],
    )
    out = od.collect_paper_fills_24h(now=NOW)
    assert out["technical_paper"]["closes"] == 1
    assert out["technical_paper"]["fills"] == 0
