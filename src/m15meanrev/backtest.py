"""One position, closed-bar fills, and no re-entry on the exit bar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import numpy as np

from m15meanrev.load import BarSeries, DailySeries, daily_from_m15
from m15meanrev.signals import (
    align_previous_trading_day,
    blacklist_mask,
    daily_price_z,
    rolling_return_z,
    session_mask,
)

LongFilter = Literal["on", "off", "strict"]
PriceSide = Literal["bid", "mid"]
PIP = 0.0001


@dataclass(frozen=True)
class StrategyParams:
    """Defaults are fixed before looking at a sample.

    The entry rule is the closed-bar return z-score. Sections 1–7 did not name
    `hit_exit_rules`. This book exits when a later close retraces the shock
    through the previous bar's close (REVERSION), or when the hold reaches
    T_max (TIME). A sample that ends while a trade is open is marked END_OF_SAMPLE.
    """

    z_entry: float = 2.0
    n_short: int = 20
    t_max_minutes: int = 60
    price_z_window: int = 60
    spread_pips: float = 1.2
    pip: float = PIP
    long_filter: LongFilter = "on"
    session_start_minute: int = 7 * 60
    session_end_minute: int = 17 * 60
    price_side: PriceSide = "bid"

    def __post_init__(self) -> None:
        if self.z_entry <= 0:
            raise ValueError("Z_entry must be positive")
        if self.n_short < 2:
            raise ValueError("N_short must be at least 2")
        if self.t_max_minutes <= 0:
            raise ValueError("T_max must be positive")
        if self.price_z_window < 2:
            raise ValueError("price z window must be at least 2")
        if self.spread_pips < 0 or self.pip <= 0:
            raise ValueError("spread must be non-negative and pip must be positive")
        if self.long_filter not in {"on", "off", "strict"}:
            raise ValueError("long_filter must be on, off, or strict")
        if self.price_side not in {"bid", "mid"}:
            raise ValueError("price_side must be bid or mid")
        if not (0 <= self.session_start_minute < self.session_end_minute <= 24 * 60):
            raise ValueError("session must be a non-empty UTC minute range inside a day")


@dataclass(frozen=True)
class Trade:
    entry_time: datetime
    exit_time: datetime
    direction: str
    entry_price: float
    exit_price: float
    hold_minutes: int
    pnl_pips: float
    exit_reason: str
    entry_return_z: float
    entry_price_z60: float | None


@dataclass
class _Position:
    direction: str
    index: int
    anchor: float
    return_z: float
    price_z: float | None


def run_backtest(
    bars: BarSeries,
    daily: DailySeries | None = None,
    params: StrategyParams | None = None,
    blacklist: tuple[tuple[datetime, datetime], ...] = (),
    return_z: np.ndarray | None = None,
) -> list[Trade]:
    """Replay the book. A missing daily series is the last M15 close of each UTC date."""
    chosen = params or StrategyParams()
    clock = daily if daily is not None else daily_from_m15(bars)
    times = [stamp.astimezone(UTC) for stamp in bars.time]
    closes = np.asarray(bars.close, dtype=np.float64)
    z_return = rolling_return_z(closes, chosen.n_short) if return_z is None else return_z
    if len(z_return) != len(times):
        raise ValueError("return z length does not match the bars")
    z_daily = daily_price_z(np.asarray(clock.close, dtype=np.float64), chosen.price_z_window)
    z_filter = align_previous_trading_day([stamp.date() for stamp in times], clock.day, z_daily)
    tradable = session_mask(times, chosen.session_start_minute, chosen.session_end_minute)
    blocked = blacklist_mask(times, blacklist)
    spread = chosen.spread_pips * chosen.pip
    trades: list[Trade] = []
    position: _Position | None = None
    count = len(times)
    for index in range(count):
        if position is not None:
            if index == position.index:
                continue
            reasons = _exit_reasons(position, closes[index], times, index, chosen.t_max_minutes)
            if reasons:
                trades.append(_trade(times, closes, position, index, "+".join(reasons), spread, chosen))
                position = None
            continue
        z_value = float(z_return[index])
        if index < 1 or not np.isfinite(z_value) or not bool(tradable[index]) or bool(blocked[index]):
            continue
        price_z = float(z_filter[index])
        known_z = price_z if np.isfinite(price_z) else None
        if z_value >= chosen.z_entry:
            position = _Position("SHORT", index, float(closes[index - 1]), z_value, known_z)
        elif _allow_long(known_z, chosen.long_filter) and z_value <= -chosen.z_entry:
            position = _Position("LONG", index, float(closes[index - 1]), z_value, known_z)
    if position is not None:
        trades.append(_trade(times, closes, position, count - 1, "END_OF_SAMPLE", spread, chosen))
    return trades


def _allow_long(price_z: float | None, mode: LongFilter) -> bool:
    """Loose filter: previous-day price z must be known and at least 0. Off allows both sides."""
    if mode == "off":
        return True
    if price_z is None:
        return False
    if mode == "strict":
        return price_z >= -1.0
    return price_z >= 0.0


def _exit_reasons(position: _Position, close: float, times: list[datetime], index: int, t_max: int) -> list[str]:
    reasons: list[str] = []
    if position.direction == "SHORT" and close <= position.anchor:
        reasons.append("REVERSION")
    elif position.direction == "LONG" and close >= position.anchor:
        reasons.append("REVERSION")
    if _minutes(times[position.index], times[index]) >= t_max:
        reasons.append("TIME")
    return reasons


def _trade(
    times: list[datetime],
    closes: np.ndarray,
    position: _Position,
    index: int,
    reason: str,
    spread: float,
    params: StrategyParams,
) -> Trade:
    entry_close = float(closes[position.index])
    exit_close = float(closes[index])
    entry_price = entry_fill(position.direction, entry_close, spread, params.price_side)
    exit_price = exit_fill(position.direction, exit_close, spread, params.price_side)
    if position.direction == "SHORT":
        pnl_pips = (entry_price - exit_price) / params.pip
    else:
        pnl_pips = (exit_price - entry_price) / params.pip
    return Trade(
        entry_time=times[position.index],
        exit_time=times[index],
        direction=position.direction,
        entry_price=entry_price,
        exit_price=exit_price,
        hold_minutes=_minutes(times[position.index], times[index]),
        pnl_pips=pnl_pips,
        exit_reason=reason,
        entry_return_z=position.return_z,
        entry_price_z60=position.price_z,
    )


def entry_fill(direction: str, close: float, spread: float, price_side: PriceSide) -> float:
    """Bid tape: sell the bid, buy the ask. Mid tape: pay half the spread on each side."""
    if price_side == "bid":
        return close if direction == "SHORT" else close + spread
    half = spread / 2.0
    return close - half if direction == "SHORT" else close + half


def exit_fill(direction: str, close: float, spread: float, price_side: PriceSide) -> float:
    if price_side == "bid":
        return close + spread if direction == "SHORT" else close
    half = spread / 2.0
    return close + half if direction == "SHORT" else close - half


def _minutes(start: datetime, end: datetime) -> int:
    return int(round((end - start).total_seconds() / 60.0))
