"""Phase-B Binance resolver shell — pair mapping, fail-soft fetch, wiring.

No real network: urlopen is patched. Pins that a failed fetch returns None
(candidate stays pending) and that resolve_with_binance wires the fetcher into
the pure resolver.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.observability import shadow_resolver as sr
from app.observability.shadow_candidate_ledger import ShadowCandidate, record_candidate

T0 = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)


def test_pair_mapping() -> None:
    assert sr.to_binance_pair("BTC/USDT") == "BTCUSDT"
    assert sr.to_binance_pair("eth-usdt") == "ETHUSDT"


class _FakeResp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


def test_binance_spot_symbols_parses_trading_only(monkeypatch) -> None:
    monkeypatch.setattr(sr, "_spot_symbols_cache", None)
    info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING"},
            {"symbol": "ETHUSDT", "status": "TRADING"},
            {"symbol": "DEADUSDT", "status": "BREAK"},  # non-trading → excluded
        ]
    }
    monkeypatch.setattr(sr.urllib.request, "urlopen", lambda *a, **k: _FakeResp(info))
    assert sr.binance_spot_symbols(force=True) == frozenset({"BTCUSDT", "ETHUSDT"})


def test_binance_spot_symbols_failsoft_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(sr, "_spot_symbols_cache", None)

    def boom(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(sr.urllib.request, "urlopen", boom)
    assert sr.binance_spot_symbols(force=True) is None


def test_kline_fetch_failsoft_returns_none(monkeypatch) -> None:
    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(sr.urllib.request, "urlopen", boom)
    assert sr.binance_kline_fetcher("BTC/USDT", 0, 1000) is None


def test_resolve_with_binance_wires_fetcher(tmp_path: Path, monkeypatch) -> None:
    ledger = tmp_path / "ledger.jsonl"
    resolved = tmp_path / "resolved.jsonl"
    cand = ShadowCandidate.from_geometry(
        candidate_id="c1",
        ts_utc=T0.isoformat(),
        symbol="BTC/USDT",
        side="long",
        entry_price=100.0,
        stop_price=99.0,
        take_price=102.0,
    )
    record_candidate(cand, path=ledger)

    t0_ms = int(T0.timestamp() * 1000)
    synthetic = [
        (t0_ms + 60_000, 101.5, 100.0, 101.0),
        (t0_ms + 3600_000, 101.0, 99.5, 100.5),
    ]
    monkeypatch.setattr(sr, "binance_kline_fetcher", lambda *a, **k: synthetic)
    monkeypatch.setattr(sr, "binance_known_symbols", lambda: frozenset({"BTCUSDT"}))

    counts = sr.resolve_with_binance(
        now=T0 + timedelta(hours=2), ledger_path=ledger, resolved_path=resolved
    )
    assert counts["resolved"] == 1
    rec = json.loads(resolved.read_text(encoding="utf-8").splitlines()[0])
    assert rec["candidate_id"] == "c1"
    assert rec["mfe_bps"] == 150.0
    assert rec["stop_dist_bps"] == 100.0


# ── Nur Paare, die Binance fuehrt (26.09.2026: 99 265 HTTP-400 in 24 h) ──────


def test_unknown_pair_is_not_fetched_and_reported_once(caplog) -> None:
    calls: list[str] = []
    lookups: list[int] = []

    def fetch(symbol: str, start_ms: int, end_ms: int):
        calls.append(symbol)
        return [(0, 1.0, 1.0, 1.0)]

    def known():
        lookups.append(1)
        return frozenset({"BTCUSDT"})

    fetcher = sr.known_pairs_only(fetch=fetch, known=known)
    with caplog.at_level("INFO", logger=sr.__name__):
        assert fetcher("VELVET/USDT", 0, 1) is None
        assert fetcher("VELVET/USDT", 0, 1) is None
        assert fetcher("BTC/USDT", 0, 1) == [(0, 1.0, 1.0, 1.0)]
    assert calls == ["BTC/USDT"], "kein Netzaufruf fuer ein unbekanntes Paar"
    assert lookups == [1], "Symbolliste nur einmal je Fetcher"
    notes = [r.getMessage() for r in caplog.records if "VELVET" in r.getMessage()]
    assert len(notes) == 1, "je Paar genau eine Meldung statt einer je Zeile"


def test_without_symbol_list_everything_is_fetched_as_before() -> None:
    calls: list[str] = []
    fetcher = sr.known_pairs_only(fetch=lambda s, a, b: calls.append(s) or None, known=lambda: None)
    fetcher("VELVET/USDT", 0, 1)
    fetcher("BTC/USDT", 0, 1)
    assert calls == ["VELVET/USDT", "BTC/USDT"]


def test_halted_pair_counts_as_known(monkeypatch) -> None:
    """Ein pausiertes Paar (status BREAK) liefert weiter historische Klines."""
    monkeypatch.setattr(sr, "_spot_symbols_cache", None)
    monkeypatch.setattr(sr, "_known_symbols_cache", None)
    info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING"},
            {"symbol": "DEADUSDT", "status": "BREAK"},
        ]
    }
    monkeypatch.setattr(sr.urllib.request, "urlopen", lambda *a, **k: _FakeResp(info))
    assert sr.binance_known_symbols() == frozenset({"BTCUSDT", "DEADUSDT"})
    assert sr.binance_spot_symbols() == frozenset({"BTCUSDT"}), "Screener-Filter unveraendert"
