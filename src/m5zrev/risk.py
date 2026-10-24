"""Account-currency size and the frozen reward-to-risk check.

EURGBP profit is in GBP. The dollar risk uses the terminal tick value, not a
fixed pip price. A minimum lot that risks more than the budget is skipped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from m5zrev.data import SymbolSpec
from m5zrev.params import Params
from m5zrev.state import Snapshot


@dataclass(frozen=True, slots=True)
class Quote:
    time: datetime
    bid: float
    ask: float


@dataclass(frozen=True, slots=True)
class EntryOrder:
    accepted: bool
    reason: str
    direction: str | None = None
    fill: float | None = None
    stop: float | None = None
    target: float | None = None
    lots: float | None = None
    risk_account: float | None = None
    reward_account: float | None = None
    spread_pips: float | None = None
    risk_price: float | None = None
    reward_price: float | None = None


def money_per_price_unit(spec: SymbolSpec) -> float:
    """Account currency for a 1.0 price move on 1 lot."""
    return spec.tick_value_account / spec.tick_size


def entries_allowed(day_pnl: float, day_start_equity: float, day_trades: int, params: Params) -> bool:
    if day_trades >= params.max_trades_per_day:
        return False
    if day_start_equity <= 0:
        return False
    return day_pnl > -params.daily_loss_fraction * day_start_equity


def size_lots(equity: float, risk_price: float, spec: SymbolSpec, params: Params) -> float | None:
    """Floor to the volume step. Never round a too-small budget up to the minimum."""
    if equity <= 0 or risk_price <= 0 or not params.allow_long and not params.allow_short:
        return None
    loss_per_lot = risk_price * money_per_price_unit(spec)
    if loss_per_lot <= 0:
        return None
    budget = equity * params.risk_fraction
    lots = _floor_step(budget / loss_per_lot, spec)
    while lots is not None and lots * loss_per_lot > budget + 1e-9:
        lots = _floor_step(lots - spec.volume_step, spec)
    return lots


def try_entry(
    state: Snapshot,
    quote: Quote,
    spec: SymbolSpec,
    params: Params,
    *,
    equity: float,
    day_pnl: float,
    day_start_equity: float,
    day_trades: int,
    position_open: bool,
    entry_deadline: datetime,
    session_open: bool,
    event_blocked: bool,
) -> EntryOrder:
    """One attempt at the first valid quote. Failure cancels the candidate."""
    if state.phase != "CONFIRMED" or position_open:
        return EntryOrder(False, "not_confirmed")
    if not session_open or event_blocked:
        return EntryOrder(False, "session_or_event")
    if state.signal_time is None or quote.time < state.signal_time or quote.time >= entry_deadline:
        return EntryOrder(False, "stale_quote")
    if not (quote.bid > 0.0 and quote.ask >= quote.bid):
        return EntryOrder(False, "invalid_quote")
    spread_pips = (quote.ask - quote.bid) / params.pip
    if spread_pips > params.max_spread_pips:
        return EntryOrder(False, "spread", spread_pips=spread_pips)
    if not entries_allowed(day_pnl, day_start_equity, day_trades, params):
        return EntryOrder(False, "daily_limit")
    atr = state.frozen_atr
    ema = state.frozen_ema
    signal_bid = state.signal_bid
    if atr is None or ema is None or signal_bid is None or state.direction not in {"LONG", "SHORT"}:
        return EntryOrder(False, "missing_signal")
    if not (math.isfinite(atr) and atr > 0.0 and math.isfinite(ema)):
        return EntryOrder(False, "missing_signal")
    mid = (quote.bid + quote.ask) / 2.0
    if abs(mid - signal_bid) > params.max_entry_jump_atr * atr:
        return EntryOrder(False, "jump")
    slip = params.slippage_pips * params.pip
    target = _round(math.exp(ema), spec)
    if state.direction == "LONG":
        fill = _round(quote.ask + slip, spec)
        stop = _round(fill - params.atr_mult * atr, spec)
        reward_price = target - fill
        risk_distance = fill - stop
    else:
        fill = _round(quote.bid - slip, spec)
        stop = _round(fill + params.atr_mult * atr, spec)
        reward_price = fill - target
        risk_distance = stop - fill
    if risk_distance <= 0.0:
        return EntryOrder(False, "stop_side")
    min_distance = spec.stops_level_points * spec.point
    if risk_distance + 1e-12 < min_distance:
        return EntryOrder(False, "stops_level")
    commission_price = (2.0 * spec.commission_per_lot_side) / money_per_price_unit(spec)
    risk_price = risk_distance + slip + commission_price
    net_reward = reward_price - slip - commission_price
    if net_reward <= 0.0 or net_reward / risk_price < params.min_rr:
        return EntryOrder(False, "reward_risk", spread_pips=spread_pips)
    lots = size_lots(equity, risk_price, spec, params)
    if lots is None:
        return EntryOrder(False, "min_lot_risk", spread_pips=spread_pips)
    unit = money_per_price_unit(spec)
    return EntryOrder(
        True,
        "accepted",
        direction=state.direction,
        fill=fill,
        stop=stop,
        target=target,
        lots=lots,
        risk_account=risk_price * unit * lots,
        reward_account=net_reward * unit * lots,
        spread_pips=spread_pips,
        risk_price=risk_price,
        reward_price=net_reward,
    )


def trade_pnl(direction: str, entry: float, exit_price: float, lots: float, spec: SymbolSpec) -> tuple[float, float]:
    """Net account currency after both commission sides, and the price move in pips is left to the caller."""
    move = exit_price - entry if direction == "LONG" else entry - exit_price
    gross = move * money_per_price_unit(spec) * lots
    commission = 2.0 * spec.commission_per_lot_side * lots
    return gross - commission, commission


def _round(price: float, spec: SymbolSpec) -> float:
    steps = round(price / spec.tick_size)
    return round(steps * spec.tick_size, spec.digits)


def _floor_step(raw: float, spec: SymbolSpec) -> float | None:
    if raw < spec.volume_min:
        return None
    steps = math.floor((raw + 1e-12) / spec.volume_step)
    lots = round(steps * spec.volume_step, 8)
    if lots > spec.volume_max:
        lots = round(math.floor((spec.volume_max + 1e-12) / spec.volume_step) * spec.volume_step, 8)
    if lots < spec.volume_min:
        return None
    return lots
