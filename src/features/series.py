"""Backward-looking transforms. Missing history stays missing."""

from __future__ import annotations

import math

import numpy as np


def _finite(value: float) -> bool:
    return bool(np.isfinite(value))


def rolling_change(level: np.ndarray, window: int) -> np.ndarray:
    out = np.full(level.shape, np.nan)
    if window < 1:
        raise ValueError("window must be positive")
    for index in range(window, len(level)):
        current = level[index]
        previous = level[index - window]
        if _finite(current) and _finite(previous):
            out[index] = current - previous
    return out


def rolling_log_return(price: np.ndarray, window: int) -> np.ndarray:
    out = np.full(price.shape, np.nan)
    for index in range(window, len(price)):
        current = price[index]
        previous = price[index - window]
        if _finite(current) and _finite(previous) and current > 0 and previous > 0:
            out[index] = math.log(current) - math.log(previous)
    return out


def rolling_zscore(level: np.ndarray, window: int) -> np.ndarray:
    out = np.full(level.shape, np.nan)
    for index in range(window - 1, len(level)):
        sample = level[index + 1 - window : index + 1]
        if sample.shape[0] < window or not np.isfinite(sample).all():
            continue
        std = float(sample.std(ddof=1))
        if std < 1e-12:
            continue
        out[index] = (sample[-1] - float(sample.mean())) / std
    return out


def rolling_percentile(level: np.ndarray, window: int) -> np.ndarray:
    out = np.full(level.shape, np.nan)
    for index in range(window - 1, len(level)):
        sample = level[index + 1 - window : index + 1]
        if sample.shape[0] < window or not np.isfinite(sample).all():
            continue
        out[index] = float(np.mean(sample <= sample[-1]))
    return out


def rolling_slope(level: np.ndarray, window: int) -> np.ndarray:
    """Ordinary-least-squares slope of the level on a within-window time index."""
    out = np.full(level.shape, np.nan)
    axis = np.arange(window, dtype=float)
    axis = axis - axis.mean()
    denominator = float(np.dot(axis, axis))
    for index in range(window - 1, len(level)):
        sample = level[index + 1 - window : index + 1]
        if sample.shape[0] < window or not np.isfinite(sample).all():
            continue
        centered = sample - sample.mean()
        out[index] = float(np.dot(axis, centered) / denominator)
    return out


def realized_vol(price: np.ndarray, window: int) -> np.ndarray:
    """Standard deviation of daily log returns. Not annualized, and never filled with zero."""
    returns = rolling_log_return(price, 1)
    out = np.full(price.shape, np.nan)
    for index in range(len(price)):
        sample = returns[index + 1 - window : index + 1]
        if sample.shape[0] < window or not np.isfinite(sample).all():
            continue
        out[index] = float(sample.std(ddof=1))
    return out


def information_ema(
    calendar_ord: np.ndarray,
    event_ord: np.ndarray,
    event_values: np.ndarray,
    half_life_days: float,
) -> np.ndarray:
    """Decay yesterday's state, then add events dated on or before today.

    The state is missing until the first event. Decay is a transform of a real
    print, not a substitute for a missing print.
    """
    if half_life_days <= 0:
        raise ValueError("half life must be positive")
    decay = 0.5 ** (1.0 / half_life_days)
    out = np.full(calendar_ord.shape, np.nan)
    state: float | None = None
    cursor = 0
    order = np.argsort(event_ord, kind="mergesort")
    event_ord = event_ord[order]
    event_values = event_values[order]
    for index, day in enumerate(calendar_ord):
        if state is not None:
            state *= decay
        while cursor < len(event_ord) and event_ord[cursor] <= day:
            shock = float(event_values[cursor])
            if np.isfinite(shock):
                state = shock if state is None else state + shock
            cursor += 1
        if state is not None:
            out[index] = state
    return out


def age_days(calendar_ord: np.ndarray, event_ord: np.ndarray) -> np.ndarray:
    out = np.full(calendar_ord.shape, np.nan)
    if len(event_ord) == 0:
        return out
    ordered = np.sort(event_ord)
    for index, day in enumerate(calendar_ord):
        position = int(np.searchsorted(ordered, day, side="right")) - 1
        if position >= 0:
            out[index] = float(day - ordered[position])
    return out
