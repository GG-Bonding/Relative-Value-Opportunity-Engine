"""Closed-bar indicators shared by the research book and the MQL5 EA.

EMA seed (both languages):

- `alpha = 2 / (period + 1)`
- `E[0] = log(close[0])` for the deviation EMA, or `close[0]` for a price EMA
- `E[t] = alpha * x[t] + (1 - alpha) * E[t - 1]`
- The value `E[period - 1]` is the first warmed EMA. It has seen `period` prices.

Deviation:

- `d[t] = log(close[t]) - E[t - 1]`
- `d[t]` exists only for `t >= period`, so the subtracted EMA is already warm.
- `Z[t]` uses `d[t - window : t]`, which excludes `d[t]`.
- The standard deviation is the sample statistic (`ddof=1`).
- A zero standard deviation, a non-positive price, or a short window is not tradable.
- Nothing is back-filled.

Return z uses the same exclusion rule on log returns. ATR and ADX use Wilder's
smoothing, with the first smoothed value equal to the sum (ADX, +DM, -DM, TR)
or the mean (ATR, ADX of DX) of the first `period` observations. See the
functions below for the exact index of that first value.
"""

from __future__ import annotations

import math

import numpy as np


def ema_alpha(period: int) -> float:
    if period < 2:
        raise ValueError("EMA period must be at least 2")
    return 2.0 / (period + 1.0)


def log_ema(close: np.ndarray, period: int) -> np.ndarray:
    """EMA of log price. Invalid prices stay NaN and break the recursion."""
    return _ema(_logs(close), period)


def price_ema(close: np.ndarray, period: int) -> np.ndarray:
    """EMA of price, used by the H1 filter. The deviation signal does not use this."""
    values = np.asarray(close, dtype=np.float64)
    cleaned = np.where(values > 0.0, values, np.nan)
    return _ema(cleaned, period)


def deviation_z(close: np.ndarray, ema_period: int, window: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return `(log_ema, deviation, z)`.

    `z[t]` is NaN until `window` warmed deviations exist strictly before `t`.
    """
    if window < 2:
        raise ValueError("deviation window must be at least 2")
    logs = _logs(close)
    ema = _ema(logs, ema_period)
    count = len(logs)
    deviation = np.full(count, np.nan)
    for index in range(ema_period, count):
        if math.isfinite(logs[index]) and math.isfinite(float(ema[index - 1])):
            deviation[index] = logs[index] - float(ema[index - 1])
    z = _rolling_z(deviation, window, first_valid=ema_period)
    return ema, deviation, z


def return_z(close: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """Return `(log_return, z)`.

    `z[t]` uses the `window` returns before `t`, never `r[t]` and never `r[t + 1]`.
    """
    if window < 2:
        raise ValueError("return window must be at least 2")
    logs = _logs(close)
    count = len(logs)
    log_return = np.full(count, np.nan)
    for index in range(1, count):
        if math.isfinite(logs[index]) and math.isfinite(logs[index - 1]):
            log_return[index] = logs[index] - logs[index - 1]
    z = _rolling_z(log_return, window, first_valid=1)
    return log_return, z


def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Wilder ATR.

    True range starts at index 1. The first ATR is the mean of `TR[1:period + 1]`
    and is stored at index `period`. Later values are
    `(ATR[t - 1] * (period - 1) + TR[t]) / period`.
    """
    if period < 2:
        raise ValueError("ATR period must be at least 2")
    tr = true_range(high, low, close)
    count = len(tr)
    atr = np.full(count, np.nan)
    if count <= period:
        return atr
    seed = tr[1 : period + 1]
    if not np.all(np.isfinite(seed)):
        return atr
    atr[period] = float(seed.mean())
    for index in range(period + 1, count):
        if not math.isfinite(float(tr[index])) or not math.isfinite(float(atr[index - 1])):
            continue
        atr[index] = (float(atr[index - 1]) * (period - 1) + float(tr[index])) / period
    return atr


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    highs, lows, closes = _ohlc(high, low, close)
    count = len(closes)
    tr = np.full(count, np.nan)
    if count == 0:
        return tr
    if math.isfinite(highs[0]) and math.isfinite(lows[0]):
        tr[0] = highs[0] - lows[0]
    for index in range(1, count):
        if not (math.isfinite(highs[index]) and math.isfinite(lows[index]) and math.isfinite(closes[index - 1])):
            continue
        tr[index] = max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        )
    return tr


def wilder_adx(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return `(plus_di, minus_di, adx)` using Wilder smoothing.

    `+DM`, `-DM`, and `TR` are summed over indices `1 .. period`. That sum is
    stored at index `period`. Directional indexes start there. The first ADX
    is the mean of the first `period` DX values and is stored at index
    `2 * period - 1`.
    """
    if period < 2:
        raise ValueError("ADX period must be at least 2")
    highs, lows, closes = _ohlc(high, low, close)
    count = len(closes)
    plus_dm = np.zeros(count)
    minus_dm = np.zeros(count)
    tr = true_range(highs, lows, closes)
    for index in range(1, count):
        if not (
            math.isfinite(highs[index])
            and math.isfinite(highs[index - 1])
            and math.isfinite(lows[index])
            and math.isfinite(lows[index - 1])
        ):
            plus_dm[index] = np.nan
            minus_dm[index] = np.nan
            continue
        up = highs[index] - highs[index - 1]
        down = lows[index - 1] - lows[index]
        plus_dm[index] = up if up > down and up > 0.0 else 0.0
        minus_dm[index] = down if down > up and down > 0.0 else 0.0
    smooth_tr = _wilder_sum(tr, period)
    smooth_plus = _wilder_sum(plus_dm, period)
    smooth_minus = _wilder_sum(minus_dm, period)
    plus_di = np.full(count, np.nan)
    minus_di = np.full(count, np.nan)
    dx = np.full(count, np.nan)
    for index in range(period, count):
        base = float(smooth_tr[index])
        if not math.isfinite(base) or base == 0.0:
            continue
        plus_value = 100.0 * float(smooth_plus[index]) / base
        minus_value = 100.0 * float(smooth_minus[index]) / base
        plus_di[index] = plus_value
        minus_di[index] = minus_value
        total = plus_value + minus_value
        if total == 0.0:
            continue
        dx[index] = 100.0 * abs(plus_value - minus_value) / total
    adx = np.full(count, np.nan)
    first = 2 * period - 1
    if count > first:
        seed = dx[period : first + 1]
        if seed.shape[0] == period and np.all(np.isfinite(seed)):
            adx[first] = float(seed.mean())
            for index in range(first + 1, count):
                if not math.isfinite(float(dx[index])) or not math.isfinite(float(adx[index - 1])):
                    continue
                adx[index] = (float(adx[index - 1]) * (period - 1) + float(dx[index])) / period
    return plus_di, minus_di, adx


def _logs(close: np.ndarray) -> np.ndarray:
    values = np.asarray(close, dtype=np.float64)
    logs = np.full(len(values), np.nan)
    for index, price in enumerate(values):
        if math.isfinite(float(price)) and float(price) > 0.0:
            logs[index] = math.log(float(price))
    return logs


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = ema_alpha(period)
    ema = np.full(len(values), np.nan)
    if len(values) == 0 or not math.isfinite(float(values[0])):
        return ema
    ema[0] = float(values[0])
    decay = 1.0 - alpha
    for index in range(1, len(values)):
        current = float(values[index])
        previous = float(ema[index - 1])
        if not math.isfinite(current) or not math.isfinite(previous):
            continue
        ema[index] = alpha * current + decay * previous
    return ema


def _rolling_z(values: np.ndarray, window: int, *, first_valid: int) -> np.ndarray:
    z = np.full(len(values), np.nan)
    start = first_valid + window
    for index in range(start, len(values)):
        sample = values[index - window : index]
        current = float(values[index])
        if sample.shape[0] != window or not np.all(np.isfinite(sample)) or not math.isfinite(current):
            continue
        scale = float(sample.std(ddof=1))
        if scale == 0.0:
            continue
        z[index] = (current - float(sample.mean())) / scale
    return z


def _wilder_sum(values: np.ndarray, period: int) -> np.ndarray:
    smoothed = np.full(len(values), np.nan)
    if len(values) <= period:
        return smoothed
    seed = values[1 : period + 1]
    if not np.all(np.isfinite(seed)):
        return smoothed
    smoothed[period] = float(seed.sum())
    for index in range(period + 1, len(values)):
        current = float(values[index])
        previous = float(smoothed[index - 1])
        if not math.isfinite(current) or not math.isfinite(previous):
            continue
        smoothed[index] = previous - previous / period + current
    return smoothed


def _ohlc(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    highs = np.asarray(high, dtype=np.float64)
    lows = np.asarray(low, dtype=np.float64)
    closes = np.asarray(close, dtype=np.float64)
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("OHLC columns have different lengths")
    return highs, lows, closes
