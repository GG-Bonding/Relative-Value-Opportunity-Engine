"""A challenger replaces the champion only when every gate is better."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelScorecard:
    name: str
    oos_ic: float
    after_cost_pnl: float
    max_drawdown: float
    parameter_stable: bool
    explainable: bool
    paper_pnl: float


def promotion_allowed(champion: ModelScorecard, challenger: ModelScorecard) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if challenger.oos_ic <= champion.oos_ic:
        reasons.append("out-of-sample IC is not higher")
    if challenger.after_cost_pnl <= champion.after_cost_pnl:
        reasons.append("after-cost pnl is not higher")
    if challenger.max_drawdown > champion.max_drawdown * 1.05:
        reasons.append("drawdown is worse")
    if not challenger.parameter_stable:
        reasons.append("parameters are unstable")
    if not challenger.explainable:
        reasons.append("the challenger is not explainable")
    if challenger.paper_pnl <= champion.paper_pnl:
        reasons.append("paper-trading pnl is not higher")
    return not reasons, reasons
