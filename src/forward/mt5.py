"""EURGBP quotes in the shape an MT5 terminal writes.

The official MetaTrader5 Python package does not run on macOS. A small Expert
Advisor writes the latest tick; this module only reads that file. It does not
send an order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from domain.errors import DataValidationError
from domain.models import require_eurgbp
from domain.timeutil import ensure_utc, parse_ts


@dataclass(frozen=True)
class Mt5Tick:
    symbol: str
    time: datetime
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    def spread_pips(self, pip: float) -> float:
        return (self.ask - self.bid) / pip


def load_mt5_tick(path: str | Path) -> Mt5Tick:
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise DataValidationError(f"MT5 tick file is empty: {path}")
    payload = json.loads(text.splitlines()[-1])
    if not isinstance(payload, dict):
        raise DataValidationError("MT5 tick file must contain a JSON object")
    return parse_mt5_tick(payload)


def parse_mt5_tick(payload: dict[str, Any]) -> Mt5Tick:
    symbol = str(payload.get("symbol") or "")
    require_eurgbp(symbol)
    bid = _price(payload.get("bid"), "bid")
    ask = _price(payload.get("ask"), "ask")
    if bid > ask:
        raise DataValidationError("bid cannot exceed ask")
    return Mt5Tick(symbol=symbol, time=parse_feed_time(payload.get("time")), bid=bid, ask=ask)


def _price(value: Any, name: str) -> float:
    if value is None or isinstance(value, bool):
        raise DataValidationError(f"MT5 tick is missing {name}")
    try:
        price = float(value)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"MT5 tick {name} is not a number") from exc
    if price <= 0:
        raise DataValidationError(f"MT5 tick {name} must be positive")
    return price


def parse_feed_time(value: Any) -> datetime:
    if isinstance(value, int | float) and not isinstance(value, bool):
        stamp = float(value)
        if stamp > 10_000_000_000:
            stamp /= 1000.0
        return datetime.fromtimestamp(stamp, tz=UTC)
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError("MT5 tick is missing time")
    return ensure_utc(parse_ts(value.strip().replace("Z", "+00:00")))
