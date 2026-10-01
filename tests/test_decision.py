from __future__ import annotations

import math

from domain.config import EngineConfig
from domain.enums import (
    AlphaLifecycle,
    DecisionState,
    Direction,
    ExitReason,
    MispricingClass,
    RegimeName,
)
from execution.decision import DecisionInput, chase_already_closed, decide
from execution.exit import exit_reasons
from portfolio.sizing import position_size


def _tradable(**overrides: object) -> DecisionInput:
    base = dict(
        mispricing_pips=40.0,
        fundamental=-0.5,
        classification=MispricingClass.POSSIBLE_MISPRICING,
        regime=RegimeName.DISLOCATION,
        alpha=AlphaLifecycle.ACTIVE,
        crowding=0.2,
        net_edge_pips=10.0,
        missing_critical=False,
        stale_critical=False,
        chase=False,
        breakout_pending=False,
        reaction_lag_blocked=False,
    )
    base.update(overrides)
    return DecisionInput(**base)  # type: ignore[arg-type]


def test_active_rich_eur_is_a_short() -> None:
    decision = decide(_tradable(), EngineConfig())
    assert decision.state is DecisionState.SHORT
    assert decision.direction is Direction.SHORT


def test_correct_fundamental_view_is_not_a_trade_once_the_gap_is_gone() -> None:
    decision = decide(_tradable(mispricing_pips=2.0, fundamental=-0.9), EngineConfig())
    assert decision.state is DecisionState.NO_TRADE


def test_each_hard_gate_blocks_an_otherwise_tradable_short() -> None:
    config = EngineConfig()
    cases = [
        ({"missing_critical": True}, DecisionState.DATA_DEGRADED),
        ({"classification": MispricingClass.MODEL_ERROR}, DecisionState.MODEL_UNCERTAIN),
        ({"alpha": AlphaLifecycle.DECAYING}, DecisionState.ALPHA_DECAYING),
        ({"alpha": AlphaLifecycle.REJECTED}, DecisionState.NO_TRADE),
        ({"reaction_lag_blocked": True}, DecisionState.NO_TRADE),
        ({"alpha": AlphaLifecycle.VALIDATING}, DecisionState.WATCH),
        ({"regime": RegimeName.TREND}, DecisionState.NO_TRADE),
        ({"fundamental": 0.8}, DecisionState.MODEL_UNCERTAIN),
        ({"net_edge_pips": 0.0}, DecisionState.NO_TRADE),
        ({"crowding": 0.95}, DecisionState.NO_TRADE),
        ({"chase": True}, DecisionState.WAIT_PULLBACK),
        ({"breakout_pending": True}, DecisionState.WAIT_BREAKOUT),
        ({"classification": MispricingClass.EVENT_DISLOCATION}, DecisionState.WAIT_CONFIRMATION),
        ({"classification": MispricingClass.UNKNOWN}, DecisionState.WATCH),
    ]
    for overrides, expected in cases:
        decision = decide(_tradable(**overrides), config)
        assert decision.state is expected, overrides


def test_do_not_chase_a_gap_that_has_already_closed() -> None:
    # Fair value 0.8490. Price fell from 0.8600 to 0.8520.
    gaps = [0.8600 - 0.8490, 0.8520 - 0.8490]
    assert chase_already_closed(gaps=gaps, direction=Direction.SHORT, chase_fraction=0.6)
    fresh = [0.8560 - 0.8490]
    assert not chase_already_closed(gaps=fresh, direction=Direction.SHORT, chase_fraction=0.6)


def test_dynamic_fair_value_closes_the_trade_when_five_pips_remain() -> None:
    reasons = exit_reasons(
        remaining_pips=5.0,
        direction=Direction.SHORT,
        entry_price=0.8550,
        market_price=0.8510,
        fair_value=0.8505,
        holding_days=4,
        alpha=AlphaLifecycle.ACTIVE,
        thesis_reasons=[],
        spread_z=0.0,
        config=EngineConfig(),
    )
    assert ExitReason.MISPRICING_CLOSED in reasons
    assert ExitReason.TARGET_REACHED in reasons


def test_vol_target_ignores_the_edge_and_is_not_kelly() -> None:
    risk = EngineConfig().risk
    base = position_size(daily_vol=0.01, risk=risk, drawdown=0.0, day_loss=0.0)
    halved = position_size(daily_vol=0.02, risk=risk, drawdown=0.0, day_loss=0.0)
    assert math.isclose(base, 2.0 * halved, rel_tol=1e-9)
    assert position_size(daily_vol=None, risk=risk, drawdown=0.0, day_loss=0.0) == 0.0
    assert position_size(daily_vol=0.01, risk=risk, drawdown=risk.max_drawdown, day_loss=0.0) == 0.0
    assert "kelly" not in position_size.__doc__.lower() or "absent" in position_size.__doc__.lower()
