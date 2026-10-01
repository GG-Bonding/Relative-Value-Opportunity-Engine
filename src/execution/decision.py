"""Ordered gates. A correct fundamental view is not, by itself, a trade."""

from __future__ import annotations

from dataclasses import dataclass

from domain.config import EngineConfig
from domain.enums import (
    AlphaLifecycle,
    DecisionState,
    Direction,
    EntryStrategy,
    MispricingClass,
    RegimeName,
)
from opportunities.score import direction_from_gap, fundamental_agrees
from regimes.engine import regime_supports_fade


@dataclass(frozen=True)
class DecisionInput:
    mispricing_pips: float | None
    fundamental: float | None
    classification: MispricingClass
    regime: RegimeName
    alpha: AlphaLifecycle
    crowding: float | None
    net_edge_pips: float | None
    missing_critical: bool
    stale_critical: bool
    chase: bool
    breakout_pending: bool
    reaction_lag_blocked: bool


@dataclass(frozen=True)
class Decision:
    state: DecisionState
    direction: Direction | None
    strategy: EntryStrategy
    reasons: list[str]


def decide(inputs: DecisionInput, config: EngineConfig) -> Decision:
    direction = direction_from_gap(inputs.mispricing_pips)
    if inputs.missing_critical or inputs.stale_critical:
        return _wait(DecisionState.DATA_DEGRADED, ["critical fields are missing or stale"])
    if inputs.classification is MispricingClass.MODEL_ERROR:
        return _wait(DecisionState.MODEL_UNCERTAIN, ["residual is classified as model error"])
    if inputs.classification is MispricingClass.LIQUIDITY_DISTORTION:
        return _wait(DecisionState.NO_TRADE, ["liquidity distortion"])
    if inputs.classification is MispricingClass.REGIME_BREAK:
        return _wait(DecisionState.NO_TRADE, ["regime break; residual is not treated as a mispricing"])
    if inputs.classification is MispricingClass.EVENT_DISLOCATION:
        return Decision(
            DecisionState.WAIT_CONFIRMATION,
            None,
            EntryStrategy.WAIT,
            ["event is not in the factor set; wait for the price path to confirm"],
        )
    if inputs.alpha is AlphaLifecycle.DECAYING:
        return _wait(DecisionState.ALPHA_DECAYING, ["tradable alpha is decaying"])
    if inputs.alpha in {AlphaLifecycle.DORMANT, AlphaLifecycle.RETIRED, AlphaLifecycle.REJECTED}:
        return _wait(DecisionState.NO_TRADE, [f"alpha lifecycle is {inputs.alpha.value}"])
    if inputs.reaction_lag_blocked:
        return _wait(DecisionState.NO_TRADE, ["reaction lag is shorter than the time needed to execute"])
    if inputs.alpha in {AlphaLifecycle.DISCOVERY, AlphaLifecycle.VALIDATING}:
        return _wait(DecisionState.WATCH, [f"alpha is {inputs.alpha.value}; watch, do not trade"])
    if not regime_supports_fade(inputs.regime):
        return _wait(DecisionState.NO_TRADE, [f"regime {inputs.regime.value} does not support a fade"])
    agreement = fundamental_agrees(inputs.fundamental, direction, config.opportunity.fundamental_conflict)
    if agreement is False:
        return _wait(
            DecisionState.MODEL_UNCERTAIN,
            ["fundamental direction and the mispricing fade disagree"],
        )
    if inputs.mispricing_pips is None or abs(inputs.mispricing_pips) < config.opportunity.min_mispricing_pips:
        return _wait(DecisionState.NO_TRADE, ["mispricing is inside the minimum, including a fully priced view"])
    if inputs.net_edge_pips is None or inputs.net_edge_pips <= config.opportunity.min_net_edge_pips:
        return _wait(DecisionState.NO_TRADE, ["expected net edge does not clear the cost threshold"])
    if inputs.crowding is not None and inputs.crowding >= config.opportunity.max_crowding:
        return _wait(DecisionState.NO_TRADE, ["positioning is crowded"])
    if inputs.chase:
        return Decision(
            DecisionState.WAIT_PULLBACK,
            direction,
            EntryStrategy.LIMIT_PULLBACK,
            ["price has already closed most of the gap; do not chase"],
        )
    if inputs.breakout_pending:
        return Decision(
            DecisionState.WAIT_BREAKOUT,
            direction,
            EntryStrategy.BREAKOUT_CONFIRMATION,
            ["breakout confirmation is not in place"],
        )
    if inputs.classification is not MispricingClass.POSSIBLE_MISPRICING or direction is None:
        return _wait(DecisionState.WATCH, ["mispricing is not yet tradable"])
    state = DecisionState.LONG if direction is Direction.LONG else DecisionState.SHORT
    return Decision(state, direction, EntryStrategy.MARKET, ["gates passed; fade the residual"])


def chase_already_closed(
    *,
    gaps: list[float],
    direction: Direction,
    chase_fraction: float,
) -> bool:
    """Gap is market minus the fair value known on that same date.

    The spec example is a short whose gap has already closed from 110 pips at
    0.8600 to 30 pips at 0.8520 against a 0.8490 fair value.
    """
    if len(gaps) < 2:
        return False
    if direction is Direction.SHORT:
        peak = max(gaps)
        now = gaps[-1]
        if peak <= 0 or now <= 0:
            return False
        return (peak - now) / peak > chase_fraction and peak > now
    trough = min(gaps)
    now = gaps[-1]
    if trough >= 0 or now >= 0:
        return False
    return (now - trough) / abs(trough) > chase_fraction and trough < now


def _wait(state: DecisionState, reasons: list[str]) -> Decision:
    return Decision(state, None, EntryStrategy.WAIT, reasons)
