"""The M15 book cannot see today's daily close or the next bar."""

from __future__ import annotations

import statistics
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from api.cli import main
from m15meanrev.backtest import StrategyParams, run_backtest
from m15meanrev.calendar import entry_windows, event_kept, quoted_instant, select_releases
from m15meanrev.histdata import m1_text_to_m15
from m15meanrev.load import BarSeries, DailySeries, load_bars, load_daily
from m15meanrev.report import NO_LOOKAHEAD, sample_info, write_report
from m15meanrev.signals import align_previous_trading_day, blacklist_mask, daily_price_z, rolling_return_z

START = datetime(2020, 6, 2, 10, 0, tzinfo=UTC)


def test_return_z_uses_this_bar_and_not_the_next() -> None:
    close = np.array([1.0, 1.01, 1.02, 1.0, 1.03, 1.04, 0.99], dtype=np.float64)
    window = 4
    z = rolling_return_z(close, window)
    returns = np.log(close[1:] / close[:-1])
    for index in range(window, len(close)):
        sample = returns[index - window : index]
        expected = (sample[-1] - statistics.mean(sample)) / statistics.stdev(sample)
        assert z[index] == pytest.approx(expected)
    assert np.all(np.isnan(z[:window]))
    changed = close.copy()
    changed[-1] = 2.0
    again = rolling_return_z(changed, window)
    np.testing.assert_allclose(z[:-1], again[:-1], equal_nan=True)
    assert again[-1] != pytest.approx(z[-1])
    assert np.all(np.isnan(rolling_return_z(np.ones(12), window)))
    assert np.all(np.isnan(rolling_return_z(close[:3], window)))


def test_previous_trading_day_hides_the_same_day_close() -> None:
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(10)]
    closes = np.linspace(0.80, 0.90, 10)
    crashed = closes.copy()
    crashed[-1] = 0.50
    base = daily_price_z(closes, 5)
    after = daily_price_z(crashed, 5)
    seen = align_previous_trading_day([days[-1]], days, after)
    assert seen[0] == pytest.approx(base[-2])
    assert seen[0] > 0
    assert after[-1] < 0
    assert not np.isfinite(align_previous_trading_day([days[4]], days, after)[0])


def test_missing_price_z_blocks_longs_and_is_not_zero() -> None:
    # 2020-06-05 is Friday. The previous daily row does not yet have a 5-day price z.
    bars = _shock(0.85, -0.004, when=datetime(2020, 6, 5, 10, 0, tzinfo=UTC))
    days = [date(2020, 6, 1) + timedelta(days=i) for i in range(10)]
    daily = DailySeries(day=days, close=np.linspace(0.80, 0.90, 10))
    params = _params(long_filter="on")
    assert run_backtest(bars, daily, params) == []
    opened = run_backtest(bars, daily, _params(long_filter="off"))
    assert len(opened) == 1
    assert opened[0].direction == "LONG"
    assert opened[0].entry_price_z60 is None


def test_loose_filter_drops_a_long_that_strict_keeps() -> None:
    bars = _shock(0.85, -0.004, when=datetime(2020, 1, 6, 10, 0, tzinfo=UTC))
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(6)]
    # Previous day is index 4. Its 5-day z is about -0.35: below 0 and above -1.
    daily = DailySeries(day=days, close=np.array([1.0, 2.0, 3.0, 4.0, 2.0, 9.0]))
    previous = daily_price_z(daily.close, 5)[4]
    assert -1.0 <= previous < 0.0
    assert run_backtest(bars, daily, _params(long_filter="on")) == []
    kept = run_backtest(bars, daily, _params(long_filter="strict"))
    assert len(kept) == 1
    assert kept[0].entry_price_z60 == pytest.approx(previous)
    crashed = DailySeries(day=days, close=daily.close.copy())
    crashed.close[-1] = 0.01
    assert run_backtest(bars, crashed, _params(long_filter="strict")) == kept


def test_negative_price_z_still_allows_the_short() -> None:
    bars = _shock(0.85, 0.003, when=datetime(2020, 1, 6, 10, 0, tzinfo=UTC))
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(6)]
    daily = DailySeries(day=days, close=np.array([1.0, 2.0, 3.0, 4.0, 2.0, 9.0]))
    trades = run_backtest(bars, daily, _params(long_filter="on"))
    assert len(trades) == 1
    assert trades[0].direction == "SHORT"
    assert trades[0].entry_price_z60 == pytest.approx(daily_price_z(daily.close, 5)[4])
    assert trades[0].entry_price_z60 < 0


def test_bid_spread_is_paid_on_the_short_cover() -> None:
    bars = _shock(0.85, 0.003)
    trades = run_backtest(bars, params=_params())
    trade = trades[0]
    assert trade.direction == "SHORT"
    assert trade.entry_price == pytest.approx(float(bars.close[9]))
    assert trade.exit_price == pytest.approx(float(bars.close[12]) + 1.2 * 0.0001)
    assert trade.hold_minutes == 45
    assert trade.exit_reason == "REVERSION"
    assert trade.pnl_pips == pytest.approx((trade.entry_price - trade.exit_price) / 0.0001)
    assert trade.entry_time == datetime(2020, 6, 2, 12, 15, tzinfo=UTC)
    assert trade.exit_time == datetime(2020, 6, 2, 13, 0, tzinfo=UTC)


def test_a_wick_does_not_exit_and_the_next_close_does_not_rewrite_the_fill() -> None:
    bars = _shock(0.85, 0.003, tail=1)
    low = bars.low.copy()
    low[10] = 0.80
    pierced = BarSeries(bars.time, bars.open, bars.high, low, bars.close.copy())
    original = run_backtest(bars, params=_params())
    assert run_backtest(pierced, params=_params()) == original
    future = bars.close.copy()
    future[-1] = 0.90
    moved = BarSeries(bars.time, bars.open, bars.high, bars.low, future)
    earlier = [trade for trade in run_backtest(moved, params=_params()) if trade.entry_time < bars.time[-1]]
    assert earlier == original
    entry_index = 9
    z_now = rolling_return_z(bars.close, 8)
    z_later = rolling_return_z(future, 8)
    assert z_now[entry_index] == pytest.approx(z_later[entry_index])


def test_exit_bar_does_not_open_a_new_trade() -> None:
    bars = _shock(0.85, 0.003)
    trades = run_backtest(bars, params=_params(long_filter="off"))
    assert trades[0].exit_reason == "REVERSION"
    assert all(trade.entry_time != trades[0].exit_time for trade in trades)


def test_time_stop_and_sample_end() -> None:
    held = _shock(0.85, 0.003, retrace=False)
    timed = run_backtest(held, params=_params(t_max_minutes=30))
    assert timed[0].exit_reason == "TIME"
    assert timed[0].hold_minutes == 30
    both = run_backtest(bars=_shock(0.85, 0.003), params=_params(t_max_minutes=45))
    assert both[0].exit_reason == "REVERSION+TIME"
    last = _shock(0.85, 0.003, after=(), tail=0)
    ended = run_backtest(last, params=_params(t_max_minutes=60))
    assert len(ended) == 1
    assert ended[0].exit_reason == "END_OF_SAMPLE"
    assert ended[0].hold_minutes == 0
    assert ended[0].pnl_pips == pytest.approx(-1.2)


def test_session_blocks_entries_and_still_allows_the_exit() -> None:
    inside = _shock(0.85, 0.003, when=datetime(2020, 6, 2, 4, 45, tzinfo=UTC))
    assert run_backtest(inside, params=_params())[0].entry_time == datetime(2020, 6, 2, 7, 0, tzinfo=UTC)
    closed = _shock(0.85, 0.003, when=datetime(2020, 6, 2, 14, 45, tzinfo=UTC))
    assert run_backtest(closed, params=_params()) == []
    saturday = _shock(0.85, 0.003, when=datetime(2020, 6, 6, 10, 0, tzinfo=UTC))
    assert run_backtest(saturday, params=_params()) == []
    late = _shock(0.85, 0.003, when=datetime(2020, 6, 2, 14, 15, tzinfo=UTC))
    trade = run_backtest(late, params=_params())[0]
    assert trade.entry_time == datetime(2020, 6, 2, 16, 30, tzinfo=UTC)
    assert trade.exit_time == datetime(2020, 6, 2, 17, 15, tzinfo=UTC)
    assert trade.exit_reason == "REVERSION"


def test_blacklist_blocks_the_entry_only() -> None:
    bars = _shock(0.85, 0.003)
    entry = datetime(2020, 6, 2, 12, 15, tzinfo=UTC)
    blocked = run_backtest(
        bars,
        params=_params(),
        blacklist=((entry - timedelta(minutes=1), entry + timedelta(minutes=1)),),
    )
    assert blocked == []
    clear = run_backtest(bars, params=_params())
    outside = run_backtest(
        bars,
        params=_params(),
        blacklist=((entry + timedelta(hours=2), entry + timedelta(hours=3)),),
    )
    assert len(clear) == 1
    assert outside[0].entry_time == clear[0].entry_time
    assert outside[0].exit_time == clear[0].exit_time
    assert outside[0].entry_return_z == clear[0].entry_return_z
    assert outside[0].pnl_pips == clear[0].pnl_pips
    covering_the_exit = run_backtest(
        bars,
        params=_params(),
        blacklist=((clear[0].exit_time - timedelta(minutes=1), clear[0].exit_time + timedelta(minutes=1)),),
    )
    assert covering_the_exit[0].exit_time == clear[0].exit_time
    assert covering_the_exit[0].exit_reason == clear[0].exit_reason


def test_release_window_is_half_open_and_nested_windows_still_block() -> None:
    release = datetime(2020, 6, 2, 7, 0, tzinfo=UTC)
    closes = [release - timedelta(minutes=minutes) for minutes in (15, 0, -15, -30, -45, -60)]
    mask = blacklist_mask(closes, entry_windows([release]))
    assert list(mask) == [True, True, True, True, True, False]
    start = datetime(2020, 6, 2, 10, 0, tzinfo=UTC)
    nested = blacklist_mask(
        [start, start + timedelta(minutes=16)],
        (
            (start, start + timedelta(minutes=20)),
            (start + timedelta(minutes=5), start + timedelta(minutes=10)),
        ),
    )
    assert list(nested) == [True, True]


def test_calendar_keeps_cpi_drops_speeches_and_fixes_the_iran_offset() -> None:
    assert event_kept("CPI y/y")
    assert event_kept("ECB Press Conference")
    assert not event_kept("BOE Gov Bailey Speaks")
    assert not event_kept("Monetary Policy Report Hearings")
    assert quoted_instant("2020-01-15T13:00:00+03:30") == datetime(2020, 1, 15, 9, 30, tzinfo=UTC)
    assert quoted_instant("2024-04-17T09:30:00+04:30") == datetime(2024, 4, 17, 6, 0, tzinfo=UTC)
    assert quoted_instant("2019-04-17T13:00:00+04:30") == datetime(2019, 4, 17, 8, 30, tzinfo=UTC)
    assert quoted_instant("2020-01-30T00:00:00+03:30") is None
    assert quoted_instant("2025-03-31T23:59:59+04:30") is None
    rows = [
        {
            "DateTime": "2020-01-15T13:00:00+03:30",
            "Currency": "GBP",
            "Impact": "High Impact Expected",
            "Event": "CPI y/y",
        },
        {
            "DateTime": "2020-01-15T13:00:00+03:30",
            "Currency": "GBP",
            "Impact": "High Impact Expected",
            "Event": "BOE Gov Bailey Speaks",
        },
        {
            "DateTime": "2020-01-30T00:00:00+03:30",
            "Currency": "GBP",
            "Impact": "High Impact Expected",
            "Event": "Official Bank Rate",
        },
        {
            "DateTime": "2020-01-30T16:00:00+03:30",
            "Currency": "GBP",
            "Impact": "High Impact Expected",
            "Event": "Monetary Policy Summary",
        },
        {
            "DateTime": "2020-03-11T00:00:00+03:30",
            "Currency": "GBP",
            "Impact": "High Impact Expected",
            "Event": "Official Bank Rate",
        },
        {
            "DateTime": "2020-02-19T13:00:00+03:30",
            "Currency": "USD",
            "Impact": "High Impact Expected",
            "Event": "CPI y/y",
        },
    ]
    build = select_releases(rows)
    by_event = {item.event: item for item in build.releases}
    assert by_event["CPI y/y"].time == datetime(2020, 1, 15, 9, 30, tzinfo=UTC)
    assert by_event["CPI y/y"].timing == "quoted"
    assert "BOE Gov Bailey Speaks" not in by_event
    assert by_event["Official Bank Rate"].time == datetime(2020, 1, 30, 12, 30, tzinfo=UTC)
    assert by_event["Official Bank Rate"].timing == "imputed"
    assert by_event["Monetary Policy Summary"].timing == "quoted"
    assert build.dropped_untimed == 1
    stored = Path(__file__).resolve().parents[1] / "configs" / "uk-ez-releases.csv"
    text = stored.read_text(encoding="utf-8")
    assert "2020-01-15T09:30:00+00:00,GBP,CPI y/y,quoted" in text
    assert "2024-04-17T06:00:00+00:00,GBP,CPI y/y,quoted" in text
    assert "Bailey Speaks" not in text


def test_histdata_stamp_is_est_without_daylight_saving() -> None:
    january = "\n".join(
        [
            "20200102 030000;0.84990;0.85020;0.84980;0.85000;0",
            *[f"20200102 03{minute:02d}00;0.85000;0.85020;0.84980;0.85010;0" for minute in range(1, 14)],
            "20200102 031400;0.85000;0.85030;0.84970;0.85015;0",
            "20200102 031500;0.85020;0.85020;0.85020;0.85020;0",
        ]
    )
    bars = m1_text_to_m15(january)
    assert bars.time[0] == datetime(2020, 1, 2, 8, 15, tzinfo=UTC)
    assert bars.open[0] == pytest.approx(0.84990)
    assert bars.high[0] == pytest.approx(0.85030)
    assert bars.low[0] == pytest.approx(0.84970)
    assert bars.close[0] == pytest.approx(0.85015)
    assert bars.time[1] == datetime(2020, 1, 2, 8, 16, tzinfo=UTC)
    july = m1_text_to_m15("20200702 030000;0.85000;0.85010;0.84990;0.85005;0\n")
    assert july.time[0] == datetime(2020, 7, 2, 8, 1, tzinfo=UTC)
    repeated = "20200102 030000;0.85000;0.85010;0.84990;0.85005;0\n"
    assert len(m1_text_to_m15(repeated + repeated).time) == 1
    with pytest.raises(ValueError, match="conflicting"):
        m1_text_to_m15(repeated + "20200102 030000;0.85000;0.85010;0.84990;0.85006;0\n")


def test_loader_rejects_a_blank_close_and_shifts_an_open_stamp(tmp_path: Path) -> None:
    daily = tmp_path / "daily.csv"
    daily.write_text("date,close\n2020-01-01,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        load_daily(daily)
    bars = tmp_path / "m15.csv"
    bars.write_text(
        "datetime,open,high,low,close\n2020-06-02T10:00:00,0.85,0.851,0.849,0.8505\n",
        encoding="utf-8",
    )
    loaded = load_bars(bars, timestamp_is_open=True)
    assert loaded.time[0] == datetime(2020, 6, 2, 10, 15, tzinfo=UTC)


def test_report_labels_the_clock(tmp_path: Path) -> None:
    bars = _shock(0.85, 0.003)
    report = tmp_path / "report.md"
    trades = tmp_path / "trades.csv"
    text = write_report(
        bars,
        _daily_for(bars),
        report,
        trades,
        sample_info(bars, "synthetic", "close times"),
        _params(),
    )
    assert NO_LOOKAHEAD in text
    assert "上一交易日" in text
    assert "调用方没提供窗口时为空" in text
    losing = _shock(0.85, 0.003, retrace=False)
    judged = write_report(
        losing,
        _daily_for(losing),
        tmp_path / "judged.md",
        tmp_path / "judged.csv",
        sample_info(losing, "synthetic", "close times"),
        _params(),
        blacklist=((datetime(2020, 6, 3, 12, 0, tzinfo=UTC), datetime(2020, 6, 3, 13, 0, tzinfo=UTC)),),
    )
    assert "毛均盈亏" in judged
    assert "这个入场停止。" in judged
    assert "黑名单打开" in judged
    blotter = trades.read_text(encoding="utf-8")
    assert "SHORT" in blotter
    assert "REVERSION" in blotter


def test_cli_writes_the_report(tmp_path: Path) -> None:
    bars = tmp_path / "m15.csv"
    bars.write_text(
        "datetime,open,high,low,close\n"
        + "\n".join(
            f"2020-06-02T{10 + (i * 15) // 60:02d}:{(i * 15) % 60:02d}:00+00:00,0.85,0.851,0.849,0.85" for i in range(4)
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "report.md"
    code = main(
        [
            "--config",
            "configs/eurgbp.toml",
            "m15-backtest",
            "--m15",
            str(bars),
            "--output",
            str(output),
            "--trades",
            str(tmp_path / "trades.csv"),
        ]
    )
    assert code == 0
    assert NO_LOOKAHEAD in output.read_text(encoding="utf-8")


def _params(**changes: object) -> StrategyParams:
    base = dict(
        z_entry=2.0,
        n_short=8,
        t_max_minutes=60,
        price_z_window=5,
        spread_pips=1.2,
        long_filter="on",
        price_side="bid",
    )
    base.update(changes)
    return StrategyParams(**base)  # type: ignore[arg-type]


def _shock(
    level: float,
    jump: float,
    *,
    when: datetime = START,
    retrace: bool = True,
    after: tuple[float, ...] | None = None,
    tail: int = 0,
) -> BarSeries:
    """Nine flat closes, one shock, then either a three-bar retrace or a hold at the extreme."""
    closes = [level] * 9 + [level + jump]
    if after is None:
        if retrace:
            closes.extend([level + jump * 0.6, level + jump * 0.3, level - jump * 0.2])
        else:
            closes.extend([level + jump] * 4)
    else:
        closes.extend(after)
    if tail:
        closes.extend([closes[-1]] * tail)
    return _bars(when, closes)


def _bars(start: datetime, closes: list[float]) -> BarSeries:
    times = [start + timedelta(minutes=15 * i) for i in range(len(closes))]
    close = np.array(closes, dtype=np.float64)
    opened = np.concatenate([close[:1], close[:-1]])
    return BarSeries(
        time=times,
        open=opened,
        high=np.maximum(opened, close) + 1e-5,
        low=np.minimum(opened, close) - 1e-5,
        close=close,
    )


def _daily_for(bars: BarSeries) -> DailySeries:
    from m15meanrev.load import daily_from_m15

    return daily_from_m15(bars)
