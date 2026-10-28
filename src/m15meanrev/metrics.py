"""Trade statistics for the M15 book. Sharpe is labeled by the clock it uses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from m15meanrev.backtest import Trade


@dataclass(frozen=True)
class Summary:
    trades: int
    wins: int
    losses: int
    flats: int
    win_rate: float | None
    avg_pnl: float | None
    payoff: float | None
    profit_factor: float | None
    net_pips: float
    max_drawdown_pips: float
    sharpe_daily: float | None
    sharpe_per_trade: float | None
    avg_hold: float | None
    median_hold: float | None
    hold_30_60_share: float | None


@dataclass(frozen=True)
class PeriodRow:
    label: str
    trades: int
    win_rate: float | None
    net_pips: float
    avg_hold: float | None


def summarize(trades: list[Trade], first_day: date, last_day: date) -> Summary:
    pnls = np.array([trade.pnl_pips for trade in trades], dtype=np.float64)
    holds = np.array([trade.hold_minutes for trade in trades], dtype=np.float64)
    wins = pnls[pnls > 0] if len(pnls) else pnls
    losses = pnls[pnls < 0] if len(pnls) else pnls
    count = int(len(pnls))
    win_rate = float(len(wins) / count) if count else None
    avg = float(pnls.mean()) if count else None
    payoff = None
    if len(wins) and len(losses):
        payoff = float(wins.mean() / abs(float(losses.mean())))
    profit_factor = None
    if len(losses) and float(losses.sum()) != 0.0:
        profit_factor = float(wins.sum() / abs(float(losses.sum())))
    return Summary(
        trades=count,
        wins=int(len(wins)),
        losses=int(len(losses)),
        flats=int(np.sum(pnls == 0)) if count else 0,
        win_rate=win_rate,
        avg_pnl=avg,
        payoff=payoff,
        profit_factor=profit_factor,
        net_pips=float(pnls.sum()) if count else 0.0,
        max_drawdown_pips=_max_drawdown(pnls),
        sharpe_daily=_daily_sharpe(trades, first_day, last_day),
        sharpe_per_trade=_per_trade_sharpe(pnls),
        avg_hold=float(holds.mean()) if count else None,
        median_hold=float(np.median(holds)) if count else None,
        hold_30_60_share=float(np.mean((holds >= 30) & (holds <= 60))) if count else None,
    )


def hold_histogram(trades: list[Trade]) -> list[tuple[str, int]]:
    """Counts by holding minutes. Edges sit on the 15-minute bar grid."""
    holds = [trade.hold_minutes for trade in trades]
    buckets = (
        ("0–15", lambda value: value <= 15),
        ("16–30", lambda value: 16 <= value <= 30),
        ("31–45", lambda value: 31 <= value <= 45),
        ("46–60", lambda value: 46 <= value <= 60),
        ("61–90", lambda value: 61 <= value <= 90),
        ("91–120", lambda value: 91 <= value <= 120),
        (">120", lambda value: value > 120),
    )
    return [(label, sum(1 for value in holds if predicate(value))) for label, predicate in buckets]


def breakdown(trades: list[Trade], first_day: date, last_day: date, by: str) -> list[PeriodRow]:
    """Every year or month in the sample, including periods with no trades. Grouped by entry time."""
    if by not in {"year", "month"}:
        raise ValueError("by must be year or month")
    groups: dict[str, list[Trade]] = {label: [] for label in _labels(first_day, last_day, by)}
    for trade in trades:
        label = f"{trade.entry_time.year:04d}" if by == "year" else trade.entry_time.strftime("%Y-%m")
        groups.setdefault(label, []).append(trade)
    rows: list[PeriodRow] = []
    for label in sorted(groups):
        bucket = groups[label]
        pnls = [trade.pnl_pips for trade in bucket]
        holds = [trade.hold_minutes for trade in bucket]
        wins = sum(1 for value in pnls if value > 0)
        rows.append(
            PeriodRow(
                label=label,
                trades=len(bucket),
                win_rate=(wins / len(bucket)) if bucket else None,
                net_pips=float(sum(pnls)),
                avg_hold=(sum(holds) / len(holds)) if holds else None,
            )
        )
    return rows


def _max_drawdown(pnls: np.ndarray) -> float:
    if len(pnls) == 0:
        return 0.0
    equity = np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    return float((peak - equity).max())


def _daily_sharpe(trades: list[Trade], first_day: date, last_day: date) -> float | None:
    """Mean/std of weekday pip pnl, zero-filled, times sqrt(252). ddof=1. Not a return Sharpe."""
    totals: dict[date, float] = {}
    end = last_day
    for trade in trades:
        day = _weekday_bucket(trade.exit_time.date())
        totals[day] = totals.get(day, 0.0) + trade.pnl_pips
        if day > end:
            end = day
    values: list[float] = []
    day = first_day
    while day <= end:
        if day.weekday() < 5:
            values.append(totals.get(day, 0.0))
        day += timedelta(days=1)
    sample = np.array(values, dtype=np.float64)
    if len(sample) < 2:
        return None
    deviation = float(sample.std(ddof=1))
    if deviation == 0.0:
        return None
    return float(sample.mean() / deviation * np.sqrt(252.0))


def _weekday_bucket(day: date) -> date:
    """Weekend exits land on the next weekday so their pip pnl stays in the daily Sharpe."""
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _per_trade_sharpe(pnls: np.ndarray) -> float | None:
    """Mean/std of trade pip pnl. ddof=1. Not annualized."""
    if len(pnls) < 2:
        return None
    deviation = float(pnls.std(ddof=1))
    if deviation == 0.0:
        return None
    return float(pnls.mean() / deviation)


def _labels(first_day: date, last_day: date, by: str) -> list[str]:
    if by == "year":
        return [f"{year:04d}" for year in range(first_day.year, last_day.year + 1)]
    labels: list[str] = []
    year, month = first_day.year, first_day.month
    while (year, month) <= (last_day.year, last_day.month):
        labels.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            month = 1
            year += 1
    return labels
