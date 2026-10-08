"""One automatic round. Shadow, paper, and live share `run_forward`; only the adapter changes.

The opening journal row is never edited. Each run appends one log line.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from data.store import PitStore
from domain.config import EngineConfig
from domain.enums import AlphaLifecycle, DecisionState, ExecutionMode, ModelReadiness
from domain.errors import PointInTimeError
from domain.timeutil import dump_ts, ensure_utc
from forward.cycle import ForwardBook, _persist_events, _persist_rates, _skip, run_forward
from forward.events import EventInput, combined_pressure, narratives_for
from forward.journal import dump_detail, load_detail
from forward.mt5 import Mt5Tick
from forward.official_rates import DE_SOURCE, UK_SOURCE
from forward.prints import InformationPrint, as_events, print_payload, usable_prints
from forward.rates import RateSnapshot
from forward.standardize import standardize_events


@dataclass(frozen=True)
class RoundInputs:
    prints: list[InformationPrint]
    information_error: str | None
    rates: RateSnapshot
    rate_error: str | None
    tick: Mt5Tick | None
    quote_error: str | None


def run_round(
    store: PitStore,
    config: EngineConfig,
    mode: ExecutionMode | str,
    inputs: RoundInputs,
    *,
    ingested_at: datetime,
    fair_value: float | None,
    log_path: str | Path,
    alpha: AlphaLifecycle = AlphaLifecycle.DISCOVERY,
) -> dict[str, Any]:
    execution = mode if isinstance(mode, ExecutionMode) else ExecutionMode(str(mode).upper())
    ingested = ensure_utc(ingested_at)
    prints = usable_prints(inputs.prints, ingested)
    events = standardize_events(store, as_events(prints), config)
    tick = inputs.tick
    if tick is not None and tick.time > ingested:
        ingested = tick.time
    if tick is None:
        outcome = _without_quote(store, execution, inputs.rates, events, ingested, fair_value)
    else:
        try:
            outcome = _with_quote(store, config, execution, inputs.rates, events, tick, ingested, fair_value, alpha)
        except PointInTimeError as exc:
            outcome = _stopped(
                execution,
                DecisionState.DATA_DEGRADED,
                ModelReadiness.NOT_READY if fair_value is None else ModelReadiness.LOW,
                [str(exc)],
                combined_pressure(events),
                narratives_for(events),
                fair_value,
            )
    payload = _payload(execution, inputs, prints, outcome, ingested, fair_value)
    append_round_log(log_path, payload)
    return payload


def append_round_log(path: str | Path, row: dict[str, Any]) -> None:
    """Append one JSON line. Existing lines, including opening records, are not rewritten."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, sort_keys=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _with_quote(
    store: PitStore,
    config: EngineConfig,
    mode: ExecutionMode,
    rates: RateSnapshot,
    events: list[EventInput],
    tick: Mt5Tick,
    ingested: datetime,
    fair_value: float | None,
    alpha: AlphaLifecycle,
) -> dict[str, Any]:
    result = run_forward(
        store,
        config,
        mode,
        ForwardBook(tick=tick, rates=rates, events=events),
        ingested_at=ingested,
        fair_value=fair_value,
        alpha=alpha,
    )
    if result.get("duplicate"):
        recorded = _duplicate(store, mode, tick, result)
        recorded["event_pressure"] = combined_pressure(events)
        recorded["narratives"] = narratives_for(events)
        return recorded
    result["live_order"] = "not armed" if mode is ExecutionMode.LIVE else None
    return result


def _without_quote(
    store: PitStore,
    mode: ExecutionMode,
    rates: RateSnapshot,
    events: list[EventInput],
    ingested: datetime,
    fair_value: float | None,
) -> dict[str, Any]:
    reasons = ["EURGBP bid/ask is missing"]
    if rates.rate_diff is None:
        reasons.insert(0, "UK2Y or DE2Y is missing")
    readiness = ModelReadiness.NOT_READY if fair_value is None else ModelReadiness.LOW
    _persist_rates(store, rates, ingested, ingested)
    _persist_events(store, events, ingested, ingested)
    _skip(
        store,
        "forward_decisions",
        {
            "mode": mode.value,
            "observed_at": dump_ts(ingested),
            "ingested_at": dump_ts(ingested),
            "decision": DecisionState.DATA_DEGRADED.value,
            "direction": None,
            "model_readiness": readiness.value,
            "bid": None,
            "ask": None,
            "rate_diff": rates.rate_diff,
            "fair_value": fair_value,
            "detail": dump_detail({"reasons": reasons, "status": None}),
        },
    )
    return _stopped(
        mode,
        DecisionState.DATA_DEGRADED,
        readiness,
        reasons,
        combined_pressure(events),
        narratives_for(events),
        fair_value,
    )


def _duplicate(store: PitStore, mode: ExecutionMode, tick: Mt5Tick, result: dict[str, Any]) -> dict[str, Any]:
    stored = _stored_decision(store, mode.value, tick.time)
    reasons: list[str] = []
    status = None
    decision = None
    readiness = None
    direction = None
    if stored is not None:
        detail = load_detail(str(stored["detail"]))
        raw_reasons = detail.get("reasons")
        if isinstance(raw_reasons, list):
            reasons = [str(item) for item in raw_reasons]
        status = detail.get("status")
        decision = stored["decision"]
        readiness = stored["model_readiness"]
        direction = stored["direction"]
    return {
        "duplicate": True,
        "mode": mode.value,
        "decision": decision,
        "direction": direction,
        "reasons": reasons,
        "model_readiness": readiness,
        "executed": False,
        "live_order": "not armed" if mode is ExecutionMode.LIVE else None,
        "status": status,
        "opportunity_id": None,
        "fill_price": None,
        "fair_value": None if stored is None else stored.get("fair_value"),
        "observed_at": result.get("observed_at"),
        "event_pressure": None,
        "narratives": [],
    }


def _stopped(
    mode: ExecutionMode,
    decision: DecisionState,
    readiness: ModelReadiness,
    reasons: list[str],
    pressure: float | None,
    narratives: list[str],
    fair_value: float | None,
) -> dict[str, Any]:
    return {
        "duplicate": False,
        "mode": mode.value,
        "decision": decision.value,
        "direction": None,
        "reasons": reasons,
        "model_readiness": readiness.value,
        "executed": False,
        "live_order": "not armed" if mode is ExecutionMode.LIVE else None,
        "status": None,
        "opportunity_id": None,
        "fill_price": None,
        "fair_value": fair_value,
        "event_pressure": pressure,
        "narratives": narratives,
    }


def _payload(
    mode: ExecutionMode,
    inputs: RoundInputs,
    prints: list[InformationPrint],
    outcome: dict[str, Any],
    ingested: datetime,
    fair_value: float | None,
) -> dict[str, Any]:
    tick = inputs.tick
    quote = None
    if tick is not None:
        quote = {
            "symbol": tick.symbol,
            "time": dump_ts(tick.time),
            "bid": tick.bid,
            "ask": tick.ask,
            "mid": tick.mid,
            "spread": tick.ask - tick.bid,
        }
    rates = inputs.rates
    return {
        "ingested_at": dump_ts(ingested),
        "mode": mode.value,
        "inputs": {
            "information": [print_payload(item) for item in prints],
            "information_error": inputs.information_error,
            "rates": {
                "uk2y": rates.uk2y,
                "de2y": rates.de2y,
                "uk2y_time": None if rates.uk2y_time is None else dump_ts(rates.uk2y_time),
                "de2y_time": None if rates.de2y_time is None else dump_ts(rates.de2y_time),
                "rate_diff": rates.rate_diff,
                "unit": "percentage_points",
                "uk_source": UK_SOURCE,
                "de_source": DE_SOURCE,
            },
            "rate_error": inputs.rate_error,
            "quote": quote,
            "quote_error": inputs.quote_error,
            "quote_source": "mt5-symbol-info-tick" if tick is not None else None,
            "fair_value": fair_value,
        },
        "decision": outcome.get("decision"),
        "direction": outcome.get("direction"),
        "reasons": outcome.get("reasons"),
        "model_readiness": outcome.get("model_readiness"),
        "event_pressure": outcome.get("event_pressure"),
        "narratives": outcome.get("narratives"),
        "alpha": outcome.get("alpha"),
        "net_edge_pips": outcome.get("net_edge_pips"),
        "data_health": outcome.get("data_health"),
        "executed": outcome.get("executed"),
        "live_order": outcome.get("live_order"),
        "status": outcome.get("status"),
        "opportunity_id": outcome.get("opportunity_id"),
        "fill_price": outcome.get("fill_price"),
        "duplicate": bool(outcome.get("duplicate")),
        "observed_at": outcome.get("observed_at"),
    }


def _stored_decision(store: PitStore, mode: str, observed_at: datetime) -> dict[str, Any] | None:
    stamp = dump_ts(observed_at)
    for row in store.rows("forward_decisions"):
        if row["mode"] == mode and row["observed_at"] == stamp:
            return row
    return None
