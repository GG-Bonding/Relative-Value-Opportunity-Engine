"""State machine, sizing, and the one-shot entry attempt."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pytest

from m5zrev.params import Params
from m5zrev.risk import Quote, entries_allowed, size_lots, try_entry
from m5zrev.state import SignalBar, Snapshot, apply_broker, restore_state, step_bar
from tests.test_m5zrev_backtest import sample_spec


def _bar(index: int, z_dev: float, **overrides: object) -> SignalBar:
    values: dict[str, object] = {
        "index": index,
        "close_time": datetime(2026, 1, 15, 10, index, tzinfo=UTC),
        "z_dev": z_dev,
        "z_ret": -2.5,
        "log_return": 0.0001,
        "ema": math.log(0.852),
        "atr": 0.0004,
        "bid_close": 0.85,
        "spread_pips": 1.0,
        "in_session": True,
        "event_blocked": False,
        "trend_block_long": False,
        "trend_block_short": False,
        "trend_ready": True,
    }
    values.update(overrides)
    return SignalBar(**values)  # type: ignore[arg-type]


def _walk(z_values: list[float], params: Params | None = None) -> Snapshot:
    chosen = params or Params()
    state = Snapshot()
    z_ret = np.full(len(z_values), -2.5)
    for index, z_value in enumerate(z_values):
        previous = z_values[index - 1] if index else float("nan")
        state, _events = step_bar(state, _bar(index, z_value), previous, z_ret, chosen)
    return state


def test_a_cross_arms_once_and_a_later_reversal_confirms() -> None:
    state = _walk([-1.0, -2.2])
    assert state.phase == "ARMED"
    assert state.direction == "LONG"
    assert state.trigger_z == pytest.approx(-2.2)
    again = _walk([-1.0, -2.2, -2.4])
    assert again.phase == "ARMED"
    assert again.trigger_index == 1
    confirmed = _walk([-1.0, -2.2, -1.8])
    assert confirmed.phase == "CONFIRMED"
    assert confirmed.entry_bar_index == 3
    assert confirmed.frozen_ema == pytest.approx(math.log(0.852))


def test_candidate_expires_after_three_bars_and_does_not_rearm_the_same_extreme() -> None:
    # Trigger at 1. Bars 2, 3 and 4 never reverse by 0.25. Bar 5 is still extreme.
    state = _walk([-1.0, -2.2, -2.3, -2.4, -2.2, -2.5])
    assert state.phase == "IDLE"
    fresh = _walk([-1.0, -2.2, -2.3, -2.4, -2.2, -1.0, -2.4])
    assert fresh.phase == "ARMED"
    assert fresh.trigger_index == 6


def test_return_filter_and_trend_filter_can_block_without_changing_the_cross() -> None:
    params = Params(use_return_filter=True)
    z_ret = np.array([0.0, -0.5, -0.4])
    state = Snapshot()
    for index, z_value in enumerate([-1.0, -2.2]):
        previous = -1.0 if index else float("nan")
        state, _events = step_bar(state, _bar(index, z_value), previous, z_ret, params)
    assert state.phase == "IDLE"
    shocked = z_ret.copy()
    shocked[0] = -2.2
    state = Snapshot()
    for index, z_value in enumerate([-1.0, -2.2]):
        previous = -1.0 if index else float("nan")
        state, _events = step_bar(state, _bar(index, z_value, z_ret=-0.2), previous, shocked, params)
    assert state.phase == "ARMED"
    blocked = Snapshot()
    series = [-1.0, -2.2, -1.8]
    returns = np.full(3, -2.5)
    for index, z_value in enumerate(series):
        previous = series[index - 1] if index else float("nan")
        bar = _bar(index, z_value, trend_block_long=True, trend_ready=True)
        blocked, _events = step_bar(blocked, bar, previous, returns, Params(use_trend_filter=True))
    assert blocked.phase == "ARMED"
    allowed = Snapshot()
    series = [-1.0, -2.2, -1.8]
    returns = np.full(3, -2.5)
    for index, z_value in enumerate(series):
        previous = series[index - 1] if index else float("nan")
        bar = _bar(index, z_value, trend_block_long=False, trend_ready=True)
        allowed, _events = step_bar(allowed, bar, previous, returns, Params(use_trend_filter=True))
    assert allowed.phase == "CONFIRMED"


def test_short_confirmation_is_the_mirror_image() -> None:
    state = Snapshot()
    series = [1.0, 2.2, 1.7]
    returns = np.full(3, 2.5)
    for index, z_value in enumerate(series):
        previous = series[index - 1] if index else float("nan")
        bar = _bar(index, z_value, z_ret=2.5, log_return=-0.0002)
        state, _events = step_bar(state, bar, previous, returns, Params())
    assert state.phase == "CONFIRMED"
    assert state.direction == "SHORT"


def test_cooldown_blocks_the_next_three_closes() -> None:
    from m5zrev.state import on_exit

    state, events = on_exit(Snapshot(phase="OPEN", direction="LONG"), 4, Params())
    assert [item.phase for item in events] == ["EXITED", "COOLDOWN"]
    assert state.cooldown_through == 6
    returns = np.full(8, -2.5)
    for index, z_value in enumerate([-1.0, -2.3], start=5):
        state, _events = step_bar(state, _bar(index, z_value), -1.0 if index == 5 else -1.0, returns, Params())
    assert state.phase == "COOLDOWN"
    state, _events = step_bar(state, _bar(7, -2.3), -1.0, returns, Params())
    assert state.phase == "ARMED"


def test_out_of_session_and_event_blackout_do_not_arm() -> None:
    returns = np.full(2, -3.0)
    state, _events = step_bar(Snapshot(), _bar(1, -2.2, in_session=False), -1.0, returns, Params())
    assert state.phase == "IDLE"
    state, _events = step_bar(Snapshot(), _bar(1, -2.2, event_blocked=True), -1.0, returns, Params())
    assert state.phase == "IDLE"


def test_disabled_direction_does_not_arm() -> None:
    assert _walk([-1.0, -2.2], Params(allow_long=False)).phase == "IDLE"
    state = Snapshot()
    returns = np.full(2, 2.5)
    for index, z_value in enumerate([1.0, 2.2]):
        previous = 1.0 if index else float("nan")
        bar = _bar(index, z_value, log_return=-0.0001, z_ret=2.5)
        state, _events = step_bar(state, bar, previous, returns, Params(allow_short=False))
    assert state.phase == "IDLE"


def test_broker_reject_is_not_a_position_and_pending_is_not_retried() -> None:
    confirmed = Snapshot(phase="CONFIRMED", direction="LONG")
    rejected = apply_broker(
        confirmed,
        retcode=10006,
        position_opened=False,
        direction="LONG",
        fill=0.85,
        stop=0.84,
        target=0.86,
        lots=0.01,
        risk_account=5.0,
        when=datetime(2026, 1, 15, 10, tzinfo=UTC),
    )
    assert rejected.phase == "IDLE"
    pending = apply_broker(
        confirmed,
        retcode=10009,
        position_opened=False,
        direction="LONG",
        fill=0.85,
        stop=0.84,
        target=0.86,
        lots=0.01,
        risk_account=5.0,
        when=datetime(2026, 1, 15, 10, tzinfo=UTC),
    )
    assert pending.phase == "PENDING_CONFIRM"
    seen = apply_broker(
        confirmed,
        retcode=10006,
        position_opened=True,
        direction="LONG",
        fill=0.85,
        stop=0.84,
        target=0.86,
        lots=0.01,
        risk_account=5.0,
        when=datetime(2026, 1, 15, 10, tzinfo=UTC),
    )
    assert seen.phase == "OPEN"


def test_restart_trusts_the_position_and_does_not_invent_one() -> None:
    open_book = Snapshot(phase="IDLE", direction="LONG", entry_price=0.85)
    restored = restore_state(open_book, position_exists=True, entry_window_open=False, now_index=10, cooldown_bars=3)
    assert restored.phase == "OPEN"
    vanished = restore_state(
        Snapshot(phase="OPEN", direction="LONG"),
        position_exists=False,
        entry_window_open=False,
        now_index=10,
        cooldown_bars=3,
    )
    assert vanished.phase == "COOLDOWN"
    assert vanished.cooldown_through == 12
    pending = Snapshot(phase="PENDING_CONFIRM", direction="LONG", lots=0.02)
    restored = restore_state(
        pending,
        position_exists=False,
        entry_window_open=True,
        now_index=3,
        cooldown_bars=3,
    )
    assert restored.phase == "PENDING_CONFIRM"
    late = restore_state(
        Snapshot(phase="CONFIRMED"),
        position_exists=False,
        entry_window_open=False,
        now_index=4,
        cooldown_bars=3,
    )
    assert late.phase == "IDLE"


def test_minimum_lot_and_reward_ratio_skip_the_trade() -> None:
    spec = sample_spec()
    state = Snapshot(
        phase="CONFIRMED",
        direction="LONG",
        frozen_ema=math.log(0.852),
        frozen_atr=0.0004,
        signal_bid=0.85,
        signal_time=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
    )
    quote = Quote(datetime(2026, 1, 15, 10, 0, tzinfo=UTC), 0.85, 0.8501)
    accepted = try_entry(
        state,
        quote,
        spec,
        Params(),
        equity=1000.0,
        day_pnl=0.0,
        day_start_equity=1000.0,
        day_trades=0,
        position_open=False,
        entry_deadline=datetime(2026, 1, 15, 10, 5, tzinfo=UTC),
        session_open=True,
        event_blocked=False,
    )
    assert accepted.accepted
    assert accepted.fill == pytest.approx(0.8501)
    assert accepted.stop == pytest.approx(0.8495)
    assert accepted.target == pytest.approx(0.852)
    assert accepted.lots == pytest.approx(0.06)
    huge = sample_spec(volume_min=0.1)
    skipped = try_entry(
        state,
        quote,
        huge,
        Params(),
        equity=1000.0,
        day_pnl=0.0,
        day_start_equity=1000.0,
        day_trades=0,
        position_open=False,
        entry_deadline=datetime(2026, 1, 15, 10, 5, tzinfo=UTC),
        session_open=True,
        event_blocked=False,
    )
    assert skipped.reason == "min_lot_risk"
    poor = try_entry(
        replace_target(state, math.log(0.8502)),
        quote,
        spec,
        Params(),
        equity=1000.0,
        day_pnl=0.0,
        day_start_equity=1000.0,
        day_trades=0,
        position_open=False,
        entry_deadline=datetime(2026, 1, 15, 10, 5, tzinfo=UTC),
        session_open=True,
        event_blocked=False,
    )
    assert poor.reason == "reward_risk"
    assert entries_allowed(-20.0, 1000.0, 0, Params()) is False
    assert entries_allowed(-19.0, 1000.0, 0, Params()) is True
    assert entries_allowed(0.0, 1000.0, 3, Params()) is False
    before = size_lots(1000.0, 0.0006, spec, Params())
    after = size_lots(900.0, 0.0006, spec, Params())
    assert before is not None and after is not None
    assert after <= before


def replace_target(state: Snapshot, ema: float) -> Snapshot:
    from dataclasses import replace

    return replace(state, frozen_ema=ema)
