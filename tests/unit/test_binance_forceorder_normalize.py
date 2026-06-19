"""Unit tests for the Binance force-order liquidation normalizer (#316)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.ingestion.liquidations.binance_forceorder import normalize_forceorder

_EVENT_MS = 1568014460893
_EVENT_TIME = datetime.fromtimestamp(_EVENT_MS / 1000, tz=UTC)


def _raw(side: str = "SELL", symbol: str = "BTCUSDT") -> dict:
    return {
        "e": "forceOrder",
        "E": _EVENT_MS,
        "o": {
            "s": symbol,
            "S": side,
            "o": "LIMIT",
            "f": "IOC",
            "q": "0.014",
            "p": "9910",
            "ap": "9910",
            "X": "FILLED",
            "l": "0.014",
            "z": "0.014",
            "T": _EVENT_MS,
        },
    }


def test_normalizes_sell_as_long_liquidation() -> None:
    received = _EVENT_TIME + timedelta(milliseconds=250)
    ev = normalize_forceorder(_raw("SELL"), received_at=received)
    assert ev is not None
    assert ev.exchange == "binance"
    assert ev.source == "binance_forceorder"
    assert ev.symbol == "BTCUSDT"
    assert ev.asset_id == "BTC"
    assert ev.side == "SELL"
    assert ev.liquidated_side == "LONG"  # forced SELL closes a LONG
    assert ev.price == 9910.0
    assert ev.quantity == 0.014
    assert ev.notional_usd == round(9910.0 * 0.014, 2)
    assert ev.event_time == _EVENT_TIME
    assert ev.latency_ms == 250
    assert ev.is_snapshot_limited is True
    assert ev.confidence == 1.0
    assert ev.schema_version == "liquidation_event.v1"


def test_buy_is_short_liquidation() -> None:
    ev = normalize_forceorder(_raw("BUY"))
    assert ev is not None
    assert ev.liquidated_side == "SHORT"


def test_unknown_side_maps_to_unknown() -> None:
    ev = normalize_forceorder(_raw(side="???"))
    assert ev is not None
    assert ev.liquidated_side == "UNKNOWN"


def test_asset_id_strips_quote_suffix() -> None:
    assert normalize_forceorder(_raw(symbol="ETHUSDC")).asset_id == "ETH"
    assert normalize_forceorder(_raw(symbol="1000PEPEUSDT")).asset_id == "1000PEPE"


def test_event_id_is_deterministic() -> None:
    a = normalize_forceorder(_raw(), received_at=_EVENT_TIME)
    b = normalize_forceorder(_raw(), received_at=_EVENT_TIME)
    assert a is not None and b is not None
    assert a.event_id == b.event_id
    assert a.raw_payload_hash == b.raw_payload_hash


def test_latency_never_negative_when_event_in_future() -> None:
    ev = normalize_forceorder(_raw(), received_at=_EVENT_TIME - timedelta(seconds=5))
    assert ev is not None
    assert ev.latency_ms == 0


def test_malformed_returns_none_failclosed() -> None:
    assert normalize_forceorder({}) is None
    assert normalize_forceorder({"o": {}}) is None
    assert normalize_forceorder({"o": {"s": "BTCUSDT"}}) is None  # no price/qty
    assert normalize_forceorder("not a dict") is None  # type: ignore[arg-type]
    # missing both ap and p
    bad = _raw()
    bad["o"].pop("ap")
    bad["o"].pop("p")
    assert normalize_forceorder(bad) is None
