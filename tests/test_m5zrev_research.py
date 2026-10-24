"""Selection protocol, the conclusion gate, and an empty market report."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from m5zrev.backtest import Trade
from m5zrev.experiments import (
    Window,
    ablation_grid,
    choose,
    iter_folds,
    prefer_hold,
    return_filter_added_value,
    select_staged,
    stage1_grid,
    trades_inside,
)
from m5zrev.metrics import Summary
from m5zrev.params import Params
from m5zrev.report import write_heatmap, write_unavailable
from m5zrev.session import in_london_session, manual_in_london_session
from m5zrev.verdict import Evidence, judge
from tests.test_m5zrev_backtest import _bars, sample_spec


def _summary(trades: int, expectancy: float | None, factor: float | None, drawdown: float | None) -> Summary:
    return Summary(trades, 0.0, expectancy, factor, drawdown)


def test_validation_cannot_see_the_test_window_and_a_failed_filter_keeps_the_simple_book() -> None:
    calls: list[tuple[int, str]] = []

    def evaluate(candidate: Params, window: Window) -> Summary:
        calls.append((candidate.ema_period, window.name))
        if window.name == "test":
            raise AssertionError("the sealed window was scored during selection")
        if candidate.ema_period == 12 and window.name == "train":
            return _summary(40, 0.2, 1.4, 0.05)
        if candidate.ema_period == 12 and window.name == "valid":
            return _summary(40, -0.1, 0.8, 0.05)
        if candidate.use_return_filter:
            return _summary(40, 0.05, 1.05, 0.04)
        return _summary(40, 0.1, 1.2, 0.04)

    train = Window("train", datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC))
    valid = Window("valid", datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 4, 1, tzinfo=UTC))
    choice = choose((Params(ema_period=12), Params(ema_period=24)), evaluate, train, valid, Params(min_trades=30))
    assert choice.reason == "selected"
    assert choice.params is not None and choice.params.ema_period == 24
    assert all(name != "test" for _period, name in calls)
    assert calls[0] == (12, "train")
    assert (12, "valid") in calls
    assert (24, "valid") in calls


def test_stage_one_stops_when_nothing_survives_validation() -> None:
    def evaluate(candidate: Params, window: Window) -> Summary:
        del candidate, window
        return _summary(40, 0.2, 1.3, 0.2)

    train = Window("train", datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC))
    valid = Window("valid", datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 4, 1, tzinfo=UTC))
    choice = select_staged(Params(), evaluate, train, valid)
    assert choice.reason == "NO_EDGE"
    assert choice.params is None
    assert len(stage1_grid(Params())) == 36


def test_folds_leave_a_sealed_holdout_and_a_trade_cannot_cross_the_cut() -> None:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 1, tzinfo=UTC)
    folds, holdout = iter_folds(start, end)
    assert folds
    assert holdout == datetime(2023, 10, 1, tzinfo=UTC)
    for fold in folds:
        assert fold.train.end == fold.valid.start
        assert fold.valid.end == fold.test.start
        assert fold.test.end <= holdout
    assert folds[0].test.end == folds[1].test.start
    crossing = _trade(datetime(2021, 3, 31, 12, tzinfo=UTC), datetime(2021, 4, 1, 12, tzinfo=UTC))
    window = Window("test", datetime(2021, 1, 1, tzinfo=UTC), datetime(2021, 4, 1, tzinfo=UTC))
    assert trades_inside((crossing,), window) == ()
    kept_window = Window("test", datetime(2021, 3, 1, tzinfo=UTC), datetime(2021, 5, 1, tzinfo=UTC))
    kept = trades_inside((crossing,), kept_window)
    assert len(kept) == 1


def test_return_filter_and_hold_decisions_do_not_invent_an_improvement() -> None:
    simple = _summary(40, 0.1, 1.3, 0.05)
    worse = _summary(40, 0.04, 1.1, 0.05)
    better = _summary(40, 0.15, 1.4, 0.05)
    assert return_filter_added_value(simple, worse, 30) == "no_added_value"
    assert return_filter_added_value(simple, better, 30) == "added"
    assert return_filter_added_value(simple, better, 50) == "insufficient"
    assert prefer_hold(((30, worse), (60, better)), 30) == "60"
    assert prefer_hold(((30, _summary(5, 0.2, 1.2, 0.01)),), 30) == "insufficient"
    assert prefer_hold(((30, _summary(40, -0.1, 0.8, 0.05)),), 30) == "NO_EDGE"


def test_conclusion_gate() -> None:
    gates = Params()
    missing = Evidence(False, "unavailable", False, 0, None, None, None, 0, 0, 0, 0, None, None, "no tape")
    assert judge(missing, gates)[0] == "INCONCLUSIVE"
    failed = Evidence(True, "tick", True, 40, -0.05, 0.8, 0.04, 1, 2, 0, 2, -0.1, 40, "")
    assert judge(failed, gates)[0] == "FAIL"
    fragile = Evidence(True, "tick", True, 40, 0.1, 1.3, 0.04, 2, 2, 2, 2, -0.01, 40, "")
    assert judge(fragile, gates)[0] == "INCONCLUSIVE"
    paper = Evidence(True, "bid_ohlc_study_spread", False, 40, 0.1, 1.3, 0.04, 2, 2, 2, 2, 0.05, 40, "")
    assert judge(paper, gates)[0] == "INCONCLUSIVE"
    ready = Evidence(True, "tick", True, 40, 0.1, 1.3, 0.04, 2, 2, 2, 2, 0.05, 40, "")
    assert judge(ready, gates)[0] == "PASS"
    short_sample = Evidence(True, "tick", True, 10, 0.2, 1.5, 0.02, 1, 1, 1, 1, 0.1, 40, "")
    assert judge(short_sample, gates)[0] == "INCONCLUSIVE"


def test_unavailable_report_has_no_performance_number(tmp_path: Path) -> None:
    write_unavailable(tmp_path, "MetaTrader5 package is not available", Params())
    text = (tmp_path / "verdict.md").read_text(encoding="utf-8")
    assert "INCONCLUSIVE" in text
    assert "未运行" in text
    assert "Sharpe" not in text
    assert "胜率" not in text
    heatmap = (tmp_path / "sensitivity.svg").read_text(encoding="utf-8")
    assert "未运行" in heatmap
    header = (tmp_path / "trades.csv").read_text(encoding="utf-8").splitlines()
    assert len(header) == 1
    write_heatmap(tmp_path / "grid.svg", (("ema12", "w48", 0.1), ("ema12", "w72", None)))
    drawn = (tmp_path / "grid.svg").read_text(encoding="utf-8")
    assert "0.100" in drawn
    assert "na" in drawn


def test_manual_london_clock_matches_the_zone_database() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for hour in range(0, 24 * 370, 6):
        stamp = start + timedelta(hours=hour)
        assert manual_in_london_session(stamp, 8 * 60, 17 * 60) == in_london_session(stamp, 8 * 60, 17 * 60)


def test_named_ablation_books() -> None:
    names = [name for name, _params in ablation_grid(Params())]
    assert names == ["A", "B", "C", "D"]
    bars = _bars(datetime(2026, 1, 15, 10, tzinfo=UTC), 20)
    assert sample_spec().symbol == "EURGBP"
    assert len(bars.close) == 20


def _trade(entry: datetime, exit_time: datetime) -> Trade:
    return Trade(
        direction="LONG",
        signal_time=entry,
        entry_time=entry,
        exit_time=exit_time,
        entry_price=0.85,
        exit_price=0.851,
        stop=0.849,
        target=0.852,
        lots=0.01,
        pnl_pips=1.0,
        pnl_account=1.0,
        pnl_r=0.2,
        hold_minutes=40,
        exit_reason="TARGET",
        path_uncertain=False,
        mae_pips=-1.0,
        mfe_pips=2.0,
        spread_pips=1.0,
        z_dev=-2.1,
        z_ret=float("nan"),
        commission=0.0,
        execution="tick",
    )
