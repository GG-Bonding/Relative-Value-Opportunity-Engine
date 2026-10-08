"""Stay up after startup. Evaluate when new information arrives.

The first pass records the book already known. Later passes run only for a new
Jin10 item, a new official rate day, a new rate-model price, or a new tick while
a position is open. An armed trader may flatten or flip EURGBP when the call is
long, short, or an exit. Sequence 0 stays frozen.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from data.store import PitStore
from domain.config import EngineConfig
from domain.enums import ExecutionMode
from domain.errors import DataValidationError
from domain.timeutil import UTC, dump_ts, ensure_utc
from execution.live_gate import live_order_allowed
from forward.collect import fetch_curves, load_round_inputs
from forward.journal import active_journal
from forward.outcomes import current_lifecycle, score_due, write_alpha
from forward.prints import usable_prints
from forward.rates import RateSnapshot
from forward.round import RoundInputs, run_round

RATE_REFRESH = timedelta(minutes=30)
_REVERSAL = frozenset({"EXITED", "INVALIDATED"})
_OPENING = frozenset({"READY", "ENTERED"})


@dataclass
class WatchMemory:
    event_ids: set[str] = field(default_factory=set)
    rate_key: str | None = None
    tick_key: str | None = None
    started: bool = False


def watch_triggers(
    memory: WatchMemory,
    inputs: RoundInputs,
    *,
    ingested_at: datetime,
    position_open: bool,
    reprice: bool = False,
) -> list[str]:
    """Name why this poll should be judged. An empty list means nothing new arrived."""
    if not memory.started:
        return ["baseline"]
    triggers: list[str] = []
    fresh = _fresh_prints(memory, inputs, ingested_at)
    if fresh:
        triggers.append("new-information")
    rate_key = _rate_key(inputs.rates)
    if memory.rate_key is not None and rate_key != memory.rate_key:
        triggers.append("rate-print")
    tick_key = None if inputs.tick is None else dump_ts(inputs.tick.time)
    if position_open and tick_key is not None and tick_key != memory.tick_key:
        triggers.append("open-book")
    if reprice and tick_key is not None and tick_key != memory.tick_key:
        triggers.append("price")
    return triggers


def remember(memory: WatchMemory, inputs: RoundInputs, ingested_at: datetime) -> WatchMemory:
    prints = usable_prints(inputs.prints, ingested_at)
    tick_key = memory.tick_key if inputs.tick is None else dump_ts(inputs.tick.time)
    return WatchMemory(
        event_ids=memory.event_ids | {item.event_id for item in prints},
        rate_key=_rate_key(inputs.rates),
        tick_key=tick_key,
        started=True,
    )


def assessment(result: dict[str, Any]) -> str:
    """SIGNAL is a new long or short. REVERSAL closes or kills the open book."""
    status = result.get("status")
    if status in _REVERSAL:
        return "REVERSAL"
    decision = result.get("decision")
    if decision in {"LONG", "SHORT"} and status in _OPENING and not result.get("duplicate"):
        return "SIGNAL"
    return "UPDATE"


def watch_loop(
    store: PitStore,
    config: EngineConfig,
    mode: ExecutionMode | str,
    *,
    terminal: str | None,
    fair_value: float | None,
    log_path: str | Path,
    interval_seconds: float,
    load_inputs: Callable[[datetime, tuple[Any, Any, str | None] | None], RoundInputs] | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], datetime] | None = None,
    max_cycles: int | None = None,
    emit: Callable[[dict[str, Any]], None] | None = None,
    fetch_rates: Callable[[], tuple[Any, Any, str | None]] | None = None,
    quote_fair_value: Callable[[datetime], float | None] | None = None,
    trader: Callable[[Any, str], dict[str, Any]] | None = None,
) -> int:
    """Poll until stopped. Orders are sent only when a trader is armed and the call is long, short, or an exit."""
    execution = mode if isinstance(mode, ExecutionMode) else ExecutionMode(str(mode).upper())
    reader = load_inputs or (lambda moment, curves: load_round_inputs(terminal, moment, curves))
    pause = sleep or _sleep
    now = clock or (lambda: datetime.now(tz=UTC))
    write = emit or _emit
    pull_rates = fetch_rates or fetch_curves
    memory = WatchMemory()
    curves: tuple[Any, Any, str | None] | None = None
    curves_at: datetime | None = None
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        moment = ensure_utc(now())
        if curves is None or curves_at is None or moment - curves_at >= RATE_REFRESH:
            curves = pull_rates()
            curves_at = moment
        inputs = reader(moment, curves)
        snapshot = score_due(store, inputs.tick, moment, config)
        write_alpha(str(log_path), snapshot, moment)
        model_value = fair_value
        if model_value is None and quote_fair_value is not None:
            model_value = quote_fair_value(moment)
        triggers = watch_triggers(
            memory,
            inputs,
            ingested_at=moment,
            position_open=active_journal(store, execution.value) is not None,
            reprice=model_value is not None,
        )
        if triggers:
            result = run_round(
                store,
                config,
                execution,
                inputs,
                ingested_at=moment,
                fair_value=model_value,
                log_path=log_path,
                alpha=current_lifecycle(store, config),
            )
            notice = _notice(triggers, _arrived(memory, inputs, moment, triggers), result, inputs)
            notice["alpha"] = snapshot
            if trader is not None:
                allowed, why = live_order_allowed(
                    mode=str(result.get("mode") or execution.value),
                    alpha=_text(result.get("alpha")),
                    model_readiness=_text(result.get("model_readiness")),
                    data_health=_text(result.get("data_health")),
                    decision=_text(result.get("decision")),
                    net_edge_pips=_pips(result.get("net_edge_pips")),
                    min_net_edge_pips=config.opportunity.min_net_edge_pips,
                    assessment=str(notice["assessment"]),
                )
                if allowed:
                    try:
                        trade = trader(result.get("decision"), str(notice["assessment"]))
                    except (DataValidationError, OSError, ValueError, TypeError) as exc:
                        trade = {"order_sent": False, "action": "error", "live_order": str(exc), "orders": []}
                    notice["trade"] = trade
                    notice["order_sent"] = bool(trade.get("order_sent"))
                    notice["live_order"] = trade.get("live_order")
                elif result.get("decision") in {"LONG", "SHORT"} or notice["assessment"] == "REVERSAL":
                    notice["live_order"] = why
                    notice["order_sent"] = False
            write(notice)
            memory = remember(memory, inputs, moment)
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        pause(interval_seconds)
    return 0


def _fresh_prints(memory: WatchMemory, inputs: RoundInputs, ingested_at: datetime) -> list[str]:
    return [
        item.event_id for item in usable_prints(inputs.prints, ingested_at) if item.event_id not in memory.event_ids
    ]


def _rate_key(rates: RateSnapshot) -> str:
    uk_time = None if rates.uk2y_time is None else dump_ts(rates.uk2y_time)
    de_time = None if rates.de2y_time is None else dump_ts(rates.de2y_time)
    return f"{uk_time}|{rates.uk2y}|{de_time}|{rates.de2y}"


def _arrived(
    memory: WatchMemory,
    inputs: RoundInputs,
    ingested_at: datetime,
    triggers: list[str],
) -> list[str]:
    prints = usable_prints(inputs.prints, ingested_at)
    if "new-information" in triggers:
        return [item.headline for item in prints if item.event_id not in memory.event_ids]
    if "baseline" in triggers:
        carrying = [item for item in prints if item.surprise not in {None, 0.0}]
        if not carrying:
            carrying = [item for item in prints if item.kind == "CALENDAR" and item.country in {"UK", "EZ"}]
        if not carrying:
            carrying = [item for item in prints if item.kind != "NEWS"]
        return [item.headline for item in carrying[:8]]
    return []


def _notice(
    triggers: list[str],
    headlines: list[str],
    result: dict[str, Any],
    inputs: RoundInputs,
) -> dict[str, Any]:
    quote = None if inputs.tick is None else {"bid": inputs.tick.bid, "ask": inputs.tick.ask}
    recorded = result.get("inputs")
    recorded_fair = recorded.get("fair_value") if isinstance(recorded, dict) else None
    return {
        "trigger": triggers,
        "assessment": assessment(result),
        "decision": result.get("decision"),
        "direction": result.get("direction"),
        "status": result.get("status"),
        "model_readiness": result.get("model_readiness"),
        "reasons": result.get("reasons"),
        "narratives": result.get("narratives"),
        "headlines": headlines,
        "quote": quote,
        "rate_diff": inputs.rates.rate_diff,
        "fair_value": recorded_fair,
        "executed": result.get("executed"),
        "live_order": result.get("live_order"),
        "order_sent": False,
    }


def _emit(notice: dict[str, Any]) -> None:
    import json
    import sys

    text = json.dumps(notice, ensure_ascii=False)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _pips(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
