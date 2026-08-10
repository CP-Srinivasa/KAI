"""FIFO-Zuordnung der Entry-Fee — die Hälfte der Round-Trip-Kosten.

``trade_pnl_usd`` zieht nur die Close-Fee ab (`paper_engine.py`), die Entry-Fee
fehlt darin. ``churn_report`` korrigiert das seit je nachträglich, jeder andere
Konsument las den Wert roh — darunter ``paper_quality_snapshot``, dessen Ausgabe
als „net-of-fee realized PnL" das Rotations-Verdikt speist.
"""

from __future__ import annotations

from typing import Any

from app.execution.open_fee_match import is_entry_fill, match_open_fees


def _open(symbol: str, qty: float, fee: float, side: str = "buy", pside: str = "long") -> dict:
    return {
        "event_type": "order_filled",
        "symbol": symbol,
        "side": side,
        "position_side": pside,
        "filled_quantity": qty,
        "fee_usd": fee,
    }


def _close(symbol: str, qty: float, pnl: float, fee: float, **extra: Any) -> dict:
    row = {
        "event_type": "position_closed",
        "symbol": symbol,
        "quantity": qty,
        "trade_pnl_usd": pnl,
        "fee_usd": fee,
    }
    row.update(extra)
    return row


class TestEntryFillDetection:
    def test_long_buy_oeffnet(self) -> None:
        assert is_entry_fill(_open("BTC/USDT", 1.0, 0.1)) is True

    def test_short_sell_oeffnet(self) -> None:
        assert is_entry_fill(_open("BTC/USDT", 1.0, 0.1, side="sell", pside="short")) is True

    def test_long_sell_schliesst_und_oeffnet_nicht(self) -> None:
        assert is_entry_fill(_open("BTC/USDT", 1.0, 0.1, side="sell", pside="long")) is False

    def test_close_event_ist_kein_entry(self) -> None:
        assert is_entry_fill(_close("BTC/USDT", 1.0, 5.0, 0.1)) is False


class TestMatching:
    def test_ein_offen_ein_zu(self) -> None:
        rows = [_open("BTC/USDT", 2.0, 1.0), _close("BTC/USDT", 2.0, 10.0, 1.0)]

        [m] = match_open_fees(rows)

        assert m.open_fee_usd == 1.0
        assert m.trade_pnl_usd == 10.0
        # Voll belastet: 10 (schon ohne Close-Fee) minus 1 Entry-Fee.
        assert m.net_pnl_usd == 9.0
        assert m.orphan is False

    def test_teilschliessung_zieht_anteilig(self) -> None:
        rows = [_open("BTC/USDT", 4.0, 2.0), _close("BTC/USDT", 1.0, 5.0, 0.5)]

        [m] = match_open_fees(rows)

        # 2.0 Fee auf 4 Einheiten = 0.5/Einheit; 1 Einheit geschlossen.
        assert m.open_fee_usd == 0.5
        assert m.matched_quantity == 1.0

    def test_fifo_ueber_mehrere_oeffnungen(self) -> None:
        rows = [
            _open("BTC/USDT", 1.0, 1.0),  # 1.0/Einheit
            _open("BTC/USDT", 1.0, 3.0),  # 3.0/Einheit
            _close("BTC/USDT", 2.0, 20.0, 1.0),
        ]

        [m] = match_open_fees(rows)

        # FIFO: erst die billige, dann die teure Öffnung.
        assert m.open_fee_usd == 4.0

    def test_close_ohne_oeffnung_ist_orphan_nicht_gebuehrenfrei(self) -> None:
        """Legacy-Kontamination darf keine Fee von 0 erfinden."""
        [m] = match_open_fees([_close("BTC/USDT", 1.0, 5.0, 0.1)])

        assert m.orphan is True
        assert m.open_fee_usd == 0.0

    def test_symbole_vermischen_sich_nicht(self) -> None:
        rows = [
            _open("BTC/USDT", 1.0, 5.0),
            _open("ETH/USDT", 1.0, 1.0),
            _close("ETH/USDT", 1.0, 10.0, 0.1),
        ]

        [m] = match_open_fees(rows)

        assert m.record["symbol"] == "ETH/USDT"
        assert m.open_fee_usd == 1.0

    def test_partial_ohne_quantity_wird_arithmetisch_abgeleitet(self) -> None:
        """``position_partial_closed`` traegt weder quantity noch position_side."""
        rows = [
            _open("BTC/USDT", 4.0, 2.0),
            {
                "event_type": "position_partial_closed",
                "symbol": "BTC/USDT",
                "entry_price": 100.0,
                "exit_price": 110.0,
                "trade_pnl_usd": 19.0,
                "fee_usd": 1.0,
            },
        ]

        [m] = match_open_fees(rows)

        # gross = 19 + 1 = 20; span = 10 ⇒ qty = 2 ⇒ Fee = 2 * 0.5
        assert m.matched_quantity == 2.0
        assert m.open_fee_usd == 1.0

    def test_short_partial_wird_nicht_verworfen(self) -> None:
        """NEO-F-201: Ableitung ueber position_side verwarf Short-Partials still."""
        rows = [
            _open("BTC/USDT", 2.0, 1.0, side="sell", pside="short"),
            {
                "event_type": "position_partial_closed",
                "symbol": "BTC/USDT",
                "entry_price": 110.0,
                "exit_price": 100.0,
                "trade_pnl_usd": 19.0,
                "fee_usd": 1.0,
            },
        ]

        [m] = match_open_fees(rows)

        assert m.matched_quantity == 2.0
        assert m.open_fee_usd == 1.0

    def test_close_ohne_trade_pnl_wird_uebersprungen(self) -> None:
        """realized_pnl_usd ist KUMULATIV und nie ein Trade-Wert (NEO-P-101-r2)."""
        rows = [
            _open("BTC/USDT", 1.0, 1.0),
            {
                "event_type": "position_closed",
                "symbol": "BTC/USDT",
                "quantity": 1.0,
                "realized_pnl_usd": 12345.0,
            },
        ]

        assert match_open_fees(rows) == []
