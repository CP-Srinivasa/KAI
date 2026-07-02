"""Hypothesis-ledger tests."""

from __future__ import annotations

from pathlib import Path

from app.research.ledger import HypothesisLedger, LedgerEntry, hypothesis_key


def _key(**over: object) -> str:
    base: dict[str, object] = {
        "name": "rsi_oversold_long",
        "timeframe": "1h",
        "horizon": 4,
        "round_trip_cost_bps": 20.0,
        "universe": ["BTC/USDT", "ETH/USDT"],
        "min_trades": 50,
        "alpha": 0.05,
    }
    base.update(over)
    return hypothesis_key(**base)  # type: ignore[arg-type]


def test_hypothesis_key_is_deterministic_and_universe_order_agnostic() -> None:
    a = _key(universe=["BTC/USDT", "ETH/USDT"])
    b = _key(universe=["ETH/USDT", "BTC/USDT"])  # reordered
    assert a == b
    assert len(a) == 16


def test_hypothesis_key_changes_with_config() -> None:
    base = _key()
    assert _key(name="macd_trend") != base
    assert _key(timeframe="4h") != base
    assert _key(horizon=8) != base
    assert _key(round_trip_cost_bps=30.0) != base
    assert _key(min_trades=30) != base
    assert _key(alpha=0.1) != base


def _entry(key: str, name: str = "rsi_oversold_long", survived: bool = False) -> LedgerEntry:
    return LedgerEntry(
        key=key,
        name=name,
        timeframe="1h",
        horizon=4,
        round_trip_cost_bps=20.0,
        universe=["BTC/USDT", "ETH/USDT"],
        survived=survived,
        mean_net_bps=-27.1,
        total_trades=1311,
        n_symbols_survived=0,
        as_of_utc="2026-06-23T13:04:58+00:00",
        lookback_days=180,
        recorded_at_utc="2026-06-23T13:04:58+00:00",
    )


def test_record_and_read_roundtrip(tmp_path: Path) -> None:
    ledger = HypothesisLedger(tmp_path / "ledger.jsonl")
    k = _key()
    ledger.record(_entry(k))
    entries = ledger.entries()
    assert len(entries) == 1
    assert entries[0].key == k
    assert entries[0].name == "rsi_oversold_long"
    assert entries[0].total_trades == 1311
    assert entries[0].universe == ["BTC/USDT", "ETH/USDT"]


def test_was_tested_and_tested_count(tmp_path: Path) -> None:
    ledger = HypothesisLedger(tmp_path / "ledger.jsonl")
    k1, k2 = _key(name="a"), _key(name="b")
    ledger.record(_entry(k1, name="a"))
    ledger.record(_entry(k2, name="b"))
    ledger.record(_entry(k1, name="a"))  # repeat config (new data run)
    assert ledger.was_tested(k1)
    assert ledger.was_tested(k2)
    assert not ledger.was_tested(_key(name="never"))
    assert ledger.tested_count() == 2  # distinct configs, not rows


def test_missing_file_is_empty(tmp_path: Path) -> None:
    ledger = HypothesisLedger(tmp_path / "nope.jsonl")
    assert ledger.entries() == []
    assert ledger.tested_count() == 0


def test_corrupt_line_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = HypothesisLedger(path)
    ledger.record(_entry(_key()))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
        fh.write("\n")  # blank line
    assert len(ledger.entries()) == 1  # the one good row survives


def test_aggregates_to_ledger_entries_maps_survived_and_keys() -> None:
    from app.research.runner import HypothesisAggregate, aggregates_to_ledger_entries

    aggs = [
        HypothesisAggregate(
            name="winner",
            n_symbols_evaluated=5,
            n_symbols_survived=2,
            mean_net_bps=12.0,
            total_trades=300,
        ),
        HypothesisAggregate(
            name="loser",
            n_symbols_evaluated=5,
            n_symbols_survived=0,
            mean_net_bps=-20.0,
            total_trades=400,
        ),
    ]
    entries = aggregates_to_ledger_entries(
        aggs,
        timeframe="1h",
        horizon=4,
        round_trip_cost_bps=20.0,
        universe=["BTC/USDT", "ETH/USDT"],
        min_trades=50,
        alpha=0.05,
        as_of_utc="2026-06-23T13:00:00+00:00",
        lookback_days=180,
        recorded_at_utc="2026-06-23T13:00:00+00:00",
    )
    by_name = {e.name: e for e in entries}
    assert by_name["winner"].survived is True  # 2 symbols survived
    assert by_name["loser"].survived is False
    assert by_name["winner"].key != by_name["loser"].key
    # The embedded key matches the standalone hypothesis_key() for the same config.
    assert by_name["winner"].key == hypothesis_key(
        name="winner",
        timeframe="1h",
        horizon=4,
        round_trip_cost_bps=20.0,
        universe=["BTC/USDT", "ETH/USDT"],
        min_trades=50,
        alpha=0.05,
    )
