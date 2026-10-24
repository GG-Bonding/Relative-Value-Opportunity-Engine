"""Ablation, staged search, and walk-forward.

Parameters are ranked on the train window and accepted only if the validation
window also passes. The sealed holdout is never passed to that choice.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from m5zrev.backtest import Trade, run_backtest
from m5zrev.data import BarTable, SymbolSpec, TickTable
from m5zrev.metrics import Summary, summarize
from m5zrev.params import Params

Evaluate = Callable[[Params, "Window"], Summary]


@dataclass(frozen=True, slots=True)
class Window:
    name: str
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class Fold:
    train: Window
    valid: Window
    test: Window


@dataclass(frozen=True, slots=True)
class Choice:
    params: Params | None
    reason: str


def ablation_grid(base: Params) -> tuple[tuple[str, Params], ...]:
    """A is the simple book. B adds return z, C adds the H1 filter, D adds both."""
    return (
        ("A", replace(base, use_return_filter=False, use_trend_filter=False)),
        ("B", replace(base, use_return_filter=True, use_trend_filter=False)),
        ("C", replace(base, use_return_filter=False, use_trend_filter=True)),
        ("D", replace(base, use_return_filter=True, use_trend_filter=True)),
    )


def direction_grid(base: Params) -> tuple[tuple[str, Params], ...]:
    return (
        ("long", replace(base, allow_long=True, allow_short=False)),
        ("short", replace(base, allow_long=False, allow_short=True)),
        ("both", replace(base, allow_long=True, allow_short=True)),
    )


def scale_extra_cost(params: Params, spec: SymbolSpec, factor: float) -> tuple[Params, SymbolSpec]:
    """Multiply configured slippage and commission. A factor below 1 is rejected.

    The historical bid/ask spread is not scaled here. If both extras are zero,
    the returned book is the same fill model and a stress test is not complete.
    """
    if factor < 1.0:
        raise ValueError("cost factor must be at least 1")
    return (
        replace(params, slippage_pips=params.slippage_pips * factor),
        replace(spec, commission_per_lot_side=spec.commission_per_lot_side * factor),
    )


def extra_cost_is_configured(params: Params, spec: SymbolSpec) -> bool:
    return params.slippage_pips > 0.0 or spec.commission_per_lot_side > 0.0


def stage1_heatmap(
    base: Params,
    evaluate: Callable[[Params], Summary],
) -> tuple[tuple[str, str, float | None], ...]:
    """Expectancy for the price-deviation grid. The evaluator must ignore the test window."""
    cells: list[tuple[str, str, float | None]] = []
    for candidate in stage1_grid(base):
        summary = evaluate(candidate)
        row = f"ema{candidate.ema_period}/w{candidate.dev_window}"
        column = f"z{candidate.z_entry:.1f}"
        cells.append((row, column, summary.expectancy_r))
    return tuple(cells)


def stage1_grid(base: Params) -> tuple[Params, ...]:
    """Price-deviation parameters only. The other filters stay as they are on `base`."""
    chosen: list[Params] = []
    for ema in (12, 24, 36):
        for window in (48, 72, 96):
            for level in (1.8, 2.0, 2.2, 2.5):
                chosen.append(
                    replace(
                        base,
                        ema_period=ema,
                        dev_window=window,
                        z_entry=level,
                        use_return_filter=False,
                        use_trend_filter=False,
                    )
                )
    return tuple(chosen)


def stage2_grid(chosen: Params) -> tuple[Params, ...]:
    """Return-z settings, plus the filter switched off."""
    grid = [replace(chosen, use_return_filter=False)]
    for window in (24, 48, 72):
        for level in (1.5, 2.0, 2.5):
            grid.append(replace(chosen, use_return_filter=True, ret_window=window, ret_z_threshold=level))
    return tuple(grid)


def stage3_grid(chosen: Params) -> tuple[Params, ...]:
    grid: list[Params] = []
    for multiple in (1.2, 1.5, 2.0):
        for minutes in (30, 45, 60):
            for reward in (1.0, 1.2, 1.5):
                grid.append(replace(chosen, atr_mult=multiple, max_hold_minutes=minutes, min_rr=reward))
    return tuple(grid)


def passes(summary: Summary, gates: Params) -> bool:
    if summary.trades < gates.min_trades:
        return False
    if summary.expectancy_r is None or summary.expectancy_r <= 0:
        return False
    if summary.profit_factor is None or summary.profit_factor <= 1.0:
        return False
    return summary.max_drawdown is not None and summary.max_drawdown <= gates.max_drawdown_fraction


def choose(
    candidates: tuple[Params, ...] | list[Params],
    evaluate: Evaluate,
    train: Window,
    valid: Window,
    gates: Params,
) -> Choice:
    """Rank by the train window. Validate the next candidate only after the better ones fail."""
    scored = [(candidate, evaluate(candidate, train)) for candidate in candidates]
    scored.sort(key=lambda item: _rank(item[1]), reverse=True)
    for candidate, train_summary in scored:
        if not passes(train_summary, gates):
            continue
        if passes(evaluate(candidate, valid), gates):
            return Choice(candidate, "selected")
    return Choice(None, "NO_EDGE")


def select_staged(base: Params, evaluate: Evaluate, train: Window, valid: Window) -> Choice:
    first = choose(stage1_grid(base), evaluate, train, valid, base)
    if first.params is None:
        return first
    second = choose(stage2_grid(first.params), evaluate, train, valid, base)
    chosen = second.params or first.params
    third = choose(stage3_grid(chosen), evaluate, train, valid, base)
    if third.params is None:
        return Choice(chosen, "selected")
    return third


def iter_folds(
    start: datetime,
    end: datetime,
    *,
    train_months: int = 12,
    valid_months: int = 3,
    test_months: int = 3,
    step_months: int = 3,
    holdout_months: int = 3,
) -> tuple[tuple[Fold, ...], datetime]:
    """Chronological folds. The returned instant is the sealed holdout start."""
    if min(train_months, valid_months, test_months, step_months, holdout_months) < 1:
        raise ValueError("fold lengths must be positive")
    holdout = shift_months(end, -holdout_months)
    folds: list[Fold] = []
    cursor = start
    while cursor < holdout:
        train_end = shift_months(cursor, train_months)
        valid_end = shift_months(train_end, valid_months)
        test_end = shift_months(valid_end, test_months)
        if test_end > holdout:
            break
        folds.append(
            Fold(
                Window("train", cursor, train_end),
                Window("valid", train_end, valid_end),
                Window("test", valid_end, test_end),
            )
        )
        cursor = shift_months(cursor, step_months)
    return tuple(folds), holdout


def trades_inside(trades: tuple[Trade, ...] | list[Trade], window: Window) -> tuple[Trade, ...]:
    """Keep a trade only when it opens and closes inside the window."""
    return tuple(item for item in trades if window.start <= item.entry_time and item.exit_time < window.end)


def score_market(
    m5: BarTable,
    h1: BarTable | None,
    ticks: TickTable | None,
    spec: SymbolSpec,
    params: Params,
    events: tuple[datetime, ...],
    window: Window,
    *,
    study_spread_pips: float | None,
) -> Summary:
    """Run the causal book on the whole tape, then drop trades that cross the cut."""
    result = run_backtest(
        m5,
        h1,
        ticks,
        spec,
        params,
        events,
        study_spread_pips=study_spread_pips,
    )
    kept = trades_inside(result.trades, window)
    return summarize(kept, initial_equity=params.initial_equity, sample_start=window.start, sample_end=window.end)


def return_filter_added_value(simple: Summary, filtered: Summary, min_trades: int) -> str:
    """`added` only when the filter improves expectancy without giving back the other gates."""
    if simple.trades < min_trades or filtered.trades < min_trades:
        return "insufficient"
    if simple.expectancy_r is None or filtered.expectancy_r is None:
        return "insufficient"
    if filtered.profit_factor is None or simple.profit_factor is None:
        return "insufficient"
    if filtered.max_drawdown is None or simple.max_drawdown is None:
        return "insufficient"
    drawdown_limit = simple.max_drawdown * 1.2 + 1e-12
    if (
        filtered.expectancy_r > simple.expectancy_r
        and filtered.expectancy_r > 0
        and filtered.profit_factor >= simple.profit_factor
        and filtered.max_drawdown <= drawdown_limit
    ):
        return "added"
    return "no_added_value"


def prefer_hold(rows: tuple[tuple[int, Summary], ...], min_trades: int) -> str:
    eligible = [item for item in rows if item[1].trades >= min_trades and item[1].expectancy_r is not None]
    if not eligible:
        return "insufficient"
    positive = [item for item in eligible if item[1].expectancy_r is not None and item[1].expectancy_r > 0]
    if not positive:
        return "NO_EDGE"
    best = max(positive, key=lambda item: item[1].expectancy_r or 0.0)
    return str(best[0])


def shift_months(value: datetime, months: int) -> datetime:
    index = value.month - 1 + months
    year = value.year + index // 12
    month = index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _rank(summary: Summary) -> tuple[float, float, float]:
    expectancy = summary.expectancy_r if summary.expectancy_r is not None else -1e9
    factor = summary.profit_factor if summary.profit_factor is not None else -1e9
    drawdown = summary.max_drawdown if summary.max_drawdown is not None else 1e9
    return (expectancy, factor, -drawdown)
