"""CORE8 forecaster panel (shadow epoch) — resolver, store and engine tests.

No network: every test injects synthetic daily-klines fixtures as the provider.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.research.forecaster_panel import (
    EPOCH_ID,
    GENESIS_PREV_HASH,
    PANELS_FILENAME,
    REASON_DATA_GAP,
    RESOLUTIONS_FILENAME,
    STATUS_INVALID_PREDECLARED,
    STATUS_ISSUED,
    STATUS_RESOLVED,
    brier_score,
    compute_panel_hash,
    issue_panel,
    line_hash,
    panel_status,
    read_resolutions,
    resolve_due,
    verify_panel_chain,
)
from app.research.forecaster_resolvers import (
    BTC_SYMBOL,
    ETH_SYMBOL,
    DailyCandle,
    DataGap,
    KlinesUnavailableError,
    baseline_b0,
    median_decimal,
    multi_venue_median_provider,
    question_outcome,
    sample_stdev,
    sealed_median_for,
)

T0 = date(2026, 6, 1)


def _day(offset: int) -> date:
    return T0 + timedelta(days=offset)


def _candle(
    day: date,
    close: str | int,
    low: str | int | None = None,
    vol: str | int = 1000,
) -> DailyCandle:
    close_d = Decimal(str(close))
    low_d = Decimal(str(low)) if low is not None else close_d - Decimal("0.5")
    return DailyCandle(day=day, close=close_d, low=low_d, volume=Decimal(str(vol)))


def _data(
    btc: dict[date, DailyCandle] | None = None,
    eth: dict[date, DailyCandle] | None = None,
) -> dict[str, dict[date, DailyCandle]]:
    return {BTC_SYMBOL: btc or {}, ETH_SYMBOL: eth or {}}


def _provider(
    data: dict[str, dict[date, DailyCandle]],
) -> Callable[[str, date, date], dict[date, DailyCandle]]:
    def fetch(symbol: str, start: date, end: date) -> dict[date, DailyCandle]:
        return {d: c for d, c in data.get(symbol, {}).items() if start <= d <= end}

    return fetch


def _fixed_clock() -> datetime:
    return datetime(2026, 6, 2, 0, 5, 0, tzinfo=UTC)


def _full_fixture() -> dict[str, dict[date, DailyCandle]]:
    """Deterministic pseudo-random candles covering [T0-200, T0+31] for BTC+ETH."""
    btc: dict[date, DailyCandle] = {}
    eth: dict[date, DailyCandle] = {}
    for i in range(-200, 32):
        day = _day(i)
        c_btc = Decimal(100) + Decimal((i * 37) % 25) - Decimal(12)  # 88..112
        c_eth = Decimal(50) + Decimal((i * 29) % 21) - Decimal(10)  # 40..60
        vol = Decimal(1000) + Decimal((i * 53) % 500)
        btc[day] = DailyCandle(day=day, close=c_btc, low=c_btc - 2, volume=vol)
        eth[day] = DailyCandle(day=day, close=c_eth, low=c_eth - 1, volume=Decimal(500))
    return {BTC_SYMBOL: btc, ETH_SYMBOL: eth}


# --------------------------------------------------------------------------- #
# Decimal helpers
# --------------------------------------------------------------------------- #


def test_median_decimal_odd_even_empty() -> None:
    assert median_decimal([Decimal(3), Decimal(1), Decimal(2)]) == Decimal(2)
    assert median_decimal([Decimal(4), Decimal(1), Decimal(3), Decimal(2)]) == Decimal("2.5")
    with pytest.raises(ValueError):
        median_decimal([])


def test_sample_stdev_basics() -> None:
    assert sample_stdev([Decimal(5)] * 7) == Decimal(0)
    # [0, 2]: mean 1, sample variance (1+1)/1 = 2 -> stdev sqrt(2)
    assert sample_stdev([Decimal(0), Decimal(2)]) == Decimal(2).sqrt()
    with pytest.raises(ValueError):
        sample_stdev([Decimal(1)])


# --------------------------------------------------------------------------- #
# Q1/Q2 — close-to-close up (strict, tie -> no)
# --------------------------------------------------------------------------- #


def test_q1_up_down_tie_gap() -> None:
    up = _data(btc={T0: _candle(T0, 100), _day(7): _candle(_day(7), 101)})
    down = _data(btc={T0: _candle(T0, 100), _day(7): _candle(_day(7), 99)})
    tie = _data(btc={T0: _candle(T0, 100), _day(7): _candle(_day(7), "100.00")})
    assert question_outcome("Q1", up, T0) is True
    assert question_outcome("Q1", down, T0) is False
    assert question_outcome("Q1", tie, T0) is False  # tie -> "no"

    gap = question_outcome("Q1", _data(btc={T0: _candle(T0, 100)}), T0)
    assert isinstance(gap, DataGap)
    assert gap.count == 1
    assert gap.missing == (f"{BTC_SYMBOL}:{_day(7).isoformat()}",)


def test_q2_up_down() -> None:
    up = _data(btc={T0: _candle(T0, 100), _day(30): _candle(_day(30), "100.01")})
    down = _data(btc={T0: _candle(T0, 100), _day(30): _candle(_day(30), "99.99")})
    assert question_outcome("Q2", up, T0) is True
    assert question_outcome("Q2", down, T0) is False


# --------------------------------------------------------------------------- #
# Q3/Q8 — ETH vs BTC log-return race (strict, tie -> no)
# --------------------------------------------------------------------------- #


def test_q3_yes_no_tie() -> None:
    def rel(eth_end: str, btc_end: str) -> dict[str, dict[date, DailyCandle]]:
        return _data(
            btc={T0: _candle(T0, 100), _day(14): _candle(_day(14), btc_end)},
            eth={T0: _candle(T0, 50), _day(14): _candle(_day(14), eth_end)},
        )

    assert question_outcome("Q3", rel("60", "110"), T0) is True  # 20% vs 10%
    assert question_outcome("Q3", rel("52", "110"), T0) is False  # 4% vs 10%
    assert question_outcome("Q3", rel("55", "110"), T0) is False  # equal ratios: tie -> no

    gap = question_outcome("Q3", rel("60", "110"), _day(-1))
    assert isinstance(gap, DataGap)
    assert gap.count == 4  # all four reference closes missing at that anchor


def test_q8_yes_no() -> None:
    yes = _data(
        btc={T0: _candle(T0, 100), _day(30): _candle(_day(30), 105)},
        eth={T0: _candle(T0, 50), _day(30): _candle(_day(30), 56)},
    )
    no = _data(
        btc={T0: _candle(T0, 100), _day(30): _candle(_day(30), 120)},
        eth={T0: _candle(T0, 50), _day(30): _candle(_day(30), 55)},
    )
    assert question_outcome("Q8", yes, T0) is True
    assert question_outcome("Q8", no, T0) is False


# --------------------------------------------------------------------------- #
# Q5 — drawdown, low-based, t0 excluded, explicit <= threshold
# --------------------------------------------------------------------------- #


def _q5_fixture(min_low: str, t0_low: str = "99") -> dict[str, dict[date, DailyCandle]]:
    btc = {T0: _candle(T0, 100, low=t0_low)}
    for offset in range(1, 15):
        low = min_low if offset == 5 else "95"
        btc[_day(offset)] = _candle(_day(offset), 96, low=low)
    return _data(btc=btc)


def test_q5_exact_threshold_is_yes() -> None:
    # min(low)/ref - 1 == -0.10 exactly -> event (rule is <=, not <)
    assert question_outcome("Q5", _q5_fixture("90"), T0) is True


def test_q5_above_threshold_is_no() -> None:
    assert question_outcome("Q5", _q5_fixture("90.01"), T0) is False


def test_q5_t0_low_excluded() -> None:
    # a catastrophic low ON t0 must not trigger the event (window is t0+1..t0+14)
    assert question_outcome("Q5", _q5_fixture("95", t0_low="1"), T0) is False


def test_q5_gap_lists_missing_days() -> None:
    fixture = _q5_fixture("90")
    del fixture[BTC_SYMBOL][_day(9)]
    gap = question_outcome("Q5", fixture, T0)
    assert isinstance(gap, DataGap)
    assert gap.count == 1
    assert gap.missing == (f"{BTC_SYMBOL}:{_day(9).isoformat()}",)


# --------------------------------------------------------------------------- #
# Q4 — RV regime with sealed trailing median (constant 30)
# --------------------------------------------------------------------------- #


def _q4_flat_history() -> dict[str, dict[date, DailyCandle]]:
    # constant closes on [T0-37, T0-1] -> every trailing 7d-RV is 0 -> median 0
    btc = {_day(-o): _candle(_day(-o), 100) for o in range(1, 38)}
    return _data(btc=btc)


def test_q4_sealed_median_degenerate_and_outcome() -> None:
    fixture = _q4_flat_history()
    sealed = sealed_median_for("Q4", fixture, T0)
    assert sealed == Decimal(0)

    # varying closes on [T0, T0+7] -> RV > 0 -> above the (zero) median
    for offset in range(0, 8):
        close = 100 if offset % 2 == 0 else 101
        fixture[BTC_SYMBOL][_day(offset)] = _candle(_day(offset), close)
    assert isinstance(sealed, Decimal)
    assert question_outcome("Q4", fixture, T0, sealed) is True

    # constant closes on [T0, T0+7] -> RV == 0 == median -> strict ">" -> no
    for offset in range(0, 8):
        fixture[BTC_SYMBOL][_day(offset)] = _candle(_day(offset), 100)
    assert question_outcome("Q4", fixture, T0, sealed) is False


def test_q4_sealed_median_gap() -> None:
    fixture = _q4_flat_history()
    del fixture[BTC_SYMBOL][_day(-20)]
    sealed = sealed_median_for("Q4", fixture, T0)
    assert isinstance(sealed, DataGap)
    # the missing day sits in the 8 windows ending on [T0-20, T0-13]
    assert sealed.count == 8


def test_q4_requires_sealed_median() -> None:
    with pytest.raises(ValueError):
        question_outcome("Q4", _q4_flat_history(), T0, None)


# --------------------------------------------------------------------------- #
# Q6 — big week vs sealed trailing median (constant 180 -> 173 windows)
# --------------------------------------------------------------------------- #


def test_q6_sealed_median_degenerate_and_outcome() -> None:
    btc = {_day(-o): _candle(_day(-o), 100) for o in range(1, 181)}
    fixture = _data(btc=btc)
    sealed = sealed_median_for("Q6", fixture, T0)
    assert sealed == Decimal(0)

    fixture[BTC_SYMBOL][T0] = _candle(T0, 100)
    fixture[BTC_SYMBOL][_day(7)] = _candle(_day(7), 105)
    assert isinstance(sealed, Decimal)
    assert question_outcome("Q6", fixture, T0, sealed) is True

    fixture[BTC_SYMBOL][_day(7)] = _candle(_day(7), 100)  # |ln(1)| == 0 == median
    assert question_outcome("Q6", fixture, T0, sealed) is False


# --------------------------------------------------------------------------- #
# Q7 — volume regime vs sealed trailing median (constant 90), hand-computed
# --------------------------------------------------------------------------- #


def test_q7_sealed_median_linear_hand_check() -> None:
    # vol(T0+i) = 100 + (i + 96) for i in [-96, 0] -> 7d sums ending e are
    # strictly increasing. Sealed ends are [T0-90 .. T0-1]; sum(e) = 7*v(e)-21.
    # Median = mean of the 45th/46th smallest = (sum(T0-46)+sum(T0-45))/2
    #        = ((7*150-21) + (7*151-21)) / 2 = (1029 + 1036) / 2 = 1032.5
    btc: dict[date, DailyCandle] = {}
    for i in range(-96, 1):
        btc[_day(i)] = _candle(_day(i), 100, vol=100 + (i + 96))
    fixture = _data(btc=btc)

    sealed = sealed_median_for("Q7", fixture, T0)
    assert sealed == Decimal("1032.5")

    # current window sums vol[T0-6..T0] = 190+..+195+196 = 1351 > 1032.5 -> yes
    assert isinstance(sealed, Decimal)
    assert question_outcome("Q7", fixture, T0, sealed) is True


def test_q7_tie_is_no() -> None:
    # constant volume everywhere: every 7d sum == 700 -> current == median -> no
    btc = {_day(i): _candle(_day(i), 100, vol=100) for i in range(-96, 1)}
    fixture = _data(btc=btc)
    sealed = sealed_median_for("Q7", fixture, T0)
    assert sealed == Decimal(700)
    assert isinstance(sealed, Decimal)
    assert question_outcome("Q7", fixture, T0, sealed) is False


# --------------------------------------------------------------------------- #
# Baselines
# --------------------------------------------------------------------------- #


def test_baseline_b0_monotone_fixture() -> None:
    # strictly rising closes: every historical Q1 outcome is "yes" -> B0 = 1;
    # anchors are capped by the fixture span: back in [8, 200] -> 193 anchors.
    btc = {_day(i): _candle(_day(i), 300 + i, low=299 + i) for i in range(-200, 32)}
    data = _data(btc=btc)
    p, n = baseline_b0("Q1", data, T0)
    assert p == Decimal(1)
    assert n == 193

    # same fixture never draws down 10% -> Q5 climatology is exactly 0
    p5, n5 = baseline_b0("Q5", data, T0)
    assert p5 == Decimal(0)
    assert n5 == 186


def test_baseline_b0_empty_falls_back_to_half() -> None:
    p, n = baseline_b0("Q1", _data(), T0)
    assert p == Decimal("0.5")
    assert n == 0


# --------------------------------------------------------------------------- #
# Multi-venue median interface
# --------------------------------------------------------------------------- #


def test_multi_venue_median_combines_and_fail_closed() -> None:
    day = T0
    venue_a = _provider(_data(btc={day: _candle(day, 100, low=90, vol=10)}))
    venue_b = _provider(
        _data(
            btc={
                day: _candle(day, 102, low=94, vol=30),
                _day(1): _candle(_day(1), 103),  # only venue B has this day
            }
        )
    )
    combined = multi_venue_median_provider([venue_a, venue_b])(BTC_SYMBOL, day, _day(1))
    assert set(combined) == {day}  # day present in ALL venues only (fail-closed)
    assert combined[day].close == Decimal(101)
    assert combined[day].low == Decimal(92)
    assert combined[day].volume == Decimal(20)

    single = multi_venue_median_provider([venue_a])(BTC_SYMBOL, day, day)
    assert single[day].close == Decimal(100)


# --------------------------------------------------------------------------- #
# Issuance + hash chain
# --------------------------------------------------------------------------- #


def test_issue_panel_record_shape(tmp_path: Path) -> None:
    record = issue_panel(T0, _provider(_full_fixture()), store_dir=tmp_path, clock=_fixed_clock)
    payload = record.payload
    assert payload["schema"] == "forecaster_panel/shadow-v1"
    assert payload["epoch_id"] == EPOCH_ID == "shadow-0"
    assert payload["sealed"] is False
    assert payload["panel_index"] == 0
    assert payload["prev_panel_hash"] == GENESIS_PREV_HASH
    assert payload["reference_observation_id"] == "2026-06-01"
    assert payload["data_cutoff_at"] == "2026-06-02T00:00:00+00:00"
    assert payload["forecast_effective_at"] == "2026-06-02T00:00:00+00:00"
    assert payload["forecast_computed_at"] == _fixed_clock().isoformat()
    assert payload["venues"] == ["binance"]
    assert payload["shadow_single_venue"] is True

    questions = payload["questions"]
    assert [q["question_id"] for q in questions] == [f"Q{i}" for i in range(1, 9)]
    for q in questions:
        assert q["p_kai"] is None  # shadow epoch: never a model forecast
        assert q["status"] == STATUS_ISSUED
        baselines = q["baselines"]
        for key in ("b0", "b1"):
            p = Decimal(baselines[key])
            assert Decimal(0) <= p <= Decimal(1)
        has_median = q["question_id"] in ("Q4", "Q6", "Q7")
        assert (q["median_sealed"] is not None) is has_median

    by_id = {q["question_id"]: q for q in questions}
    assert by_id["Q1"]["due_at"] == "2026-06-09T00:00:00+00:00"  # t0+7 candle closed
    assert by_id["Q7"]["due_at"] == "2026-06-02T00:00:00+00:00"  # outcome window ends AT t0
    assert by_id["Q2"]["due_at"] == "2026-07-02T00:00:00+00:00"
    assert by_id["Q4"]["median_sealed"]["constant"] == 30
    assert by_id["Q6"]["median_sealed"]["n_windows"] == 173
    assert by_id["Q7"]["median_sealed"]["constant"] == 90

    lines = (tmp_path / PANELS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert lines == [record.line]
    assert record.panel_hash == compute_panel_hash(payload)
    assert verify_panel_chain(tmp_path) == []


def test_issue_panel_deterministic(tmp_path: Path) -> None:
    fixture = _full_fixture()
    a = issue_panel(T0, _provider(fixture), store_dir=tmp_path / "a", clock=_fixed_clock)
    b = issue_panel(T0, _provider(fixture), store_dir=tmp_path / "b", clock=_fixed_clock)
    assert a.line == b.line
    assert a.panel_hash == b.panel_hash


def test_issue_panel_duplicate_t0_raises(tmp_path: Path) -> None:
    provider = _provider(_full_fixture())
    issue_panel(T0, provider, store_dir=tmp_path, clock=_fixed_clock)
    with pytest.raises(ValueError, match="already issued"):
        issue_panel(T0, provider, store_dir=tmp_path, clock=_fixed_clock)


def test_issue_panel_chain_and_tamper_detection(tmp_path: Path) -> None:
    provider = _provider(_full_fixture())
    first = issue_panel(T0, provider, store_dir=tmp_path, clock=_fixed_clock)
    second = issue_panel(_day(7), provider, store_dir=tmp_path, clock=_fixed_clock)
    assert second.panel_index == 1
    assert second.payload["prev_panel_hash"] == line_hash(first.line)
    assert verify_panel_chain(tmp_path) == []

    panels_path = tmp_path / PANELS_FILENAME
    tampered = panels_path.read_text(encoding="utf-8").replace('"sealed":false', '"sealed":true', 1)
    panels_path.write_text(tampered, encoding="utf-8")
    errors = verify_panel_chain(tmp_path)
    assert any("panel_hash_mismatch" in e for e in errors)


def test_issuance_data_gap_invalidates_median_questions(tmp_path: Path) -> None:
    fixture = _full_fixture()
    del fixture[BTC_SYMBOL][_day(-20)]  # hole inside all three trailing-median ranges
    record = issue_panel(T0, _provider(fixture), store_dir=tmp_path, clock=_fixed_clock)

    by_id = {q["question_id"]: q for q in record.payload["questions"]}
    for qid in ("Q4", "Q6", "Q7"):
        q = by_id[qid]
        assert q["status"] == STATUS_INVALID_PREDECLARED
        assert q["invalid_reason"] == REASON_DATA_GAP
        assert q["median_sealed"] is None
        assert q["baselines"] is None
        assert q["missing_data"]["count"] >= 1
    for qid in ("Q1", "Q2", "Q3", "Q5", "Q8"):
        assert by_id[qid]["status"] == STATUS_ISSUED

    # invalid-at-issuance is terminal: resolution only touches the 5 issued ones
    written = resolve_due(datetime(2026, 7, 2, tzinfo=UTC), _provider(fixture), store_dir=tmp_path)
    assert {w["question_id"] for w in written} == {"Q1", "Q2", "Q3", "Q5", "Q8"}
    status = panel_status(store_dir=tmp_path)
    for qid in ("Q4", "Q6", "Q7"):
        assert status["questions"][qid]["invalid_at_issuance"] == 1


# --------------------------------------------------------------------------- #
# Resolution roundtrip
# --------------------------------------------------------------------------- #


def test_resolve_due_roundtrip(tmp_path: Path) -> None:
    fixture = _full_fixture()
    provider = _provider(fixture)
    issue_panel(T0, provider, store_dir=tmp_path, clock=_fixed_clock)

    now = datetime(2026, 7, 2, tzinfo=UTC)  # T0+31: every horizon complete
    written = resolve_due(now, provider, store_dir=tmp_path)
    assert len(written) == 8
    assert {w["question_id"] for w in written} == {f"Q{i}" for i in range(1, 9)}
    for w in written:
        assert w["schema"] == "forecaster_resolution/shadow-v1"
        assert w["panel_index"] == 0
        assert w["reference_observation_id"] == "2026-06-01"
        assert w["status"] == STATUS_RESOLVED
        assert isinstance(w["outcome"], bool)
        assert w["resolved_at"] == now.isoformat()
        assert set(w["brier"]) == {"b0", "b1"}

    # hand-derived from the fixture: close(T0)=88, close(T0+7)=97, close(T0+30)=98
    by_id = {w["question_id"]: w for w in written}
    assert by_id["Q1"]["outcome"] is True
    assert by_id["Q2"]["outcome"] is True

    # idempotent: a second pass writes nothing
    assert resolve_due(now, provider, store_dir=tmp_path) == []
    assert len(read_resolutions(tmp_path)) == 8
    assert verify_panel_chain(tmp_path) == []

    status = panel_status(store_dir=tmp_path)
    assert status["panels"] == 1
    assert status["resolutions"] == 8
    for qid in (f"Q{i}" for i in range(1, 9)):
        row = status["questions"][qid]
        assert row == {
            "issued": 1,
            "invalid_at_issuance": 0,
            "resolved": 1,
            "invalid_at_resolution": 0,
            "pending": 0,
        }


def test_resolve_due_partial_dueness(tmp_path: Path) -> None:
    fixture = _full_fixture()
    provider = _provider(fixture)
    issue_panel(T0, provider, store_dir=tmp_path, clock=_fixed_clock)

    # at T0+8 00:00 UTC only the 7d questions (and Q7) are complete
    written = resolve_due(datetime(2026, 6, 9, tzinfo=UTC), provider, store_dir=tmp_path)
    assert {w["question_id"] for w in written} == {"Q1", "Q4", "Q6", "Q7"}

    status = panel_status(store_dir=tmp_path)
    for qid in ("Q2", "Q3", "Q5", "Q8"):
        assert status["questions"][qid]["pending"] == 1
    for qid in ("Q1", "Q4", "Q6", "Q7"):
        assert status["questions"][qid]["resolved"] == 1


def test_resolve_due_data_gap(tmp_path: Path) -> None:
    fixture = _full_fixture()
    issue_panel(T0, _provider(fixture), store_dir=tmp_path, clock=_fixed_clock)

    # the T0+7 candle vanishes from the (authoritative) resolution fetch:
    # Q1/Q4/Q6 need its close, Q5 its low -> INVALID_PREDECLARED(data-gap)
    del fixture[BTC_SYMBOL][_day(7)]
    written = resolve_due(datetime(2026, 7, 2, tzinfo=UTC), _provider(fixture), store_dir=tmp_path)
    by_id = {w["question_id"]: w for w in written}
    for qid in ("Q1", "Q4", "Q5", "Q6"):
        assert by_id[qid]["status"] == STATUS_INVALID_PREDECLARED
        assert by_id[qid]["invalid_reason"] == REASON_DATA_GAP
        assert by_id[qid]["outcome"] is None
        assert by_id[qid]["brier"] is None
        assert by_id[qid]["missing_data"]["count"] >= 1
    for qid in ("Q2", "Q3", "Q7", "Q8"):
        assert by_id[qid]["status"] == STATUS_RESOLVED

    status = panel_status(store_dir=tmp_path)
    assert status["questions"]["Q1"]["invalid_at_resolution"] == 1
    assert status["questions"]["Q2"]["resolved"] == 1


def test_resolve_due_provider_failure_skips(tmp_path: Path) -> None:
    issue_panel(T0, _provider(_full_fixture()), store_dir=tmp_path, clock=_fixed_clock)

    def broken(symbol: str, start: date, end: date) -> dict[date, DailyCandle]:
        raise KlinesUnavailableError("venue down")

    written = resolve_due(datetime(2026, 7, 2, tzinfo=UTC), broken, store_dir=tmp_path)
    assert written == []
    assert not (tmp_path / RESOLUTIONS_FILENAME).exists()
    status = panel_status(store_dir=tmp_path)
    assert status["questions"]["Q1"]["pending"] == 1  # fail-closed: stays pending


# --------------------------------------------------------------------------- #
# Brier scoring
# --------------------------------------------------------------------------- #


def test_brier_score_hand_check() -> None:
    assert brier_score(Decimal("0.6"), True) == Decimal("0.16")
    assert brier_score(Decimal("0.6"), False) == Decimal("0.36")
    assert brier_score(Decimal("0.5"), True) == Decimal("0.25")
    assert brier_score(Decimal(1), True) == Decimal(0)


def test_resolution_brier_uses_recorded_baselines(tmp_path: Path) -> None:
    fixture = _full_fixture()
    provider = _provider(fixture)
    record = issue_panel(T0, provider, store_dir=tmp_path, clock=_fixed_clock)
    written = resolve_due(datetime(2026, 7, 2, tzinfo=UTC), provider, store_dir=tmp_path)

    panel_q1 = next(q for q in record.payload["questions"] if q["question_id"] == "Q1")
    resolution_q1 = next(w for w in written if w["question_id"] == "Q1")
    outcome = resolution_q1["outcome"]
    assert isinstance(outcome, bool)
    for key in ("b0", "b1"):
        recorded_p = Decimal(panel_q1["baselines"][key])
        expected = ((recorded_p - (Decimal(1) if outcome else Decimal(0))) ** 2).quantize(
            Decimal("0.00000001")
        )
        assert Decimal(resolution_q1["brier"][key]) == expected


# --------------------------------------------------------------------------- #
# Status on an empty store
# --------------------------------------------------------------------------- #


def test_panel_status_empty_store(tmp_path: Path) -> None:
    status = panel_status(store_dir=tmp_path)
    assert status["panels"] == 0
    assert status["resolutions"] == 0
    assert set(status["questions"]) == {f"Q{i}" for i in range(1, 9)}
    assert all(set(row.values()) == {0} for row in status["questions"].values())
