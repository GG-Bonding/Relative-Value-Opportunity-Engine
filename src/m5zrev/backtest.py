"""Event-driven EURGBP book.

Signals are computed when an M5 bar closes. The next bar's first valid quote
is the only entry attempt. A tick tape sells the bid and buys the ask. An
OHLC tape is a study: the ask is the bid plus a spread, and a bar that can
reach both the stop and the target is closed at the worse price.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from m5zrev.data import BarTable, SymbolSpec, TickTable, require_valid, validate_bars, validate_ticks
from m5zrev.features import Features, build_features
from m5zrev.params import Params
from m5zrev.risk import EntryOrder, Quote, trade_pnl, try_entry
from m5zrev.session import in_london_session, london_local
from m5zrev.state import RETCODE_DONE, SignalBar, Snapshot, Transition, apply_broker, on_exit, step_bar

M5 = timedelta(minutes=5)
H1 = timedelta(hours=1)
BrokerSubmit = Callable[[EntryOrder], tuple[int, bool, float | None]]


@dataclass(frozen=True, slots=True)
class Trade:
    direction: str
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop: float
    target: float
    lots: float
    pnl_pips: float
    pnl_account: float
    pnl_r: float
    hold_minutes: float
    exit_reason: str
    path_uncertain: bool
    mae_pips: float
    mfe_pips: float
    spread_pips: float
    z_dev: float
    z_ret: float
    commission: float
    execution: str


@dataclass(frozen=True, slots=True)
class BacktestResult:
    trades: tuple[Trade, ...]
    execution: str
    warnings: tuple[str, ...]
    equity: float
    order_sends: int
    journal: tuple[Transition, ...]
    calendar_status: str


@dataclass
class _Position:
    direction: str
    entry_time: datetime
    entry_price: float
    stop: float
    target: float
    lots: float
    risk_account: float
    signal_time: datetime
    z_dev: float
    z_ret: float
    spread_pips: float
    entry_bar: int
    mae: float = 0.0
    mfe: float = 0.0


@dataclass
class _Book:
    state: Snapshot = field(default_factory=Snapshot)
    position: _Position | None = None
    equity: float = 1000.0
    day: date | None = None
    day_start: float = 1000.0
    day_pnl: float = 0.0
    day_trades: int = 0
    trades: list[Trade] = field(default_factory=list)
    journal: list[Transition] = field(default_factory=list)
    order_sends: int = 0
    attempted: bool = False


def fill_broker(order: EntryOrder) -> tuple[int, bool, float | None]:
    lots = order.lots if order.lots is not None else 0.0
    return RETCODE_DONE, True, lots


def run_backtest(
    m5: BarTable,
    h1: BarTable | None,
    ticks: TickTable | None,
    spec: SymbolSpec,
    params: Params,
    events: tuple[datetime, ...] = (),
    *,
    study_spread_pips: float | None = None,
    broker: BrokerSubmit = fill_broker,
    initial_equity: float | None = None,
) -> BacktestResult:
    require_valid(validate_bars(m5, expected_duration=M5, pip=params.pip))
    if h1 is not None:
        require_valid(validate_bars(h1, expected_duration=H1, pip=params.pip))
    if ticks is not None:
        require_valid(validate_ticks(ticks))
    features, notes = build_features(m5, h1, ticks, events, params, study_spread_pips)
    if ticks is not None:
        execution = "tick"
    elif study_spread_pips is not None:
        execution = "bid_ohlc_study_spread"
    else:
        execution = "bid_ohlc"
    warnings = list(notes)
    if execution != "tick":
        warnings.insert(
            0,
            "Execution uses M5 bid OHLC. Buys pay an estimated ask and a bar that can hit both "
            "the stop and the target is closed at the worse price. This is not a tick backtest.",
        )
    if not spec.verified_from_terminal:
        warnings.append("Contract spec was not read from the MT5 terminal.")
    return simulate(
        m5,
        ticks,
        spec,
        params,
        features,
        execution=execution,
        warnings=tuple(warnings),
        broker=broker,
        initial_equity=initial_equity if initial_equity is not None else params.initial_equity,
    )


def simulate(
    m5: BarTable,
    ticks: TickTable | None,
    spec: SymbolSpec,
    params: Params,
    features: Features,
    *,
    execution: str,
    warnings: tuple[str, ...] = (),
    broker: BrokerSubmit = fill_broker,
    initial_equity: float = 1000.0,
) -> BacktestResult:
    """Replay `features` bar by bar. `run_backtest` is the path that builds them."""
    if len(features.z_dev) != len(m5.close):
        raise ValueError("features do not match the M5 bars")
    book = _Book(equity=initial_equity, day_start=initial_equity)
    cursor = 0
    for index in range(len(m5.close)):
        _roll_day(book, m5.open_time[index])
        book.attempted = False
        if ticks is not None:
            cursor = _on_ticks(book, index, m5, ticks, features, spec, params, cursor, broker, execution)
        else:
            _on_ohlc(book, index, m5, features, spec, params, broker, execution)
        previous = float(features.z_dev[index - 1]) if index else float("nan")
        book.state, events_out = step_bar(book.state, _signal(index, m5, features), previous, features.z_ret, params)
        book.journal.extend(events_out)
    if book.position is not None:
        _force_flat(book, m5, ticks, features, spec, params, execution)
    return BacktestResult(
        trades=tuple(book.trades),
        execution=execution,
        warnings=warnings,
        equity=book.equity,
        order_sends=book.order_sends,
        journal=tuple(book.journal),
        calendar_status=features.calendar_status,
    )


def resolve_ohlc_exit(
    *,
    direction: str,
    stop: float,
    target: float,
    open_bid: float,
    high_bid: float,
    low_bid: float,
    close_bid: float,
    open_time: datetime,
    close_time: datetime,
    deadline: datetime,
    slippage: float,
    spread: float,
) -> tuple[float, datetime, str, bool] | None:
    """Return `(fill, time, reason, path_uncertain)` for a bid bar.

    `spread` is the full bid-to-ask distance. A short buy-back pays the ask.
    The open is the first print. High and low come after it, in unknown order.
    """
    if open_time >= deadline:
        return _gap_after_deadline(direction, open_bid, stop, open_time, slippage, spread)
    candidates: list[tuple[float, datetime, str]] = []
    if direction == "LONG":
        if open_bid <= stop:
            candidates.append((open_bid - slippage, open_time, "STOP_GAP"))
        elif low_bid <= stop:
            candidates.append((stop - slippage, close_time, "STOP"))
        if high_bid >= target:
            candidates.append((target, close_time, "TARGET"))
        if close_time >= deadline:
            candidates.append((close_bid - slippage, deadline, "TIME"))
    else:
        open_ask = open_bid + spread
        if open_ask >= stop:
            candidates.append((open_ask + slippage, open_time, "STOP_GAP"))
        elif high_bid + spread >= stop:
            candidates.append((stop + slippage, close_time, "STOP"))
        if low_bid + spread <= target:
            candidates.append((target, close_time, "TARGET"))
        if close_time >= deadline:
            candidates.append((close_bid + spread + slippage, deadline, "TIME"))
    if not candidates:
        return None
    if len(candidates) == 1:
        price, when, reason = candidates[0]
        return price, when, reason, False
    if direction == "LONG":
        chosen = min(candidates, key=lambda item: item[0])
    else:
        chosen = max(candidates, key=lambda item: item[0])
    return chosen[0], chosen[1], chosen[2], True


def _gap_after_deadline(
    direction: str,
    open_bid: float,
    stop: float,
    open_time: datetime,
    slippage: float,
    spread: float,
) -> tuple[float, datetime, str, bool]:
    if direction == "LONG":
        if open_bid <= stop:
            return open_bid - slippage, open_time, "STOP_GAP", True
        return open_bid - slippage, open_time, "TIME_DELAYED", False
    open_ask = open_bid + spread
    if open_ask >= stop:
        return open_ask + slippage, open_time, "STOP_GAP", True
    return open_ask + slippage, open_time, "TIME_DELAYED", False


def _on_ticks(
    book: _Book,
    index: int,
    bars: BarTable,
    ticks: TickTable,
    features: Features,
    spec: SymbolSpec,
    params: Params,
    cursor: int,
    broker: BrokerSubmit,
    execution: str,
) -> int:
    while cursor < len(ticks.time) and ticks.time[cursor] < bars.close_time[index]:
        if ticks.time[cursor] < bars.open_time[index]:
            cursor += 1
            continue
        quote = Quote(ticks.time[cursor], float(ticks.bid[cursor]), float(ticks.ask[cursor]))
        entering = book.state.phase == "CONFIRMED" and book.state.entry_bar_index == index and not book.attempted
        if entering:
            if quote.bid > 0.0 and quote.ask >= quote.bid:
                book.attempted = True
                _attempt(book, quote, index, features, spec, params, broker)
            cursor += 1
            continue
        if book.position is not None:
            _mark(book.position, quote.bid, quote.ask)
            closed = _tick_exit(book.position, quote, params)
            if closed is not None:
                _close(book, index, closed[0], quote.time, closed[1], False, spec, params, execution)
        cursor += 1
    return cursor


def _on_ohlc(
    book: _Book,
    index: int,
    bars: BarTable,
    features: Features,
    spec: SymbolSpec,
    params: Params,
    broker: BrokerSubmit,
    execution: str,
) -> None:
    spread_pips = float(features.spread_pips[index])
    if not math.isfinite(spread_pips):
        return
    spread = spread_pips * params.pip
    open_bid = float(bars.open[index])
    if book.state.phase == "CONFIRMED" and book.state.entry_bar_index == index and not book.attempted:
        book.attempted = True
        _attempt(book, Quote(bars.open_time[index], open_bid, open_bid + spread), index, features, spec, params, broker)
    position = book.position
    if position is None:
        return
    outcome = resolve_ohlc_exit(
        direction=position.direction,
        stop=position.stop,
        target=position.target,
        open_bid=open_bid,
        high_bid=float(bars.high[index]),
        low_bid=float(bars.low[index]),
        close_bid=float(bars.close[index]),
        open_time=bars.open_time[index],
        close_time=bars.close_time[index],
        deadline=position.entry_time + timedelta(minutes=params.max_hold_minutes),
        slippage=params.slippage_pips * params.pip,
        spread=spread,
    )
    _mark(position, float(bars.low[index]), float(bars.high[index]) + spread)
    _mark(position, float(bars.high[index]), float(bars.low[index]) + spread)
    if outcome is None:
        return
    price, when, reason, uncertain = outcome
    _close(book, index, price, when, reason, uncertain, spec, params, execution)


def _attempt(
    book: _Book,
    quote: Quote,
    index: int,
    features: Features,
    spec: SymbolSpec,
    params: Params,
    broker: BrokerSubmit,
) -> None:
    signal_time = book.state.signal_time
    deadline = signal_time + M5 if signal_time is not None else quote.time
    _roll_day(book, quote.time)
    order = try_entry(
        book.state,
        quote,
        spec,
        params,
        equity=book.equity,
        day_pnl=book.day_pnl,
        day_start_equity=book.day_start,
        day_trades=book.day_trades,
        position_open=book.position is not None,
        entry_deadline=deadline,
        session_open=in_london_session(quote.time, params.session_start_minute, params.session_end_minute),
        event_blocked=bool(features.event_blocked[index]),
    )
    if not order.accepted or order.fill is None or order.stop is None or order.target is None or order.lots is None:
        book.state = Snapshot()
        book.journal.append(Transition(index, "IDLE", order.reason))
        return
    if order.direction is None or order.risk_account is None:
        book.state = Snapshot()
        book.journal.append(Transition(index, "IDLE", "missing_signal"))
        return
    book.order_sends += 1
    retcode, opened, lots = broker(order)
    filled = lots if lots is not None else order.lots
    book.state = apply_broker(
        book.state,
        retcode=retcode,
        position_opened=opened,
        direction=order.direction,
        fill=order.fill,
        stop=order.stop,
        target=order.target,
        lots=filled,
        risk_account=order.risk_account,
        when=quote.time,
    )
    if book.state.phase != "OPEN":
        book.journal.append(Transition(index, book.state.phase, f"broker_{retcode}"))
        return
    book.day_trades += 1
    book.position = _Position(
        direction=order.direction,
        entry_time=quote.time,
        entry_price=order.fill,
        stop=order.stop,
        target=order.target,
        lots=filled,
        risk_account=order.risk_account,
        signal_time=book.state.signal_time or quote.time,
        z_dev=book.state.z_dev if book.state.z_dev is not None else float("nan"),
        z_ret=book.state.z_ret if book.state.z_ret is not None else float("nan"),
        spread_pips=order.spread_pips or 0.0,
        entry_bar=index,
    )
    _mark(book.position, quote.bid, quote.ask)


def _tick_exit(position: _Position, quote: Quote, params: Params) -> tuple[float, str] | None:
    slip = params.slippage_pips * params.pip
    if position.direction == "LONG":
        if quote.bid <= position.stop:
            reason = "STOP_GAP" if quote.bid < position.stop else "STOP"
            return min(quote.bid, position.stop) - slip, reason
        if quote.bid >= position.target:
            return position.target, "TARGET"
    elif quote.ask >= position.stop:
        reason = "STOP_GAP" if quote.ask > position.stop else "STOP"
        return max(quote.ask, position.stop) + slip, reason
    elif quote.ask <= position.target:
        return position.target, "TARGET"
    deadline = position.entry_time + timedelta(minutes=params.max_hold_minutes)
    if quote.time < deadline:
        return None
    delayed = quote.time > deadline + timedelta(minutes=1)
    reason = "TIME_DELAYED" if delayed else "TIME"
    if position.direction == "LONG":
        return quote.bid - slip, reason
    return quote.ask + slip, reason


def _close(
    book: _Book,
    index: int,
    price: float,
    when: datetime,
    reason: str,
    path_uncertain: bool,
    spec: SymbolSpec,
    params: Params,
    execution: str,
) -> None:
    position = book.position
    if position is None:
        return
    pnl, commission = trade_pnl(position.direction, position.entry_price, price, position.lots, spec)
    risk = position.risk_account
    pnl_r = pnl / risk if risk else float("nan")
    move = price - position.entry_price if position.direction == "LONG" else position.entry_price - price
    book.trades.append(
        Trade(
            direction=position.direction,
            signal_time=position.signal_time,
            entry_time=position.entry_time,
            exit_time=when,
            entry_price=position.entry_price,
            exit_price=price,
            stop=position.stop,
            target=position.target,
            lots=position.lots,
            pnl_pips=move / params.pip,
            pnl_account=pnl,
            pnl_r=pnl_r,
            hold_minutes=(when - position.entry_time).total_seconds() / 60.0,
            exit_reason=reason,
            path_uncertain=path_uncertain,
            mae_pips=position.mae / params.pip,
            mfe_pips=position.mfe / params.pip,
            spread_pips=position.spread_pips,
            z_dev=position.z_dev,
            z_ret=position.z_ret,
            commission=commission,
            execution=execution,
        )
    )
    _roll_day(book, when)
    book.equity += pnl
    book.day_pnl += pnl
    book.position = None
    book.state, events = on_exit(book.state, index, params)
    book.journal.extend(events)


def _force_flat(
    book: _Book,
    bars: BarTable,
    ticks: TickTable | None,
    features: Features,
    spec: SymbolSpec,
    params: Params,
    execution: str,
) -> None:
    position = book.position
    if position is None:
        return
    last = len(bars.close) - 1
    spread_pips = float(features.spread_pips[last])
    spread = spread_pips * params.pip if math.isfinite(spread_pips) else 0.0
    if ticks is not None and ticks.time:
        when = ticks.time[-1]
        price = float(ticks.bid[-1]) if position.direction == "LONG" else float(ticks.ask[-1])
    else:
        when = bars.close_time[last]
        price = float(bars.close[last]) if position.direction == "LONG" else float(bars.close[last]) + spread
    _close(book, last, price, when, "END_OF_SAMPLE", False, spec, params, execution)


def _mark(position: _Position, bid: float, ask: float) -> None:
    move = bid - position.entry_price if position.direction == "LONG" else position.entry_price - ask
    position.mae = min(position.mae, move)
    position.mfe = max(position.mfe, move)


def _roll_day(book: _Book, when: datetime) -> None:
    day = london_local(when).date()
    if book.day != day:
        book.day = day
        book.day_start = book.equity
        book.day_pnl = 0.0
        book.day_trades = 0


def _signal(index: int, bars: BarTable, features: Features) -> SignalBar:
    return SignalBar(
        index=index,
        close_time=bars.close_time[index],
        z_dev=float(features.z_dev[index]),
        z_ret=float(features.z_ret[index]),
        log_return=float(features.log_return[index]),
        ema=float(features.ema[index]),
        atr=float(features.atr[index]),
        bid_close=float(bars.close[index]),
        spread_pips=float(features.spread_pips[index]),
        in_session=bool(features.in_session[index]),
        event_blocked=bool(features.event_blocked[index]),
        trend_block_long=bool(features.trend_block_long[index]),
        trend_block_short=bool(features.trend_block_short[index]),
        trend_ready=bool(features.trend_ready[index]),
    )
