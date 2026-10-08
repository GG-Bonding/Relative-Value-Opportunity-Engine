"""One forward step. The opening row is never edited; later rows are new sequences."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from data.policy_news import policy_context
from data.store import PitStore
from domain.config import EngineConfig
from domain.enums import (
    AlphaLifecycle,
    DecisionState,
    Direction,
    EntryStrategy,
    ExecutionMode,
    ExitReason,
    MispricingClass,
    ModelReadiness,
    OpportunityStatus,
    RegimeName,
)
from domain.errors import DataValidationError, PointInTimeError
from domain.hashing import stable_id
from domain.models import MarketObservation, RateObservation
from domain.timeutil import dump_ts, ensure_utc, parse_ts
from execution.costs import expected_net_edge_pips
from execution.decision import Decision, DecisionInput, decide
from execution.exit import exit_reasons, invalidation_reasons
from execution.live_gate import assess_data_health
from forward.events import EventInput, combined_pressure, event_row, narratives_for
from forward.execute import entry_fill, exit_fill
from forward.journal import active_journal, append_journal, dump_detail, load_detail, opening_row
from forward.mt5 import Mt5Tick
from forward.rates import RateSnapshot
from forward.standardize import standardize_events
from opportunities.status import transition

MT5_SOURCE = "mt5"
MT5_VERSION = "mt5-tick-v1"
RATE_SOURCE = "rate-provider"
RATE_VERSION = "rate-print-v1"
_GROWTH = frozenset({"GDP", "PMI", "WAGES", "RETAIL_SALES", "INDUSTRIAL_PRODUCTION", "EMPLOYMENT"})
_CLOSED = frozenset({ExitReason.MISPRICING_CLOSED, ExitReason.TARGET_REACHED})


@dataclass(frozen=True)
class ForwardBook:
    tick: Mt5Tick
    rates: RateSnapshot
    events: list[EventInput]


def run_forward(
    store: PitStore,
    config: EngineConfig,
    mode: ExecutionMode | str,
    book: ForwardBook,
    *,
    ingested_at: datetime,
    fair_value: float | None,
    alpha: AlphaLifecycle = AlphaLifecycle.DISCOVERY,
) -> dict[str, Any]:
    """Record the inputs and the call. Paper fills the bid or the ask. Live sends nothing."""
    execution = mode if isinstance(mode, ExecutionMode) else ExecutionMode(str(mode).upper())
    ingested = ensure_utc(ingested_at)
    tick = book.tick
    if ingested < tick.time:
        raise PointInTimeError("MT5 tick is after the ingest time")
    if store.has_forward_decision(execution.value, tick.time):
        return {
            "duplicate": True,
            "mode": execution.value,
            "observed_at": dump_ts(tick.time),
            "executed": False,
            "alpha": alpha.value,
            "net_edge_pips": None,
            "data_health": assess_data_health(
                rate_diff=book.rates.rate_diff,
                bid=tick.bid,
                ask=tick.ask,
                fair_value=fair_value,
                decision=None,
            ).value,
        }

    book = replace(book, events=standardize_events(store, book.events, config))
    _persist_market(store, tick, ingested)
    _persist_rates(store, book.rates, tick.time, ingested)
    _persist_events(store, book.events, tick.time, ingested)

    pressure = combined_pressure(book.events)
    narratives = narratives_for(book.events)
    regime = _regime(book.events)
    decision, readiness, net_edge = _decision(
        book,
        config,
        fair_value=fair_value,
        alpha=alpha,
        pressure=pressure,
        regime=regime,
        mode=execution,
    )
    follow_up = _follow(
        store,
        config,
        execution,
        book,
        decision,
        readiness,
        regime,
        pressure,
        narratives,
        fair_value,
        alpha,
        ingested,
    )
    _append_decision(store, execution, tick, ingested, decision, readiness, book.rates.rate_diff, fair_value, follow_up)
    follow_up["duplicate"] = False
    follow_up["event_pressure"] = pressure
    follow_up["narratives"] = narratives
    follow_up["rate_diff"] = book.rates.rate_diff
    follow_up["alpha"] = alpha.value
    follow_up["net_edge_pips"] = net_edge
    follow_up["data_health"] = assess_data_health(
        rate_diff=book.rates.rate_diff,
        bid=tick.bid,
        ask=tick.ask,
        fair_value=fair_value,
        decision=decision.state.value,
    ).value
    return follow_up


def _decision(
    book: ForwardBook,
    config: EngineConfig,
    *,
    fair_value: float | None,
    alpha: AlphaLifecycle,
    pressure: float | None,
    regime: RegimeName,
    mode: ExecutionMode,
) -> tuple[Decision, ModelReadiness, float | None]:
    if book.rates.rate_diff is None:
        return (
            Decision(DecisionState.DATA_DEGRADED, None, EntryStrategy.WAIT, ["UK2Y or DE2Y is missing"]),
            ModelReadiness.NOT_READY if fair_value is None else ModelReadiness.LOW,
            None,
        )
    if fair_value is None:
        return (
            Decision(DecisionState.WATCH, None, EntryStrategy.WAIT, ["fair value is MODEL_NOT_READY"]),
            ModelReadiness.NOT_READY,
            None,
        )
    pips = (book.tick.mid - fair_value) / config.pip
    _edge, _cost, net = expected_net_edge_pips(
        pips,
        config.opportunity.prior_convergence_probability,
        config.costs,
        book.tick.spread_pips(config.pip),
    )
    inputs = DecisionInput(
        mispricing_pips=pips,
        fundamental=pressure,
        classification=MispricingClass.POSSIBLE_MISPRICING,
        regime=regime,
        alpha=alpha,
        crowding=None,
        net_edge_pips=net,
        missing_critical=False,
        stale_critical=False,
        chase=False,
        breakout_pending=False,
        reaction_lag_blocked=False,
    )
    readiness = ModelReadiness.READY if alpha is AlphaLifecycle.ACTIVE else ModelReadiness.LOW
    if mode is ExecutionMode.LIVE:
        return decide(inputs, config), readiness, net
    return _research_decision(inputs, config), readiness, net


def _research_decision(inputs: DecisionInput, config: EngineConfig) -> Decision:
    """Shadow and paper may sample a call while alpha is still DISCOVERY.

    The recorded reason says the sample is not an active-alpha claim. Every other
    gate is the same one `decide` uses.
    """
    base = decide(inputs, config)
    if base.state in {DecisionState.LONG, DecisionState.SHORT}:
        return base
    if base.state is not DecisionState.WATCH:
        return base
    if not any("alpha is" in reason for reason in base.reasons):
        return base
    probed = decide(replace(inputs, alpha=AlphaLifecycle.ACTIVE), config)
    if probed.state not in {DecisionState.LONG, DecisionState.SHORT}:
        return base
    return Decision(
        probed.state,
        probed.direction,
        probed.strategy,
        [*probed.reasons, "research sample; alpha is not ACTIVE"],
    )


def _follow(
    store: PitStore,
    config: EngineConfig,
    mode: ExecutionMode,
    book: ForwardBook,
    decision: Decision,
    readiness: ModelReadiness,
    regime: RegimeName,
    pressure: float | None,
    narratives: list[str],
    fair_value: float | None,
    alpha: AlphaLifecycle,
    ingested: datetime,
) -> dict[str, Any]:
    active = active_journal(store, mode.value)
    if active is not None:
        return _advance(store, config, mode, book, decision, readiness, regime, fair_value, alpha, ingested, active)
    tradable = decision.state in {DecisionState.LONG, DecisionState.SHORT}
    if tradable and decision.direction is not None and fair_value is not None:
        return _open(
            store,
            config,
            mode,
            book,
            decision,
            readiness,
            regime,
            pressure,
            narratives,
            fair_value,
            ingested,
        )
    return _view(mode, book, decision, readiness, fair_value, executed=False)


def _open(
    store: PitStore,
    config: EngineConfig,
    mode: ExecutionMode,
    book: ForwardBook,
    decision: Decision,
    readiness: ModelReadiness,
    regime: RegimeName,
    pressure: float | None,
    narratives: list[str],
    fair_value: float,
    ingested: datetime,
) -> dict[str, Any]:
    direction = decision.direction
    assert direction is not None
    fill = entry_fill(mode, decision.state, direction, book.tick, config)
    status = OpportunityStatus.ENTERED if fill is not None else OpportunityStatus.READY
    opportunity_id = stable_id(
        "forward-opportunity",
        {"mode": mode.value, "observed_at": dump_ts(book.tick.time), "decision": decision.state.value},
    )
    expected = abs(book.tick.mid - fair_value) / config.pip
    row = _journal_row(
        opportunity_id=opportunity_id,
        sequence=0,
        observed_at=book.tick.time,
        ingested_at=ingested,
        mode=mode,
        status=status,
        decision=decision,
        tick=book.tick,
        fair_value=fair_value,
        fill_price=fill,
        rate_diff=book.rates.rate_diff,
        readiness=readiness,
        detail={
            "reasons": list(decision.reasons),
            "narratives": narratives,
            "event_pressure": pressure,
            "regime": regime.value,
            "expected_pips": expected,
            "spread_source": "OBSERVED_BOOK",
            "executed": fill is not None,
        },
    )
    append_journal(store, row)
    return _view(
        mode,
        book,
        decision,
        readiness,
        fair_value,
        executed=fill is not None,
        status=status.value,
        opportunity_id=opportunity_id,
        fill_price=fill,
    )


def _advance(
    store: PitStore,
    config: EngineConfig,
    mode: ExecutionMode,
    book: ForwardBook,
    decision: Decision,
    readiness: ModelReadiness,
    regime: RegimeName,
    fair_value: float | None,
    alpha: AlphaLifecycle,
    ingested: datetime,
    active: dict[str, Any],
) -> dict[str, Any]:
    opening = opening_row(store, str(active["opportunity_id"]))
    frozen = _optional_float(opening["fair_value"])
    direction = Direction(str(opening["direction"]))
    entry_mid = float(opening["mid"])
    entry_fill = _optional_float(opening["fill_price"])
    entry_rate = _optional_float(opening["rate_diff"])
    entry_detail = load_detail(str(opening["detail"]))
    frozen_regime = RegimeName(str(entry_detail.get("regime") or regime.value))
    live_regime = regime if book.events else frozen_regime
    remaining = None if frozen is None else (book.tick.mid - frozen) / config.pip
    rate_change = None if entry_rate is None or book.rates.rate_diff is None else book.rates.rate_diff - entry_rate
    holding_days = (book.tick.time - parse_ts(str(opening["observed_at"]))).total_seconds() / 86400.0
    thesis = invalidation_reasons(
        direction=direction,
        rate_change=rate_change,
        policy_change=None,
        growth_change=None,
        inflation_change=None,
        risk_change=None,
        momentum_change=None,
        regime=live_regime,
        config=config,
    )
    exits = exit_reasons(
        remaining_pips=remaining,
        direction=direction,
        entry_price=entry_mid,
        market_price=book.tick.mid,
        fair_value=frozen if frozen is not None else book.tick.mid,
        holding_days=holding_days,
        alpha=alpha,
        thesis_reasons=thesis,
        spread_z=None,
        config=config,
    )
    status = OpportunityStatus(str(active["status"]))
    proposed = _proposed_status(exits)
    if proposed is None and frozen is not None and remaining is not None:
        entry_gap = (entry_mid - frozen) / config.pip
        if entry_gap != 0 and remaining * entry_gap > 0:
            if abs(remaining) <= abs(entry_gap) * (1.0 - config.exit.convergence_fraction):
                proposed = OpportunityStatus.CONVERGING
    fill: float | None = None
    research: float | None = None
    executable: float | None = None
    if proposed is not None and proposed is not status:
        status = transition(status, proposed)
        research = _signed_pips(direction, entry_mid, book.tick.mid, config.pip)
        if proposed in {OpportunityStatus.EXITED, OpportunityStatus.INVALIDATED} and entry_fill is not None:
            fill = exit_fill(direction, book.tick, config)
            executable = _signed_pips(direction, entry_fill, fill, config.pip)
        expected = entry_detail.get("expected_pips")
        captured = None if expected in (None, 0) or research is None else research / float(expected)
        review = "gap is closing" if proposed is OpportunityStatus.CONVERGING else _review(exits, research)
        detail = {
            "reasons": list(decision.reasons),
            "exit_reasons": [reason.value for reason in exits],
            "thesis": thesis,
            "fair_value_now": fair_value,
            "remaining_pips": remaining,
            "research_pips": research,
            "executable_pips": executable,
            "captured": captured,
            "review": review,
            "regime": live_regime.value,
        }
        append_journal(
            store,
            _journal_row(
                opportunity_id=str(opening["opportunity_id"]),
                sequence=int(active["sequence"]) + 1,
                observed_at=book.tick.time,
                ingested_at=ingested,
                mode=mode,
                status=status,
                decision=decision,
                tick=book.tick,
                fair_value=frozen,
                fill_price=fill,
                rate_diff=book.rates.rate_diff,
                readiness=readiness,
                detail=detail,
            ),
        )
    return _view(
        mode,
        book,
        decision,
        readiness,
        frozen,
        executed=False,
        status=status.value,
        opportunity_id=str(opening["opportunity_id"]),
        fill_price=fill,
        research_pips=research,
        executable_pips=executable,
    )


def _proposed_status(reasons: list[ExitReason]) -> OpportunityStatus | None:
    if not reasons:
        return None
    if any(reason in _CLOSED for reason in reasons):
        return OpportunityStatus.EXITED
    if ExitReason.THESIS_INVALIDATED in reasons or ExitReason.REGIME_CHANGE in reasons:
        return OpportunityStatus.INVALIDATED
    return OpportunityStatus.EXITED


def _review(reasons: list[ExitReason], pips: float | None) -> str:
    if ExitReason.THESIS_INVALIDATED in reasons:
        return "new information invalidated the thesis"
    if any(reason in _CLOSED for reason in reasons) and pips is not None and pips > 0:
        return "thesis held; mispricing closed"
    if pips is not None and pips < 0:
        return "thesis did not converge"
    return "position still open"


def _view(
    mode: ExecutionMode,
    book: ForwardBook,
    decision: Decision,
    readiness: ModelReadiness,
    fair_value: float | None,
    *,
    executed: bool,
    status: str | None = None,
    opportunity_id: str | None = None,
    fill_price: float | None = None,
    research_pips: float | None = None,
    executable_pips: float | None = None,
) -> dict[str, Any]:
    return {
        "mode": mode.value,
        "decision": decision.state.value,
        "direction": None if decision.direction is None else decision.direction.value,
        "reasons": list(decision.reasons),
        "model_readiness": readiness.value,
        "executed": executed,
        "live_order": "not armed" if mode is ExecutionMode.LIVE else None,
        "status": status,
        "opportunity_id": opportunity_id,
        "fill_price": fill_price,
        "fair_value": fair_value,
        "bid": book.tick.bid,
        "ask": book.tick.ask,
        "mid": book.tick.mid,
        "research_pips": research_pips,
        "executable_pips": executable_pips,
        "observed_at": dump_ts(book.tick.time),
    }


def _journal_row(
    *,
    opportunity_id: str,
    sequence: int,
    observed_at: datetime,
    ingested_at: datetime,
    mode: ExecutionMode,
    status: OpportunityStatus,
    decision: Decision,
    tick: Mt5Tick,
    fair_value: float | None,
    fill_price: float | None,
    rate_diff: float | None,
    readiness: ModelReadiness,
    detail: dict[str, Any],
) -> dict[str, Any]:
    return {
        "opportunity_id": opportunity_id,
        "sequence": sequence,
        "observed_at": dump_ts(observed_at),
        "ingested_at": dump_ts(ingested_at),
        "mode": mode.value,
        "status": status.value,
        "decision": decision.state.value,
        "direction": None if decision.direction is None else decision.direction.value,
        "bid": tick.bid,
        "ask": tick.ask,
        "mid": tick.mid,
        "fair_value": fair_value,
        "fill_price": fill_price,
        "rate_diff": rate_diff,
        "model_readiness": readiness.value,
        "detail": dump_detail(detail),
    }


def _persist_market(store: PitStore, tick: Mt5Tick, ingested: datetime) -> None:
    row = MarketObservation(
        instrument="EURGBP",
        pair="EURGBP",
        bar_size="1m",
        bid=tick.bid,
        ask=tick.ask,
        mid=tick.mid,
        effective_at=tick.time,
        released_at=tick.time,
        observed_at=tick.time,
        ingested_at=ingested,
        source=MT5_SOURCE,
        version=MT5_VERSION,
    )
    _skip(store, "market_observations", _model_row(row))


def _persist_rates(store: PitStore, rates: RateSnapshot, tick_time: datetime, ingested: datetime) -> None:
    prints = (("UK2Y", rates.uk2y, rates.uk2y_time), ("DE2Y", rates.de2y, rates.de2y_time))
    for curve_id, rate, stamp in prints:
        if rate is None or stamp is None:
            continue
        if stamp > tick_time or stamp > ingested:
            raise PointInTimeError(f"{curve_id} print is after the decision time")
        row = RateObservation(
            curve_id=curve_id,
            rate=rate,
            effective_at=stamp,
            released_at=stamp,
            observed_at=stamp,
            ingested_at=ingested,
            source=RATE_SOURCE,
            version=RATE_VERSION,
        )
        _skip(store, "rate_observations", _model_row(row))


def _persist_events(store: PitStore, events: list[EventInput], tick_time: datetime, ingested: datetime) -> None:
    for event in events:
        row = event_row(event, ingested_at=ingested, observed_at=tick_time, source="event-feed", version="event-v1")
        _skip(store, "information_events", row)


def _append_decision(
    store: PitStore,
    mode: ExecutionMode,
    tick: Mt5Tick,
    ingested: datetime,
    decision: Decision,
    readiness: ModelReadiness,
    rate_diff: float | None,
    fair_value: float | None,
    follow_up: dict[str, Any],
) -> None:
    _skip(
        store,
        "forward_decisions",
        {
            "mode": mode.value,
            "observed_at": dump_ts(tick.time),
            "ingested_at": dump_ts(ingested),
            "decision": decision.state.value,
            "direction": None if decision.direction is None else decision.direction.value,
            "model_readiness": readiness.value,
            "bid": tick.bid,
            "ask": tick.ask,
            "rate_diff": rate_diff,
            "fair_value": fair_value,
            "detail": dump_detail({"reasons": list(decision.reasons), "status": follow_up.get("status")}),
        },
    )


def _skip(store: PitStore, table: str, row: dict[str, Any]) -> None:
    try:
        store.insert_records(table, [row])
    except DataValidationError:
        return


def _model_row(model: MarketObservation | RateObservation) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in model.model_dump().items():
        payload[key] = dump_ts(value) if isinstance(value, datetime) else value
    return payload


def _regime(events: list[EventInput]) -> RegimeName:
    categories = {event.category for event in events if event.category}
    if categories & {"CPI", "CORE_CPI"}:
        return RegimeName.INFLATION_DIVERGENCE
    if categories & _GROWTH:
        return RegimeName.GROWTH_DIVERGENCE
    if any(policy_context(event.headline) is not None for event in events):
        return RegimeName.POLICY_DIVERGENCE
    return RegimeName.NEUTRAL


def _signed_pips(direction: Direction, entry: float, exit_price: float, pip: float) -> float:
    if direction is Direction.SHORT:
        return (entry - exit_price) / pip
    return (exit_price - entry) / pip


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
