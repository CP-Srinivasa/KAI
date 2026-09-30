"""L2-Ergebnisse (Weg 2, Operator-Entscheid 2026-09-30).

Jede L2-Messung mit Kandidatenkontext bekommt nach 1 h ein Ergebnis in einer eigenen
Datei, unabhaengig davon, ob das Risiko-Gate das Signal gestoppt hat. Diese Tests
halten fest: richtige Rendite und Richtung, ehrlicher Einstiegskurs, kein zweites
Schreiben, Warten statt Raten bei fehlenden Kerzen, und dass die Ergebnisse mit
``pit_join`` wirklich Paare bilden (Befund: bis 30.09. null Paare).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.health_check_streams import check_l2_outcomes
from app.observability import l2_outcomes as lo
from app.observability.l2_evidence_eval import pit_join

_START = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
_NOW = _START + timedelta(hours=2)


def _bars(start: datetime, *, first: float = 100.0, step: float = 0.1, minutes: int = 70) -> list:
    t0 = int((start - timedelta(minutes=3)).timestamp() * 1000)
    return [
        (t0 + i * 60_000, first + i * step + 0.5, first + i * step - 0.5, first + i * step)
        for i in range(minutes)
    ]


def _new_row(cid: str = "cyc_a", direction: str = "long", price: float | None = 100.3) -> dict:
    row = {
        "ts": (_START + timedelta(seconds=0.6)).isoformat(),
        "symbol": "BTC/USDT",
        "direction": direction,
        "fee_percentile": 0.7,
        "mempool_percentile": 0.4,
        "candidate_id": cid,
        "cycle_started_at": _START.isoformat(),
        "input_cutoff_ts": (_START + timedelta(seconds=0.4)).isoformat(),
        "reference_price_ts": (_START + timedelta(seconds=0.17)).isoformat(),
        "l1_observed_ts": (_START - timedelta(minutes=10)).isoformat(),
        "causality_ok": True,
    }
    if price is not None:
        row["reference_price"] = price
    return row


def _interim_row(cid: str = "cyc_i") -> dict:
    return {
        "ts": (_START + timedelta(seconds=0.63)).isoformat(),
        "symbol": "BTC/USDT",
        "direction": "long",
        "fee_percentile": 0.2,
        "mempool_percentile": 0.9,
        "candidate_id": cid,
        "decision_ts": _START.isoformat(),
        "reference_price_ts": (_START + timedelta(seconds=0.17)).isoformat(),
        "causality_ok": False,
    }


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


# ---------------------------------------------------------------- Rechnung


def test_long_return_uses_the_price_the_signal_saw() -> None:
    row = lo.outcome_for(_new_row(price=100.3), _bars(_START), _NOW)
    assert row is not None and row["entry_price_basis"] == "reference_price"
    # Anker = Kerze 3 (Kurs 100,3); nach 60 min Kerze 63 (Kurs 106,3): +6 / 100,3.
    assert row["net_bps"] == round((106.3 - 100.3) / 100.3 * 1e4, 2)
    assert row["entry_ts"] == _START.isoformat() and row["status"] == "resolved"
    assert row["measurement_form"] == "input_cutoff"


def test_short_return_is_sign_flipped() -> None:
    row = lo.outcome_for(_new_row(direction="short"), _bars(_START), _NOW)
    assert row is not None and row["net_bps"] < 0


def test_interim_rows_take_the_last_complete_minute_before_the_anchor() -> None:
    row = lo.outcome_for(_interim_row(), _bars(_START), _NOW)
    assert row is not None
    assert row["entry_price_basis"] == "kline_prev_close"
    # Letzte vollstaendige Minute vor 12:00 ist 11:59 (Index 2, Schluss 100,2) -- nicht
    # die Kerze, die erst nach dem Anker schliesst.
    assert row["entry_price"] == 100.2
    assert row["measurement_form"] == "decision_ts"


def test_incomplete_bars_wait_and_old_ones_close_as_no_data() -> None:
    short = _bars(_START, minutes=30)
    assert lo.outcome_for(_new_row(), short, _NOW) is None  # spaeter erneut
    late = lo.outcome_for(_new_row(), short, _START + timedelta(days=3))
    assert late is not None and late["status"] == "no_data" and late["net_bps"] is None
    assert late["no_data_reason"] == "no_complete_bars"


def test_pairs_without_bars_that_do_not_trade_close_at_once(tmp_path: Path) -> None:
    # 30.09.: COIN/NVDA/KLAY/XDP haette der Waechter sonst zwei Tage lang gemeldet. Ein
    # angehaltenes Paar mit Historie (HALT) bekommt dagegen sein Ergebnis.
    shadow = _write(
        tmp_path / "l2.jsonl",
        [{**_new_row("c"), "symbol": "COIN/USDT"}, {**_new_row("h"), "symbol": "HALT/USDT"}],
    )
    out = tmp_path / "o.jsonl"
    fetched: list[str] = []

    def fetch(symbol: str, start_ms: int, end_ms: int):  # noqa: ANN202
        fetched.append(symbol)
        return None if symbol == "COIN/USDT" else _bars(_START)

    counts = lo.resolve(
        fetch,
        now=_NOW,
        shadow_path=shadow,
        outcomes_path=out,
        not_trading=lambda s: True,
    )
    assert (
        counts["no_data"] == 1 and counts["resolved"] == 1 and fetched == ["COIN/USDT", "HALT/USDT"]
    )
    coin = next(r for r in lo._read_jsonl(out) if r["candidate_id"] == "c")
    assert coin["no_data_reason"] == "pair_not_trading"
    assert lo.backlog(_NOW + timedelta(hours=2), shadow_path=shadow, outcomes_path=out) == []


def test_only_unique_context_rows_with_direction_are_resolvable() -> None:
    rows = [_new_row("dup"), _new_row("dup"), {**_new_row("x"), "direction": "flat"}]
    rows += [{k: v for k, v in _new_row("noid").items() if k != "candidate_id"}, _new_row("ok")]
    assert list(lo.resolvable(rows)) == ["ok"]


def test_due_only_after_horizon_plus_settle_and_within_14_days() -> None:
    m = _new_row()
    assert not lo.due(m, _START + timedelta(minutes=62))
    assert lo.due(m, _START + timedelta(minutes=64))
    assert not lo.due(m, _START + timedelta(days=15))


# ---------------------------------------------------------------- Lauf


def test_resolve_writes_once_and_waits_on_missing_data(tmp_path: Path) -> None:
    shadow = _write(tmp_path / "l2.jsonl", [_new_row("a"), _new_row("b")])
    out = tmp_path / "l2_outcomes.jsonl"
    calls: list[str] = []

    def fetch(symbol: str, start_ms: int, end_ms: int):  # noqa: ANN202
        calls.append(symbol)
        return _bars(_START) if len(calls) == 1 else None  # zweite Messung: Quelle weg

    first = lo.resolve(fetch, now=_NOW, shadow_path=shadow, outcomes_path=out)
    assert first["resolved"] == 1 and first["pending"] == 1
    again = lo.resolve(lambda *a: _bars(_START), now=_NOW, shadow_path=shadow, outcomes_path=out)
    assert again["already"] == 1 and again["resolved"] == 1
    assert [json.loads(line)["candidate_id"] for line in out.read_text().splitlines()] == ["a", "b"]


def test_a_failing_source_never_raises(tmp_path: Path) -> None:
    shadow = _write(tmp_path / "l2.jsonl", [_new_row("a")])

    def boom(*_a):  # noqa: ANN202
        raise RuntimeError("binance down")

    counts = lo.resolve(boom, now=_NOW, shadow_path=shadow, outcomes_path=tmp_path / "o.jsonl")
    assert counts["pending"] == 1


def test_results_form_real_pairs_for_both_measurement_forms(tmp_path: Path) -> None:
    rows = [_new_row("new"), _interim_row("old")]
    shadow = _write(tmp_path / "l2.jsonl", rows)
    out = tmp_path / "l2_outcomes.jsonl"
    lo.resolve(lambda *a: _bars(_START), now=_NOW, shadow_path=shadow, outcomes_path=out)
    pairs = pit_join(rows, lo.load_feature_outcomes(out))
    assert sorted(m["candidate_id"] for m, _o in pairs) == ["new", "old"]


# ---------------------------------------------------------------- Waechter


def test_watcher_warns_on_backlog_and_is_quiet_when_resolved(tmp_path: Path) -> None:
    _write(tmp_path / "l2_evidence_shadow.jsonl", [_new_row("a")])
    later = _START + timedelta(hours=4)
    # Anlauf: Rechner hat noch nie geschrieben -> still (Release-Nachkontrolle prueft).
    assert check_l2_outcomes(tmp_path, later) == []
    (tmp_path / "l2_outcomes.jsonl").write_text("", encoding="utf-8")
    [issue] = check_l2_outcomes(tmp_path, later)
    assert issue.component == "l2_outcomes" and "(z. B. a)" in issue.message
    lo.resolve(
        lambda *a: _bars(_START),
        now=later,
        shadow_path=tmp_path / "l2_evidence_shadow.jsonl",
        outcomes_path=tmp_path / "l2_outcomes.jsonl",
    )
    assert check_l2_outcomes(tmp_path, later) == []


def test_watcher_is_quiet_without_measurements_or_within_3h(tmp_path: Path) -> None:
    (tmp_path / "l2_outcomes.jsonl").write_text("", encoding="utf-8")
    assert check_l2_outcomes(tmp_path, _NOW) == []
    _write(tmp_path / "l2_evidence_shadow.jsonl", [_new_row("a")])
    assert check_l2_outcomes(tmp_path, _START + timedelta(hours=2)) == []


# ---------------------------------------------------------------- Einbindung


def test_shadow_resolver_runs_l2_and_survives_its_failure(monkeypatch) -> None:
    from app.observability import shadow_resolver as sr

    monkeypatch.setattr(sr, "known_pairs_only", lambda: lambda *a: None)
    monkeypatch.setattr(sr, "resolve_pending", lambda **kw: {"resolved": 0})
    monkeypatch.setattr(
        "app.observability.l2_outcomes.resolve",
        lambda fetch, now=None, not_trading=None: {"resolved": 3, "pending": 1, "not_due": 2},
    )
    assert sr.resolve_with_binance()["l2_resolved"] == 3
    monkeypatch.setattr(sr, "binance_spot_symbols", lambda: frozenset({"BTCUSDT"}))
    assert sr._not_trading_on_binance("KLAY/USDT") and not sr._not_trading_on_binance("BTC/USDT")
    monkeypatch.setattr(sr, "binance_spot_symbols", lambda: None)
    assert not sr._not_trading_on_binance("KLAY/USDT")  # Liste unbekannt: weiter warten

    def boom(*a, **k):  # noqa: ANN202
        raise RuntimeError("kaputt")

    monkeypatch.setattr("app.observability.l2_outcomes.resolve", boom)
    assert sr.resolve_with_binance()["l2_resolved"] == -1


def test_the_measurement_carries_the_entry_price(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from app.core.evidence_settings import L2OnChainEvidenceSettings
    from app.signals.l2_wiring import build_l2_onchain_evidence_provider
    from app.signals.models import SignalDirection

    stream = tmp_path / "s.jsonl"
    now = datetime.now(UTC)
    stream.write_text(
        "".join(
            json.dumps(
                {
                    "ts": (now - timedelta(minutes=30 - i)).isoformat(),
                    "fee_sat_vb": i + 1.0,
                    "mempool_tx": i,
                }
            )
            + "\n"
            for i in range(30)
        ),
        encoding="utf-8",
    )
    shadow = tmp_path / "l2_shadow.jsonl"
    cfg = L2OnChainEvidenceSettings(
        enabled=True, stream_path=stream, shadow_log_path=shadow, min_window=5
    )
    provider = build_l2_onchain_evidence_provider(cfg)
    provider(None, SimpleNamespace(symbol="BTC/USDT", price=65_000.5), SignalDirection.LONG)
    assert json.loads(shadow.read_text(encoding="utf-8").strip())["reference_price"] == 65_000.5
