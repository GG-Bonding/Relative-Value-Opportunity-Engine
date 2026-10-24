"""Run the pre-registered book on a real M5 tape and write the report set.

A full-sample trade is not treated as out of sample. The sealed holdout is the
last three months. Parameter search sees only the train and validation windows.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta
from pathlib import Path

from m5zrev.backtest import Trade, run_backtest
from m5zrev.data import BarTable, SymbolSpec
from m5zrev.experiments import (
    Fold,
    Window,
    ablation_grid,
    direction_grid,
    extra_cost_is_configured,
    iter_folds,
    prefer_hold,
    return_filter_added_value,
    scale_extra_cost,
    select_staged,
    stage1_heatmap,
    trades_inside,
)
from m5zrev.metrics import Summary, group_trades, summarize
from m5zrev.params import Params
from m5zrev.report import format_summary, write_heatmap, write_trades
from m5zrev.session import assess_calendar
from m5zrev.verdict import Evidence, judge


@dataclass(frozen=True, slots=True)
class _Run:
    trades: tuple[Trade, ...]
    reasons: tuple[tuple[str, int], ...]
    execution: str
    warnings: tuple[str, ...]


class _Cache:
    def __init__(
        self,
        m5: BarTable,
        h1: BarTable | None,
        spec: SymbolSpec,
        events: tuple[datetime, ...],
    ) -> None:
        self._m5 = m5
        self._h1 = h1
        self._spec = spec
        self._events = events
        self._runs: dict[tuple[object, ...], _Run] = {}

    def run(self, params: Params, spread: float) -> _Run:
        key = (*((field.name, getattr(params, field.name)) for field in fields(params)), spread)
        cached = self._runs.get(key)
        if cached is not None:
            return cached
        print(
            f"backtest ema={params.ema_period} window={params.dev_window} z={params.z_entry} "
            f"ret={params.use_return_filter} trend={params.use_trend_filter} "
            f"hold={params.max_hold_minutes} slip={params.slippage_pips} spread={spread}",
            file=sys.stderr,
            flush=True,
        )
        result = run_backtest(
            self._m5,
            self._h1,
            None,
            self._spec,
            params,
            self._events,
            study_spread_pips=spread,
        )
        counts = Counter(item.reason for item in result.journal)
        cached = _Run(result.trades, tuple(sorted(counts.items())), result.execution, result.warnings)
        self._runs[key] = cached
        return cached


def run_study(
    m5: BarTable,
    h1: BarTable | None,
    spec: SymbolSpec,
    events: tuple[datetime, ...],
    params: Params,
    output: Path,
    *,
    study_spread_pips: float,
    notes: str,
) -> str:
    """Write the report files and return the verdict label."""
    if study_spread_pips < 0:
        raise ValueError("study spread must be non-negative")
    if len(m5.close_time) < 2:
        raise ValueError("the M5 tape needs at least two bars")
    cache = _Cache(m5, h1, spec, events)
    start = m5.close_time[0]
    end = m5.close_time[-1] + timedelta(seconds=1)
    folds, holdout_start = iter_folds(start, end)
    holdout = Window("holdout", holdout_start, end)
    full = Window("full", start, end)
    calendar = assess_calendar(events, m5.close_time[-1], timedelta(days=params.calendar_max_age_days))
    base = cache.run(params, study_spread_pips)
    base_full = _summary(base.trades, full, params)
    base_hold = _summary(trades_inside(base.trades, holdout), holdout, params)
    output.mkdir(parents=True, exist_ok=True)
    _write_baseline(
        output,
        params,
        spec,
        study_spread_pips,
        notes,
        calendar,
        len(events),
        m5,
        base,
        base_full,
        base_hold,
        cache,
    )
    _write_ablation(output, cache, params, study_spread_pips, holdout, full)
    cells, fold_positive, fold_scored = _walkforward(
        output,
        cache,
        params,
        spec,
        study_spread_pips,
        folds,
        holdout,
        base_hold,
    )
    write_heatmap(output / "sensitivity.svg", cells)
    write_trades(output / "trades.csv", base.trades)
    stressed = _stress_expectancy(cache, params, spec, study_spread_pips, holdout)
    years = group_trades(trades_inside(base.trades, holdout), "year")
    years_scored = 0
    years_positive = 0
    for bucket in years:
        if bucket.trades < params.min_trades or bucket.expectancy_r is None:
            continue
        years_scored += 1
        if bucket.expectancy_r > 0:
            years_positive += 1
    evidence = Evidence(
        ran_market=True,
        execution=base.execution,
        costs_verified=spec.verified_from_terminal,
        oos_trades=base_hold.trades,
        oos_expectancy_r=base_hold.expectancy_r,
        oos_profit_factor=base_hold.profit_factor,
        oos_drawdown=base_hold.max_drawdown,
        folds_positive=fold_positive,
        folds_scored=fold_scored,
        years_positive=years_positive,
        years_scored=years_scored,
        stressed_expectancy_r=stressed,
        median_hold=base_hold.median_hold,
        note=notes,
    )
    label, reason = judge(evidence, params)
    (output / "verdict.md").write_text(
        _verdict_text(
            label,
            reason,
            notes,
            spec,
            study_spread_pips,
            calendar,
            base_full,
            base_hold,
            fold_scored,
            params,
        ),
        encoding="utf-8",
    )
    return label


def _summary(trades: tuple[Trade, ...] | list[Trade], window: Window, params: Params) -> Summary:
    return summarize(trades, initial_equity=params.initial_equity, sample_start=window.start, sample_end=window.end)


def _write_baseline(
    output: Path,
    params: Params,
    spec: SymbolSpec,
    spread: float,
    notes: str,
    calendar: str,
    event_count: int,
    m5: BarTable,
    base: _Run,
    full: Summary,
    hold: Summary,
    cache: _Cache,
) -> None:
    lines = [
        "# EURGBP M5 基准\n\n",
        "预先登记的基准只使用价格偏离 Z。收益 Z 和 H1 过滤是关闭的。\n\n",
        f"执行口径：{base.execution}。这不是 Tick 回测。\n\n",
        f"M5 收盘从 {m5.close_time[0].isoformat()} 到 {m5.close_time[-1].isoformat()}，共 {len(m5.close)} 根。\n\n",
        f"研究点差假设 {spread:g} pip。合约来源 `{spec.source}`，终端核对 {spec.verified_from_terminal}。\n\n",
        f"经济日历状态 `{calendar}`，过滤后事件 {event_count} 个。模式是 `{params.event_filter}`。\n\n",
        "## 终端和编译器\n\n",
        f"{notes}\n\n",
        "## 全样本\n\n",
        "下面的数字包含了后来被封存的区间，不能当作样本外。\n\n",
        format_summary(full) + "\n\n",
        _extras(base.trades),
        "\n\n## 封存的最后三个月\n\n",
        format_summary(hold) + "\n\n",
        _extras(trades_inside(base.trades, _holdout_window(m5))),
        "\n\n## 方向\n\n",
    ]
    end = m5.close_time[-1] + timedelta(seconds=1)
    full_window = Window("full", m5.close_time[0], end)
    lines.append("全样本：\n\n")
    lines.append(_direction_block(cache, params, spread, full_window))
    lines.append("\n封存区间：\n\n")
    lines.append(_direction_block(cache, params, spread, _holdout_window(m5)))
    lines.append("\n## 点差和滑点假设\n\n")
    lines.append(
        "这些数字不是 Anzo Capital 的报价。点差是常数，滑点是额外假设。佣金保持文件里的值；"
        "研究规格的佣金是 0，所以只放大佣金不会改变成交。\n\n"
    )
    sealed = _holdout_window(m5)
    for extra in (spread, 1.5, 2.0):
        run = cache.run(params, extra)
        lines.append(f"- 全样本点差 {extra:g} pip：{format_summary(_summary(run.trades, full_window, params))}\n")
        kept = trades_inside(run.trades, sealed)
        lines.append(f"- 封存点差 {extra:g} pip：{format_summary(_summary(kept, sealed, params))}\n")
    for slip in (0.0, 0.2, 0.4):
        run = cache.run(replace(params, slippage_pips=slip), spread)
        lines.append(f"- 全样本滑点 {slip:g} pip：{format_summary(_summary(run.trades, full_window, params))}\n")
        kept = trades_inside(run.trades, sealed)
        lines.append(f"- 封存滑点 {slip:g} pip：{format_summary(_summary(kept, sealed, params))}\n")
    lines.append("\n## 信号被拒绝的原因\n\n")
    if not base.reasons:
        lines.append("日志里没有状态变化。\n")
    for reason, count in base.reasons:
        lines.append(f"- {reason}: {count}\n")
    for warning in base.warnings:
        lines.append(f"\n- {warning}\n")
    lines.append("\n## 全样本时段、年份和月份\n\n")
    for kind in ("hour", "year", "month"):
        lines.append(f"\n### {kind}\n\n")
        buckets = group_trades(base.trades, kind)
        if not buckets:
            lines.append("没有成交。\n")
        for bucket in buckets:
            lines.append(
                f"- {bucket.label}: {bucket.trades} 笔，净额 {bucket.net_account:.4f}，"
                f"平均 R {_num(bucket.expectancy_r)}\n"
            )
    (output / "baseline-report.md").write_text("".join(lines), encoding="utf-8")


def _write_ablation(
    output: Path,
    cache: _Cache,
    params: Params,
    spread: float,
    holdout: Window,
    full: Window,
) -> None:
    lines = [
        "# 消融\n\n",
        "A 只有价格偏离 Z。B 加上收益 Z。C 加上 H1 趋势过滤。D 两者都加。数据和点差假设相同。\n\n",
    ]
    hold_summaries: dict[str, Summary] = {}
    for name, candidate in ablation_grid(params):
        run = cache.run(candidate, spread)
        full_summary = _summary(run.trades, full, params)
        hold_summary = _summary(trades_inside(run.trades, holdout), holdout, params)
        hold_summaries[name] = hold_summary
        lines.append(f"## {name}\n\n")
        lines.append(f"全样本：{format_summary(full_summary)}\n\n")
        lines.append(f"封存区间：{format_summary(hold_summary)}\n\n")
    added = return_filter_added_value(hold_summaries["A"], hold_summaries["B"], params.min_trades)
    trend = return_filter_added_value(hold_summaries["A"], hold_summaries["C"], params.min_trades)
    lines.append(f"封存区间里，收益 Z 是否带来额外价值：{added}。\n\n")
    lines.append(f"封存区间里，H1 过滤是否带来额外价值：{trend}。\n")
    lines.append("这里的“额外价值”沿用同一条规则：期望更高、大于 0、Profit Factor 不下降、回撤不超过 1.2 倍。\n")
    (output / "ablation-report.md").write_text("".join(lines), encoding="utf-8")


def _walkforward(
    output: Path,
    cache: _Cache,
    params: Params,
    spec: SymbolSpec,
    spread: float,
    folds: tuple[Fold, ...],
    holdout: Window,
    base_hold: Summary,
) -> tuple[tuple[tuple[str, str, float | None], ...], int, int]:
    lines = [
        "# 走步样本外\n\n",
        "训练 12 个月，验证 3 个月，测试 3 个月，每 3 个月向前滚一次。",
        f"封存起点 {holdout.start.isoformat()}。选择参数时看不到测试窗，也看不到封存区间。\n\n",
        "第一阶段只搜价格偏离 Z。过不了训练和验证就记 NO_EDGE，不再加过滤。\n\n",
    ]
    cells: tuple[tuple[str, str, float | None], ...] = ()
    positive = 0
    scored = 0
    typed_folds = folds
    if not typed_folds:
        lines.append("样本短于一整套训练、验证、测试和封存，没有走出一折。\n")
        (output / "walkforward-report.md").write_text("".join(lines), encoding="utf-8")
        return cells, 0, 0
    first_train = typed_folds[0].train
    cells = stage1_heatmap(params, lambda candidate: _summary(cache.run(candidate, spread).trades, first_train, params))
    lines.append("热力图是第一折的训练窗，不是测试窗。\n\n")
    for index, fold in enumerate(typed_folds, start=1):
        train = fold.train
        valid = fold.valid
        test = fold.test

        def evaluate(candidate: Params, window: Window, _train: Window = train, _valid: Window = valid) -> Summary:
            if window is not _train and window is not _valid:
                raise RuntimeError("selection asked for a window outside this fold's train and validation")
            kept = trades_inside(cache.run(candidate, spread).trades, window)
            return _summary(kept, window, params)

        choice = select_staged(params, evaluate, train, valid)
        frozen = cache.run(params, spread)
        frozen_test = _summary(trades_inside(frozen.trades, test), test, params)
        lines.append(f"## 折 {index} {test.start.date()} 至 {test.end.date()}\n\n")
        lines.append(f"选择：{choice.reason}。预先登记参数在这一折测试窗：{format_summary(frozen_test)}\n\n")
        if choice.params is None:
            lines.append("这一折没有参数同时通过训练和验证。\n\n")
            continue
        chosen_test = _summary(trades_inside(cache.run(choice.params, spread).trades, test), test, params)
        lines.append(
            "冻结参数："
            f"EMA {choice.params.ema_period}，窗口 {choice.params.dev_window}，阈值 {choice.params.z_entry}，"
            f"收益过滤 {choice.params.use_return_filter}，趋势过滤 {choice.params.use_trend_filter}，"
            f"止损倍数 {choice.params.atr_mult}，持仓 {choice.params.max_hold_minutes}，"
            f"盈亏比 {choice.params.min_rr}。\n\n"
        )
        lines.append(f"测试窗：{format_summary(chosen_test)}\n\n")
        if chosen_test.trades >= params.min_trades and chosen_test.expectancy_r is not None:
            scored += 1
            if chosen_test.expectancy_r > 0:
                positive += 1
    hold_runs = []
    for minutes in (30, 45, 60):
        candidate = _replace_hold(params, minutes)
        summary = _summary(trades_inside(cache.run(candidate, spread).trades, holdout), holdout, params)
        hold_runs.append((minutes, summary))
        lines.append(f"封存区间持仓上限 {minutes} 分钟：{format_summary(summary)}\n\n")
    lines.append(f"封存区间上，按平均 R 比较持仓上限：{prefer_hold(tuple(hold_runs), params.min_trades)}。\n\n")
    lines.append("这个比较只描述封存区间，不把胜出的持仓时间写回基准。基准仍是预先登记的 60 分钟。\n\n")
    lines.append(f"预先登记基准在封存区间：{format_summary(base_hold)}\n\n")
    stressed_params, stressed_spec = scale_extra_cost(params, spec, 2.0)
    lines.append(
        "把配置里的滑点和佣金乘以 2："
        f"滑点 {stressed_params.slippage_pips:g} pip，单边佣金 {stressed_spec.commission_per_lot_side:g}。"
    )
    if extra_cost_is_configured(params, spec):
        lines.append("配置里有非零的额外成本。\n\n")
    else:
        lines.append("配置里的滑点和佣金都是 0，这个倍数没有改变成交。\n\n")
    (output / "walkforward-report.md").write_text("".join(lines), encoding="utf-8")
    return cells, positive, scored


def _stress_expectancy(
    cache: _Cache,
    params: Params,
    spec: SymbolSpec,
    spread: float,
    holdout: Window,
) -> float | None:
    """Two times a 0.2 pip research slippage. Zero configured cost is not this number."""
    if extra_cost_is_configured(params, spec):
        stressed, _spec = scale_extra_cost(params, spec, 2.0)
        summary = _summary(trades_inside(cache.run(stressed, spread).trades, holdout), holdout, params)
        return summary.expectancy_r
    assumed = replace(params, slippage_pips=0.4)
    summary = _summary(trades_inside(cache.run(assumed, spread).trades, holdout), holdout, params)
    return summary.expectancy_r


def _verdict_text(
    label: str,
    reason: str,
    notes: str,
    spec: SymbolSpec,
    spread: float,
    calendar: str,
    full: Summary,
    hold: Summary,
    fold_scored: int,
    params: Params,
) -> str:
    return (
        "# EURGBP M5 偏离回归\n\n"
        f"结论：**{label}**\n\n"
        f"{reason}\n\n"
        "## 这次实际跑了什么\n\n"
        f"{notes}\n\n"
        f"点差假设 {spread:g} pip。合约 `{spec.source}`，终端核对 {spec.verified_from_terminal}。\n\n"
        f"经济日历 `{calendar}`。收益 Z 和 H1 过滤在基准里是关闭的。\n\n"
        f"全样本：{format_summary(full)}\n\n"
        f"封存区间：{format_summary(hold)}\n\n"
        f"预先登记门槛：至少 {params.min_trades} 笔，平均 R 为正，Profit Factor 大于 1，"
        f"回撤不超过 {params.max_drawdown_fraction:.0%}。\n\n"
        "Anzo Capital 的真实点差、佣金、Tick 和服务器时差没有从终端读到。"
        "上面的美元盈亏依赖研究规格里的 GBPUSD 1.25 假设。平均 R 不依赖这个汇率，但手数取整会受它影响。\n\n"
        "走步明细在 `walkforward-report.md`。消融在 `ablation-report.md`。\n\n"
        f"有足够测试成交、因而计入门槛的走步折：{fold_scored}。明细在走步报告里。\n"
    )


def _direction_block(cache: _Cache, params: Params, spread: float, window: Window) -> str:
    lines = []
    for name, candidate in direction_grid(params):
        kept = trades_inside(cache.run(candidate, spread).trades, window)
        summary = _summary(kept, window, params)
        lines.append(f"- {name}：{format_summary(summary)}\n")
    return "".join(lines)


def _extras(trades: tuple[Trade, ...] | list[Trade]) -> str:
    rows = list(trades)
    if not rows:
        return "没有成交，所以没有持仓分布、MAE 或 MFE。"
    mae = [item.mae_pips for item in rows]
    mfe = [item.mfe_pips for item in rows]
    reasons = Counter(item.exit_reason for item in rows)
    reason_text = ", ".join(f"{name} {count}" for name, count in sorted(reasons.items()))
    return (
        f"退出原因：{reason_text}。平均 MAE {sum(mae) / len(mae):.2f} pips，"
        f"平均 MFE {sum(mfe) / len(mfe):.2f} pips，最大不利 {min(mae):.2f} pips，"
        f"最大有利 {max(mfe):.2f} pips。"
    )


def _replace_hold(params: Params, minutes: int) -> Params:
    return replace(params, max_hold_minutes=minutes)


def _holdout_window(m5: BarTable) -> Window:
    _folds, start = iter_folds(m5.close_time[0], m5.close_time[-1] + timedelta(seconds=1))
    return Window("holdout", start, m5.close_time[-1] + timedelta(seconds=1))


def _num(value: float | None) -> str:
    if value is None:
        return "未计算"
    return f"{value:.4f}"
