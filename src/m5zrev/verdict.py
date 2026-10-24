"""The only market conclusions this book is allowed to print."""

from __future__ import annotations

from dataclasses import dataclass

from m5zrev.params import Params


@dataclass(frozen=True, slots=True)
class Evidence:
    ran_market: bool
    execution: str
    costs_verified: bool
    oos_trades: int
    oos_expectancy_r: float | None
    oos_profit_factor: float | None
    oos_drawdown: float | None
    folds_positive: int
    folds_scored: int
    years_positive: int
    years_scored: int
    stressed_expectancy_r: float | None
    median_hold: float | None
    note: str


def judge(evidence: Evidence, gates: Params) -> tuple[str, str]:
    """Return `PASS`, `INCONCLUSIVE`, or `FAIL`, and the reason.

    A positive study that was not read from the terminal stays inconclusive.
    Enough out-of-sample trades with a non-positive expectancy fail.
    """
    if not evidence.ran_market:
        return "INCONCLUSIVE", "没有可复核的行情，市场结论未运行。"
    if evidence.oos_trades < gates.min_trades:
        return "INCONCLUSIVE", f"样本外成交 {evidence.oos_trades} 笔，少于预先登记的 {gates.min_trades} 笔。"
    if evidence.oos_expectancy_r is None or evidence.oos_expectancy_r <= 0:
        return "FAIL", "样本外平均 R 不是正数。"
    if evidence.oos_profit_factor is None or evidence.oos_profit_factor <= 1:
        return "FAIL", "样本外 Profit Factor 没有大于 1。"
    if evidence.oos_drawdown is None or evidence.oos_drawdown > gates.max_drawdown_fraction:
        return "FAIL", "样本外回撤超过预先登记的上限。"
    if evidence.stressed_expectancy_r is None:
        return "INCONCLUSIVE", "没有完成两倍执行成本的压力测试。"
    if evidence.stressed_expectancy_r <= 0:
        return "INCONCLUSIVE", "一倍成本下样本外为正，两倍执行成本下期望不再为正。"
    if evidence.folds_scored < 2 or evidence.folds_positive * 2 <= evidence.folds_scored:
        return "INCONCLUSIVE", "样本外折数不够，或者多数折不是正期望。"
    if evidence.years_scored < 2 or evidence.years_positive != evidence.years_scored:
        return "INCONCLUSIVE", "有足够成交的年份不足两年，或者并非每一年都为正。"
    if evidence.median_hold is None or evidence.median_hold > 90:
        return "INCONCLUSIVE", "中位持仓没有落在时间退出能够解释的范围内。"
    if evidence.execution != "tick" or not evidence.costs_verified:
        return "INCONCLUSIVE", "行情或合约规格不是从 MT5 终端读取的 Tick。"
    return "PASS", "预先登记的门槛在终端 Tick 和两倍成本下仍然成立，可以进入模拟盘观察。"
