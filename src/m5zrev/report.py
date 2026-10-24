"""Reports that say when a number was not produced."""

from __future__ import annotations

import csv
import math
from pathlib import Path

from m5zrev.backtest import Trade
from m5zrev.metrics import Summary
from m5zrev.params import Params
from m5zrev.verdict import Evidence, judge


def write_heatmap(path: Path, cells: tuple[tuple[str, str, float | None], ...]) -> None:
    """SVG grid of expectancy. An empty grid says the study did not run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cells:
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="680" height="120">'
            '<text x="24" y="64">未运行：没有行情，不能画参数热力图。</text></svg>\n',
            encoding="utf-8",
        )
        return
    columns = list(dict.fromkeys(column for _row, column, _value in cells))
    rows = list(dict.fromkeys(row for row, _column, _value in cells))
    width = 140 + 80 * len(columns)
    height = 80 + 36 * len(rows)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="#f7f4ee"/>',
    ]
    for column_index, column in enumerate(columns):
        parts.append(f'<text x="{140 + column_index * 80}" y="32" font-size="12">{column}</text>')
    lookup = {(row, column): value for row, column, value in cells}
    for row_index, row in enumerate(rows):
        y = 52 + row_index * 36
        parts.append(f'<text x="16" y="{y + 16}" font-size="12">{row}</text>')
        for column_index, column in enumerate(columns):
            value = lookup.get((row, column))
            label = "na" if value is None or not math.isfinite(value) else f"{value:.3f}"
            parts.append(
                f'<rect x="{132 + column_index * 80}" y="{y}" width="72" height="28" fill="#d7e3d8"/>'
                f'<text x="{136 + column_index * 80}" y="{y + 18}" font-size="12">{label}</text>'
            )
    parts.append("</svg>")
    path.write_text("".join(parts) + "\n", encoding="utf-8")


def write_trades(path: Path, trades: tuple[Trade, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "direction",
                "signal_time",
                "entry_time",
                "exit_time",
                "entry_price",
                "exit_price",
                "stop",
                "target",
                "lots",
                "pnl_pips",
                "pnl_account",
                "pnl_r",
                "hold_minutes",
                "exit_reason",
                "path_uncertain",
                "mae_pips",
                "mfe_pips",
                "spread_pips",
                "z_dev",
                "execution",
            ]
        )
        for trade in trades:
            writer.writerow(
                [
                    trade.direction,
                    trade.signal_time.isoformat(),
                    trade.entry_time.isoformat(),
                    trade.exit_time.isoformat(),
                    trade.entry_price,
                    trade.exit_price,
                    trade.stop,
                    trade.target,
                    trade.lots,
                    trade.pnl_pips,
                    trade.pnl_account,
                    trade.pnl_r,
                    trade.hold_minutes,
                    trade.exit_reason,
                    trade.path_uncertain,
                    trade.mae_pips,
                    trade.mfe_pips,
                    trade.spread_pips,
                    trade.z_dev,
                    trade.execution,
                ]
            )


def write_unavailable(directory: Path, probe_detail: str, gates: Params) -> None:
    """Write the report set for a machine that has no EURGBP tape."""
    directory.mkdir(parents=True, exist_ok=True)
    evidence = Evidence(
        ran_market=False,
        execution="unavailable",
        costs_verified=False,
        oos_trades=0,
        oos_expectancy_r=None,
        oos_profit_factor=None,
        oos_drawdown=None,
        folds_positive=0,
        folds_scored=0,
        years_positive=0,
        years_scored=0,
        stressed_expectancy_r=None,
        median_hold=None,
        note=probe_detail,
    )
    label, reason = judge(evidence, gates)
    body = _unavailable_body(label, reason, probe_detail, gates)
    (directory / "verdict.md").write_text(body, encoding="utf-8")
    (directory / "baseline-report.md").write_text(body, encoding="utf-8")
    (directory / "ablation-report.md").write_text(body, encoding="utf-8")
    (directory / "walkforward-report.md").write_text(body, encoding="utf-8")
    write_heatmap(directory / "sensitivity.svg", ())
    write_trades(directory / "trades.csv", ())


def format_summary(summary: Summary) -> str:
    if summary.trades == 0:
        return "成交 0。没有胜率、Profit Factor 或 Sharpe 可以计算。"
    factor = "inf" if summary.profit_factor == math.inf else _num(summary.profit_factor)
    return (
        f"成交 {summary.trades}，净额 {_num(summary.net_account)}，"
        f"胜率 {_num(summary.win_rate)}，平均 R {_num(summary.expectancy_r)}，"
        f"Profit Factor {factor}，回撤 {_num(summary.max_drawdown)}，"
        f"Sharpe {_num(summary.sharpe)}，中位持仓 {_num(summary.median_hold)} 分钟，"
        f"30–60 分钟 {summary.hold_30_60} 笔，路径不确定 {summary.path_uncertain} 笔。"
    )


def _unavailable_body(label: str, reason: str, probe_detail: str, gates: Params) -> str:
    return (
        "# EURGBP M5 偏离回归\n\n"
        f"结论：**{label}**\n\n"
        f"{reason}\n\n"
        "基准回测、消融、走步样本外和成本压力都没有运行。这里不给出任何收益或风险数字。\n\n"
        "## 验证状态\n\n"
        "| 项目 | 状态 |\n"
        "| --- | --- |\n"
        "| 偏离 Z、收益 Z、ATR、ADX、状态机、买卖价和止损碰撞 | 已真实执行验证，对象是单元测试里的构造序列 |\n"
        "| Python 与 EA 源码中的黄金序列 | 已真实执行验证。这不是 MetaEditor 跑出来的结果 |\n"
        "| MetaEditor 编译和策略测试器 | 仅通过代码检查；本机若没有编译器则尚未验证 |\n"
        "| Anzo 终端的 M5、H1、Tick、合约规格和服务器时差 | 因缺少数据尚未验证 |\n"
        "| 样本外净期望、Profit Factor、回撤、年份稳定性 | 未运行 |\n\n"
        "## 终端探针\n\n"
        f"{probe_detail}\n\n"
        "没有用随机价格代替。服务器时差也没有按经纪商名称猜测。\n\n"
        "## 预先登记、尚未用行情检验的基准\n\n"
        f"EMA {gates.ema_period}，偏离窗口 {gates.dev_window}，收益窗口 {gates.ret_window}，"
        f"阈值 ±{gates.z_entry}，确认幅度 {gates.reversal_gap}，ATR {gates.atr_period} × {gates.atr_mult}，"
        f"最低盈亏比 {gates.min_rr}，最长持仓 {gates.max_hold_minutes} 分钟，"
        f"点差上限 {gates.max_spread_pips} pip，单笔风险 {gates.risk_fraction:.1%}。\n\n"
        "收益 Z 和 H1 过滤在这个基准里是关闭的。它们只在消融证明有增益之后才打开。\n"
    )


def _num(value: float | None) -> str:
    if value is None:
        return "未计算"
    if not math.isfinite(value):
        return "inf"
    return f"{value:.4f}"
