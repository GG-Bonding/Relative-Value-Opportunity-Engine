"""Closed-bar return z-scores and the previous-day price filter."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import UTC, date, datetime

import numpy as np


def rolling_return_z(close: np.ndarray, window: int) -> np.ndarray:
    """Z-score of each bar's log return. The window includes that bar. ddof=1.

    Index i uses log(close[i] / close[i - 1]) and the `window` returns ending at i.
    A short history or a zero standard deviation stays NaN, so the bar is not a signal.
    close[i + 1] is not read.
    """
    if window < 2:
        raise ValueError("N_short must be at least 2")
    count = len(close)
    z = np.full(count, np.nan)
    if count < window + 1:
        return z
    log_return = np.empty(count)
    log_return[0] = np.nan
    previous = close[:-1]
    current = close[1:]
    valid = (previous > 0) & (current > 0)
    log_return[1:] = np.where(valid, np.log(current / previous), np.nan)
    for index in range(window, count):
        sample = log_return[index - window + 1 : index + 1]
        if sample.shape[0] != window or not np.all(np.isfinite(sample)):
            continue
        deviation = float(sample.std(ddof=1))
        if deviation == 0.0:
            continue
        z[index] = (float(sample[-1]) - float(sample.mean())) / deviation
    return z


def daily_price_z(closes: np.ndarray, window: int) -> np.ndarray:
    """Z-score of each daily close against the window ending that day. ddof=1.

    The value at date D uses closes through D. Callers must not show it to bars on D.
    """
    if window < 2:
        raise ValueError("price z window must be at least 2")
    z = np.full(len(closes), np.nan)
    for index in range(window - 1, len(closes)):
        sample = closes[index - window + 1 : index + 1]
        if not np.all(np.isfinite(sample)):
            continue
        deviation = float(sample.std(ddof=1))
        if deviation == 0.0:
            continue
        z[index] = (float(sample[-1]) - float(sample.mean())) / deviation
    return z


def align_previous_trading_day(
    bar_dates: list[date],
    daily_dates: list[date],
    daily_z: np.ndarray,
) -> np.ndarray:
    """Map each bar to the price z of the latest daily date strictly before the bar's date.

    A missing z stays NaN. It is not replaced with zero, and it does not fall through
    to an older day that happens to have a z.
    """
    if len(daily_dates) != len(daily_z):
        raise ValueError("daily dates and z-scores have different lengths")
    aligned = np.full(len(bar_dates), np.nan)
    if not daily_dates:
        return aligned
    for index, bar_date in enumerate(bar_dates):
        previous = bisect_left(daily_dates, bar_date) - 1
        if previous >= 0:
            aligned[index] = daily_z[previous]
    return aligned


def session_mask(times: list[datetime], start_minute: int, end_minute: int) -> np.ndarray:
    """Weekday bar-close clock in [start, end), evaluated in UTC."""
    mask = np.zeros(len(times), dtype=bool)
    for index, stamp in enumerate(times):
        utc = stamp.astimezone(UTC)
        if utc.weekday() >= 5:
            continue
        minute = utc.hour * 60 + utc.minute
        mask[index] = start_minute <= minute < end_minute
    return mask


def blacklist_mask(times: list[datetime], windows: tuple[tuple[datetime, datetime], ...]) -> np.ndarray:
    """True when the bar close falls in a half-open [start, end) blackout.

    Overlapping windows are merged first. A bar is then one bisect, so a full
    calendar does not scan every release on every bar.
    """
    mask = np.zeros(len(times), dtype=bool)
    merged = _merge_windows(windows)
    if not merged:
        return mask
    starts = [start for start, _ in merged]
    ends = [end for _, end in merged]
    for index, stamp in enumerate(times):
        utc = stamp.astimezone(UTC)
        pos = bisect_right(starts, utc) - 1
        if pos >= 0 and utc < ends[pos]:
            mask[index] = True
    return mask


def _merge_windows(
    windows: tuple[tuple[datetime, datetime], ...],
) -> list[tuple[datetime, datetime]]:
    ordered = sorted((start.astimezone(UTC), end.astimezone(UTC)) for start, end in windows if start < end)
    merged: list[tuple[datetime, datetime]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            continue
        merged.append((start, end))
    return merged
