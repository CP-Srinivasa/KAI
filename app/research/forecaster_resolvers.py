"""CORE8 forecaster panel — resolvers, sealed medians, shadow baselines (pure math).

SHADOW-epoch machinery for the "CORE8 Calibrated Forecaster Panel" (design draft
v0.6, NOT sealed, NOT a product, NOT published). The shadow phase exercises the
issuance/resolution/scoring machinery ONLY: there is no KAI model forecast
(``p_kai`` stays ``null``); only the deterministic baselines B0/B1 are scored.

This module is the PURE layer — no I/O, no network, no store:

* :class:`DailyCandle` + the injected daily-klines provider contract
  (:data:`DailyKlinesProvider`) and a multi-venue-median combinator (the sealed
  epoch will run >=2 venues; shadow runs single-venue Binance).
* Decimal-only window helpers (log returns, 7d realized vol, 7d volume sums).
  Design pin: "Decimal-Arithmetik, kein float" — every resolver comparison is
  ``decimal.Decimal`` vs ``decimal.Decimal``, strict inequality, tie -> "no"
  (Q5 is the documented exception: its rule is an explicit ``<= -0.10``).
* The eight CORE8 outcome predicates (:func:`question_outcome`).
* The sealed trailing-median constants for Q4/Q6/Q7
  (:func:`sealed_median_for`): computed from pre-t0 data at issuance and FROZEN
  into the panel record, so resolution later runs deterministically against the
  recorded value. "VOR t0" is implemented strictly: every median window ends at
  ``t0 - 1`` or earlier (only pre-anchor data enters a sealed constant).
* SHADOW baselines: B0 (trailing-365d climatology) and SHADOW-B1 (simple,
  deterministic per-question naive forecasters). These are honest placeholders;
  the final B1 adjudication happens at seal time (design draft §5).

Missing datapoints NEVER guess: a resolver returns :class:`DataGap` and the
engine records ``INVALID_PREDECLARED(data-gap)``. Records are never deleted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

BTC_SYMBOL = "BTCUSDT"
ETH_SYMBOL = "ETHUSDT"

# Q5 rule is an explicit non-strict bound: min(low)/ref - 1 <= -0.10 -> "yes".
DRAWDOWN_THRESHOLD = Decimal("-0.10")

# Baseline trailing windows (calendar days before t0).
B0_TRAILING_DAYS = 365
B1_TRAILING_DAYS = 180

# Sealed trailing-median constants (design draft §4, frozen per question).
Q4_MEDIAN_CONSTANT = 30  # trailing 30 rolling 7d-RV windows before t0
Q6_MEDIAN_CONSTANT = 180  # day-anchored 7d windows inside trailing 180 calendar days
Q7_MEDIAN_CONSTANT = 90  # trailing 90 rolling 7d volume-sum windows before t0
_WINDOW_SPAN_DAYS = 7  # the 7d window length shared by Q4/Q6/Q7

# Probabilities / scores are quantized for stable, deterministic serialization.
PROB_QUANTUM = Decimal("0.000001")

# Last data date a question needs, as a calendar-day offset from t0.
# Q7 is 0 by construction: its sealed rule sums vol[t0-6 .. t0] (draft §4), so
# the outcome window closes with the t0 candle itself.
FINAL_DATA_OFFSET_DAYS: dict[str, int] = {
    "Q1": 7,
    "Q2": 30,
    "Q3": 14,
    "Q4": 7,
    "Q5": 14,
    "Q6": 7,
    "Q7": 0,
    "Q8": 30,
}

_MEDIAN_QUESTIONS = ("Q4", "Q6", "Q7")


@dataclass(frozen=True)
class DailyCandle:
    """One daily (00:00 UTC anchored) candle with Decimal fields only."""

    day: date
    close: Decimal
    low: Decimal
    volume: Decimal


# fetch(symbol, start_date, end_date) -> {day: candle} for days present at the venue.
# Contract: a RETURNED mapping is authoritative (absent day == data gap); a
# transport/venue failure must RAISE (e.g. KlinesUnavailableError) so the engine
# skips instead of writing false data-gap invalidations.
DailyKlinesProvider = Callable[[str, date, date], Mapping[date, DailyCandle]]

# symbol -> day -> candle, as fetched for one issuance/resolution pass.
PanelData = Mapping[str, Mapping[date, DailyCandle]]

# Memoization for window values within one engine pass: (question_id, end) -> value.
WindowCache = dict[tuple[str, date], Decimal | None]


class KlinesUnavailableError(RuntimeError):
    """Venue fetch failed (transport/empty) — NOT a data gap; caller must skip."""


@dataclass(frozen=True)
class DataGap:
    """A required datapoint/window is missing — resolves to INVALID_PREDECLARED.

    ``missing`` carries up to 10 detail keys (``SYMBOL:YYYY-MM-DD``); ``count``
    is the total number of missing datapoints/windows.
    """

    missing: tuple[str, ...]
    count: int


@dataclass(frozen=True)
class QuestionSpec:
    """Static metadata of one CORE8 question (shadow epoch)."""

    question_id: str
    title: str
    rule: str
    symbols: tuple[str, ...]
    horizon_days: int  # final-data offset from t0 (drives due_at)
    median_constant: int | None  # 30 / 180 / 90 for Q4/Q6/Q7, else None

    @property
    def needs_sealed_median(self) -> bool:
        return self.median_constant is not None


CORE8: tuple[QuestionSpec, ...] = (
    QuestionSpec("Q1", "BTC 7d up", "ref[t0+7] > ref[t0]", (BTC_SYMBOL,), 7, None),
    QuestionSpec("Q2", "BTC 30d up", "ref[t0+30] > ref[t0]", (BTC_SYMBOL,), 30, None),
    QuestionSpec(
        "Q3",
        "ETH beats BTC 14d",
        "ln(ETH[t0+14]/ETH[t0]) > ln(BTC[t0+14]/BTC[t0])",
        (ETH_SYMBOL, BTC_SYMBOL),
        14,
        None,
    ),
    QuestionSpec(
        "Q4",
        "Vol regime 7d (RV)",
        "RV_7d(t0..t0+7) > median_sealed(trailing 30 rolling 7d-RV before t0)",
        (BTC_SYMBOL,),
        7,
        Q4_MEDIAN_CONSTANT,
    ),
    QuestionSpec(
        "Q5",
        "Drawdown >=10% in 14d",
        "min(low[t0+1..t0+14]) / ref[t0] - 1 <= -0.10",
        (BTC_SYMBOL,),
        14,
        None,
    ),
    QuestionSpec(
        "Q6",
        "Big week",
        "|ln(ref[t0+7]/ref[t0])| > median_sealed(day-anchored 7d windows, trailing 180d)",
        (BTC_SYMBOL,),
        7,
        Q6_MEDIAN_CONSTANT,
    ),
    QuestionSpec(
        "Q7",
        "Volume activity regime 7d",
        "sum(vol[t0-6..t0]) > median_sealed(trailing 90 rolling 7d volume sums before t0)",
        (BTC_SYMBOL,),
        0,
        Q7_MEDIAN_CONSTANT,
    ),
    QuestionSpec(
        "Q8",
        "ETH/BTC rotation 30d",
        "ln(ETH[t0+30]/ETH[t0]) > ln(BTC[t0+30]/BTC[t0])",
        (ETH_SYMBOL, BTC_SYMBOL),
        30,
        None,
    ),
)

QUESTION_IDS: tuple[str, ...] = tuple(spec.question_id for spec in CORE8)


def spec_for(question_id: str) -> QuestionSpec:
    """Lookup a CORE8 spec by id (raises ``ValueError`` on unknown ids)."""
    for spec in CORE8:
        if spec.question_id == question_id:
            return spec
    raise ValueError(f"unknown CORE8 question_id: {question_id!r}")


# --------------------------------------------------------------------------- #
# Decimal helpers
# --------------------------------------------------------------------------- #


def median_decimal(values: Sequence[Decimal]) -> Decimal:
    """Median of Decimals: odd n -> middle; even n -> mean of the two middles."""
    if not values:
        raise ValueError("median of empty sequence")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def sample_stdev(values: Sequence[Decimal]) -> Decimal:
    """Sample standard deviation (n-1 denominator) in pure Decimal."""
    n = len(values)
    if n < 2:
        raise ValueError("sample_stdev needs >= 2 values")
    mean = sum(values, Decimal(0)) / Decimal(n)
    var = sum(((v - mean) ** 2 for v in values), Decimal(0)) / Decimal(n - 1)
    return var.sqrt()


def _prob(yes: int, n: int) -> Decimal:
    """Empirical frequency as quantized Decimal; n == 0 -> 0.5 (documented fallback)."""
    if n == 0:
        return Decimal("0.5")
    return (Decimal(yes) / Decimal(n)).quantize(PROB_QUANTUM, rounding=ROUND_HALF_EVEN)


def _close(data: PanelData, symbol: str, day: date) -> Decimal | None:
    candle = data.get(symbol, {}).get(day)
    if candle is None or candle.close <= 0:
        return None
    return candle.close


def _low(data: PanelData, symbol: str, day: date) -> Decimal | None:
    candle = data.get(symbol, {}).get(day)
    if candle is None or candle.low <= 0:
        return None
    return candle.low


def _volume(data: PanelData, symbol: str, day: date) -> Decimal | None:
    candle = data.get(symbol, {}).get(day)
    if candle is None or candle.volume < 0:
        return None
    return candle.volume


def _gap(missing: list[str]) -> DataGap:
    return DataGap(tuple(missing[:10]), len(missing))


# --------------------------------------------------------------------------- #
# Window values (shared by resolvers, sealed medians and baselines)
# --------------------------------------------------------------------------- #


def window_value(
    question_id: str,
    data: PanelData,
    end: date,
    cache: WindowCache | None = None,
) -> Decimal | None:
    """The Q4/Q6/Q7 rolling-window value for the 7d window ENDING at ``end``.

    * Q4: sample stdev of the 7 daily log returns over closes ``[end-7, end]``.
    * Q6: ``|ln(close[end] / close[end-7])|``.
    * Q7: sum of daily volumes over ``[end-6, end]``.

    Returns ``None`` when any required candle is missing (data gap).
    """
    key = (question_id, end)
    if cache is not None and key in cache:
        return cache[key]
    value = _window_value_uncached(question_id, data, end)
    if cache is not None:
        cache[key] = value
    return value


def _window_value_uncached(question_id: str, data: PanelData, end: date) -> Decimal | None:
    if question_id == "Q4":
        closes: list[Decimal] = []
        for back in range(_WINDOW_SPAN_DAYS, -1, -1):
            c = _close(data, BTC_SYMBOL, end - timedelta(days=back))
            if c is None:
                return None
            closes.append(c)
        returns = [(closes[i] / closes[i - 1]).ln() for i in range(1, len(closes))]
        return sample_stdev(returns)
    if question_id == "Q6":
        c_start = _close(data, BTC_SYMBOL, end - timedelta(days=_WINDOW_SPAN_DAYS))
        c_end = _close(data, BTC_SYMBOL, end)
        if c_start is None or c_end is None:
            return None
        return abs((c_end / c_start).ln())
    if question_id == "Q7":
        total = Decimal(0)
        for back in range(_WINDOW_SPAN_DAYS - 1, -1, -1):
            v = _volume(data, BTC_SYMBOL, end - timedelta(days=back))
            if v is None:
                return None
            total += v
        return total
    raise ValueError(f"window_value undefined for {question_id!r}")


def median_window_ends(question_id: str, t0: date) -> list[date]:
    """Window-END dates of the sealed trailing median, newest first.

    Strictly pre-t0: every end is ``t0 - 1`` or earlier. Q6's constant 180 is
    the trailing calendar-day SPAN; its day-anchored 7d windows inside
    ``[t0-180, t0-1]`` yield 180 - 7 = 173 fully contained windows.
    """
    if question_id == "Q4":
        n = Q4_MEDIAN_CONSTANT
    elif question_id == "Q6":
        n = Q6_MEDIAN_CONSTANT - _WINDOW_SPAN_DAYS
    elif question_id == "Q7":
        n = Q7_MEDIAN_CONSTANT
    else:
        raise ValueError(f"no sealed median for {question_id!r}")
    return [t0 - timedelta(days=back) for back in range(1, n + 1)]


def sealed_median_for(
    question_id: str,
    data: PanelData,
    t0: date,
    cache: WindowCache | None = None,
) -> Decimal | DataGap | None:
    """Sealed trailing-median constant for Q4/Q6/Q7 at anchor ``t0``.

    ``None`` for questions without a sealed median. Every window in the declared
    trailing range must be computable, otherwise a :class:`DataGap` is returned
    (fail-closed: a partially computable median would silently shift the bar).
    """
    if question_id not in _MEDIAN_QUESTIONS:
        return None
    values: list[Decimal] = []
    missing: list[str] = []
    for end in median_window_ends(question_id, t0):
        value = window_value(question_id, data, end, cache)
        if value is None:
            missing.append(f"{BTC_SYMBOL}:{end.isoformat()}")
        else:
            values.append(value)
    if missing:
        return _gap(missing)
    return median_decimal(values)


# --------------------------------------------------------------------------- #
# Outcome predicates (strict inequalities; tie -> "no"; Q5 explicit <=)
# --------------------------------------------------------------------------- #


def _outcome_window_end(question_id: str, t0: date) -> date:
    """End date of the outcome window for the median questions."""
    if question_id in ("Q4", "Q6"):
        return t0 + timedelta(days=_WINDOW_SPAN_DAYS)
    if question_id == "Q7":
        return t0  # sealed rule sums vol[t0-6 .. t0]
    raise ValueError(f"no outcome window for {question_id!r}")


def _state_window_end(question_id: str, t0: date) -> date:
    """End date of the last COMPLETED window known at anchor time (B1 state)."""
    if question_id in ("Q4", "Q6"):
        return t0  # window [t0-7, t0]; the t0 close is inside the data cutoff
    if question_id == "Q7":
        return t0 - timedelta(days=_WINDOW_SPAN_DAYS)  # previous 7d block
    raise ValueError(f"no state window for {question_id!r}")


def _close_up(data: PanelData, symbol: str, t0: date, horizon_days: int) -> bool | DataGap:
    c0 = _close(data, symbol, t0)
    ch = _close(data, symbol, t0 + timedelta(days=horizon_days))
    missing = []
    if c0 is None:
        missing.append(f"{symbol}:{t0.isoformat()}")
    if ch is None:
        missing.append(f"{symbol}:{(t0 + timedelta(days=horizon_days)).isoformat()}")
    if missing:
        return _gap(missing)
    assert c0 is not None and ch is not None
    return ch > c0


def _relative_strength(data: PanelData, start: date, end: date) -> bool | DataGap:
    """ETH log return beats BTC log return over [start, end] (strict; tie -> no)."""
    e0 = _close(data, ETH_SYMBOL, start)
    e1 = _close(data, ETH_SYMBOL, end)
    b0 = _close(data, BTC_SYMBOL, start)
    b1 = _close(data, BTC_SYMBOL, end)
    missing = []
    for sym, day, val in (
        (ETH_SYMBOL, start, e0),
        (ETH_SYMBOL, end, e1),
        (BTC_SYMBOL, start, b0),
        (BTC_SYMBOL, end, b1),
    ):
        if val is None:
            missing.append(f"{sym}:{day.isoformat()}")
    if missing:
        return _gap(missing)
    assert e0 is not None and e1 is not None and b0 is not None and b1 is not None
    return (e1 / e0).ln() > (b1 / b0).ln()


def _drawdown(data: PanelData, t0: date) -> bool | DataGap:
    c0 = _close(data, BTC_SYMBOL, t0)
    missing = [] if c0 is not None else [f"{BTC_SYMBOL}:{t0.isoformat()}"]
    lows: list[Decimal] = []
    for offset in range(1, 15):  # t0+1 .. t0+14; t0 itself is EXCLUDED
        day = t0 + timedelta(days=offset)
        low = _low(data, BTC_SYMBOL, day)
        if low is None:
            missing.append(f"{BTC_SYMBOL}:{day.isoformat()}")
        else:
            lows.append(low)
    if missing:
        return _gap(missing)
    assert c0 is not None
    return (min(lows) / c0) - Decimal(1) <= DRAWDOWN_THRESHOLD


def question_outcome(
    question_id: str,
    data: PanelData,
    t0: date,
    median_sealed: Decimal | None = None,
    cache: WindowCache | None = None,
) -> bool | DataGap:
    """Resolve one CORE8 question at anchor ``t0`` against ``data``.

    Q4/Q6/Q7 REQUIRE the sealed median (from the issued record — resolution must
    never recompute the bar). Missing datapoints return :class:`DataGap`.
    """
    if question_id == "Q1":
        return _close_up(data, BTC_SYMBOL, t0, 7)
    if question_id == "Q2":
        return _close_up(data, BTC_SYMBOL, t0, 30)
    if question_id == "Q3":
        return _relative_strength(data, t0, t0 + timedelta(days=14))
    if question_id == "Q5":
        return _drawdown(data, t0)
    if question_id == "Q8":
        return _relative_strength(data, t0, t0 + timedelta(days=30))
    if question_id in _MEDIAN_QUESTIONS:
        if median_sealed is None:
            raise ValueError(f"{question_id} requires the sealed median")
        end = _outcome_window_end(question_id, t0)
        value = window_value(question_id, data, end, cache)
        if value is None:
            return _gap([f"{BTC_SYMBOL}:window-end:{end.isoformat()}"])
        return value > median_sealed
    raise ValueError(f"unknown CORE8 question_id: {question_id!r}")


def _anchored_outcome(
    question_id: str,
    data: PanelData,
    anchor: date,
    cache: WindowCache | None,
) -> bool | None:
    """Historical outcome at ``anchor`` for baselines (None = not computable).

    Median questions use the PER-ANCHOR trailing median (same constants), so the
    baseline sees exactly the bar an issuance at that anchor would have sealed.
    """
    median: Decimal | None = None
    if question_id in _MEDIAN_QUESTIONS:
        sealed = sealed_median_for(question_id, data, anchor, cache)
        if not isinstance(sealed, Decimal):
            return None
        median = sealed
    outcome = question_outcome(question_id, data, anchor, median, cache)
    if isinstance(outcome, DataGap):
        return None
    return outcome


# --------------------------------------------------------------------------- #
# SHADOW baselines (deterministic placeholders — honest label, draft §5)
# --------------------------------------------------------------------------- #


def baseline_b0(
    question_id: str,
    data: PanelData,
    t0: date,
    cache: WindowCache | None = None,
) -> tuple[Decimal, int]:
    """B0 climatology: yes-frequency over the trailing 365 days before t0.

    Anchors ``a = t0-365 .. t0-1`` whose outcome is computable from data up to
    ``t0-1`` (anchor + final-data offset <= t0-1). Zero computable anchors ->
    0.5 (documented fallback). Returns ``(probability, n_anchors)``.
    """
    horizon = FINAL_DATA_OFFSET_DAYS[question_id]
    yes = 0
    n = 0
    for back in range(B0_TRAILING_DAYS, horizon, -1):
        anchor = t0 - timedelta(days=back)
        outcome = _anchored_outcome(question_id, data, anchor, cache)
        if outcome is None:
            continue
        n += 1
        yes += int(outcome)
    return _prob(yes, n), n


def baseline_b1(
    question_id: str,
    data: PanelData,
    t0: date,
    median_sealed: Decimal | None = None,
    cache: WindowCache | None = None,
) -> tuple[Decimal, int]:
    """SHADOW-B1 — simple deterministic naive forecaster per question.

    Exact definitions (all trailing windows are the 180 calendar days before t0,
    outcomes restricted to pre-t0 data; empty conditioning cell -> 0.5):

    * Q1/Q2 (drift persistence): unconditional share of positive 7d / 30d
      close-to-close returns over trailing-180d anchors.
    * Q3/Q8 (relative momentum): frequency of "ETH beats BTC over [a, a+h]"
      among trailing anchors whose PREVIOUS same-length window [a-h, a] has the
      same sign as the current previous window [t0-h, t0].
    * Q4/Q6/Q7 (median persistence, "above median stays above median"):
      frequency of the yes-outcome among trailing anchors whose last completed
      window state (window ending at the anchor vs the anchor's own trailing
      median) matches the current state (window ending t0 vs ``median_sealed``).
      Q7's state window is the previous 7d block ending t0-7, because its
      outcome window already ends at t0.
    * Q5 (volatility-regime-naive): unconditional trailing-180d frequency of
      the drawdown event.

    Returns ``(probability, n_anchors_in_conditioning_cell)``.
    """
    horizon = FINAL_DATA_OFFSET_DAYS[question_id]

    if question_id in ("Q1", "Q2", "Q5"):
        yes = 0
        n = 0
        for back in range(B1_TRAILING_DAYS, horizon, -1):
            anchor = t0 - timedelta(days=back)
            outcome = _anchored_outcome(question_id, data, anchor, cache)
            if outcome is None:
                continue
            n += 1
            yes += int(outcome)
        return _prob(yes, n), n

    if question_id in ("Q3", "Q8"):
        span = timedelta(days=horizon)
        current_prev = _relative_strength(data, t0 - span, t0)
        if isinstance(current_prev, DataGap):
            return Decimal("0.5"), 0
        yes = 0
        n = 0
        for back in range(B1_TRAILING_DAYS, horizon, -1):
            anchor = t0 - timedelta(days=back)
            prev_sign = _relative_strength(data, anchor - span, anchor)
            rel_outcome = _relative_strength(data, anchor, anchor + span)
            if isinstance(prev_sign, DataGap) or isinstance(rel_outcome, DataGap):
                continue
            if prev_sign != current_prev:
                continue
            n += 1
            yes += int(rel_outcome)
        return _prob(yes, n), n

    if question_id in _MEDIAN_QUESTIONS:
        if median_sealed is None:
            return Decimal("0.5"), 0
        value_now = window_value(question_id, data, _state_window_end(question_id, t0), cache)
        if value_now is None:
            return Decimal("0.5"), 0
        current_state = value_now > median_sealed
        yes = 0
        n = 0
        for back in range(B1_TRAILING_DAYS, horizon, -1):
            anchor = t0 - timedelta(days=back)
            anchor_median = sealed_median_for(question_id, data, anchor, cache)
            if not isinstance(anchor_median, Decimal):
                continue
            state_value = window_value(
                question_id, data, _state_window_end(question_id, anchor), cache
            )
            if state_value is None:
                continue
            hist_outcome = question_outcome(question_id, data, anchor, anchor_median, cache)
            if isinstance(hist_outcome, DataGap):
                continue
            if (state_value > anchor_median) != current_state:
                continue
            n += 1
            yes += int(hist_outcome)
        return _prob(yes, n), n

    raise ValueError(f"unknown CORE8 question_id: {question_id!r}")


# --------------------------------------------------------------------------- #
# Multi-venue median (interface for the sealed epoch; shadow = single venue)
# --------------------------------------------------------------------------- #


def multi_venue_median_provider(
    providers: Sequence[DailyKlinesProvider],
) -> DailyKlinesProvider:
    """Combine venue fetchers into a per-field Decimal-median provider.

    A day is present only when EVERY venue has it (fail-closed — a partially
    covered day would make the median venue-count dependent). Shadow mode runs
    this with a single venue (identity semantics); the sealed epoch adds venues
    without touching resolver code.
    """
    if not providers:
        raise ValueError("multi_venue_median_provider needs >= 1 provider")

    def fetch(symbol: str, start: date, end: date) -> Mapping[date, DailyCandle]:
        per_venue = [dict(provider(symbol, start, end)) for provider in providers]
        common = set(per_venue[0])
        for venue in per_venue[1:]:
            common &= set(venue)
        combined: dict[date, DailyCandle] = {}
        for day in sorted(common):
            candles = [venue[day] for venue in per_venue]
            combined[day] = DailyCandle(
                day=day,
                close=median_decimal([c.close for c in candles]),
                low=median_decimal([c.low for c in candles]),
                volume=median_decimal([c.volume for c in candles]),
            )
        return combined

    return fetch


__all__ = [
    "B0_TRAILING_DAYS",
    "B1_TRAILING_DAYS",
    "BTC_SYMBOL",
    "CORE8",
    "DRAWDOWN_THRESHOLD",
    "ETH_SYMBOL",
    "FINAL_DATA_OFFSET_DAYS",
    "PROB_QUANTUM",
    "Q4_MEDIAN_CONSTANT",
    "Q6_MEDIAN_CONSTANT",
    "Q7_MEDIAN_CONSTANT",
    "QUESTION_IDS",
    "DailyCandle",
    "DailyKlinesProvider",
    "DataGap",
    "KlinesUnavailableError",
    "PanelData",
    "QuestionSpec",
    "WindowCache",
    "baseline_b0",
    "baseline_b1",
    "median_decimal",
    "median_window_ends",
    "multi_venue_median_provider",
    "question_outcome",
    "sample_stdev",
    "sealed_median_for",
    "spec_for",
    "window_value",
]
