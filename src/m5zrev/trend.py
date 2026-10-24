"""Closed-H1 trend gate.

A long is blocked only when all three are true on the latest H1 bar that has
already closed: ADX is above the level, EMA is below its value `slope_bars`
ago, and the H1 close is below that EMA. The short rule is symmetric.

An H1 bar whose close is still in the future is not readable. A filter that
is switched on but not yet warm blocks new entries instead of failing open.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np

from m5zrev.indicators import price_ema, wilder_adx


def blocks_long(adx: float, ema_now: float, ema_then: float, close: float, level: float) -> bool:
    """Strong downtrend. Equality at the ADX level does not block."""
    if not _finite(adx, ema_now, ema_then, close):
        return False
    return adx > level and ema_now < ema_then and close < ema_now


def blocks_short(adx: float, ema_now: float, ema_then: float, close: float, level: float) -> bool:
    if not _finite(adx, ema_now, ema_then, close):
        return False
    return adx > level and ema_now > ema_then and close > ema_now


def closed_h1_index(signal_time: datetime, h1_close_time: list[datetime]) -> int | None:
    """Latest H1 close at or before the M5 close. A forming hour is not selected."""
    chosen: int | None = None
    for index, stamp in enumerate(h1_close_time):
        if stamp <= signal_time:
            chosen = index
            continue
        break
    return chosen


def align_closed_h1(m5_close_time: list[datetime], h1_close_time: list[datetime]) -> np.ndarray:
    """Index of the closed H1 bar for each M5 close, or -1 when none has closed."""
    aligned = np.full(len(m5_close_time), -1, dtype=int)
    h1_index = 0
    last = -1
    for index, stamp in enumerate(m5_close_time):
        while h1_index < len(h1_close_time) and h1_close_time[h1_index] <= stamp:
            last = h1_index
            h1_index += 1
        aligned[index] = last
    return aligned


def trend_blocks(
    h1_high: np.ndarray,
    h1_low: np.ndarray,
    h1_close: np.ndarray,
    *,
    ema_period: int,
    adx_period: int,
    adx_level: float,
    slope_bars: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return `(block_long, block_short, ready)` aligned to the H1 bars."""
    ema = price_ema(h1_close, ema_period)
    _plus, _minus, adx = wilder_adx(h1_high, h1_low, h1_close, adx_period)
    count = len(h1_close)
    block_long = np.zeros(count, dtype=bool)
    block_short = np.zeros(count, dtype=bool)
    ready = np.zeros(count, dtype=bool)
    first = max(ema_period - 1 + slope_bars, 2 * adx_period - 1)
    for index in range(count):
        previous = index - slope_bars
        if index < first or previous < 0:
            continue
        ema_now = float(ema[index])
        ema_then = float(ema[previous])
        adx_now = float(adx[index])
        close_now = float(h1_close[index])
        if not all(np.isfinite([ema_now, ema_then, adx_now, close_now])):
            continue
        ready[index] = True
        block_long[index] = blocks_long(adx_now, ema_now, ema_then, close_now, adx_level)
        block_short[index] = blocks_short(adx_now, ema_now, ema_then, close_now, adx_level)
    return block_long, block_short, ready


def _finite(*values: float) -> bool:
    return all(math.isfinite(value) for value in values)
