"""Fills, stops, and the cases where the book must stay flat."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from m5zrev.backtest import resolve_ohlc_exit, run_backtest, simulate
from m5zrev.data import BarTable, SymbolSpec, TickTable
from m5zrev.features import Features, build_features
from m5zrev.params import Params
from m5zrev.risk import EntryOrder


def sample_spec(**overrides: float) -> SymbolSpec:
    values: dict[str, object] = {
        "symbol": "EURGBP",
        "digits": 5,
        "point": 0.00001,
        "tick_size": 0.00001,
        "tick_value_account": 1.3,
        "volume_min": 0.01,
        "volume_step": 0.01,
        "volume_max": 10.0,
        "stops_level_points": 0,
        "contract_size": 100_000.0,
        "profit_currency": "GBP",
        "account_currency": "USD",
        "commission_per_lot_side": 0.0,
        "source": "unit-test",
        "verified_from_terminal": False,
    }
    values.update(overrides)
    return SymbolSpec(**values)  # type: ignore[arg-type]


def _bars(start: datetime, count: int, *, step: timedelta = timedelta(minutes=5)) -> BarTable:
    close_time = [start + step * index for index in range(count)]
    price = np.full(count, 0.85)
    return BarTable(
        open_time=[stamp - step for stamp in close_time],
        close_time=close_time,
        open=price.copy(),
        high=price + 0.0004,
        low=price - 0.0004,
        close=price.copy(),
        spread_pips=np.full(count, 1.0),
    )


def _features(count: int, z_dev: list[float], log_return: list[float], *, ema: float | None = None) -> Features:
    ema_value = math.log(0.852) if ema is None else ema
    return Features(
        z_dev=np.array(z_dev, dtype=np.float64),
        z_ret=np.full(count, -2.5),
        log_return=np.array(log_return, dtype=np.float64),
        ema=np.full(count, ema_value),
        atr=np.full(count, 0.0004),
        spread_pips=np.full(count, 1.0),
        in_session=np.ones(count, dtype=bool),
        event_blocked=np.zeros(count, dtype=bool),
        trend_block_long=np.zeros(count, dtype=bool),
        trend_block_short=np.zeros(count, dtype=bool),
        trend_ready=np.ones(count, dtype=bool),
        calendar_status="missing",
    )


def test_no_tick_does_not_fill_and_ohlc_without_a_spread_is_not_a_tick_result() -> None:
    start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    bars = _bars(start, 5)
    features = _features(5, [-1.0, -2.2, -1.8, -1.5, -1.4], [float("nan"), -0.001, 0.0002, 0.0, 0.0])
    empty = TickTable(time=[], bid=np.array([]), ask=np.array([]))
    result = simulate(bars, empty, sample_spec(), Params(), features, execution="tick")
    assert result.trades == ()
    assert result.order_sends == 0
    assert any(item.reason == "entry_window_elapsed" for item in result.journal)
    params = Params(ema_period=4, dev_window=4, ret_window=4, atr_period=2)
    study = run_backtest(bars, None, None, sample_spec(), params, study_spread_pips=1.0)
    assert study.execution == "bid_ohlc_study_spread"
    assert study.trades == ()
    assert any("not a tick backtest" in note for note in study.warnings)


def test_long_buys_the_ask_and_a_stop_does_not_wait_for_the_time_limit() -> None:
    start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    bars = _bars(start, 4)
    # Confirm on bar 2 (10:10). Entry bar 3 opens at 10:10 and closes at 10:15.
    features = _features(4, [-1.0, -2.2, -1.8, -1.4], [float("nan"), -0.001, 0.0002, 0.0])
    ticks = TickTable(
        time=[
            datetime(2026, 1, 15, 10, 10, tzinfo=UTC),
            datetime(2026, 1, 15, 10, 12, tzinfo=UTC),
        ],
        bid=np.array([0.85000, 0.84940]),
        ask=np.array([0.85010, 0.84950]),
    )
    result = simulate(bars, ticks, sample_spec(), Params(), features, execution="tick")
    assert result.order_sends == 1
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.direction == "LONG"
    assert trade.entry_price == pytest.approx(0.85010)
    assert trade.exit_reason == "STOP_GAP"
    assert trade.exit_price == pytest.approx(0.84940)
    assert trade.hold_minutes == pytest.approx(2.0)
    assert trade.hold_minutes < 60
    assert trade.pnl_account < 0


def test_short_sells_the_bid_and_buys_back_the_ask() -> None:
    start = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    bars = _bars(start, 4)
    features = _features(4, [1.0, 2.2, 1.7, 1.4], [float("nan"), 0.001, -0.0002, 0.0], ema=math.log(0.848))
    ticks = TickTable(
        time=[
            datetime(2026, 1, 15, 10, 10, tzinfo=UTC),
            datetime(2026, 1, 15, 10, 12, tzinfo=UTC),
        ],
        bid=np.array([0.85000, 0.85210]),
        ask=np.array([0.85010, 0.85220]),
    )
    result = simulate(bars, ticks, sample_spec(), Params(), features, execution="tick")
    trade = result.trades[0]
    assert trade.direction == "SHORT"
    assert trade.entry_price == pytest.approx(0.85000)
    assert trade.exit_reason == "STOP_GAP"
    assert trade.exit_price == pytest.approx(0.85220)


def test_ohlc_collision_takes_the_stop_and_marks_the_path() -> None:
    direct = resolve_ohlc_exit(
        direction="LONG",
        stop=0.8495,
        target=0.8520,
        open_bid=0.8500,
        high_bid=0.8530,
        low_bid=0.8490,
        close_bid=0.8510,
        open_time=datetime(2026, 1, 15, 10, 10, tzinfo=UTC),
        close_time=datetime(2026, 1, 15, 10, 15, tzinfo=UTC),
        deadline=datetime(2026, 1, 15, 11, 10, tzinfo=UTC),
        slippage=0.0,
        spread=0.0001,
    )
    assert direct is not None
    assert direct[2] == "STOP"
    assert direct[0] == pytest.approx(0.8495)
    assert direct[3] is True
    delayed = resolve_ohlc_exit(
        direction="LONG",
        stop=0.8495,
        target=0.8520,
        open_bid=0.8502,
        high_bid=0.8504,
        low_bid=0.8500,
        close_bid=0.8501,
        open_time=datetime(2026, 1, 19, 8, 0, tzinfo=UTC),
        close_time=datetime(2026, 1, 19, 8, 5, tzinfo=UTC),
        deadline=datetime(2026, 1, 16, 17, 50, tzinfo=UTC),
        slippage=0.0,
        spread=0.0001,
    )
    assert delayed is not None
    assert delayed[2] == "TIME_DELAYED"
    assert delayed[3] is False


def test_weekend_time_exit_and_a_rejected_order_is_not_sent_twice() -> None:
    friday = datetime(2026, 1, 16, 16, 45, tzinfo=UTC)
    bars = _bars(friday, 4)
    monday_open = datetime(2026, 1, 19, 8, 0, tzinfo=UTC)
    bars.close_time[3] = monday_open + timedelta(minutes=5)
    bars.open_time[3] = monday_open
    features = _features(4, [-1.0, -2.2, -1.8, -1.0], [float("nan"), -0.001, 0.0002, 0.0])
    ticks = TickTable(
        time=[friday + timedelta(minutes=10), monday_open],
        bid=np.array([0.85000, 0.85020]),
        ask=np.array([0.85010, 0.85030]),
    )
    # Bar 2 closes at friday+10min and confirms. Entry bar is bar 3, which is Monday.
    # That quote is outside the five-minute entry window, so the book must not chase it.
    chased = simulate(bars, ticks, sample_spec(), Params(), features, execution="tick")
    assert chased.trades == ()
    assert chased.order_sends == 0

    held = _bars(friday, 4)
    held_features = _features(4, [-1.0, -2.2, -1.8, 0.0], [float("nan"), -0.001, 0.0002, 0.0])
    entry = friday + timedelta(minutes=10)
    held_ticks = TickTable(
        time=[entry, entry + timedelta(minutes=2), monday_open],
        bid=np.array([0.85000, 0.85005, 0.85020]),
        ask=np.array([0.85010, 0.85015, 0.85030]),
    )
    # The Monday tick has no bar. Add a Monday bar so the replay can see it.
    extended = _append_bar(held, monday_open)
    extended_features = _append_features(held_features, 0.0, 0.0)
    result = simulate(extended, held_ticks, sample_spec(), Params(), extended_features, execution="tick")
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "TIME_DELAYED"
    assert result.trades[0].hold_minutes > 60

    sends: list[int] = []

    def reject(order: EntryOrder) -> tuple[int, bool, float | None]:
        sends.append(1)
        return 10006, False, None

    stopped = simulate(
        _bars(datetime(2026, 1, 15, 10, 0, tzinfo=UTC), 4),
        TickTable(
            time=[
                datetime(2026, 1, 15, 10, 10, tzinfo=UTC),
                datetime(2026, 1, 15, 10, 11, tzinfo=UTC),
            ],
            bid=np.array([0.85, 0.85]),
            ask=np.array([0.8501, 0.8501]),
        ),
        sample_spec(),
        Params(),
        _features(4, [-1.0, -2.2, -1.8, -1.4], [float("nan"), -0.001, 0.0002, 0.0]),
        execution="tick",
        broker=reject,
    )
    assert sends == [1]
    assert stopped.trades == ()
    assert stopped.order_sends == 1
    assert all(item.phase != "OPEN" for item in stopped.journal)


def test_closed_features_ignore_a_future_h1_and_a_future_m5_close() -> None:
    start = datetime(2026, 1, 15, 10, 5, tzinfo=UTC)
    m5 = _bars(start, 30)
    future = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    h1 = _bars(future, 1, step=timedelta(hours=1))
    features, _notes = build_features(m5, h1, None, (), Params(), 1.0)
    assert not features.trend_ready.any()
    changed = BarTable(
        open_time=m5.open_time,
        close_time=m5.close_time,
        open=m5.open.copy(),
        high=m5.high.copy(),
        low=m5.low.copy(),
        close=m5.close.copy(),
        spread_pips=m5.spread_pips.copy(),
    )
    changed.close[-1] = 0.90
    again, _notes = build_features(changed, None, None, (), Params(), 1.0)
    base, _notes = build_features(m5, None, None, (), Params(), 1.0)
    np.testing.assert_allclose(base.z_dev[:-1], again.z_dev[:-1], equal_nan=True)


def _append_bar(bars: BarTable, open_time: datetime) -> BarTable:
    price = float(bars.close[-1])
    return BarTable(
        open_time=[*bars.open_time, open_time],
        close_time=[*bars.close_time, open_time + timedelta(minutes=5)],
        open=np.append(bars.open, price),
        high=np.append(bars.high, price + 0.0004),
        low=np.append(bars.low, price - 0.0004),
        close=np.append(bars.close, price),
        spread_pips=np.append(bars.spread_pips, 1.0),
    )


def _append_features(features: Features, z_dev: float, log_return: float) -> Features:
    return Features(
        z_dev=np.append(features.z_dev, z_dev),
        z_ret=np.append(features.z_ret, -2.5),
        log_return=np.append(features.log_return, log_return),
        ema=np.append(features.ema, features.ema[-1]),
        atr=np.append(features.atr, features.atr[-1]),
        spread_pips=np.append(features.spread_pips, 1.0),
        in_session=np.append(features.in_session, True),
        event_blocked=np.append(features.event_blocked, False),
        trend_block_long=np.append(features.trend_block_long, False),
        trend_block_short=np.append(features.trend_block_short, False),
        trend_ready=np.append(features.trend_ready, True),
        calendar_status=features.calendar_status,
    )
