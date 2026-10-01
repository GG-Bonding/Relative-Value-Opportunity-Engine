"""Buys pay the ask. Sells receive the bid. A missing book uses an explicit simulated spread."""

from __future__ import annotations

from domain.config import CostConfig
from domain.enums import FillSide, SpreadSource


def execution_price(
    *,
    side: FillSide,
    mid: float,
    bid: float | None,
    ask: float | None,
    pip: float,
    costs: CostConfig,
) -> tuple[float, SpreadSource, float]:
    if bid is not None and ask is not None and bid <= ask:
        spread_pips = (ask - bid) / pip
        source = SpreadSource.OBSERVED_BOOK
        if side is FillSide.BUY:
            price = ask + costs.slippage_pips * pip
        else:
            price = bid - costs.slippage_pips * pip
        return price, source, spread_pips
    half = costs.simulated_spread_pips * pip / 2.0
    slip = costs.slippage_pips * pip
    if side is FillSide.BUY:
        price = mid + half + slip
    else:
        price = mid - half - slip
    return price, SpreadSource.SIMULATED_SPREAD, costs.simulated_spread_pips


def cost_pips(costs: CostConfig, spread_pips: float) -> float:
    """Round trip: the full spread, both sides of slippage and fees, and the latency charge."""
    return spread_pips + 2.0 * costs.slippage_pips + 2.0 * costs.fee_pips + costs.latency_cost_pips


def expected_net_edge_pips(
    mispricing_pips: float | None,
    probability: float,
    costs: CostConfig,
    spread_pips: float,
) -> tuple[float | None, float | None, float | None]:
    if mispricing_pips is None:
        return None, None, None
    edge = abs(mispricing_pips) * probability
    cost = cost_pips(costs, spread_pips)
    return edge, cost, edge - cost
