"""Das Rotations-Verdikt muss voll belastet rechnen — beide Arme.

``asset_performance_score`` dokumentiert seinen PnL-Arm als „net-of-fee realized
PnL". Geliefert wurde ``sum(trade_pnl_usd)``, und dieses Feld trägt die
Entry-Fee **nicht**: ``paper_engine`` zieht beim Schließen nur die Close-Fee ab.
``churn_report`` korrigiert das seit je nachträglich (`net = trade_pnl - ofee`),
dieser Pfad nicht.

Gemessen am Pi-Buch (2026-08-10, Epoche nach dem letzten `portfolio_epoch_reset`,
174 Closes): die fehlende Entry-Fee macht **43,5 %** des ausgewiesenen Betrags
aus — 261,90 USD auf 602,37 USD. Bei Fee-Drag ~120 % grob die halben
Round-Trip-Kosten.

Beide Arme hängen daran, nicht nur die Summe: ``wins`` zählt ``trade_pnl > 0``,
und ein Trade knapp über Null ist nach Abzug der Entry-Fee ein Verlust. Die
Verzerrung wirkt ausschließlich Richtung ``healthy`` — ein Asset wird nur dann
``weak``, wenn **beide** Arme fallen.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.learning.asset_lifecycle import AssetStatus
from app.learning.asset_rotation_shadow import AssetRotationState, evaluate_rotations
from app.observability.paper_quality_snapshot import build_paper_quality_snapshot


def _open(symbol: str, qty: float, fee: float) -> dict:
    return {
        "event_type": "order_filled",
        "symbol": symbol,
        "side": "buy",
        "position_side": "long",
        "filled_quantity": qty,
        "fee_usd": fee,
    }


def _close(symbol: str, qty: float, pnl: float, fee: float) -> dict:
    return {
        "event_type": "position_closed",
        "symbol": symbol,
        "schema_version": "v2",
        "quantity": qty,
        "trade_pnl_usd": pnl,
        "fee_usd": fee,
        "position_side": "long",
    }


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


class TestSnapshotLiefertBeideGroessen:
    def test_netto_zieht_die_entry_fee_ab(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.jsonl"
        _write(audit, [_open("BTC/USDT", 1.0, 2.0), _close("BTC/USDT", 1.0, 10.0, 1.0)])

        snap = build_paper_quality_snapshot(audit_path=audit, last_n=10)

        assert snap.sum_trade_pnl_usd == 10.0
        assert snap.sum_net_pnl_usd == 8.0
        assert snap.sum_open_fee_usd == 2.0

    def test_by_symbol_traegt_netto_und_netto_wins(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.jsonl"
        _write(
            audit,
            [
                _open("BTC/USDT", 1.0, 3.0),
                _close("BTC/USDT", 1.0, 2.0, 0.5),  # brutto +2, netto -1
            ],
        )

        snap = build_paper_quality_snapshot(audit_path=audit, last_n=10)
        stats = snap.by_symbol["BTC/USDT"]

        assert stats["sum_pnl_usd"] == 2.0
        assert stats["sum_net_pnl_usd"] == -1.0
        assert stats["wins"] == 1.0  # brutto zaehlt es als Gewinn
        assert stats["net_wins"] == 0.0  # voll belastet ist es ein Verlust

    def test_epochen_reset_leert_auch_die_offenen_positionen(self, tmp_path: Path) -> None:
        """Eine Entry-Fee aus dem archivierten Buch darf keinen neuen Trade belasten."""
        audit = tmp_path / "audit.jsonl"
        _write(
            audit,
            [
                _open("BTC/USDT", 1.0, 99.0),  # alte Epoche, teure Oeffnung
                {"event_type": "portfolio_epoch_reset", "timestamp_utc": "2026-08-01T00:00:00Z"},
                _open("BTC/USDT", 1.0, 1.0),
                _close("BTC/USDT", 1.0, 10.0, 0.5),
            ],
        )

        snap = build_paper_quality_snapshot(audit_path=audit, last_n=10)

        assert snap.sum_open_fee_usd == 1.0
        assert snap.sum_net_pnl_usd == 9.0


class TestVerdiktRechnetVollBelastet:
    def test_knapp_positives_asset_wird_weak(self) -> None:
        """Der Kern des Fehlers: brutto ``healthy``, voll belastet ``weak``."""
        by_symbol = {
            "BTC/USDT": {
                "count": 10.0,
                "wins": 3.0,
                "sum_pnl_usd": 5.0,  # brutto knapp positiv
                "net_wins": 2.0,
                "sum_net_pnl_usd": -20.0,  # voll belastet negativ
            }
        }

        decisions, _ = evaluate_rotations(
            by_symbol, {"BTC/USDT": AssetRotationState(AssetStatus.PROBATION, 0)}
        )

        [d] = decisions
        assert d["verdict"] == "weak", (
            "Mit der Bruttozahl waere dieses Asset healthy geblieben — die "
            "Verzerrung wirkt ausschliesslich in diese Richtung."
        )

    def test_fallback_auf_bruttofelder_bleibt_lesbar(self) -> None:
        """Aeltere Snapshots ohne Nettofelder duerfen nicht auf 0 kippen."""
        by_symbol = {"ETH/USDT": {"count": 10.0, "wins": 8.0, "sum_pnl_usd": 50.0}}

        decisions, _ = evaluate_rotations(
            by_symbol, {"ETH/USDT": AssetRotationState(AssetStatus.PROBATION, 0)}
        )

        [d] = decisions
        assert d["verdict"] == "healthy"
