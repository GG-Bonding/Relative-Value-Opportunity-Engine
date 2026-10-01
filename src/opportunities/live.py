"""Build the current EURGBP opportunity from a fair-value path and an alpha state."""

from __future__ import annotations

from domain.config import EngineConfig
from domain.enums import (
    AlphaLifecycle,
    DecisionState,
    EntryStrategy,
    OpportunityStatus,
    RegimeName,
)
from domain.hashing import stable_id
from domain.models import (
    AlphaHealth,
    EntryZone,
    Opportunity,
    TradeThesis,
)
from domain.timeutil import parse_ts
from models.fair_value import FairValuePath
from opportunities.score import (
    direction_from_gap,
    fundamental_score,
    mispricing_component,
    opportunity_score,
)
from opportunities.status import transition


def opportunity_from_path(
    path: FairValuePath,
    index: int,
    lifecycle: AlphaLifecycle,
    config: EngineConfig,
    decision: DecisionState,
    reasons: list[str],
    net_edge_pips: float | None,
    cost_pips: float | None,
    regime_name: RegimeName,
) -> Opportunity:
    row = path.row(index)
    as_of = parse_ts(str(row["observed_at"]))
    market = _opt(row.get("mid"))
    fair = _opt(row.get("fair_value"))
    residual = _opt(row.get("residual"))
    pips = None if residual is None else residual / config.pip
    direction = direction_from_gap(pips)
    score, parts = fundamental_score(
        rate_z=_opt(row.get("rate_zscore_252d")),
        policy_repricing=_opt(row.get("policy_repricing_5d")),
        growth_diff=_opt(row.get("growth_diff")),
        inflation_diff=_opt(row.get("inflation_diff")),
        risk_diff_change=None,
        weights=config.fundamental,
    )
    components = {
        "fundamental_edge": None if score is None else abs(score),
        "mispricing": mispricing_component(pips),
        "regime": 1.0 if decision in {DecisionState.LONG, DecisionState.SHORT} else 0.0,
        "alpha": {
            AlphaLifecycle.ACTIVE: 0.9,
            AlphaLifecycle.VALIDATING: 0.4,
            AlphaLifecycle.DISCOVERY: 0.1,
            AlphaLifecycle.DECAYING: 0.2,
        }.get(lifecycle, 0.0),
        "positioning": None,
        "cost": _cost_component(net_edge_pips, cost_pips),
        "execution": 1.0 if decision in {DecisionState.LONG, DecisionState.SHORT} else 0.0,
    }
    total, _ = opportunity_score(components, config.score)
    status = _status(decision)
    status = transition(OpportunityStatus.DISCOVERED, status) if status is not OpportunityStatus.DISCOVERED else status
    zone = _zone(market, fair, config)
    thesis = TradeThesis(
        why=_why(decision, pips, score, regime_name),
        dominant_drivers=[name for name, value in parts.items() if value is not None],
        expected_transmission="relative carry, policy path, and surprises move spot toward fair value",
        expected_holding_period_days=config.exit.expected_hold_days,
        expected_convergence="the gap closes because price catches the fair value, or the fair value moves to price",
        invalidation=[
            "UK-DE 2Y or the BoE-ECB path moves against the book",
            "relative growth, inflation, or risk premium reverses",
            "the regime stops supporting a fade",
            "the alpha lifecycle leaves ACTIVE",
        ],
        potential_risks=[
            "the residual is model error rather than mispricing",
            "the reaction lag collapses before the exit",
            "execution cost consumes a small gap",
        ],
    )
    health = AlphaHealth(
        ic_21d=None,
        ic_63d=None,
        ic_252d=None,
        rank_ic_63d=None,
        oos_r2=None,
        hit_rate=None,
        sharpe=None,
        after_cost_pnl=None,
        turnover=None,
        lead_time_minutes=None,
        reaction_lag_minutes=None,
        signal_half_life_minutes=None,
        coefficient_stability=_opt(row.get("coefficient_instability")),
        factor_stability=None,
        residual_stability=None,
        correlation_stability=None,
    )
    return Opportunity(
        opportunity_id=stable_id("live", as_of, decision.value),
        pair="EURGBP",
        direction=direction,
        discovered_at=as_of,
        market_price=market,
        fair_value=fair,
        mispricing=residual,
        mispricing_z=_opt(row.get("residual_z")),
        fundamental_score=score,
        fundamental_attribution=parts,
        expected_fundamentals={
            "expected_rate_diff_3m": _opt(row.get("expected_rate_diff_3m")),
            "expected_policy_diff_3m": _opt(row.get("expected_policy_diff_3m")),
            "expected_growth_diff": _opt(row.get("expected_growth_diff")),
            "expected_inflation_diff": _opt(row.get("expected_inflation_diff")),
        },
        regime=regime_name,
        alpha_state=lifecycle,
        alpha_health=health,
        positioning_state="momentum_proxy" if _opt(row.get("cftc_net")) is None else "cftc",
        crowding=None,
        opportunity_score=total,
        score_components=components,
        expected_edge=_edge_price(net_edge_pips, cost_pips, config.pip),
        expected_cost=None if cost_pips is None else cost_pips * config.pip,
        expected_net_edge=None if net_edge_pips is None else net_edge_pips * config.pip,
        entry_strategy=_entry_strategy(decision),
        entry_zone=zone,
        target_zone=_target_zone(fair, zone),
        expected_holding_period=config.exit.expected_hold_days,
        trade_thesis=thesis,
        invalidation_conditions=thesis.invalidation,
        status=status,
        decision=decision,
        decision_reasons=reasons,
    )


def _status(decision: DecisionState) -> OpportunityStatus:
    if decision in {DecisionState.LONG, DecisionState.SHORT}:
        return OpportunityStatus.READY
    waiting = {
        DecisionState.WATCH,
        DecisionState.WAIT_PULLBACK,
        DecisionState.WAIT_CONFIRMATION,
        DecisionState.WAIT_BREAKOUT,
    }
    if decision in waiting:
        return OpportunityStatus.WATCHING
    rejected = {
        DecisionState.MODEL_UNCERTAIN,
        DecisionState.DATA_DEGRADED,
        DecisionState.ALPHA_DECAYING,
    }
    if decision in rejected:
        return OpportunityStatus.REJECTED
    return OpportunityStatus.DISCOVERED


def _zone(market: float | None, fair: float | None, config: EngineConfig) -> EntryZone:
    if market is None or fair is None:
        return EntryZone(low=None, high=None, strategy=EntryStrategy.WAIT)
    width = max(
        abs(market - fair) * config.entry.pullback_retrace,
        config.costs.simulated_spread_pips * config.pip,
    )
    return EntryZone(low=market - width, high=market + width, strategy=EntryStrategy.LIMIT_PULLBACK)


def _why(decision: DecisionState, pips: float | None, score: float | None, regime: str) -> str:
    gap = "unknown" if pips is None else f"{pips:.1f} pips"
    fundamental = "unknown" if score is None else f"{score:.2f}"
    return (
        f"Decision {decision.value}. Market versus fair value is {gap}. "
        f"Fundamental score is {fundamental}. Regime is {regime}."
    )


def _target_zone(fair: float | None, zone: EntryZone) -> EntryZone:
    if fair is None:
        return zone
    return EntryZone(low=fair, high=fair, strategy=EntryStrategy.WAIT)


def _cost_component(net_edge_pips: float | None, cost_pips: float | None) -> float | None:
    if net_edge_pips is None or cost_pips is None or cost_pips <= 0:
        return None
    return max(0.0, min(1.0, net_edge_pips / (net_edge_pips + cost_pips)))


def _edge_price(net_edge_pips: float | None, cost_pips: float | None, pip: float) -> float | None:
    if net_edge_pips is None or cost_pips is None:
        return None
    return (net_edge_pips + cost_pips) * pip


def _entry_strategy(decision: DecisionState) -> EntryStrategy:
    if decision in {DecisionState.LONG, DecisionState.SHORT}:
        return EntryStrategy.MARKET
    return EntryStrategy.WAIT


def _opt(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number
