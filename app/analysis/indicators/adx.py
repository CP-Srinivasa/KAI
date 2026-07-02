"""Wilder's Average Directional Index (ADX) with +DI / -DI — pure function.

Reference: J. Welles Wilder, "New Concepts in Technical Trading Systems" (1978).

Directional Movement:
    up_move[i]   = high[i] - high[i-1]
    down_move[i] = low[i-1]  - low[i]
    +DM[i] = up_move   if up_move > down_move and up_move > 0     else 0
    -DM[i] = down_move if down_move > up_move and down_move > 0   else 0

True Range:
    TR[i] = max(high[i] - low[i], |high[i] - close[i-1]|, |low[i] - close[i-1]|)

Wilder smoothing (alpha = 1/period):
    Initial sum at index `period` = sum(values[1..period]).
    Subsequent: smoothed[i] = smoothed[i-1] - smoothed[i-1]/period + values[i].

Directional Indicators:
    +DI[i] = 100 * smoothed_+DM[i] / smoothed_TR[i]
    -DI[i] = 100 * smoothed_-DM[i] / smoothed_TR[i]

Directional Index:
    DX[i] = 100 * |+DI - -DI| / (+DI + -DI)        (0 when denominator == 0)

Average Directional Index:
    Initial ADX[2*period - 1] = mean(DX[period..2*period-1])
    ADX[i] = (ADX[i-1] * (period - 1) + DX[i]) / period   for i >= 2*period

Output:
    Three lists (adx, plus_di, minus_di) aligned to input length.
    plus_di / minus_di first non-None at index `period`.
    adx first non-None at index `2*period - 1`.
"""

from __future__ import annotations

from dataclasses import dataclass

ADX_DEFAULT_PERIOD = 14


@dataclass(frozen=True)
class AdxResult:
    """Aligned Wilder ADX components."""

    adx: list[float | None]
    plus_di: list[float | None]
    minus_di: list[float | None]


def compute_adx_di(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = ADX_DEFAULT_PERIOD,
) -> AdxResult:
    """Compute Wilder ADX + Plus-DI + Minus-DI series aligned to inputs.

    Args:
        highs / lows / closes: ordered OHLC inputs (oldest first).
        period: Wilder period, default 14. Must be >= 1.

    Returns:
        AdxResult with three lists of length len(closes). Warm-up positions
        are None: plus_di / minus_di for indices [0, period); adx for
        indices [0, 2*period - 1).

    Raises:
        ValueError: period < 1, or input list lengths mismatch.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    n = len(closes)
    if len(highs) != n or len(lows) != n:
        raise ValueError("highs, lows, closes must have equal length")

    adx_out: list[float | None] = [None] * n
    plus_di_out: list[float | None] = [None] * n
    minus_di_out: list[float | None] = [None] * n

    if n < period + 1:
        return AdxResult(adx=adx_out, plus_di=plus_di_out, minus_di=minus_di_out)

    trs: list[float] = [0.0]
    plus_dms: list[float] = [0.0]
    minus_dms: list[float] = [0.0]
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)

    # Initial smoothed sums at index `period`: sum of first `period` values
    # (indices 1..period inclusive).
    smoothed_tr = sum(trs[1 : period + 1])
    smoothed_plus_dm = sum(plus_dms[1 : period + 1])
    smoothed_minus_dm = sum(minus_dms[1 : period + 1])

    dxs: list[float | None] = [None] * n

    p_di, m_di = _di_pair(smoothed_plus_dm, smoothed_minus_dm, smoothed_tr)
    plus_di_out[period] = p_di
    minus_di_out[period] = m_di
    dxs[period] = _dx(p_di, m_di)

    for i in range(period + 1, n):
        smoothed_tr = smoothed_tr - smoothed_tr / period + trs[i]
        smoothed_plus_dm = smoothed_plus_dm - smoothed_plus_dm / period + plus_dms[i]
        smoothed_minus_dm = smoothed_minus_dm - smoothed_minus_dm / period + minus_dms[i]
        p_di, m_di = _di_pair(smoothed_plus_dm, smoothed_minus_dm, smoothed_tr)
        plus_di_out[i] = p_di
        minus_di_out[i] = m_di
        dxs[i] = _dx(p_di, m_di)

    # ADX: initial = mean of first `period` DX values starting at index `period`.
    if n >= 2 * period:
        first_adx_idx = 2 * period - 1
        adx_initial = sum(d for d in dxs[period : 2 * period] if d is not None) / period
        adx_out[first_adx_idx] = adx_initial

        adx_val = adx_initial
        for i in range(2 * period, n):
            dx_i = dxs[i] or 0.0
            adx_val = (adx_val * (period - 1) + dx_i) / period
            adx_out[i] = adx_val

    return AdxResult(adx=adx_out, plus_di=plus_di_out, minus_di=minus_di_out)


def _di_pair(plus: float, minus: float, tr_total: float) -> tuple[float, float]:
    if tr_total == 0.0:
        return 0.0, 0.0
    return 100.0 * plus / tr_total, 100.0 * minus / tr_total


def _dx(p_di: float, m_di: float) -> float:
    s = p_di + m_di
    if s == 0.0:
        return 0.0
    return 100.0 * abs(p_di - m_di) / s
