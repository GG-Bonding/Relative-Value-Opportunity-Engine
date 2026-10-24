"""Closed-bar features. The M5 bid close is the only signal price."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from m5zrev.data import BarTable, TickTable
from m5zrev.indicators import deviation_z, return_z, wilder_atr
from m5zrev.params import Params
from m5zrev.session import assess_calendar, event_block_mask, session_mask
from m5zrev.trend import align_closed_h1, trend_blocks


@dataclass(frozen=True, slots=True)
class Features:
    z_dev: np.ndarray
    z_ret: np.ndarray
    log_return: np.ndarray
    ema: np.ndarray
    atr: np.ndarray
    spread_pips: np.ndarray
    in_session: np.ndarray
    event_blocked: np.ndarray
    trend_block_long: np.ndarray
    trend_block_short: np.ndarray
    trend_ready: np.ndarray
    calendar_status: str


def build_features(
    m5: BarTable,
    h1: BarTable | None,
    ticks: TickTable | None,
    events: tuple[datetime, ...],
    params: Params,
    study_spread_pips: float | None,
) -> tuple[Features, tuple[str, ...]]:
    """Indicators stop at each bar. A later close cannot change an earlier z."""
    ema, _deviation, z_dev = deviation_z(m5.close, params.ema_period, params.dev_window)
    log_return, z_ret = return_z(m5.close, params.ret_window)
    atr = wilder_atr(m5.high, m5.low, m5.close, params.atr_period)
    spreads, spread_notes = _spreads(m5, ticks, params.pip, study_spread_pips)
    in_session = session_mask(m5.close_time, params.session_start_minute, params.session_end_minute)
    status = assess_calendar(
        tuple(events),
        m5.close_time[-1],
        timedelta(days=params.calendar_max_age_days),
    )
    blocked = event_block_mask(
        m5.close_time,
        tuple(events),
        before=timedelta(minutes=params.event_before_minutes),
        after=timedelta(minutes=params.event_after_minutes),
        mode=params.event_filter,
        status=status,
    )
    block_long = np.zeros(len(m5.close), dtype=bool)
    block_short = np.zeros(len(m5.close), dtype=bool)
    ready = np.zeros(len(m5.close), dtype=bool)
    if h1 is not None and len(h1.close):
        h_long, h_short, h_ready = trend_blocks(
            h1.high,
            h1.low,
            h1.close,
            ema_period=params.h1_ema_period,
            adx_period=params.h1_adx_period,
            adx_level=params.h1_adx_level,
            slope_bars=params.h1_slope_bars,
        )
        aligned = align_closed_h1(m5.close_time, h1.close_time)
        for index, h1_index in enumerate(aligned):
            if h1_index < 0:
                continue
            block_long[index] = bool(h_long[h1_index])
            block_short[index] = bool(h_short[h1_index])
            ready[index] = bool(h_ready[h1_index])
    notes = list(spread_notes)
    if status == "missing":
        notes.append("Economic calendar is missing. The event filter was not verified.")
    elif status == "stale":
        notes.append("Economic calendar does not cover the end of the sample.")
    features = Features(
        z_dev=z_dev,
        z_ret=z_ret,
        log_return=log_return,
        ema=ema,
        atr=atr,
        spread_pips=spreads,
        in_session=in_session,
        event_blocked=blocked,
        trend_block_long=block_long,
        trend_block_short=block_short,
        trend_ready=ready,
        calendar_status=status,
    )
    return features, tuple(notes)


def _spreads(
    m5: BarTable,
    ticks: TickTable | None,
    pip: float,
    study_spread_pips: float | None,
) -> tuple[np.ndarray, tuple[str, ...]]:
    spreads = np.array(m5.spread_pips, dtype=np.float64, copy=True)
    notes: list[str] = []
    if ticks is not None:
        cursor = 0
        for index, close_time in enumerate(m5.close_time):
            last = float("nan")
            while cursor < len(ticks.time) and ticks.time[cursor] < close_time:
                if ticks.time[cursor] >= m5.open_time[index] and ticks.ask[cursor] >= ticks.bid[cursor]:
                    last = (float(ticks.ask[cursor]) - float(ticks.bid[cursor])) / pip
                cursor += 1
            if math.isfinite(last):
                spreads[index] = last
    used_study = False
    for index, value in enumerate(spreads):
        if math.isfinite(float(value)):
            continue
        if study_spread_pips is not None:
            spreads[index] = study_spread_pips
            used_study = True
    if used_study:
        notes.append(
            "At least one M5 bar has no bid/ask spread. The study spread was added to the bid "
            "to estimate the ask. This is not a tick backtest."
        )
    return spreads, tuple(notes)
