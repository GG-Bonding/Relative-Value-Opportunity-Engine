"""Signal state machine.

IDLE -> ARMED -> CONFIRMED -> OPEN -> EXITED -> COOLDOWN

A deviation can arm once. Confirmation looks at the next `confirm_bars`
closed M5 bars, and the order is sent on the following bar's first valid
quote. A rejected or unconfirmed broker result is not treated as a fill.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime

import numpy as np

from m5zrev.params import Params

RETCODE_DONE = 10009
KNOWN_REJECTS = frozenset({10006, 10013, 10014, 10015, 10016, 10017, 10018, 10019, 10030})


@dataclass(frozen=True, slots=True)
class SignalBar:
    index: int
    close_time: datetime
    z_dev: float
    z_ret: float
    log_return: float
    ema: float
    atr: float
    bid_close: float
    spread_pips: float
    in_session: bool
    event_blocked: bool
    trend_block_long: bool
    trend_block_short: bool
    trend_ready: bool


@dataclass(frozen=True, slots=True)
class Transition:
    index: int
    phase: str
    reason: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    phase: str = "IDLE"
    direction: str | None = None
    trigger_index: int | None = None
    trigger_time: datetime | None = None
    trigger_z: float | None = None
    confirm_index: int | None = None
    entry_bar_index: int | None = None
    cooldown_through: int | None = None
    frozen_ema: float | None = None
    frozen_atr: float | None = None
    signal_time: datetime | None = None
    signal_bid: float | None = None
    z_dev: float | None = None
    z_ret: float | None = None
    entry_time: datetime | None = None
    entry_price: float | None = None
    stop: float | None = None
    target: float | None = None
    lots: float | None = None
    risk_account: float | None = None


def step_bar(
    state: Snapshot,
    bar: SignalBar,
    prev_z: float,
    z_returns: np.ndarray,
    params: Params,
) -> tuple[Snapshot, tuple[Transition, ...]]:
    """Advance one closed M5 bar. This does not send an order."""
    events: list[Transition] = []
    current = state
    if current.phase == "COOLDOWN":
        if current.cooldown_through is not None and bar.index <= current.cooldown_through:
            return current, ()
        current = _idle()
        events.append(Transition(bar.index, "IDLE", "cooldown_elapsed"))
    if current.phase in {"OPEN", "PENDING_CONFIRM"}:
        return current, tuple(events)
    if current.phase == "CONFIRMED":
        if current.entry_bar_index is not None and bar.index >= current.entry_bar_index:
            current = _idle()
            events.append(Transition(bar.index, "IDLE", "entry_window_elapsed"))
        else:
            return current, tuple(events)
    if current.phase == "ARMED":
        current, armed_events = _advance_armed(current, bar, prev_z, params)
        events.extend(armed_events)
        if current.phase != "IDLE":
            return current, tuple(events)
    if current.phase == "IDLE":
        armed = _arm(bar, prev_z, z_returns, params)
        if armed is not None:
            events.append(Transition(bar.index, "ARMED", armed.direction or ""))
            current = armed
    return current, tuple(events)


def on_exit(state: Snapshot, bar_index: int, params: Params) -> tuple[Snapshot, tuple[Transition, ...]]:
    """OPEN becomes EXITED and then COOLDOWN for `cooldown_bars` closed M5 bars."""
    through = bar_index + params.cooldown_bars - 1
    cooled = replace(state, phase="COOLDOWN", cooldown_through=through, trigger_index=None, confirm_index=None)
    return cooled, (
        Transition(bar_index, "EXITED", "position_closed"),
        Transition(bar_index, "COOLDOWN", "cooldown_started"),
    )


def apply_broker(
    state: Snapshot,
    *,
    retcode: int,
    position_opened: bool,
    direction: str,
    fill: float,
    stop: float,
    target: float,
    lots: float,
    risk_account: float,
    when: datetime,
) -> Snapshot:
    """A position on the book is the only proof of a fill. A reject stays flat."""
    if position_opened:
        return replace(
            state,
            phase="OPEN",
            direction=direction,
            entry_time=when,
            entry_price=fill,
            stop=stop,
            target=target,
            lots=lots,
            risk_account=risk_account,
        )
    if retcode in KNOWN_REJECTS:
        return _idle()
    return replace(
        state,
        phase="PENDING_CONFIRM",
        direction=direction,
        entry_time=when,
        entry_price=fill,
        stop=stop,
        target=target,
        lots=lots,
        risk_account=risk_account,
    )


def restore_state(
    snapshot: Snapshot,
    *,
    position_exists: bool,
    entry_window_open: bool,
    now_index: int,
    cooldown_bars: int,
) -> Snapshot:
    """Rebuild after a restart. A missing position is not still open."""
    if position_exists:
        return replace(snapshot, phase="OPEN")
    if snapshot.phase == "OPEN":
        through = now_index + cooldown_bars - 1
        return replace(snapshot, phase="COOLDOWN", cooldown_through=through, trigger_index=None, confirm_index=None)
    if snapshot.phase == "PENDING_CONFIRM":
        return snapshot
    if snapshot.phase == "CONFIRMED" and not entry_window_open:
        return _idle()
    return snapshot


def _advance_armed(
    state: Snapshot,
    bar: SignalBar,
    prev_z: float,
    params: Params,
) -> tuple[Snapshot, list[Transition]]:
    trigger = state.trigger_index if state.trigger_index is not None else -1
    if bar.index <= trigger:
        return state, []
    last = trigger + params.confirm_bars
    if bar.index > last:
        return _idle(), [Transition(bar.index, "IDLE", "candidate_expired")]
    if _can_confirm(state, bar, prev_z, params):
        confirmed = replace(
            state,
            phase="CONFIRMED",
            confirm_index=bar.index,
            entry_bar_index=bar.index + 1,
            frozen_ema=bar.ema,
            frozen_atr=bar.atr,
            signal_time=bar.close_time,
            signal_bid=bar.bid_close,
            z_dev=bar.z_dev,
            z_ret=bar.z_ret,
        )
        return confirmed, [Transition(bar.index, "CONFIRMED", state.direction or "")]
    if bar.index == last:
        return _idle(), [Transition(bar.index, "IDLE", "candidate_expired")]
    return state, []


def _can_confirm(state: Snapshot, bar: SignalBar, prev_z: float, params: Params) -> bool:
    z_value = bar.z_dev
    if not math.isfinite(z_value) or not math.isfinite(prev_z):
        return False
    if state.direction == "LONG":
        if not (z_value > prev_z and z_value - prev_z >= params.reversal_gap):
            return False
        if not bar.log_return > 0.0:
            return False
        if params.use_trend_filter and (not bar.trend_ready or bar.trend_block_long):
            return False
    elif state.direction == "SHORT":
        if not (z_value < prev_z and prev_z - z_value >= params.reversal_gap):
            return False
        if not bar.log_return < 0.0:
            return False
        if params.use_trend_filter and (not bar.trend_ready or bar.trend_block_short):
            return False
    else:
        return False
    if not math.isfinite(bar.spread_pips) or bar.spread_pips > params.max_spread_pips:
        return False
    if not bar.in_session or bar.event_blocked:
        return False
    if not math.isfinite(bar.atr) or bar.atr <= 0.0 or not math.isfinite(bar.ema):
        return False
    return math.isfinite(bar.bid_close) and bar.bid_close > 0.0


def _arm(bar: SignalBar, prev_z: float, z_returns: np.ndarray, params: Params) -> Snapshot | None:
    if not bar.in_session or bar.event_blocked or not math.isfinite(bar.z_dev):
        return None
    long_cross = params.allow_long and _crossed_down(prev_z, bar.z_dev, -params.z_entry)
    short_cross = params.allow_short and _crossed_up(prev_z, bar.z_dev, params.z_entry)
    if long_cross == short_cross:
        return None
    direction = "LONG" if long_cross else "SHORT"
    if params.use_return_filter and not _return_shock(z_returns, bar.index, params, direction):
        return None
    return Snapshot(
        phase="ARMED",
        direction=direction,
        trigger_index=bar.index,
        trigger_time=bar.close_time,
        trigger_z=bar.z_dev,
        z_dev=bar.z_dev,
        z_ret=bar.z_ret,
    )


def _return_shock(z_returns: np.ndarray, index: int, params: Params, direction: str) -> bool:
    start = max(0, index - params.ret_lookback)
    window = z_returns[start : index + 1]
    level = params.ret_z_threshold
    for value in window:
        current = float(value)
        if not math.isfinite(current):
            continue
        if direction == "LONG" and current <= -level:
            return True
        if direction == "SHORT" and current >= level:
            return True
    return False


def _crossed_down(previous: float, current: float, level: float) -> bool:
    return math.isfinite(previous) and math.isfinite(current) and previous > level and current <= level


def _crossed_up(previous: float, current: float, level: float) -> bool:
    return math.isfinite(previous) and math.isfinite(current) and previous < level and current >= level


def _idle() -> Snapshot:
    return Snapshot()
