"""Closed-trade statistics. Ratios stay empty when the sample cannot support them."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from m5zrev.backtest import Trade
from m5zrev.session import london_local


@dataclass(frozen=True, slots=True)
class Summary:
    trades: int
    net_account: float
    expectancy_r: float | None
    profit_factor: float | None
    max_drawdown: float | None
    win_rate: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None
    avg_pips: float | None = None
    avg_account: float | None = None
    sharpe: float | None = None
    avg_hold: float | None = None
    median_hold: float | None = None
    hold_under_30: int = 0
    hold_30_60: int = 0
    hold_over_60: int = 0
    path_uncertain: int = 0


@dataclass(frozen=True, slots=True)
class Bucket:
    label: str
    trades: int
    net_account: float
    expectancy_r: float | None


def summarize(
    trades: tuple[Trade, ...] | list[Trade],
    *,
    initial_equity: float,
    sample_start: datetime | None = None,
    sample_end: datetime | None = None,
) -> Summary:
    rows = list(trades)
    count = len(rows)
    net = sum(item.pnl_account for item in rows)
    if count == 0:
        return Summary(0, 0.0, None, None, 0.0 if initial_equity > 0 else None)
    wins = [item.pnl_account for item in rows if item.pnl_account > 0]
    losses = [item.pnl_account for item in rows if item.pnl_account < 0]
    r_values = [item.pnl_r for item in rows if math.isfinite(item.pnl_r)]
    holds = [item.hold_minutes for item in rows]
    under = sum(item.hold_minutes < 30 for item in rows)
    inside = sum(30 <= item.hold_minutes <= 60 for item in rows)
    over = count - under - inside
    return Summary(
        trades=count,
        net_account=net,
        expectancy_r=statistics.fmean(r_values) if r_values else None,
        profit_factor=_profit_factor(wins, losses),
        max_drawdown=_drawdown(rows, initial_equity),
        win_rate=len(wins) / count,
        avg_win=statistics.fmean(wins) if wins else None,
        avg_loss=statistics.fmean(losses) if losses else None,
        avg_pips=statistics.fmean(item.pnl_pips for item in rows),
        avg_account=net / count,
        sharpe=_sharpe(rows, sample_start, sample_end),
        avg_hold=statistics.fmean(holds),
        median_hold=statistics.median(holds),
        hold_under_30=under,
        hold_30_60=inside,
        hold_over_60=over,
        path_uncertain=sum(item.path_uncertain for item in rows),
    )


def group_trades(trades: tuple[Trade, ...] | list[Trade], kind: str) -> tuple[Bucket, ...]:
    """Group by entry London year, month, or clock hour."""
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        local = london_local(trade.entry_time)
        if kind == "year":
            label = f"{local.year}"
        elif kind == "month":
            label = f"{local.year}-{local.month:02d}"
        elif kind == "hour":
            label = f"{local.hour:02d}:00"
        else:
            raise ValueError("group must be year, month, or hour")
        buckets[label].append(trade)
    ordered: list[Bucket] = []
    for label in sorted(buckets):
        rows = buckets[label]
        r_values = [item.pnl_r for item in rows if math.isfinite(item.pnl_r)]
        ordered.append(
            Bucket(
                label,
                len(rows),
                sum(item.pnl_account for item in rows),
                statistics.fmean(r_values) if r_values else None,
            )
        )
    return tuple(ordered)


def _profit_factor(wins: list[float], losses: list[float]) -> float | None:
    if not wins and not losses:
        return None
    gross_loss = abs(sum(losses))
    gross_win = sum(wins)
    if gross_loss == 0.0:
        return math.inf if gross_win > 0 else None
    return gross_win / gross_loss


def _drawdown(trades: list[Trade], initial_equity: float) -> float | None:
    if initial_equity <= 0:
        return None
    equity = initial_equity
    peak = initial_equity
    worst = 0.0
    for trade in sorted(trades, key=lambda item: item.exit_time):
        adverse = min(trade.pnl_account, _mae_account(trade))
        trough = equity + min(0.0, adverse)
        if peak > 0:
            worst = max(worst, (peak - trough) / peak)
        equity += trade.pnl_account
        peak = max(peak, equity)
    return worst


def _mae_account(trade: Trade) -> float:
    gross = trade.pnl_account + trade.commission
    if trade.pnl_pips == 0.0:
        return min(0.0, trade.pnl_account)
    return trade.mae_pips * gross / trade.pnl_pips


def _sharpe(trades: list[Trade], sample_start: datetime | None, sample_end: datetime | None) -> float | None:
    if sample_start is None or sample_end is None or sample_end < sample_start:
        return None
    start = london_local(sample_start).date()
    end = london_local(sample_end).date()
    pnl: dict[object, float] = defaultdict(float)
    for trade in trades:
        pnl[london_local(trade.exit_time).date()] += trade.pnl_account
    values: list[float] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            values.append(pnl.get(day, 0.0))
        day += timedelta(days=1)
    if len(values) < 2:
        return None
    scale = statistics.stdev(values)
    if scale == 0.0:
        return None
    return statistics.fmean(values) / scale * math.sqrt(252.0)
