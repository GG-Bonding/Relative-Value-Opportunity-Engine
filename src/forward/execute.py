"""One signal, three adapters. Live does not send an order."""

from __future__ import annotations

from domain.config import EngineConfig
from domain.enums import DecisionState, Direction, ExecutionMode, FillSide, SpreadSource
from domain.errors import DataValidationError
from execution.costs import execution_price
from forward.mt5 import Mt5Tick


def entry_fill(
    mode: ExecutionMode,
    decision: DecisionState,
    direction: Direction | None,
    tick: Mt5Tick,
    config: EngineConfig,
) -> float | None:
    if decision not in {DecisionState.LONG, DecisionState.SHORT} or direction is None:
        return None
    if mode is not ExecutionMode.PAPER:
        return None
    side = FillSide.SELL if direction is Direction.SHORT else FillSide.BUY
    return _book_price(side, tick, config)


def exit_fill(direction: Direction, tick: Mt5Tick, config: EngineConfig) -> float:
    side = FillSide.BUY if direction is Direction.SHORT else FillSide.SELL
    return _book_price(side, tick, config)


def _book_price(side: FillSide, tick: Mt5Tick, config: EngineConfig) -> float:
    price, source, _spread = execution_price(
        side=side,
        mid=tick.mid,
        bid=tick.bid,
        ask=tick.ask,
        pip=config.pip,
        costs=config.costs,
    )
    if source is not SpreadSource.OBSERVED_BOOK:
        raise DataValidationError("a paper fill requires the broker bid and ask")
    return price
