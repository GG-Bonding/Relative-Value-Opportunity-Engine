"""Bar and tick checks fail closed. A missing terminal does not invent prices."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from m5zrev.data import (
    BarTable,
    DataUnavailableError,
    SymbolSpec,
    TickTable,
    load_bars,
    load_events,
    load_spec,
    probe_mt5,
    require_valid,
    validate_bars,
    validate_ticks,
)

DURATION = timedelta(minutes=5)


def _bars(start: datetime, closes: list[float], *, step: timedelta = DURATION) -> BarTable:
    close_time = [start + step * index for index in range(len(closes))]
    open_time = [stamp - DURATION for stamp in close_time]
    prices = np.array(closes, dtype=np.float64)
    return BarTable(
        open_time=open_time,
        close_time=close_time,
        open=prices,
        high=prices + 0.0002,
        low=prices - 0.0002,
        close=prices,
        spread_pips=np.full(len(closes), np.nan),
    )


def test_weekday_gap_is_reported_and_not_filled() -> None:
    start = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    bars = _bars(start, [0.85, 0.851, 0.852], step=timedelta(minutes=10))
    report = validate_bars(bars, expected_duration=DURATION, pip=0.0001)
    assert report.bars == 3
    assert report.gaps == 2
    assert report.ok
    with pytest.raises(DataUnavailableError):
        require_valid(validate_bars(bars, expected_duration=DURATION, pip=0.0))


def test_duplicate_or_bad_prices_are_errors() -> None:
    start = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    bars = _bars(start, [0.85, 0.851])
    bars.close_time[1] = bars.close_time[0]
    report = validate_bars(bars, expected_duration=DURATION, pip=0.0001)
    assert not report.ok
    assert report.duplicate_timestamps == 1
    broken = _bars(start, [0.85, 0.851])
    broken.low[1] = broken.high[1] + 0.01
    assert not validate_bars(broken, expected_duration=DURATION, pip=0.0001).ok
    crashed = _bars(start, [0.85, 0.0])
    assert any("non-positive" in item for item in validate_bars(crashed, expected_duration=DURATION, pip=0.0001).errors)


def test_weekend_is_not_called_a_missing_bar_and_a_jump_is_kept() -> None:
    friday = datetime(2026, 7, 17, 20, 55, tzinfo=UTC)
    monday = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    bars = _bars(friday, [0.85, 0.90])
    bars.close_time[1] = monday
    bars.open_time[1] = monday - DURATION
    report = validate_bars(bars, expected_duration=DURATION, pip=0.0001)
    assert report.gaps == 0
    assert report.abnormal_returns == 1
    assert report.bars == 2


def test_naive_csv_and_incomplete_spec_fail(tmp_path: Path) -> None:
    bars_path = tmp_path / "bars.csv"
    bars_path.write_text(
        "close_time_utc,open,high,low,close\n2026-07-15T10:00:00,0.85,0.86,0.84,0.85\n",
        encoding="utf-8",
    )
    with pytest.raises(DataUnavailableError, match="naive"):
        load_bars(bars_path, duration=DURATION)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text('{"symbol":"EURGBP"}', encoding="utf-8")
    with pytest.raises(DataUnavailableError, match="contract"):
        load_spec(spec_path)
    with pytest.raises(ValueError, match="EURGBP"):
        SymbolSpec(
            symbol="EURUSD",
            digits=5,
            point=0.00001,
            tick_size=0.00001,
            tick_value_account=1.0,
            volume_min=0.01,
            volume_step=0.01,
            volume_max=1.0,
            stops_level_points=0,
            contract_size=100000,
            profit_currency="USD",
            account_currency="USD",
            commission_per_lot_side=0.0,
            source="user-file",
            verified_from_terminal=False,
        )


def test_event_loader_keeps_gbp_eur_prints_and_drops_speeches(tmp_path: Path) -> None:
    path = tmp_path / "events.csv"
    path.write_text(
        "release_time_utc,currency,event\n"
        "2026-07-15T06:00:00+00:00,GBP,CPI y/y\n"
        "2026-07-15T11:00:00+00:00,EUR,Main Refinancing Rate\n"
        "2026-07-15T12:00:00+00:00,GBP,BOE Governor Speech\n"
        "2026-07-15T13:00:00+00:00,USD,CPI y/y\n",
        encoding="utf-8",
    )
    events = load_events(path)
    assert len(events) == 2
    assert events[0] == datetime(2026, 7, 15, 6, 0, tzinfo=UTC)


def test_crossed_ticks_are_rejected() -> None:
    ticks = TickTable(
        time=[datetime(2026, 7, 15, 10, 0, tzinfo=UTC), datetime(2026, 7, 15, 10, 0, 1, tzinfo=UTC)],
        bid=np.array([0.85, 0.86]),
        ask=np.array([0.8502, 0.84]),
    )
    report = validate_ticks(ticks)
    assert not report.ok
    assert any("crossed" in item for item in report.errors)


def test_mt5_probe_does_not_invent_a_price_when_the_package_is_missing() -> None:
    def missing() -> object:
        raise ImportError("no MetaTrader5 on this machine")

    probe = probe_mt5(missing)
    assert probe.available is False
    assert probe.symbol is None
    assert probe.bars == 0
    assert "MetaTrader5" in probe.detail


def test_aware_csv_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "m5.csv"
    path.write_text(
        "close_time_utc,open,high,low,close,spread_pips\n"
        "2026-07-15T10:00:00+00:00,0.8500,0.8504,0.8498,0.8502,1.1\n"
        "2026-07-15T10:05:00+00:00,0.8502,0.8506,0.8500,0.8504,1.2\n",
        encoding="utf-8",
    )
    bars = load_bars(path, duration=DURATION)
    report = validate_bars(bars, expected_duration=DURATION, pip=0.0001)
    assert report.ok
    assert bars.close_time[1] == datetime(2026, 7, 15, 10, 5, tzinfo=UTC)
    assert bars.spread_pips[1] == pytest.approx(1.2)
