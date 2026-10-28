"""Section 8 report: blotter, summary, calendar split, filter, and sensitivity."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import numpy as np

from m15meanrev.backtest import StrategyParams, Trade, run_backtest
from m15meanrev.load import BarSeries, DailySeries
from m15meanrev.metrics import PeriodRow, Summary, breakdown, hold_histogram, summarize
from m15meanrev.signals import rolling_return_z

Z_GRID = (1.5, 2.0, 2.5, 3.0)
N_GRID = (8, 16, 20, 32, 48)
T_GRID = (30, 45, 60, 90, 120)

NO_LOOKAHEAD = (
    "不含未来函数：日线过滤用上一交易日的 price_z_60（窗口含该日收盘，ddof=1）；"
    "信号和成交只用已经收盘的 M15 bar。return_z 的窗口含当前 bar，不含下一根。"
)


@dataclass(frozen=True)
class SampleInfo:
    source: str
    timestamp_note: str
    bar_count: int
    start: datetime
    end: datetime
    close_sha256: str


def sample_info(bars: BarSeries, source: str, timestamp_note: str) -> SampleInfo:
    digest = hashlib.sha256(np.ascontiguousarray(bars.close, dtype=np.float64).tobytes()).hexdigest()
    return SampleInfo(
        source=source,
        timestamp_note=timestamp_note,
        bar_count=len(bars.time),
        start=bars.time[0],
        end=bars.time[-1],
        close_sha256=digest,
    )


def write_report(
    bars: BarSeries,
    daily: DailySeries,
    path: Path,
    trades_path: Path,
    info: SampleInfo,
    params: StrategyParams | None = None,
    blacklist: tuple[tuple[datetime, datetime], ...] = (),
    calendar_note: str = "",
) -> str:
    """Write the markdown report and the full trade blotter. Returns the markdown."""
    chosen = params or StrategyParams()
    base = run_backtest(bars, daily, chosen, blacklist)
    off = run_backtest(bars, daily, replace(chosen, long_filter="off"), blacklist)
    strict = run_backtest(bars, daily, replace(chosen, long_filter="strict"), blacklist)
    wider = run_backtest(bars, daily, replace(chosen, spread_pips=2.0), blacklist)
    open_book = run_backtest(bars, daily, chosen, ()) if blacklist else None
    first_day = bars.time[0].date()
    last_day = bars.time[-1].date()
    text = render(
        chosen,
        info,
        base,
        summarize(base, first_day, last_day),
        summarize(off, first_day, last_day),
        summarize(strict, first_day, last_day),
        summarize(wider, first_day, last_day),
        breakdown(base, first_day, last_day, "year"),
        breakdown(base, first_day, last_day, "month"),
        _sensitivity(bars, daily, chosen, blacklist),
        trades_path,
        None if open_book is None else summarize(open_book, first_day, last_day),
        calendar_note,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _write_trades(trades_path, base)
    return text


def render(
    params: StrategyParams,
    info: SampleInfo,
    trades: list[Trade],
    base: Summary,
    filter_off: Summary,
    strict: Summary,
    wider_spread: Summary,
    years: list[PeriodRow],
    months: list[PeriodRow],
    sensitivity: dict[str, list[tuple[str, Summary]]],
    trades_path: Path,
    open_book: Summary | None = None,
    calendar_note: str = "",
) -> str:
    lines = [
        "# EURGBP M15 均值回归回测",
        "",
        f"**{NO_LOOKAHEAD}**",
        "",
        "这是独立于日频 ridge 公允价值引擎的一本书。入场扳机是已收盘 M15 的对数收益 z。日线 price_z 只决定能不能做多。",
        "",
        "## 规则",
        "",
        f"- 样本：{info.bar_count} 根 M15，{info.start.isoformat()} → {info.end.isoformat()}",
        f"- 来源：{info.source}",
        f"- 时间：{info.timestamp_note}",
        f"- close sha256：`{info.close_sha256}`",
        (
            f"- 基准参数：Z_entry={params.z_entry:g}，N_short={params.n_short}，"
            f"T_max={params.t_max_minutes} 分钟，price_z 窗口={params.price_z_window}"
        ),
        (
            f"- 会话：周一至周五，bar 收盘 UTC [{_hhmm(params.session_start_minute)}, "
            f"{_hhmm(params.session_end_minute)})。出场不受会话限制。"
        ),
        _blacklist_rule(open_book is not None, calendar_note),
        "- 多头过滤开启（loose）：上一交易日 price_z_60 缺失，或小于 0，则不做多。做空始终允许。",
        "- 多头过滤关闭：多空都由 return_z 决定。严格变体要求 price_z_60 ≥ -1，单独一行对照。",
        "- 出场 REVERSION：后来的收盘价回到冲击前一根 bar 的收盘。",
        "- 出场 TIME：持仓分钟达到 T_max。同一根 bar 先平仓，不再开新仓。样本结束仍持仓的，记 END_OF_SAMPLE。",
        "- 不用 high/low 做出场。影线在收盘前不可成交，收盘后也不用它改写信号。",
        f"- 价格类型：{params.price_side}。往返点差 {params.spread_pips:g} 点（1 点 = {params.pip}）。"
        + ("买在 ask = bid + 点差，卖在 bid。" if params.price_side == "bid" else "中间价每边付一半点差。"),
        "- 点差是固定假设，不是逐笔买卖价。EURGBP 日波大约 0.1%–0.2%，这个成本相对目标盈利很大。",
        "",
        "## 1. 交易明细",
        "",
        (
            f"全部 {len(trades)} 笔在 `{trades_path.as_posix()}`。"
            "列：时间、方向、入场/出场价、持仓分钟、pnl（点）、出场原因。"
        ),
        "",
        _blotter(trades),
        "",
        "## 2. 总体",
        "",
        _summary_table(base),
        "",
        "Sharpe（按日）：每笔净盈亏记到出场日；周末出场记到下一个工作日。",
        "样本首尾之间的工作日没有交易则记 0。均值 / 标准差（ddof=1）× sqrt(252)。单位是点，不是收益率。",
        "Sharpe（按笔）：单笔净盈亏的均值 / 标准差（ddof=1），不年化。",
        "盈亏比 = 平均盈利 / 平均亏损的绝对值。利润因子 = 盈利合计 / 亏损合计的绝对值。",
        "胜率只计 pnl > 0。最大回撤是累计净点从峰值到谷值的落差。",
        "",
        f"同一本账把往返点差改成 2.0 点后，净盈亏 {_num(wider_spread.net_pips)} 点，"
        f"胜率 {_pct(wider_spread.win_rate)}，利润因子 {_num(wider_spread.profit_factor)}，"
        f"按日 Sharpe {_num(wider_spread.sharpe_daily)}。",
        "",
        "## 3. 按年 / 按月",
        "",
        "按入场时间归入年份和月份。没有成交的月份净点为 0。",
        "",
        _period_table(years),
        "",
        _period_table(months),
        "",
        "## 4. 仅空过滤开启 vs 关闭",
        "",
        "开启 = loose：price_z_60 < 0 或缺失时不做多。关闭 = 多空都做。做空两边都允许。参数除过滤外与基准相同。",
        "",
        _filter_table(base, filter_off, strict),
        "",
        *_blackout_section(base, open_book, params.spread_pips),
        "## 5. 参数敏感性",
        "",
        "Z_entry × N_short 固定 T_max 为基准值，过滤开启。格子里是：笔数 / 净点 / 胜率 / 平均持仓分钟。",
        *(
            ["格子用的是黑名单打开后的账，只作记录，不据此改 Z_entry、N_short 或 T_max。"]
            if open_book is not None
            else []
        ),
        "",
        _z_table(sensitivity),
        "",
        "T_max 在基准 Z_entry 和 N_short 上变化，过滤开启。",
        "",
        _t_table(sensitivity),
        "",
        "## 6. 持仓时间",
        "",
        _hold_section(base, trades),
        "",
        "## 7. 时点",
        "",
        NO_LOOKAHEAD,
        "",
        "日线 price_z 在日期 D 上用到 D 的收盘，但从下一交易日的 bar 才生效。",
        "M15 信号在 bar i 收盘时计算。改 bar i+1 的收盘不会改变 bar i 的 z 或成交价。",
        "",
    ]
    return "\n".join(lines)


def _sensitivity(
    bars: BarSeries,
    daily: DailySeries,
    params: StrategyParams,
    blacklist: tuple[tuple[datetime, datetime], ...],
) -> dict[str, list[tuple[str, Summary]]]:
    cache: dict[int, np.ndarray] = {}
    first_day = bars.time[0].date()
    last_day = bars.time[-1].date()

    def once(z_entry: float, n_short: int, t_max: int) -> Summary:
        if n_short not in cache:
            cache[n_short] = rolling_return_z(np.asarray(bars.close, dtype=np.float64), n_short)
        chosen = replace(params, z_entry=z_entry, n_short=n_short, t_max_minutes=t_max)
        trades = run_backtest(bars, daily, chosen, blacklist, return_z=cache[n_short])
        return summarize(trades, first_day, last_day)

    z_rows: list[tuple[str, Summary]] = []
    for z_entry in Z_GRID:
        for n_short in N_GRID:
            z_rows.append((f"{z_entry:g}|{n_short}", once(z_entry, n_short, params.t_max_minutes)))
    t_rows = [(str(t_max), once(params.z_entry, params.n_short, t_max)) for t_max in T_GRID]
    return {"z_by_n": z_rows, "t_max": t_rows}


def _z_table(sensitivity: dict[str, list[tuple[str, Summary]]]) -> str:
    lookup = {key: summary for key, summary in sensitivity["z_by_n"]}
    header = "| Z_entry \\ N_short | " + " | ".join(str(n_short) for n_short in N_GRID) + " |"
    rule = "| --- | " + " | ".join("---" for _ in N_GRID) + " |"
    body = []
    for z_entry in Z_GRID:
        cells = []
        for n_short in N_GRID:
            summary = lookup[f"{z_entry:g}|{n_short}"]
            cells.append(
                f"{summary.trades} / {_num(summary.net_pips)} / {_pct(summary.win_rate)} / {_num(summary.avg_hold)}"
            )
        body.append(f"| {z_entry:g} | " + " | ".join(cells) + " |")
    return "\n".join([header, rule, *body])


def _t_table(sensitivity: dict[str, list[tuple[str, Summary]]]) -> str:
    lines = [
        "| T_max | 笔数 | 胜率 | 均盈亏 | 盈亏比 | 回撤 | Sharpe日 | 利润因子 | 均持仓 | 30-60占比 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for label, summary in sensitivity["t_max"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    str(summary.trades),
                    _pct(summary.win_rate),
                    _num(summary.avg_pnl),
                    _num(summary.payoff),
                    _num(summary.max_drawdown_pips),
                    _num(summary.sharpe_daily),
                    _num(summary.profit_factor),
                    _num(summary.avg_hold),
                    _pct(summary.hold_30_60_share),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _summary_table(summary: Summary) -> str:
    rows = (
        ("交易次数", str(summary.trades)),
        ("胜率", _pct(summary.win_rate)),
        ("平均盈亏（点）", _num(summary.avg_pnl)),
        ("盈亏比", _num(summary.payoff)),
        ("最大回撤（点）", _num(summary.max_drawdown_pips)),
        ("Sharpe（按日，√252，点）", _num(summary.sharpe_daily)),
        ("Sharpe（按笔，不年化）", _num(summary.sharpe_per_trade)),
        ("利润因子", _num(summary.profit_factor)),
        ("净盈亏（点）", _num(summary.net_pips)),
        ("盈利笔数 / 亏损笔数 / 持平", f"{summary.wins} / {summary.losses} / {summary.flats}"),
    )
    lines = ["| 指标 | 值 |", "| --- | --- |"]
    lines.extend(f"| {name} | {value} |" for name, value in rows)
    return "\n".join(lines)


def _blacklist_rule(blackout: bool, calendar_note: str) -> str:
    if not blackout:
        return "- 事件黑名单：调用方没提供窗口时为空，报告里的基准运行不挡数据公布。"
    if calendar_note:
        return f"- {calendar_note}"
    return "- 事件黑名单：调用方传入了开仓窗口。只挡开仓，出场照常。"


def _blackout_section(on: Summary, off: Summary | None, spread: float) -> list[str]:
    if off is None:
        return []
    gross_on = None if on.avg_pnl is None else on.avg_pnl + spread
    gross_off = None if off.avg_pnl is None else off.avg_pnl + spread
    rows = [
        ("交易次数", str(on.trades), str(off.trades)),
        ("胜率", _pct(on.win_rate), _pct(off.win_rate)),
        ("净均盈亏（点）", _num(on.avg_pnl), _num(off.avg_pnl)),
        ("毛均盈亏（点）", _num(gross_on), _num(gross_off)),
        ("净盈亏（点）", _num(on.net_pips), _num(off.net_pips)),
        ("利润因子", _num(on.profit_factor), _num(off.profit_factor)),
        ("Sharpe（按日）", _num(on.sharpe_daily), _num(off.sharpe_daily)),
    ]
    table = [
        "| 指标 | 黑名单打开 | 黑名单关闭 |",
        "| --- | --- | --- |",
        *[f"| {name} | {left} | {right} |" for name, left, right in rows],
    ]
    lines = [
        "## 数据公布黑名单",
        "",
        "基准参数不变。打开 = 公布前 15 分钟到公布后 60 分钟不再开仓。关闭 = 同一参数、没有窗口。出场两边都不挡。",
        "每笔只付一次往返点差，毛均盈亏 = 净均盈亏 + 点差。",
        "",
        "\n".join(table),
        "",
    ]
    if gross_on is not None and gross_on < 1.0:
        lines.append(f"黑名单打开后，毛均盈亏是 {_num(gross_on)} 点，仍低于 1 点。这个入场停止。")
    elif gross_on is not None:
        lines.append(f"黑名单打开后，毛均盈亏是 {_num(gross_on)} 点。")
    else:
        lines.append("黑名单打开后没有成交，无法计算毛均盈亏。")
    lines.append("")
    return lines


def _filter_table(on: Summary, off: Summary, strict: Summary) -> str:
    header = "| 指标 | 过滤开启 | 过滤关闭 | 严格（pz≥-1） |"
    rule = "| --- | --- | --- | --- |"
    rows = [
        ("交易次数", on.trades, off.trades, strict.trades),
        ("胜率", _pct(on.win_rate), _pct(off.win_rate), _pct(strict.win_rate)),
        ("平均盈亏（点）", _num(on.avg_pnl), _num(off.avg_pnl), _num(strict.avg_pnl)),
        ("盈亏比", _num(on.payoff), _num(off.payoff), _num(strict.payoff)),
        ("最大回撤（点）", _num(on.max_drawdown_pips), _num(off.max_drawdown_pips), _num(strict.max_drawdown_pips)),
        ("Sharpe（按日）", _num(on.sharpe_daily), _num(off.sharpe_daily), _num(strict.sharpe_daily)),
        ("利润因子", _num(on.profit_factor), _num(off.profit_factor), _num(strict.profit_factor)),
        ("净盈亏（点）", _num(on.net_pips), _num(off.net_pips), _num(strict.net_pips)),
        ("平均持仓（分钟）", _num(on.avg_hold), _num(off.avg_hold), _num(strict.avg_hold)),
    ]
    body = [f"| {name} | {left} | {middle} | {right} |" for name, left, middle, right in rows]
    return "\n".join([header, rule, *body])


def _period_table(rows: list[PeriodRow]) -> str:
    lines = ["| 期间 | 笔数 | 胜率 | 净盈亏（点） | 平均持仓（分钟） |", "| --- | --- | --- | --- | --- |"]
    for row in rows:
        lines.append(
            f"| {row.label} | {row.trades} | {_pct(row.win_rate)} | {_num(row.net_pips)} | {_num(row.avg_hold)} |"
        )
    return "\n".join(lines)


def _hold_section(summary: Summary, trades: list[Trade]) -> str:
    lines = [
        f"平均持仓 {_num(summary.avg_hold)} 分钟，中位数 {_num(summary.median_hold)} 分钟，"
        f"落在 30–60 分钟（含端点）的比例 {_pct(summary.hold_30_60_share)}。",
        "",
        "| 持仓分钟 | 笔数 |",
        "| --- | --- |",
    ]
    lines.extend(f"| {label} | {count} |" for label, count in hold_histogram(trades))
    lines.append("")
    time_only = sum(trade.exit_reason == "TIME" for trade in trades)
    reversion_only = sum(trade.exit_reason == "REVERSION" for trade in trades)
    both = sum(trade.exit_reason == "REVERSION+TIME" for trade in trades)
    lines.append(
        f"只因 TIME 出场 {time_only} 笔，只因 REVERSION 出场 {reversion_only} 笔，"
        f"同一根 bar 两者都触发 {both} 笔。"
    )
    share = summary.hold_30_60_share
    if summary.trades == 0:
        lines.append("样本里没有成交，无法对照 30–60 分钟这个预期。")
    elif share is not None and share >= 0.5:
        lines.append("持仓时间的主体落在 30–60 分钟。")
    else:
        lines.append("持仓时间的主体落在 30–60 分钟以外。参数保持事先设定的基准，没有按这个分布再调。")
    return "\n".join(lines)


def _blotter(trades: list[Trade]) -> str:
    if not trades:
        return "没有成交。"
    lines = [
        "| 入场时间 | 出场时间 | 方向 | 入场价 | 出场价 | 持仓分钟 | pnl（点） | 出场原因 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for trade in trades:
        lines.append(
            "| "
            + " | ".join(
                [
                    trade.entry_time.strftime("%Y-%m-%d %H:%M"),
                    trade.exit_time.strftime("%Y-%m-%d %H:%M"),
                    trade.direction,
                    f"{trade.entry_price:.5f}",
                    f"{trade.exit_price:.5f}",
                    str(trade.hold_minutes),
                    f"{trade.pnl_pips:.2f}",
                    trade.exit_reason,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _write_trades(path: Path, trades: list[Trade]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "entry_time",
                "exit_time",
                "direction",
                "entry_price",
                "exit_price",
                "hold_minutes",
                "pnl_pips",
                "exit_reason",
                "entry_return_z",
                "entry_price_z60",
            ]
        )
        for trade in trades:
            writer.writerow(
                [
                    trade.entry_time.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    trade.exit_time.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    trade.direction,
                    f"{trade.entry_price:.6f}",
                    f"{trade.exit_price:.6f}",
                    trade.hold_minutes,
                    f"{trade.pnl_pips:.4f}",
                    trade.exit_reason,
                    f"{trade.entry_return_z:.6f}",
                    "" if trade.entry_price_z60 is None else f"{trade.entry_price_z60:.6f}",
                ]
            )


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _num(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{100.0 * value:.1f}%"

