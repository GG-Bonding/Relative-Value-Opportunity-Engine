"""EURGBP bid and ask from the local MetaTrader 5 terminal.

The official MetaTrader5 package reads SymbolInfoTick. This module does not
send an order. A missing tick is a missing book, not a Jin10 cross.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from domain.errors import DataValidationError
from domain.timeutil import UTC
from forward.mt5 import Mt5Tick, parse_mt5_tick


def default_terminal() -> str | None:
    configured = os.environ.get("MT5_TERMINAL", "").strip()
    if configured:
        return configured
    sibling = Path(__file__).resolve().parents[3] / "MetaTrader_init" / "terminal64.exe"
    if sibling.is_file():
        return str(sibling)
    return None


def read_eurgbp_tick(terminal: str | None = None) -> Mt5Tick:
    mt5 = _package()
    path = terminal if terminal else default_terminal()
    started = mt5.initialize(path) if path else mt5.initialize()
    if not started:
        code, message = mt5.last_error()
        raise DataValidationError(f"MT5 initialize failed: {code} {message}")
    try:
        if not mt5.symbol_select("EURGBP", True):
            raise DataValidationError("MT5 could not select EURGBP")
        tick = mt5.symbol_info_tick("EURGBP")
        if tick is None:
            raise DataValidationError("MT5 returned no EURGBP tick")
        stamp = getattr(tick, "time_msc", None)
        raw: Any
        if isinstance(stamp, int | float) and not isinstance(stamp, bool) and stamp > 0:
            raw = stamp
        else:
            raw = tick.time
        when = raw.isoformat() if isinstance(raw, datetime) else raw
        return parse_mt5_tick({"symbol": "EURGBP", "time": when, "bid": tick.bid, "ask": tick.ask})
    finally:
        mt5.shutdown()


def read_eurgbp_closes(terminal: str | None = None, count: int = 800) -> dict[date, float]:
    """Daily closes from the local terminal. The newest bar can still be forming."""
    mt5 = _package()
    path = terminal if terminal else default_terminal()
    started = mt5.initialize(path) if path else mt5.initialize()
    if not started:
        code, message = mt5.last_error()
        raise DataValidationError(f"MT5 initialize failed: {code} {message}")
    try:
        if not mt5.symbol_select("EURGBP", True):
            raise DataValidationError("MT5 could not select EURGBP")
        bars = mt5.copy_rates_from_pos("EURGBP", mt5.TIMEFRAME_D1, 0, count)
        if bars is None:
            raise DataValidationError("MT5 returned no EURGBP daily bars")
        closes: dict[date, float] = {}
        for bar in bars:
            stamp = int(bar["time"])
            close = float(bar["close"])
            if stamp <= 0 or close <= 0:
                continue
            closes[datetime.fromtimestamp(stamp, UTC).date()] = close
        if not closes:
            raise DataValidationError("MT5 daily bars had no EURGBP close")
        return closes
    finally:
        mt5.shutdown()


def _package() -> Any:
    loaded = sys.modules.get("MetaTrader5")
    if loaded is not None:
        return loaded
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise DataValidationError("MetaTrader5 package is not installed") from exc
    return mt5
