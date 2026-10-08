"""EURGBP market orders for a long or short signal. A watch does not send anything.

A short signal closes this program's longs, then sells if it is not already short.
A long signal closes this program's shorts, then buys if it is not already long.
An exit closes the position and does not open the other side.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domain.errors import DataValidationError
from forward.terminal import _package, default_terminal

SYMBOL = "EURGBP"
MAGIC = 20261007
COMMENT = "rv-eurgbp"
DEVIATION = 20
_DONE = frozenset({10009, 10010})


@dataclass(frozen=True)
class Held:
    ticket: int
    side: str
    volume: float


@dataclass(frozen=True)
class OrderPlan:
    close: tuple[Held, ...]
    open_side: str | None


def plan_orders(decision: str | None, assessment: str, positions: list[Held], lots: float) -> OrderPlan:
    """Which of our EURGBP positions to close, and whether a new side is still needed."""
    if assessment == "REVERSAL":
        return OrderPlan(close=tuple(positions), open_side=None)
    if decision not in {"LONG", "SHORT"} or lots <= 0:
        return OrderPlan(close=(), open_side=None)
    opposite = tuple(item for item in positions if item.side != decision)
    same = any(item.side == decision for item in positions)
    return OrderPlan(close=opposite, open_side=None if same else decision)


def align_eurgbp(
    terminal: str | None,
    decision: str | None,
    assessment: str,
    lots: float,
    log_path: str | Path,
) -> dict[str, Any]:
    """Send the plan to the local terminal. Other symbols and other programs stay untouched."""
    plan = plan_orders(decision, assessment, [], lots)
    if plan.open_side is None and assessment != "REVERSAL" and decision not in {"LONG", "SHORT"}:
        report = {"order_sent": False, "action": "none", "live_order": None, "orders": []}
        return report
    mt5 = _package()
    path = terminal if terminal else default_terminal()
    started = mt5.initialize(path) if path else mt5.initialize()
    if not started:
        code, message = mt5.last_error()
        raise DataValidationError(f"MT5 initialize failed: {code} {message}")
    try:
        if not mt5.symbol_select(SYMBOL, True):
            raise DataValidationError("MT5 could not select EURGBP")
        info = mt5.symbol_info(SYMBOL)
        tick = mt5.symbol_info_tick(SYMBOL)
        if info is None or tick is None:
            raise DataValidationError("MT5 has no EURGBP book")
        held = _held(mt5)
        plan = plan_orders(decision, assessment, held, lots)
        volume = _volume(lots, info)
        sent: list[dict[str, Any]] = []
        for item in plan.close:
            sent.append(_send(mt5, _close_request(mt5, info, tick, item)))
        if plan.open_side is not None:
            blocked = _open_block(plan.open_side, sent, _held(mt5))
            if blocked is not None:
                sent.append(blocked)
            elif volume is None:
                sent.append({"ok": False, "side": plan.open_side, "error": "lots are below the broker minimum"})
            else:
                sent.append(_send(mt5, _open_request(mt5, info, tick, plan.open_side, volume)))
        action = "flat" if assessment == "REVERSAL" else plan.open_side or "hold"
        report = {
            "order_sent": any(item.get("ok") for item in sent),
            "action": action,
            "live_order": _summary(sent),
            "orders": sent,
            "decision": decision,
            "assessment": assessment,
        }
        if sent:
            _append(log_path, report)
        return report
    finally:
        mt5.shutdown()


def _open_block(side: str, sent: list[dict[str, Any]], held: list[Held]) -> dict[str, Any] | None:
    """Skip the new ticket unless every close succeeded and the book is flat on that side."""
    if any(item.get("ok") is not True for item in sent):
        return {"ok": False, "side": side, "error": "close failed; new side was not opened"}
    if any(item.side != side for item in held):
        return {"ok": False, "side": side, "error": "opposite position is still open"}
    if any(item.side == side for item in held):
        return {"ok": False, "side": side, "error": "already open"}
    return None


def _held(mt5: Any) -> list[Held]:
    rows = mt5.positions_get(symbol=SYMBOL) or []
    held: list[Held] = []
    buy = int(getattr(mt5, "POSITION_TYPE_BUY", 0))
    for row in rows:
        if int(getattr(row, "magic", -1)) != MAGIC:
            continue
        side = "LONG" if int(row.type) == buy else "SHORT"
        held.append(Held(ticket=int(row.ticket), side=side, volume=float(row.volume)))
    return held


def _volume(lots: float, info: Any) -> float | None:
    step = float(getattr(info, "volume_step", 0.01) or 0.01)
    minimum = float(getattr(info, "volume_min", step) or step)
    maximum = float(getattr(info, "volume_max", lots) or lots)
    if lots + 1e-9 < minimum or step <= 0:
        return None
    steps = math.floor((lots + 1e-9) / step)
    volume = round(steps * step, 8)
    if volume + 1e-9 < minimum:
        return None
    return min(volume, maximum)


def _filling(mt5: Any, info: Any) -> int:
    allowed = int(getattr(info, "filling_mode", 0) or 0)
    if allowed & 2:
        return int(getattr(mt5, "ORDER_FILLING_IOC", 1))
    if allowed & 1:
        return int(getattr(mt5, "ORDER_FILLING_FOK", 0))
    return int(getattr(mt5, "ORDER_FILLING_RETURN", 2))


def _close_request(mt5: Any, info: Any, tick: Any, item: Held) -> dict[str, Any]:
    sell = item.side == "LONG"
    return {
        "action": int(getattr(mt5, "TRADE_ACTION_DEAL", 1)),
        "symbol": SYMBOL,
        "position": item.ticket,
        "volume": item.volume,
        "type": int(getattr(mt5, "ORDER_TYPE_SELL", 1) if sell else getattr(mt5, "ORDER_TYPE_BUY", 0)),
        "price": float(tick.bid if sell else tick.ask),
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": COMMENT,
        "type_time": int(getattr(mt5, "ORDER_TIME_GTC", 0)),
        "type_filling": _filling(mt5, info),
    }


def _open_request(mt5: Any, info: Any, tick: Any, side: str, volume: float) -> dict[str, Any]:
    buy = side == "LONG"
    return {
        "action": int(getattr(mt5, "TRADE_ACTION_DEAL", 1)),
        "symbol": SYMBOL,
        "volume": volume,
        "type": int(getattr(mt5, "ORDER_TYPE_BUY", 0) if buy else getattr(mt5, "ORDER_TYPE_SELL", 1)),
        "price": float(tick.ask if buy else tick.bid),
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": COMMENT,
        "type_time": int(getattr(mt5, "ORDER_TIME_GTC", 0)),
        "type_filling": _filling(mt5, info),
    }


def _send(mt5: Any, request: dict[str, Any]) -> dict[str, Any]:
    result = mt5.order_send(request)
    if result is None:
        code, message = mt5.last_error()
        return {"ok": False, "request": request["type"], "error": f"{code} {message}"}
    retcode = int(getattr(result, "retcode", -1))
    closing = "position" in request
    side = "LONG" if request["type"] == int(getattr(mt5, "ORDER_TYPE_BUY", 0)) else "SHORT"
    label = f"close {request['volume']}" if closing else f"open {side} {request['volume']}"
    return {
        "ok": retcode in _DONE,
        "retcode": retcode,
        "side": label,
        "volume": request["volume"],
        "position": request.get("position"),
        "comment": getattr(result, "comment", ""),
    }


def _summary(sent: list[dict[str, Any]]) -> str | None:
    if not sent:
        return None
    parts = []
    for item in sent:
        if item.get("ok"):
            parts.append(f"{item.get('side')} {item.get('volume')}")
        else:
            parts.append(str(item.get("error") or item.get("comment") or item.get("retcode")))
    return "; ".join(parts)


def _append(path: str | Path, row: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
